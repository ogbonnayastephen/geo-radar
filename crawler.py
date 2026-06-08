"""
AEO Radar — Site Crawler.

Takes a homepage URL and maps every page on the site by following
internal links. Then matches each query to the most relevant page
automatically, so the team never has to hunt for URLs manually.

Uses only requests + BeautifulSoup — no extra dependencies needed.
"""

import time
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from anthropic import Anthropic
import os
import json

REQUEST_TIMEOUT = 15
CLAUDE_MODEL    = "claude-sonnet-4-6"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (AEO-Radar site crawler; research tool)"
}


# ---------------------------------------------------------------------------
# PAGE MAP BUILDER
# ---------------------------------------------------------------------------
def crawl_site(homepage_url: str, max_pages: int = 60, progress_callback=None) -> list[dict]:
    """
    Starting from homepage_url, follow every internal link and collect:
      - url
      - title
      - meta description (if present)
      - first 300 characters of body text

    Returns a list of page dicts. Stops at max_pages to keep it fast.
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    base_domain = urlparse(homepage_url).netloc.replace("www.", "")
    visited     = set()
    to_visit    = [homepage_url]
    pages       = []

    progress(f"Starting crawl of {base_domain}...")

    while to_visit and len(pages) < max_pages:
        url = to_visit.pop(0)

        # Normalise and skip if already visited
        clean_url = url.split("#")[0].rstrip("/")
        if clean_url in visited:
            continue
        visited.add(clean_url)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("Content-Type", ""):
                continue
        except Exception:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract page info
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta  = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag and meta_tag.get("content"):
            meta = meta_tag["content"].strip()

        # Clean body text snippet
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        body_text = " ".join(soup.get_text(separator=" ").split())[:300]

        pages.append({
            "url":     url,
            "title":   title,
            "meta":    meta,
            "snippet": body_text,
        })

        progress(f"Crawled {len(pages)} pages... ({url[:60]})")

        # Collect internal links
        for a_tag in soup.find_all("a", href=True):
            href     = a_tag["href"].strip()
            full_url = urljoin(url, href).split("#")[0].rstrip("/")
            parsed   = urlparse(full_url)

            # Only follow same-domain, http/https links not yet visited
            if (
                parsed.scheme in ("http", "https")
                and base_domain in parsed.netloc
                and full_url not in visited
                and full_url not in to_visit
            ):
                to_visit.append(full_url)

        time.sleep(0.3)  # polite crawling

    progress(f"Crawl complete. Found {len(pages)} pages.")
    return pages


# ---------------------------------------------------------------------------
# QUERY-TO-PAGE MATCHER
# ---------------------------------------------------------------------------
def match_queries_to_pages(
    queries: list[str],
    pages: list[dict],
    org_name: str,
    progress_callback=None,
) -> dict[str, str]:
    """
    For each query, find the most relevant page from the crawled site map.
    Uses Claude to do the matching — it reads page titles, meta descriptions,
    and snippets to decide which page best answers each query.

    Returns: {query: best_page_url}
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {q: "" for q in queries}

    client = Anthropic(api_key=api_key)

    # Build a compact site map for Claude to reason over
    site_map = []
    for i, p in enumerate(pages):
        site_map.append({
            "index":   i,
            "url":     p["url"],
            "title":   p["title"],
            "meta":    p["meta"],
            "snippet": p["snippet"][:150],
        })

    progress("Matching queries to pages...")

    prompt = f"""You are helping match search queries to the most relevant pages
on {org_name}'s website.

Here is the site map (page index, URL, title, meta description, content snippet):
{json.dumps(site_map, indent=2)}

Here are the queries to match:
{json.dumps(queries, indent=2)}

For each query, return the index number of the single most relevant page.
If no page is a good match, return null for that query.

Return ONLY valid JSON, no markdown fences, in this exact format:
{{
  "query text exactly as given": <page_index or null>,
  ...
}}"""

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].replace("json", "", 1).strip()
        matches = json.loads(raw)
    except Exception:
        return {q: "" for q in queries}

    # Convert index back to URL
    result = {}
    for query, idx in matches.items():
        if idx is not None and 0 <= int(idx) < len(pages):
            result[query] = pages[int(idx)]["url"]
        else:
            result[query] = ""

    progress("Matching complete.")
    return result


# ---------------------------------------------------------------------------
# FULL PIPELINE
# ---------------------------------------------------------------------------
def map_site_and_match(
    homepage_url: str,
    queries: list[str],
    org_name: str,
    max_pages: int = 60,
    progress_callback=None,
) -> dict:
    """
    Full pipeline:
      1. Crawl the site and build a page map.
      2. Match each query to its best page.

    Returns: {matches: {query: url}, pages: [...], error: None}
    """
    if not homepage_url.startswith("http"):
        homepage_url = f"https://{homepage_url}"

    pages = crawl_site(homepage_url, max_pages=max_pages, progress_callback=progress_callback)

    if not pages:
        return {
            "matches": {q: "" for q in queries},
            "pages":   [],
            "error":   "Could not crawl the site. Check the URL and try again.",
        }

    matches = match_queries_to_pages(queries, pages, org_name, progress_callback=progress_callback)

    return {
        "matches": matches,
        "pages":   pages,
        "error":   None,
    }
