"""
GEO Radar — core engine.

Four jobs, four functions:
  1. check_citation_perplexity() -> ask Perplexity a query, check citations.
  2. check_citation_chatgpt()    -> ask ChatGPT a query, check citations.
  3. scrape_page()               -> pull readable text from a URL for auditing.
  4. audit_page()                -> ask Claude to score the page and produce fixes.

run_audit() ties all four together for one query and returns one clean result dict.

Keys are passed explicitly as a Keys dataclass — no global os.environ mutation.
"""

import json
import ipaddress
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from anthropic import Anthropic
from openai import OpenAI

import prompts
from prompts import CLAUDE_MODEL
from config import Keys

# ---------------------------------------------------------------------------
# Configuration — change models here if you want cheaper or stronger runs.
# ---------------------------------------------------------------------------
PERPLEXITY_MODEL    = "sonar"                  # cheapest Perplexity model with clean citations
OPENAI_SEARCH_MODEL = "gpt-4o-search-preview"  # ChatGPT with built-in web search + citations
CLAUDE_MAX_TOKENS   = 2500
REQUEST_TIMEOUT     = 45
MAX_PAGE_BYTES      = 5 * 1024 * 1024          # refuse pages larger than 5 MB

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"


# ---------------------------------------------------------------------------
# 1a. PERPLEXITY CITATION CHECK
# ---------------------------------------------------------------------------
def check_citation_perplexity(
    query: str, target_domains: list[str], keys: Keys, n_samples: int = 1
) -> dict:
    """
    Ask Perplexity the query and check whether any target domain appears
    in the answer's citations.
    n_samples > 1 runs the check multiple times and returns a confidence band.
    Returns: {cited, matched_url, all_citations, answer, error, cited_count, sample_count}
    """
    if n_samples > 1:
        samples = [_perplexity_once(query, target_domains, keys) for _ in range(n_samples)]
        return _aggregate_samples(samples)
    result = _perplexity_once(query, target_domains, keys)
    result["cited_count"]  = 1 if result.get("cited") else 0
    result["sample_count"] = 1
    return result


