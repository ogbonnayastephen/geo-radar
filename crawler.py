"""
GEO Radar — Site Crawler.

Sitemap-first strategy: checks sitemap.xml / sitemap_index.xml first so every
indexable URL is discovered in one request regardless of site size. Falls back
to BFS link-following (cap 150 pages) for sites without a sitemap.

Also provides auto_extract_business_info() which reads a homepage and asks
Claude to return org name, services, audience, and domains — replacing the
manual discovery form with a single URL input.
"""

import json
import time
import xml.etree.ElementTree as ET
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup

from config import Keys
from prompts import CLAUDE_MODEL
from radar import is_safe_url

REQUEST_TIMEOUT = 15

HEADERS = {"User-Agent": "Mozilla/5.0 (GEO-Radar site crawler; research tool)"}

# URL path segments that almost never contain auditable content
_SKIP_PATTERNS = (
    "/tag/", "/page/", "/author/", "/feed/", "/category/",
    "/wp-json/", "/wp-admin/", "?", "#",
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg",
    ".css", ".js", ".xml", ".zip",
)


# ---------------------------------------------------------------------------
# SITEMAP FETCHING
# ---------------------------------------------------------------------------
def fetch_sitemap(homepage_url: str, progress_callback=None) -> list[str]:
    """
    Try to load sitemap.xml (or sitemap_index.xml) for the given site.
    Checks robots.txt first for a Sitemap: directive, then tries common paths.
    Handles sitemap index files by fetching each child sitemap.
    Returns a list of page URLs, filtered to remove non-content paths.
    Returns [] if no sitemap is found or parseable.
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    parsed = urlparse(homepage_url)
    base   = f"{parsed.scheme}://{parsed.netloc}"
    base_domain = parsed.netloc.replace("www.", "")

    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
        f"{base}/sitemaps/sitemap.xml",
    ]

    # Check robots.txt for a Sitemap: directive — often the most reliable source
    try:
        r = requests.get(f"{base}/robots.txt", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_url = line.split(":", 1)[1].strip()
                    if sitemap_url not in candidates:
                        candidates.insert(0, sitemap_url)
    except Exception:
        pass

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    def _parse_sitemap(content: bytes) -> tuple[list[str], list[str]]:
        """Returns (page_urls, child_sitemap_urls)."""
        try:
            root = ET.fromstring(content)
            child_sitemaps = [e.text.strip() for e in root.findall(".//sm:sitemap/sm:loc", ns) if e.text]
            page_urls      = [e.text.strip() for e in root.findall(".//sm:url/sm:loc",     ns) if e.text]
            return page_urls, child_sitemaps
        except Exception:
            return [], []

    urls: list[str] = []

    for candidate in candidates:
        try:
            r = requests.get(candidate, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue

            page_urls, child_sitemaps = _parse_sitemap(r.content)

            if child_sitemaps:
                # It's a sitemap index — fetch each child
                progress(f"Sitemap index found with {len(child_sitemaps)} child sitemaps. Fetching...")
                for child_url in child_sitemaps[:15]:
                    try:
                        cr = requests.get(child_url, headers=HEADERS, timeout=15)
                        if cr.status_code == 200:
                            child_pages, _ = _parse_sitemap(cr.content)
                            urls.extend(child_pages)
                    except Exception:
                        continue
            else:
                urls.extend(page_urls)

            if urls:
                break

        except Exception:
            continue

    # Keep only same-domain, content-likely URLs
    filtered = [
        u for u in urls
        if base_domain in u
        and not any(p in u.lower() for p in _SKIP_PATTERNS)
    ]

    if filtered:
        progress(f"Sitemap: found {len(filtered)} content pages.")
    return filtered


# ---------------------------------------------------------------------------
# FETCH PAGE CONTENT FROM A URL LIST (sitemap path)
# ---------------------------------------------------------------------------
def _fetch_pages_from_urls(
    urls: list[str],
    progress_callback=None,
    max_pages: int = 300,
) -> list[dict]:
    """
    Fetch title / meta description / body snippet for each URL.
    Used when the sitemap path is taken instead of BFS.
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    pages = []
    for i, url in enumerate(urls[:max_pages]):
        safe, _ = is_safe_url(url)
        if not safe:
            continue
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("Content-Type", ""):
                continue

            soup  = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""

            meta_tag = soup.find("meta", attrs={"name": "description"})
            meta = meta_tag["content"].strip() if meta_tag and meta_tag.get("content") else ""

            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            snippet = " ".join(soup.get_text(separator=" ").split())[:400]

            pages.append({"url": url, "title": title, "meta": meta, "snippet": snippet})

            if progress_callback and (i + 1) % 20 == 0:
                progress(f"Fetching pages from sitemap: {i + 1}/{min(len(urls), max_pages)}...")
        except Exception:
            continue
        time.sleep(0.2)

    return pages


