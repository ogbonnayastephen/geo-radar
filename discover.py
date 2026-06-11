"""
AEO Radar — Query Discovery Engine.

Finds real questions people ask using two data sources:
  1. Google autocomplete — live suggestions from billions of real searches.
  2. Reddit JSON search  — titles from real community posts.

Claude then clusters and prioritizes the raw results by intent
(people seeking help / donors / volunteers) so the team can pick
the queries that matter most without doing keyword research manually.

No paid APIs needed for discovery. Google autocomplete and Reddit
search are both publicly accessible.
"""

import os
import json
import time
import requests
from anthropic import Anthropic

from prompts import CLAUDE_MODEL

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GOOGLE_AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"
REDDIT_SEARCH_URL       = "https://www.reddit.com/search.json"
REQUEST_TIMEOUT         = 10

HEADERS_GOOGLE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
        "Gecko/20100101 Firefox/120.0"
    )
}
HEADERS_REDDIT = {"User-Agent": "AEO-Radar-Discovery/1.0 (research tool)"}


# ---------------------------------------------------------------------------
# SYSTEM PROMPT — stays the same for every discovery call.
# ---------------------------------------------------------------------------
DISCOVERY_SYSTEM_PROMPT = """\
You are an AEO (Answer Engine Optimization) strategist specializing in nonprofit
and social services organizations. You take raw, messy search data scraped from
Google and Reddit and transform it into a clean, prioritized set of queries the
organization should optimize for.

Your job is to:
1. Remove anything irrelevant to the organization's actual work.
2. Remove duplicates — keep the clearest, most natural-sounding version.
3. Group surviving queries by WHO is asking:
   - people_seeking_help: someone who needs the service (highest AEO priority)
   - donors_and_supporters: someone who wants to give money or advocate
   - volunteers: someone who wants to give their time
4. Phrase each query exactly as a real person would type it into ChatGPT or Google.
   Short, natural, no jargon.

You prioritize queries where the intent is specific enough that an AI search engine
would cite a named organization (not just generic government sites)."""


def build_discovery_prompt(
    raw_queries: list[str],
    org_name: str,
    services: str,
    audience: str,
    location: str,
) -> str:
    return f"""\
Organization: {org_name}
Services offered: {services}
People served: {audience}
Location: {location}

Raw queries scraped from Google autocomplete and Reddit (unfiltered):
{json.dumps(raw_queries, indent=2)}

Clean, deduplicate, and group these into the most valuable AEO queries.

Return ONLY valid JSON, no markdown fences, no preamble, with this exact structure:
{{
  "people_seeking_help":  ["query 1", "query 2", "query 3"],
  "donors_and_supporters": ["query 1", "query 2"],
  "volunteers":           ["query 1", "query 2"]
}}

Rules:
- Maximum 8 queries per category. Fewer is fine if there is not enough quality data.
- Every query must be phrased as a real person would type it — natural language.
- Remove anything that is not clearly related to {org_name}'s actual services.
- Prefer specific over vague. "emergency rent help in the Bronx" beats "help".
- If a category has no relevant queries in the data, return an empty list for it."""


# ---------------------------------------------------------------------------
# SEED BUILDER
# ---------------------------------------------------------------------------
def build_seeds(services: str, audience: str, location: str) -> list[str]:
    """
    Build a list of seed search terms from the org's context.
    These seeds are what we run through Google autocomplete and Reddit.
    No API call needed — just string manipulation.
    """
    loc      = location.strip()
    short    = loc.replace("New York City", "NYC").replace("New York", "NYC")
    seeds    = []

    for s in services.split(","):
        s = s.strip()
        if s:
            seeds.append(f"{s} {short}")
            seeds.append(f"{s} help {short}")

    for a in audience.split(","):
        a = a.strip()
        if a:
            seeds.append(f"help for {a} {short}")
            seeds.append(f"{a} resources {short}")

    # Donor and volunteer seeds — without these Google/Reddit return only
    # service-seeker queries and the donor/volunteer categories stay empty.
    seeds.append(f"donate to help families {short}")
    seeds.append(f"volunteer opportunities {short}")
    seeds.append(f"how to support {short} nonprofits")

    # Deduplicate while preserving order, cap at 12 seeds.
    seen = set()
    unique = [s for s in seeds if not (s.lower() in seen or seen.add(s.lower()))]
    return unique[:12]