def _perplexity_once(query: str, target_domains: list[str], keys: Keys) -> dict:
    if not keys.perplexity:
        return _citation_error("Perplexity API key is not set.")

    headers = {"Authorization": f"Bearer {keys.perplexity}", "Content-Type": "application/json"}
    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": [{"role": "user", "content": query}],
        "max_tokens": 400,
    }

    try:
        resp = requests.post(PERPLEXITY_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        return _citation_error(f"Perplexity request failed: {e}")
    except ValueError:
        return _citation_error("Perplexity returned a non-JSON response.")

    citations = data.get("citations") or []
    if not citations:
        citations = [r.get("url", "") for r in (data.get("search_results") or []) if r.get("url")]

    answer = ""
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        pass

    matched = next(
        (url for url in citations
         if any(domain.lower() in url.lower() for domain in target_domains)),
        None,
    )

    return {
        "cited": matched is not None,
        "matched_url": matched,
        "all_citations": citations,
        "answer": answer,
        "error": None,
    }


# ---------------------------------------------------------------------------
# 1b. CHATGPT CITATION CHECK
# ---------------------------------------------------------------------------
def check_citation_chatgpt(
    query: str, target_domains: list[str], keys: Keys, n_samples: int = 1
) -> dict:
    """
    Ask ChatGPT (gpt-4o-search-preview) the query and check whether any
    target domain appears in the answer's url_citation annotations.
    n_samples > 1 returns a confidence band.
    Returns: {cited, matched_url, all_citations, answer, error, cited_count, sample_count}
    """
    if n_samples > 1:
        samples = [_chatgpt_once(query, target_domains, keys) for _ in range(n_samples)]
        return _aggregate_samples(samples)
    result = _chatgpt_once(query, target_domains, keys)
    result["cited_count"]  = 1 if result.get("cited") else 0
    result["sample_count"] = 1
    return result


def _chatgpt_once(query: str, target_domains: list[str], keys: Keys) -> dict:
    if not keys.openai:
        return _citation_error("OpenAI API key is not set.")

    client = OpenAI(api_key=keys.openai)

    try:
        response = client.chat.completions.create(
            model=OPENAI_SEARCH_MODEL,
            messages=[{"role": "user", "content": query}],
            max_tokens=400,
        )
    except Exception as e:
        return _citation_error(f"ChatGPT request failed: {e}")

    try:
        message = response.choices[0].message
        answer  = message.content or ""

        citations  = []
        annotations = getattr(message, "annotations", None) or []
        for ann in annotations:
            if getattr(ann, "type", "") == "url_citation":
                uc  = getattr(ann, "url_citation", None)
                url = getattr(uc, "url", "") if uc else ""
                if url:
                    citations.append(url)

        seen      = set()
        citations = [u for u in citations if not (u in seen or seen.add(u))]

        matched = next(
            (url for url in citations
             if any(domain.lower() in url.lower() for domain in target_domains)),
            None,
        )

        return {
            "cited": matched is not None,
            "matched_url": matched,
            "all_citations": citations,
            "answer": answer,
            "error": None,
        }

    except Exception as e:
        return _citation_error(f"ChatGPT response parsing failed: {e}")


# Module-level cache so model resolution runs once per process, not per query.
_gemini_model_cache: str | None = None


def _resolve_gemini_model(client) -> str:
    """
    Query the Gemini models list and return the best available Flash model.
    Prefers stable releases over previews. Cached after the first call so
    subsequent queries in the same session never hit the list endpoint again.
    Falls back to 'gemini-2.5-flash' if the list call fails.
    """
    global _gemini_model_cache
    if _gemini_model_cache:
        return _gemini_model_cache

    try:
        all_flash = [
            m.name.removeprefix("models/")
            for m in client.models.list()
            if "gemini" in m.name and "flash" in m.name
        ]
        # Prefer stable builds; fall back to preview/exp if nothing else exists
        stable = [m for m in all_flash if "preview" not in m and "exp" not in m]
        candidates = sorted(stable or all_flash, reverse=True)
        if candidates:
            _gemini_model_cache = candidates[0]
            return _gemini_model_cache
    except Exception:
        pass

    _gemini_model_cache = "gemini-2.5-flash"
    return _gemini_model_cache


def check_citation_google(
    query: str, target_domains: list[str], keys: Keys, n_samples: int = 1
) -> dict:
    """
    Ask Gemini (with Google Search grounding) the query and check whether any
    target domain appears in the grounding citations.
    n_samples > 1 returns a confidence band.
    Returns: {cited, matched_url, all_citations, answer, error, cited_count, sample_count}
    """
    if n_samples > 1:
        samples = [_google_once(query, target_domains, keys) for _ in range(n_samples)]
        return _aggregate_samples(samples)
    result = _google_once(query, target_domains, keys)
    result["cited_count"]  = 1 if result.get("cited") else 0
    result["sample_count"] = 1
    return result


def _google_once(query: str, target_domains: list[str], keys: Keys) -> dict:
    """Single Gemini citation check call."""
    if not keys.google:
        return _citation_error("No Google API key provided.")

    try:
        from google import genai as google_genai
        from google.genai.types import Tool, GenerateContentConfig, GoogleSearch
    except ImportError:
        return _citation_error(
            "google-genai not installed. Run: pip install -r requirements.txt"
        )

    try:
        client     = google_genai.Client(api_key=keys.google)
        model_name = _resolve_gemini_model(client)
        response   = client.models.generate_content(
            model=model_name,
            contents=query,
            config=GenerateContentConfig(
                tools=[Tool(google_search=GoogleSearch())],
                response_modalities=["TEXT"],
            ),
        )
    except Exception as e:
        return _citation_error(f"Gemini API error: {e}")

    try:
        answer    = ""
        citations = []

        for candidate in getattr(response, "candidates", []):
            # Extract answer text
            for part in getattr(candidate.content, "parts", []):
                answer = answer or getattr(part, "text", "")

            # Extract grounding citations
            metadata = getattr(candidate, "grounding_metadata", None)
            if metadata:
                for chunk in getattr(metadata, "grounding_chunks", []):
                    web = getattr(chunk, "web", None)
                    uri = getattr(web, "uri", "") if web else ""
                    if uri:
                        citations.append(uri)

        seen      = set()
        citations = [u for u in citations if not (u in seen or seen.add(u))]

        matched = next(
            (url for url in citations
             if any(domain.lower() in url.lower() for domain in target_domains)),
            None,
        )

        return {
            "cited":         matched is not None,
            "matched_url":   matched,
            "all_citations": citations,
            "answer":        answer,
            "error":         None,
        }
    except Exception as e:
        return _citation_error(f"Gemini response parsing failed: {e}")


def _citation_error(msg: str) -> dict:
    return {
        "cited": None, "matched_url": None, "all_citations": [], "answer": "", "error": msg,
        "cited_count": 0, "sample_count": 1,
    }


def _aggregate_samples(samples: list[dict]) -> dict:
    """Combine N citation check results into one with confidence metrics."""
    valid = [s for s in samples if s.get("error") is None and s.get("cited") is not None]
    if not valid:
        result = samples[-1].copy()
        result["cited_count"]  = 0
        result["sample_count"] = len(samples)
        return result

    cited_count  = sum(1 for s in valid if s["cited"] is True)
    sample_count = len(valid)

    # Use a cited sample's data when available, else the last valid one
    best = next((s for s in valid if s["cited"]), valid[-1])

    # Union of all citations across samples (preserve first-seen order)
    seen_urls: set[str] = set()
    all_cit: list[str]  = []
    for s in valid:
        for url in s.get("all_citations", []):
            if url not in seen_urls:
                seen_urls.add(url)
                all_cit.append(url)

    return {
        "cited":         cited_count > 0,
        "matched_url":   best.get("matched_url"),
        "all_citations": all_cit,
        "answer":        best.get("answer", ""),
        "error":         None,
        "cited_count":   cited_count,
        "sample_count":  sample_count,
    }


# ---------------------------------------------------------------------------
# SSRF GUARD — shared with crawler.py
# ---------------------------------------------------------------------------
def is_safe_url(url: str) -> tuple[bool, str]:
    """
    Return (True, '') if the URL is safe to fetch.
    Return (False, reason) if it points to a private/reserved address.

    Blocks: localhost, loopback, link-local (169.254.x), private RFC-1918
    ranges, and any non-http(s) scheme. This prevents SSRF attacks where a
    user-supplied URL is used to reach internal infrastructure.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Could not parse URL."

    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' is not allowed. Use http or https."

    host = (parsed.hostname or "").lower()
    if not host:
        return False, "URL has no hostname."

    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return False, f"Requests to '{host}' are not allowed."

    # If the hostname is a bare IP, check whether it's in a reserved range.
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False, f"Requests to private/reserved IP '{host}' are not allowed."
    except ValueError:
        pass  # It's a normal hostname — OK to proceed.

    return True, ""


# ---------------------------------------------------------------------------
# 2. PAGE SCRAPE
# ---------------------------------------------------------------------------
def scrape_page(url: str) -> dict:
    """
    Fetch a URL and return clean readable text (scripts/styles/nav stripped).
    Returns: {ok, text, title, error}
    """
    safe, reason = is_safe_url(url)
    if not safe:
        return {"ok": False, "text": "", "title": "", "error": f"Blocked URL: {reason}"}

    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (GEO-Radar audit bot)"},
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "text": "", "title": "", "error": f"Could not fetch page: {e}"}

    if len(resp.content) > MAX_PAGE_BYTES:
        return {"ok": False, "text": "", "title": "", "error": "Page too large to audit (> 5 MB)."}

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text  = " ".join(soup.get_text(separator=" ").split())

    if not text:
        return {"ok": False, "text": "", "title": title, "error": "Page had no readable text."}

    return {"ok": True, "text": text, "title": title, "error": None}


# ---------------------------------------------------------------------------
# 3. CLAUDE AUDIT
# ---------------------------------------------------------------------------
def audit_page(
    query: str,
    page_url: str,
    page_text: str,
    org_name: str,
    keys: Keys,
    competitor_snippets: list[dict] | None = None,
) -> dict:
    """
    Ask Claude to score the page and produce fixes (rewrite + schema).
    Optionally includes competitor page snippets to ground the gap analysis.
    Returns the parsed JSON from the prompt, plus an "error" key.
    """
    if not keys.anthropic:
        return {"error": "Anthropic API key is not set."}

    client      = Anthropic(api_key=keys.anthropic)
    user_prompt = prompts.build_audit_prompt(query, page_url, page_text, org_name, competitor_snippets)

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=prompts.AUDIT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        return {"error": f"Claude request failed: {e}"}

    try:
        raw = message.content[0].text.strip()
    except (IndexError, AttributeError):
        return {"error": "Claude returned an empty response."}

    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip()

    try:
        parsed = json.loads(raw)
        parsed["error"] = None
        return parsed
    except json.JSONDecodeError:
        return {"error": "Claude did not return valid JSON.", "raw": raw}


# ---------------------------------------------------------------------------
# ORCHESTRATION — one query end to end.
# ---------------------------------------------------------------------------
def run_audit(
    query: str,
    page_url: str,
    target_domains: list[str],
    org_name: str,
    keys: Keys,
    n_samples: int = 1,
) -> dict:
    """
    Full pipeline for a single query:
      Perplexity check + ChatGPT check + Google check -> (if page URL given) scrape -> Claude audit.

    n_samples > 1 runs each citation check multiple times and returns confidence bands.
    Claude audit always runs once (it's deterministic given the same page content).
    Returns a flat dict ready to drop into a results table.
    """
    result = {
        "query":                    query,
        "page_url":                 page_url,
        "perplexity_cited":         None,
        "perplexity_matched_url":   None,
        "perplexity_citations":     [],
        "perplexity_cited_count":   None,
        "perplexity_sample_count":  1,
        "chatgpt_cited":            None,
        "chatgpt_matched_url":      None,
        "chatgpt_citations":        [],
        "chatgpt_cited_count":      None,
        "chatgpt_sample_count":     1,
        "google_cited":             None,
        "google_matched_url":       None,
        "google_citations":         [],
        "google_cited_count":       None,
        "google_sample_count":      1,
        "google_error":             None,
        "readiness_score":          None,
        "verdict":                  "",
        "gaps":                     [],
        "rewritten_section":        "",
        "suggested_headings":       [],
        "faq_schema":               "",
        "error":                    None,
    }

    perplexity = check_citation_perplexity(query, target_domains, keys, n_samples)
    chatgpt    = check_citation_chatgpt(query, target_domains, keys, n_samples)
    google     = check_citation_google(query, target_domains, keys, n_samples)

    result["perplexity_cited"]        = perplexity["cited"]
    result["perplexity_matched_url"]  = perplexity["matched_url"]
    result["perplexity_citations"]    = perplexity["all_citations"]
    result["perplexity_cited_count"]  = perplexity.get("cited_count", 1 if perplexity.get("cited") else 0)
    result["perplexity_sample_count"] = perplexity.get("sample_count", 1)
    result["chatgpt_cited"]           = chatgpt["cited"]
    result["chatgpt_matched_url"]     = chatgpt["matched_url"]
    result["chatgpt_citations"]       = chatgpt["all_citations"]
    result["chatgpt_cited_count"]     = chatgpt.get("cited_count", 1 if chatgpt.get("cited") else 0)
    result["chatgpt_sample_count"]    = chatgpt.get("sample_count", 1)
    result["google_cited"]            = google["cited"]
    result["google_matched_url"]      = google["matched_url"]
    result["google_citations"]        = google["all_citations"]
    result["google_cited_count"]      = google.get("cited_count", 1 if google.get("cited") else 0)
    result["google_sample_count"]     = google.get("sample_count", 1)
    result["google_error"]            = google["error"]

    if perplexity["error"] and chatgpt["error"]:
        result["error"] = f"Perplexity: {perplexity['error']} | ChatGPT: {chatgpt['error']}"
        return result

    if not page_url:
        result["verdict"] = "No page URL provided — citation check only."
        return result

    platforms_checked = [perplexity["cited"], chatgpt["cited"]]
    if keys.google and google["cited"] is not None:
        platforms_checked.append(google["cited"])
    cited_on_all = all(platforms_checked)
    if cited_on_all:
        result["verdict"] = "Cited on all checked platforms. No audit needed."
        return result

    # Collect competitor URLs from citations (non-target domains, max 2 unique)
    all_cit = list(perplexity.get("all_citations", [])) + list(chatgpt.get("all_citations", []))
    competitor_snippets = []
    seen_comp_domains: set[str] = set()
    for url in all_cit:
        if any(d.lower() in url.lower() for d in target_domains):
            continue
        dom = domain_from_url(url)
        if dom in seen_comp_domains:
            continue
        seen_comp_domains.add(dom)
        comp = scrape_page(url)
        if comp["ok"]:
            competitor_snippets.append({"url": url, "snippet": comp["text"]})
        if len(competitor_snippets) >= 2:
            break

    scraped = scrape_page(page_url)
    if not scraped["ok"]:
        result["error"] = scraped["error"]
        return result

    audit = audit_page(query, page_url, scraped["text"], org_name, keys, competitor_snippets or None)
    if audit.get("error"):
        result["error"] = audit["error"]
        return result

    result["readiness_score"]    = audit.get("readiness_score")
    result["verdict"]            = audit.get("verdict", "")
    result["gaps"]               = audit.get("gaps", [])
    result["rewritten_section"]  = audit.get("rewritten_section", "")
    result["suggested_headings"] = audit.get("suggested_headings", [])
    result["faq_schema"]         = audit.get("faq_schema", "")
    return result


def domain_from_url(url: str) -> str:
    """Helper: pull a bare domain (no www.) from a URL for matching."""
    netloc = urlparse(url if "://" in url else f"https://{url}").netloc
    return netloc.replace("www.", "").lower()


# ---------------------------------------------------------------------------
# SYNTHESIS — cross-query root cause analysis.
# ---------------------------------------------------------------------------
def synthesize_results(results: list[dict], org_name: str, keys: Keys) -> dict:
    """
    Single Claude call that reads all audit results and identifies the root
    causes of citation failures across queries — not per-query symptoms.
    Returns: {root_causes, priority_fixes, citation_rate, error}
    """
    if not keys.anthropic:
        return {"error": "Anthropic API key is not set."}

    audited = [r for r in results if not r.get("error") and r.get("readiness_score") is not None]
    if not audited:
        return {"error": "No audited results to synthesize."}

    total   = len([r for r in results if r.get("perplexity_cited") is not None])
    cited   = sum(1 for r in results if r.get("perplexity_cited") or r.get("chatgpt_cited"))
    rate    = round(cited / total * 100) if total else 0

    summary_lines = []
    for r in results:
        p = "✅" if r.get("perplexity_cited") else "❌"
        g = "✅" if r.get("chatgpt_cited") else "❌"
        score = r.get("readiness_score", "—")
        verdict = r.get("verdict", "")
        gaps = "; ".join(r.get("gaps") or [])
        summary_lines.append(
            f'Query: "{r["query"]}"\n'
            f'  Perplexity: {p}  ChatGPT: {g}  Score: {score}/100\n'
            f'  Verdict: {verdict}\n'
            f'  Gaps: {gaps}'
        )

    prompt = f"""You are reviewing a full AEO/GEO audit for {org_name}.
Overall citation rate: {cited}/{total} queries cited on at least one platform ({rate}%).

Here are all query-level results:

{chr(10).join(summary_lines)}

Identify the SYSTEMIC root causes — the underlying reasons this site is not being cited
across multiple queries. Do not repeat per-query verdicts. Look for patterns.

Return ONLY valid JSON, no markdown, with exactly these keys:
{{
  "root_causes": [
    "<root cause 1 — a pattern seen across multiple queries, not just one>",
    "<root cause 2>",
    "<root cause 3>"
  ],
  "priority_fixes": [
    "<the single highest-impact change that would unlock the most citations>",
    "<second fix>",
    "<third fix>"
  ],
  "citation_rate": {rate}
}}"""

    try:
        client  = Anthropic(api_key=keys.anthropic)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].replace("json", "", 1).strip()
        parsed = json.loads(raw)
        parsed["error"] = None
        return parsed
    except Exception as e:
        return {"error": f"Synthesis failed: {e}"}


# ---------------------------------------------------------------------------
# PROOF-OF-FIX — re-check a single page after the user applies fixes.
# ---------------------------------------------------------------------------
def recheck_single(
    query: str,
    page_url: str,
    target_domains: list[str],
    org_name: str,
    keys: Keys,
    n_samples: int = 1,
) -> dict:
    """
    Run a fresh single-query audit (citation checks + Claude audit) and return
    the new result dict. The before/after comparison is done in the UI layer.
    """
    return run_audit(query, page_url, target_domains, org_name, keys, n_samples)