# ---------------------------------------------------------------------------
# BFS CRAWLER (fallback when no sitemap)
# ---------------------------------------------------------------------------
def crawl_site(homepage_url: str, max_pages: int = 150, progress_callback=None) -> list[dict]:
    """
    BFS link-following crawler. Used only when sitemap is absent or too small.
    Collects up to max_pages pages with title, meta description, and 400-char snippet.
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    base_domain = urlparse(homepage_url).netloc.replace("www.", "")
    visited     = set()
    to_visit    = deque([homepage_url])
    pages       = []

    progress(f"No sitemap found. Crawling {base_domain} via link-following...")

    while to_visit and len(pages) < max_pages:
        url       = to_visit.popleft()
        clean_url = url.split("#")[0].rstrip("/")
        if clean_url in visited:
            continue
        visited.add(clean_url)

        safe, _ = is_safe_url(url)
        if not safe:
            continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("Content-Type", ""):
                continue
        except Exception:
            continue

        soup  = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        meta_tag = soup.find("meta", attrs={"name": "description"})
        meta = meta_tag["content"].strip() if meta_tag and meta_tag.get("content") else ""

        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        snippet = " ".join(soup.get_text(separator=" ").split())[:400]

        pages.append({"url": url, "title": title, "meta": meta, "snippet": snippet})
        progress(f"Crawled {len(pages)} pages... ({url[:60]})")

        for a_tag in soup.find_all("a", href=True):
            href     = a_tag["href"].strip()
            full_url = urljoin(url, href).split("#")[0].rstrip("/")
            parsed   = urlparse(full_url)
            if (
                parsed.scheme in ("http", "https")
                and base_domain in parsed.netloc
                and full_url not in visited
                and full_url not in to_visit
                and len(to_visit) < 500
            ):
                to_visit.append(full_url)

        time.sleep(0.3)

    progress(f"Crawl complete — {len(pages)} pages found.")
    return pages


# ---------------------------------------------------------------------------
# UNIFIED PAGE MAP BUILDER (sitemap-first, BFS fallback)
# ---------------------------------------------------------------------------
def build_page_map(homepage_url: str, progress_callback=None) -> list[dict]:
    """
    Try sitemap first; fall back to BFS if sitemap is absent or too small.
    Returns list of page dicts ready for query matching.
    """
    if not homepage_url.startswith("http"):
        homepage_url = f"https://{homepage_url}"

    sitemap_urls = fetch_sitemap(homepage_url, progress_callback)
    if len(sitemap_urls) >= 5:
        return _fetch_pages_from_urls(sitemap_urls, progress_callback)
    return crawl_site(homepage_url, max_pages=150, progress_callback=progress_callback)


# ---------------------------------------------------------------------------
# AUTO-EXTRACT BUSINESS INFO FROM HOMEPAGE
# ---------------------------------------------------------------------------
def auto_extract_business_info(homepage_url: str, keys: Keys) -> dict:
    """
    Fetch the homepage and ask Claude to extract:
      org_name, services, audience, domains
    Replaces the manual discovery form — user only needs to enter a URL.
    Returns {org_name, services, audience, domains, error}.
    """
    if not homepage_url.startswith("http"):
        homepage_url = f"https://{homepage_url}"

    parsed     = urlparse(homepage_url)
    domain     = parsed.netloc.replace("www.", "")
    fallback   = {"org_name": domain, "services": "", "audience": "", "domains": [domain], "error": None}

    try:
        resp = requests.get(homepage_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")

        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        og_site  = soup.find("meta", attrs={"property": "og:site_name"})
        og_name  = og_site.get("content", "").strip() if og_site else ""

        meta_tag = soup.find("meta", attrs={"name": "description"})
        meta_desc = meta_tag["content"].strip() if meta_tag and meta_tag.get("content") else ""

        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        body_text = " ".join(soup.get_text(separator=" ").split())[:1500]

    except Exception as e:
        fallback["error"] = str(e)
        return fallback

    if not keys.anthropic:
        return {**fallback, "org_name": og_name or title or domain}

    try:
        client = Anthropic(api_key=keys.anthropic)
        prompt = f"""Read this homepage content and extract key business information.