# ---------------------------------------------------------------------------
# GOOGLE AUTOCOMPLETE SCRAPER
# ---------------------------------------------------------------------------
def scrape_google_autocomplete(seed: str) -> list[str]:
    """
    Hit Google's public autocomplete endpoint for a seed term and
    a few natural variations. Returns raw suggestions — no filtering yet.
    """
    queries_to_try = [
        seed,
        f"how to get {seed}",
        f"where to find {seed}",
        f"what is {seed}",
    ]

    suggestions = []
    for q in queries_to_try:
        try:
            resp = requests.get(
                GOOGLE_AUTOCOMPLETE_URL,
                params={"client": "firefox", "q": q, "hl": "en"},
                headers=HEADERS_GOOGLE,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Google returns [query, [suggestion, ...], ...]
                if len(data) > 1 and isinstance(data[1], list):
                    suggestions.extend(data[1])
        except Exception:
            pass  # network hiccup — skip this variation, keep going
        time.sleep(0.4)  # be a polite scraper

    return suggestions


# ---------------------------------------------------------------------------
# REDDIT SCRAPER
# ---------------------------------------------------------------------------
def scrape_reddit(seed: str) -> list[str]:
    """
    Search Reddit for posts related to the seed term.
    Post titles are real questions written by real people in real situations.
    Returns post titles — no filtering yet.
    """
    try:
        resp = requests.get(
            REDDIT_SEARCH_URL,
            params={"q": seed, "sort": "relevance", "limit": 20, "type": "link"},
            headers=HEADERS_REDDIT,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            post["data"]["title"]
            for post in data["data"]["children"]
            if post.get("data", {}).get("title")
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CLAUDE CLUSTERING
# ---------------------------------------------------------------------------
def cluster_with_claude(
    raw_queries: list[str],
    org_name: str,
    services: str,
    audience: str,
    location: str,
) -> dict:
    """
    Send the raw scraped queries to Claude and ask it to cluster,
    deduplicate, and prioritize by intent.
    Returns: {people_seeking_help, donors_and_supporters, volunteers, error}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY is not set."}

    client = Anthropic(api_key=api_key)
    prompt = build_discovery_prompt(raw_queries, org_name, services, audience, location)

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=DISCOVERY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return {"error": f"Claude clustering failed: {e}"}

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip()

    try:
        parsed = json.loads(raw)
        parsed["error"] = None
        return parsed
    except json.JSONDecodeError:
        return {"error": "Claude returned invalid JSON during clustering.", "raw": raw}


# ---------------------------------------------------------------------------
# MAIN DISCOVERY PIPELINE
# ---------------------------------------------------------------------------
def discover_queries(
    org_name: str,
    services: str,
    audience: str,
    location: str,
    progress_callback=None,
) -> dict:
    """
    Full pipeline:
      1. Build seed terms from org context.
      2. Scrape Google autocomplete for each seed.
      3. Scrape Reddit for each seed.
      4. Deduplicate the raw pile.
      5. Ask Claude to cluster and prioritize.

    progress_callback: optional callable(message: str) for UI updates.
    Returns: {people_seeking_help, donors_and_supporters, volunteers, error,
              raw_count, seeds_used}
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    seeds = build_seeds(services, audience, location)
    if not seeds:
        return {"error": "Could not build seeds. Check your services and audience inputs."}

    progress(f"Built {len(seeds)} search terms. Scraping Google...")

    raw_queries = []

    # Google autocomplete
    for i, seed in enumerate(seeds):
        suggestions = scrape_google_autocomplete(seed)
        raw_queries.extend(suggestions)
        progress(f"Google: {i + 1}/{len(seeds)} seeds scraped ({len(raw_queries)} suggestions so far)...")

    progress("Scraping Reddit for real community questions...")

    # Reddit — use first 3 seeds to keep it focused
    for seed in seeds[:3]:
        titles = scrape_reddit(seed)
        raw_queries.extend(titles)
        time.sleep(0.5)

    # Deduplicate the full raw pile
    seen = set()
    unique = []
    for q in raw_queries:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            unique.append(q)

    raw_count = len(unique)
    progress(f"Collected {raw_count} real queries. Asking Claude to organize them...")

    if raw_count == 0:
        return {
            "error": "No queries found. Try broader service or audience terms.",
            "people_seeking_help": [],
            "donors_and_supporters": [],
            "volunteers": [],
        }

    # Claude clustering
    clustered = cluster_with_claude(unique, org_name, services, audience, location)
    clustered["raw_count"]  = raw_count
    clustered["seeds_used"] = seeds
    return clustered
