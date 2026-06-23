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
def check_citation_perplexity(query: str, target_domains: list[str], keys: Keys) -> dict:
    """
    Ask Perplexity the query and check whether any target domain appears
    in the answer's citations.
    Returns: {cited, matched_url, all_citations, answer, error}
    """
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

    citations = data.get("citations", [])
    if not citations and "search_results" in data:
        citations = [r.get("url", "") for r in data["search_results"] if r.get("url")]

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
def check_citation_chatgpt(query: str, target_domains: list[str], keys: Keys) -> dict:
    """
    Ask ChatGPT (gpt-4o-search-preview) the query and check whether any
    target domain appears in the answer's url_citation annotations.
    Returns: {cited, matched_url, all_citations, answer, error}
    """
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

        # Deduplicate, preserve order
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


def _citation_error(msg: str) -> dict:
    return {"cited": None, "matched_url": None, "all_citations": [], "answer": "", "error": msg}


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
            headers={"User-Agent": "Mozilla/5.0 (AEO-Radar audit bot)"},
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
def audit_page(query: str, page_url: str, page_text: str, org_name: str, keys: Keys) -> dict:
    """
    Ask Claude to score the page and produce fixes (rewrite + schema).
    Returns the parsed JSON from the prompt, plus an "error" key.
    """
    if not keys.anthropic:
        return {"error": "Anthropic API key is not set."}

    client      = Anthropic(api_key=keys.anthropic)
    user_prompt = prompts.build_audit_prompt(query, page_url, page_text, org_name)

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=prompts.AUDIT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        return {"error": f"Claude request failed: {e}"}

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip()

    try:
        parsed        = json.loads(raw)
        parsed["error"] = None
        return parsed
    except json.JSONDecodeError:
        return {"error": "Claude did not return valid JSON.", "raw": raw}


# ---------------------------------------------------------------------------
# ORCHESTRATION — one query end to end.
# ---------------------------------------------------------------------------
def run_audit(query: str, page_url: str, target_domains: list[str], org_name: str, keys: Keys) -> dict:
    """
    Full pipeline for a single query:
      Perplexity check + ChatGPT check -> (if page URL given) scrape -> Claude audit.

    Claude audit runs if the page URL is provided AND either platform is not citing.
    Returns a flat dict ready to drop into a results table.
    """
    result = {
        "query":                  query,
        "page_url":               page_url,
        "perplexity_cited":       None,
        "perplexity_matched_url": None,
        "perplexity_citations":   [],
        "chatgpt_cited":          None,
        "chatgpt_matched_url":    None,
        "chatgpt_citations":      [],
        "readiness_score":        None,
        "verdict":                "",
        "gaps":                   [],
        "rewritten_section":      "",
        "suggested_headings":     [],
        "faq_schema":             "",
        "error":                  None,
    }

    perplexity = check_citation_perplexity(query, target_domains, keys)
    chatgpt    = check_citation_chatgpt(query, target_domains, keys)

    result["perplexity_cited"]       = perplexity["cited"]
    result["perplexity_matched_url"] = perplexity["matched_url"]
    result["perplexity_citations"]   = perplexity["all_citations"]
    result["chatgpt_cited"]          = chatgpt["cited"]
    result["chatgpt_matched_url"]    = chatgpt["matched_url"]
    result["chatgpt_citations"]      = chatgpt["all_citations"]

    if perplexity["error"] and chatgpt["error"]:
        result["error"] = f"Perplexity: {perplexity['error']} | ChatGPT: {chatgpt['error']}"
        return result

    if not page_url:
        result["verdict"] = "No page URL provided — citation check only."
        return result

    cited_on_both = perplexity["cited"] and chatgpt["cited"]
    if cited_on_both:
        result["verdict"] = "Cited on both platforms. No audit needed."
        return result

    scraped = scrape_page(page_url)
    if not scraped["ok"]:
        result["error"] = scraped["error"]
        return result

    audit = audit_page(query, page_url, scraped["text"], org_name, keys)
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