Page title: {title}
OG site name: {og_name}
Meta description: {meta_desc}
Body text (first 1500 chars): {body_text}
Domain: {domain}

Return ONLY valid JSON with exactly these keys:
{{
  "org_name": "The brand or company name (not the domain — the actual name people call them)",
  "services": "3-8 words describing their main services or product category",
  "audience": "3-8 words describing who they serve",
  "domains": ["{domain}"]
}}

Rules: org_name should be the brand name, not a domain. services and audience must be 3-10 words, phrased naturally."""

        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].replace("json", "", 1).strip()
        result = json.loads(raw)
        result["error"] = None
        if not result.get("domains"):
            result["domains"] = [domain]
        return result

    except Exception:
        return {**fallback, "org_name": og_name or title or domain}


# ---------------------------------------------------------------------------
# QUERY-TO-PAGE MATCHER
# ---------------------------------------------------------------------------
def match_queries_to_pages(
    queries: list[str],
    pages: list[dict],
    org_name: str,
    keys: Keys,
    progress_callback=None,
) -> dict[str, str]:
    """
    For each query, find the most relevant page from the crawled site map.
    Uses Claude to do the matching using title + meta + 400-char snippet.
    Returns: {query: best_page_url}
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    if not keys.anthropic or not pages:
        return {q: "" for q in queries}

    client = Anthropic(api_key=keys.anthropic)

    site_map = [
        {
            "index":   i,
            "url":     p["url"],
            "title":   p["title"],
            "meta":    p["meta"],
            "snippet": p["snippet"][:400],
        }
        for i, p in enumerate(pages)
    ]

    progress("Matching queries to your pages...")

    prompt = f"""Match each search query to the most relevant page on {org_name}'s website.

Site pages (index, URL, title, meta, content snippet):
{json.dumps(site_map, indent=2)}

Queries to match:
{json.dumps(queries, indent=2)}

For each query return the index of the single best matching page, or null if no page is a good match.
Return ONLY valid JSON — no markdown, no preamble:
{{
  "query text exactly as given": <page_index or null>,
  ...
}}"""

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].replace("json", "", 1).strip()
        matches = json.loads(raw)
    except Exception:
        return {q: "" for q in queries}

    result = {}
    for query, idx in matches.items():
        if idx is None:
            result[query] = ""
            continue
        try:
            page_idx = int(idx)
            result[query] = pages[page_idx]["url"] if 0 <= page_idx < len(pages) else ""
        except (ValueError, TypeError):
            result[query] = ""

    progress("Page matching complete.")
    return result


# ---------------------------------------------------------------------------
# FULL PIPELINE (kept for backwards compatibility)
# ---------------------------------------------------------------------------
def map_site_and_match(
    homepage_url: str,
    queries: list[str],
    org_name: str,
    keys: Keys,
    max_pages: int = 150,
    progress_callback=None,
) -> dict:
    """
    Sitemap-first pipeline:
      1. Fetch sitemap.xml (or BFS fallback up to max_pages).
      2. Match each query to its best page via Claude.
    Returns: {matches: {query: url}, pages: [...], error: None}
    """
    if not homepage_url.startswith("http"):
        homepage_url = f"https://{homepage_url}"

    try:
        pages = build_page_map(homepage_url, progress_callback)
    except Exception as e:
        return {"matches": {q: "" for q in queries}, "pages": [], "error": f"Crawl failed: {e}"}

    if not pages:
        return {
            "matches": {q: "" for q in queries},
            "pages":   [],
            "error":   "Could not crawl the site. Check the URL and try again.",
        }

    matches = match_queries_to_pages(queries, pages, org_name, keys, progress_callback)
    return {"matches": matches, "pages": pages, "error": None}
