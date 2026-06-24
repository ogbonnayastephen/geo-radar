"""
GEO Radar — Streamlit UI.

Run locally:   streamlit run app.py
Deploy:        push to GitHub, connect at share.streamlit.io.

The team enters their own API keys in the sidebar each session.
Keys are never stored — they only live in memory while the app is open.

Four-step workflow:
  Step 1 — Discover:  scrape Google + Reddit for real queries, Claude organizes them.
  Step 2 — Crawl:     enter homepage URL, tool maps all pages and matches queries.
  Step 3 — Review:    confirm query + page pairings, edit if needed.
  Step 4 — Run:       Perplexity + ChatGPT citation check, Claude audit + fixes.
"""

import io
import csv
import time
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import radar
import discover
import crawler
import db
from config import Keys

db.init()

st.set_page_config(page_title="GEO Radar", page_icon="📡", layout="wide")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
for key, default in {
    "discovered":        {},
    "query_text":        "",
    "audit_done":        False,
    "audit_results":     [],
    "audit_synthesis":   {},
    "crawled_pages":     [],
    "page_matches":      {},
    "selected_for_crawl": [],
    "keys_set":          False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("📡 GEO Radar")
st.caption(
    "Find real queries → crawl your site → check ChatGPT and Perplexity → "
    "get Claude's exact fixes to make your pages citable."
)

# ---------------------------------------------------------------------------
# Sidebar — Organization + API keys
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Organization")
    org_name = st.text_input("Organization name", value="Your Organization")
    domains_raw = st.text_input(
        "Your domains (comma-separated)",
        value="yourdomain.com",
        help="Used to detect when a citation points to you. No https needed.",
    )
    target_domains = [d.strip() for d in domains_raw.split(",") if d.strip()]

    st.divider()

    # ── API Keys ─────────────────────────────────────────────────────────────
    st.header("🔑 API Keys")
    st.caption(
        "Your keys are never saved. They only exist while this tab is open. "
        "You will need to re-enter them each session."
    )

    perplexity_key = st.text_input(
        "Perplexity API key",
        type="password",
        placeholder="Paste your Perplexity key here",
        help="Get yours at perplexity.ai/settings/api",
    )
    openai_key = st.text_input(
        "OpenAI (ChatGPT) API key",
        type="password",
        placeholder="Paste your OpenAI key here",
        help="Get yours at platform.openai.com/api-keys",
    )
    anthropic_key = st.text_input(
        "Anthropic (Claude) API key",
        type="password",
        placeholder="Paste your Anthropic key here",
        help="Get yours at console.anthropic.com",
    )
    google_key = st.text_input(
        "Google AI API key (optional)",
        type="password",
        placeholder="Enables Google AI Overview check",
        help="Get yours at aistudio.google.com — adds a third citation platform.",
    )

    keys = Keys(
        anthropic=anthropic_key,
        openai=openai_key,
        perplexity=perplexity_key,
        google=google_key,
    )

    keys_ready = all([perplexity_key, openai_key, anthropic_key])

    if keys_ready:
        st.success("✅ All keys entered. Ready to run.")
    else:
        missing = []
        if not perplexity_key: missing.append("Perplexity")
        if not openai_key:     missing.append("OpenAI")
        if not anthropic_key:  missing.append("Anthropic")
        st.warning(f"Missing: {', '.join(missing)}")

    st.divider()
    st.markdown("**APIs used**")
    st.markdown(
        "🔵 Perplexity — citation check  \n"
        "🟢 ChatGPT — citation check  \n"
        "🔴 Google AI — citation check (optional)  \n"
        "🟠 Claude — discovery, matching + audit"
    )
    st.divider()
    st.markdown(
        "**Estimated costs**  \n"
        "Discovery + crawl: ~$0.03  \n"
        "20 queries once/month: ~$1.20  \n"
        "20 queries weekly: ~$4.80/month  \n\n"
        "All costs come out of your own API accounts."
    )

    st.divider()
    with st.expander("Where do I get API keys?"):
        st.markdown(
            "**Perplexity**  \n"
            "Go to perplexity.ai → sign in → Settings → API  \n\n"
            "**OpenAI (ChatGPT)**  \n"
            "Go to platform.openai.com → sign in → API Keys → Create  \n\n"
            "**Anthropic (Claude)**  \n"
            "Go to console.anthropic.com → sign in → API Keys → Create  \n\n"
            "Each service requires a small credit balance to start ($5 each). "
            "At this tool's usage level, $5 lasts several months."
        )

# ---------------------------------------------------------------------------
# Helpers — defined here so they are available in both the demo and results
# ---------------------------------------------------------------------------
def citation_badge(cited) -> str:
    if cited is None:
        return "⚠️ No key"
    return "✅ Cited" if cited else "❌ Not cited"


def google_badge(r: dict) -> str:
    cited = r.get("google_cited")
    if cited is True:
        return "✅ Cited"
    if cited is False:
        return "❌ Not cited"
    error = r.get("google_error", "")
    if error and "No Google API key" not in error:
        return "⚠️ Error"
    return "⚑ No key"


# ---------------------------------------------------------------------------
# Gate — show demo landing page until keys are entered
# ---------------------------------------------------------------------------
if not keys_ready:

    st.markdown(
        "GEO Radar checks whether your business is being cited by AI answer engines — "
        "Perplexity, ChatGPT, and Google AI — then gives you the exact content fixes to change that. "
        "Enter your API keys in the sidebar to run a real audit on your own site."
    )

    st.markdown("#### How it works")
    h1, h2, h3, h4 = st.columns(4)
    h1.markdown("**1 — Discover**  \nFind the real questions buyers, partners, and media ask about your category")
    h2.markdown("**2 — Match**  \nCrawl your site and map each query to your most relevant page")
    h3.markdown("**3 — Check**  \nSee whether Perplexity, ChatGPT, and Google AI are citing you for each query")
    h4.markdown("**4 — Fix**  \nGet a rewritten content block, question-phrased headings, and FAQ schema for every gap")

    st.divider()
    st.markdown("#### Sample audit — Nexus Consulting (B2B strategy firm)")
    st.info(
        "This is sample output showing exactly what your audit will look like. "
        "Enter your API keys in the sidebar to run it on your own site.",
        icon="ℹ️",
    )

    _demo = [
        {
            "query": "go-to-market strategy for B2B SaaS",
            "perplexity_cited": False, "chatgpt_cited": False, "google_cited": False,
            "readiness_score": 12,
            "verdict": "The page is a generic services list with no direct answer to this query.",
            "perplexity_citations": ["https://notion.so/blog/gtm-strategy", "https://hubspot.com/go-to-market"],
            "chatgpt_citations": ["https://a16z.com/go-to-market", "https://openviewpartners.com/gtm"],
            "google_citations": ["https://hbr.org/gtm-saas", "https://mckinsey.com/saas-growth"],
            "perplexity_matched_url": None, "chatgpt_matched_url": None, "google_matched_url": None,
            "gaps": [
                "No direct answer to 'what is a B2B SaaS GTM strategy' in the opening paragraph",
                "No statistics, timelines, or named client outcomes — AI engines do not cite vague service descriptions",
                "Missing FAQPage schema — competitor pages with schema consistently outperform on AI citations",
            ],
            "rewritten_section": (
                "A B2B SaaS go-to-market strategy defines how you acquire your first 100 customers, "
                "which channels drive repeatable revenue, and when to expand beyond your initial segment. "
                "Nexus Consulting builds GTM strategies for Series A–C SaaS companies, typically reducing "
                "time-to-first-enterprise-deal by 40% through ICP refinement and channel sequencing. "
                "[ORG TO CONFIRM: average deal cycle, named client result]"
            ),
            "suggested_headings": [
                "What does a B2B SaaS go-to-market strategy include?",
                "How long does it take to build a GTM strategy for a SaaS company?",
            ],
            "faq_schema": (
                '<script type="application/ld+json">\n'
                '{\n  "@context": "https://schema.org",\n  "@type": "FAQPage",\n'
                '  "mainEntity": [{\n    "@type": "Question",\n'
                '    "name": "What does a B2B SaaS go-to-market strategy include?",\n'
                '    "acceptedAnswer": {\n      "@type": "Answer",\n'
                '      "text": "A B2B SaaS GTM strategy includes ICP definition, channel selection, '
                'sales motion design, and a sequenced expansion plan."\n    }\n  }]\n}\n'
                '</script>'
            ),
            "page_url": "https://nexusconsulting.com/services", "error": None,
        },
        {
            "query": "GTM consulting for enterprise software companies",
            "perplexity_cited": True, "chatgpt_cited": False, "google_cited": False,
            "readiness_score": 54,
            "verdict": "Perplexity cites the client roster page, but ChatGPT and Google AI find no direct answer to the enterprise GTM question.",
            "perplexity_citations": [],
            "chatgpt_citations": ["https://bain.com/gtm-consulting", "https://mckinsey.com/enterprise-software"],
            "google_citations": ["https://gartner.com/gtm-enterprise"],
            "perplexity_matched_url": "https://nexusconsulting.com/clients",
            "chatgpt_matched_url": None, "google_matched_url": None,
            "gaps": [
                "Enterprise-specific content is buried — the page targets all company sizes with the same copy",
                "No answer to 'how do you run GTM for enterprise software' visible in the first screen",
                "Missing case study with a named enterprise client, deal size, or sales cycle data",
            ],
            "rewritten_section": (
                "Nexus Consulting specializes in GTM strategy for enterprise software companies "
                "selling to Fortune 1000 buyers, typically with deal sizes above $50,000 ARR. "
                "[ORG TO CONFIRM: exact deal size range, named enterprise client]"
            ),
            "suggested_headings": [
                "How does GTM strategy differ for enterprise vs. SMB software companies?",
                "What results do enterprise software companies see from GTM consulting?",
            ],
            "faq_schema": "",
            "page_url": "https://nexusconsulting.com/enterprise", "error": None,
        },
        {
            "query": "how to build a sales motion for SaaS",
            "perplexity_cited": False, "chatgpt_cited": True, "google_cited": True,
            "readiness_score": 71,
            "verdict": "ChatGPT and Google AI cite the blog post for its step-by-step breakdown, but Perplexity favors pages with embedded benchmark statistics.",
            "perplexity_citations": ["https://salesforce.com/blog/saas-sales-motion"],
            "chatgpt_citations": [],
            "google_citations": [],
            "perplexity_matched_url": None,
            "chatgpt_matched_url": "https://nexusconsulting.com/blog/saas-sales-motion",
            "google_matched_url": "https://nexusconsulting.com/blog/saas-sales-motion",
            "gaps": [
                "No benchmark statistics — Perplexity citations consistently include conversion rates or timeline data",
                "The numbered steps are strong but lack outcome data per step",
            ],
            "rewritten_section": (
                "Building a SaaS sales motion starts with defining your ICP, then sequencing outbound, "
                "inbound, and partner channels by deal size. Companies that nail their sales motion in "
                "the first 18 months typically see 2–3x faster ramp times for new reps. "
                "[ORG TO CONFIRM: client benchmark data]"
            ),
            "suggested_headings": [
                "What are the five stages of a SaaS sales motion?",
                "How do you know when your SaaS sales motion is working?",
            ],
            "faq_schema": "",
            "page_url": "https://nexusconsulting.com/blog/saas-sales-motion", "error": None,
        },
    ]
    _demo_synthesis = {
        "root_causes": [
            "Pages are written for human browsing, not machine extraction — answers are buried rather than leading",
            "No statistics, named outcomes, or specific timelines anywhere on the site — AI engines consistently prefer pages with concrete evidence",
            "FAQPage schema is absent across all audited pages, giving competitor pages a structural citation advantage",
        ],
        "priority_fixes": [
            "Add a direct-answer opening paragraph to every service page — the first sentence must answer the query, not describe the company",
            "Add at least two concrete data points per page (deal size, timeline, client category, or percentage outcome)",
            "Implement FAQPage schema on the top five pages — this alone will measurably improve Google AI citation rate",
        ],
    }

    # Metrics
    _total = len(_demo)
    _perp  = sum(1 for r in _demo if r["perplexity_cited"])
    _gpt   = sum(1 for r in _demo if r["chatgpt_cited"])
    _goog  = sum(1 for r in _demo if r.get("google_cited"))
    _all3  = sum(1 for r in _demo if r.get("perplexity_cited") and r.get("chatgpt_cited") and r.get("google_cited"))
    dm1, dm2, dm3, dm4, dm5 = st.columns(5)
    dm1.metric("Queries checked",     _total)
    dm2.metric("Cited on Perplexity", f"{_perp}/{_total}")
    dm3.metric("Cited on ChatGPT",    f"{_gpt}/{_total}")
    dm4.metric("Cited on Google AI",  f"{_goog}/{_total}")
    dm5.metric("Cited on all 3",      f"{_all3}/{_total}")

    # Results table
    _rows = []
    for r in _demo:
        _rows.append({
            "Query":      r["query"],
            "Perplexity": citation_badge(r["perplexity_cited"]),
            "ChatGPT":    citation_badge(r["chatgpt_cited"]),
            "Google AI":  citation_badge(r.get("google_cited")),
            "Readiness":  r["readiness_score"],
            "Verdict":    r["verdict"],
        })
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

    # Strategic diagnosis
    st.divider()
    st.markdown("### Strategic diagnosis")
    st.caption("Root causes across all queries — not per-page symptoms.")
    _rc, _pf = st.columns(2)
    with _rc:
        st.markdown("**Root causes**")
        for cause in _demo_synthesis["root_causes"]:
            st.markdown(f"- {cause}")
    with _pf:
        st.markdown("**Priority fixes (highest impact first)**")
        for i, fix in enumerate(_demo_synthesis["priority_fixes"], 1):
            st.markdown(f"{i}. {fix}")

    # One fully expanded Fix
    st.markdown("### Fixes")
    _r = _demo[0]
    with st.expander(
        f"{_r['query']}   |   "
        f"Perplexity {citation_badge(_r['perplexity_cited'])}   "
        f"ChatGPT {citation_badge(_r['chatgpt_cited'])}   "
        f"Google AI {citation_badge(_r.get('google_cited'))}   ·   readiness {_r['readiness_score']}/100",
        expanded=True,
    ):
        _cp, _cg, _cgg = st.columns(3)
        with _cp:
            st.markdown("**Perplexity**")
            st.error("Not citing you. Currently citing:")
            for c in _r["perplexity_citations"]:
                st.write(f"- {c}")
        with _cg:
            st.markdown("**ChatGPT**")
            st.error("Not citing you. Currently citing:")
            for c in _r["chatgpt_citations"]:
                st.write(f"- {c}")
        with _cgg:
            st.markdown("**Google AI**")
            st.error("Not citing you. Currently citing:")
            for c in _r["google_citations"]:
                st.write(f"- {c}")
        st.divider()
        st.markdown("**What is missing on the page**")
        for g in _r["gaps"]:
            st.write(f"- {g}")
        st.markdown("**Answer-first rewrite**")
        st.info(_r["rewritten_section"])
        st.markdown("**Suggested question-phrased headings**")
        for h in _r["suggested_headings"]:
            st.write(f"- {h}")
        st.markdown("**FAQ schema — paste into the page's `<head>`**")
        st.code(_r["faq_schema"], language="html")

    st.divider()
    st.markdown("### Ready to audit your own site?")
    st.markdown(
        "Enter your **Perplexity**, **OpenAI**, and **Anthropic** API keys in the sidebar. "
        "Each key requires a small credit balance — $5 per service lasts several months at this tool's usage level. "
        "Expand **'Where do I get API keys?'** in the sidebar for step-by-step instructions."
    )
    st.stop()

# ---------------------------------------------------------------------------
# STEP 1 — DISCOVER REAL QUERIES
# ---------------------------------------------------------------------------
st.subheader("Step 1 — Find real questions people ask")
st.write(
    "Tell us what your organization does. We scrape Google and Reddit "
    "for questions real people actually ask, then Claude organizes them."
)

with st.form("discovery_form"):
    col1, col2 = st.columns(2)
    with col1:
        services = st.text_input(
            "What services do you offer?",
            placeholder="web design, SEO consulting, social media management",
        )
        audience = st.text_input(
            "Who do you serve?",
            placeholder="small businesses, startups, local brands",
        )
    with col2:
        location = st.text_input("City or region", value="")

    st.markdown("**Intent categories** — who is searching for you?")
    cat_col1, cat_col2, cat_col3 = st.columns(3)
    with cat_col1:
        cat1 = st.text_input("Category 1", value="customers", help="e.g. customers, buyers, patients")
    with cat_col2:
        cat2 = st.text_input("Category 2", value="partners")
    with cat_col3:
        cat3 = st.text_input("Category 3", value="media")

    discover_btn = st.form_submit_button("🔍 Find real queries", type="primary")

if discover_btn:
    try:
        categories = [c.strip().lower().replace(" ", "_") for c in [cat1, cat2, cat3] if c.strip()]
    except Exception:
        categories = ["customers", "partners", "media"]
    if not categories:
        categories = ["customers", "partners", "media"]

    if not services or not audience:
        st.warning("Fill in services and audience to discover queries.")
    else:
        progress_placeholder = st.empty()

        def update_progress(msg):
            progress_placeholder.info(f"⏳ {msg}")

        with st.spinner("Scraping Google and Reddit for real queries..."):
            result = discover.discover_queries(
                org_name=org_name,
                services=services,
                audience=audience,
                location=location,
                categories=categories,
                progress_callback=update_progress,
                keys=keys,
            )

        progress_placeholder.empty()

        if result.get("error"):
            st.error(f"Discovery failed: {result['error']}")
        else:
            SKIP_KEYS = {"error", "raw_count", "seeds_used"}
            st.session_state.discovered = {k: v for k, v in result.items() if k not in SKIP_KEYS and isinstance(v, list)}
            st.success(
                f"Found {result.get('raw_count', 0)} real queries from Google and Reddit. "
                "Claude organized the best ones below."
            )

# Show discovered queries as checkboxes
if st.session_state.discovered:
    st.markdown("**Select the queries you want to audit:**")

    discovered_keys = list(st.session_state.discovered.keys())
    selected_queries = []
    cols = st.columns(max(len(discovered_keys), 1))

    for col_idx, intent_key in enumerate(discovered_keys):
        queries = st.session_state.discovered.get(intent_key, [])
        label = intent_key.replace("_", " ").title()
        with cols[col_idx]:
            st.markdown(f"**{label}**")
            if queries:
                for q in queries:
                    if st.checkbox(q, key=f"chk_{intent_key}_{q}"):
                        selected_queries.append(q)
            else:
                st.caption("No queries found for this intent.")

    if selected_queries:
        st.info(f"{len(selected_queries)} queries selected.")
        if st.button("➕ Add selected queries and crawl my site below"):
            st.session_state.selected_for_crawl = selected_queries
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# STEP 2 — CRAWL THE SITE
# ---------------------------------------------------------------------------
st.subheader("Step 2 — Crawl your website")
st.write(
    "Enter your homepage URL. The tool maps every page on your site "
    "and automatically matches each query to the most relevant page."
)

homepage_url = st.text_input(
    "Homepage URL",
    placeholder="https://yourwebsite.com",
)

crawl_btn = st.button("🕷️ Crawl site and match pages", type="primary")

if crawl_btn:
    selected = st.session_state.get("selected_for_crawl", [])

    if not homepage_url:
        st.warning("Enter your homepage URL first.")
    elif not selected:
        st.warning("Select queries in Step 1 first.")
    else:
        progress_placeholder = st.empty()

        def crawl_progress(msg):
            progress_placeholder.info(f"⏳ {msg}")

        with st.spinner("Crawling site and matching pages..."):
            result = crawler.map_site_and_match(
                homepage_url=homepage_url,
                queries=selected,
                org_name=org_name,
                keys=keys,
                max_pages=60,
                progress_callback=crawl_progress,
            )

        progress_placeholder.empty()

        if result.get("error"):
            st.error(result["error"])
        else:
            st.session_state.crawled_pages = result["pages"]
            st.session_state.page_matches  = result["matches"]

            matched_count = sum(1 for v in result["matches"].values() if v)
            st.success(
                f"Crawled {len(result['pages'])} pages. "
                f"Matched {matched_count}/{len(selected)} queries to pages automatically."
            )

            lines = []
            for q in selected:
                url = result["matches"].get(q, "")
                lines.append(f"{q} | {url}" if url else q)
            st.session_state.query_text = "\n".join(lines)

if st.session_state.page_matches:
    st.markdown("**Query to page matches — edit any URL if needed:**")
    for q, url in st.session_state.page_matches.items():
        if url:
            st.write(f"✅ **{q}**  →  {url}")
        else:
            st.write(f"⚠️ **{q}**  →  No page found — add URL manually below")

st.divider()

# ---------------------------------------------------------------------------
# STEP 3 — REVIEW AND CONFIRM
# ---------------------------------------------------------------------------
st.subheader("Step 3 — Review and confirm")
st.write(
    "Queries and matched pages appear below. "
    "Edit any URL, add missing ones, or add extra queries manually."
)
st.code(
    "affordable web design for restaurants | https://yourwebsite.com/services/web-design\n"
    "SEO consulting for small businesses\n"
    "social media management near me | https://yourwebsite.com/services/social-media",
    language=None,
)

queries_raw = st.text_area(
    "Queries",
    key="query_text",
    height=200,
    label_visibility="collapsed",
)

run_btn = st.button("🚀 Run audit", type="primary")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_queries(raw: str):
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            q, url = line.split("|", 1)
            q   = q.strip()
            url = url.strip()
            # Normalise missing scheme so the scraper always receives a full URL.
            if url and not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            items.append((q, url))
        else:
            items.append((line, ""))
    return items


# ---------------------------------------------------------------------------
# STEP 4 — RUN AND RESULTS
# ---------------------------------------------------------------------------
if run_btn:
    queries = parse_queries(st.session_state.query_text)
    if not queries:
        st.warning("Add at least one query in Step 3.")
        st.stop()
    if not org_name.strip():
        st.warning("Enter your organization name in the sidebar.")
        st.stop()
    if not target_domains:
        st.warning("Add at least one domain in the sidebar.")
        st.stop()

    results  = []
    progress = st.progress(0.0, text="Starting...")

    for i, (query, page_url) in enumerate(queries):
        progress.progress(i / len(queries), text=f"Checking: {query}")
        result = radar.run_audit(query, page_url, target_domains, org_name, keys)
        results.append(result)
        time.sleep(0.3)

    progress.progress(1.0, text="Running synthesis...")
    synthesis = radar.synthesize_results(results, org_name, keys)
    st.session_state.audit_results   = results
    st.session_state.audit_synthesis = synthesis
    st.session_state.audit_done      = True
    db.save_run(org_name, results, synthesis if not synthesis.get("error") else None)

if st.session_state.audit_done and st.session_state.audit_results:
    results   = st.session_state.audit_results
    synthesis = st.session_state.get("audit_synthesis", {})

    st.divider()
    st.subheader("Step 4 — Results")

    valid      = [r for r in results if r["perplexity_cited"] is not None
                                     or r["chatgpt_cited"] is not None]
    total      = len(valid)
    perp_cited = sum(1 for r in valid if r["perplexity_cited"])
    gpt_cited  = sum(1 for r in valid if r["chatgpt_cited"])
    goog_cited = sum(1 for r in valid if r.get("google_cited"))
    all_cited  = sum(1 for r in valid if r.get("perplexity_cited") and r.get("chatgpt_cited") and r.get("google_cited"))

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Queries checked",     total)
    m2.metric("Cited on Perplexity", f"{perp_cited}/{total}")
    m3.metric("Cited on ChatGPT",    f"{gpt_cited}/{total}")
    m4.metric("Cited on Google AI",  f"{goog_cited}/{total}")
    m5.metric("Cited on all 3",      f"{all_cited}/{total}")

    table_rows = []
    for r in results:
        table_rows.append({
            "Query":      r["query"],
            "Perplexity": citation_badge(r["perplexity_cited"]),
            "ChatGPT":    citation_badge(r["chatgpt_cited"]),
            "Google AI":  google_badge(r),
            "Readiness":  r["readiness_score"] if r["readiness_score"] is not None else "—",
            "Verdict":    r["verdict"] if not r["error"] else f"⚠️ {r['error']}",
        })

    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

    # Synthesis panel
    if synthesis and not synthesis.get("error"):
        st.divider()
        st.markdown("### Strategic diagnosis")
        st.caption("Root causes across all queries — not per-page symptoms.")
        col_rc, col_pf = st.columns(2)
        with col_rc:
            st.markdown("**Root causes**")
            for cause in synthesis.get("root_causes", []):
                st.markdown(f"- {cause}")
        with col_pf:
            st.markdown("**Priority fixes (highest impact first)**")
            for i, fix in enumerate(synthesis.get("priority_fixes", []), 1):
                st.markdown(f"{i}. {fix}")

    needs_fixes = [
        r for r in results
        if not r["error"] and (
            not r["perplexity_cited"]
            or not r["chatgpt_cited"]
            or r.get("google_cited") is False
        )
    ]

    if needs_fixes:
        st.markdown("### Fixes")
        for r in needs_fixes:
            score = f"  ·  readiness {r['readiness_score']}/100" if r["readiness_score"] else ""
            google_part = f"   Google AI {google_badge(r)}" if r.get("google_cited") is not None or r.get("google_error") else ""
            label = (
                f"{r['query']}   |   "
                f"Perplexity {citation_badge(r['perplexity_cited'])}   "
                f"ChatGPT {citation_badge(r['chatgpt_cited'])}"
                f"{google_part}{score}"
            )
            with st.expander(label):
                col_p, col_g, col_gg = st.columns(3)
                with col_p:
                    st.markdown("**Perplexity**")
                    if r["perplexity_matched_url"]:
                        st.success(f"Cited: {r['perplexity_matched_url']}")
                    elif r["perplexity_citations"]:
                        st.error("Not citing you. Currently citing:")
                        for c in r["perplexity_citations"][:4]:
                            st.write(f"- {c}")
                    else:
                        st.warning("No citations returned.")

                with col_g:
                    st.markdown("**ChatGPT**")
                    if r["chatgpt_matched_url"]:
                        st.success(f"Cited: {r['chatgpt_matched_url']}")
                    elif r["chatgpt_citations"]:
                        st.error("Not citing you. Currently citing:")
                        for c in r["chatgpt_citations"][:4]:
                            st.write(f"- {c}")
                    else:
                        st.warning("No citations returned.")

                with col_gg:
                    st.markdown("**Google AI**")
                    if r.get("google_matched_url"):
                        st.success(f"Cited: {r['google_matched_url']}")
                    elif r.get("google_cited") is None:
                        google_err = r.get("google_error", "")
                        if google_err:
                            st.error(f"Error: {google_err}")
                        else:
                            st.caption("No Google key provided.")
                    elif r.get("google_citations"):
                        st.error("Not citing you. Currently citing:")
                        for c in r["google_citations"][:4]:
                            st.write(f"- {c}")
                    else:
                        st.warning("No citations returned.")

                st.divider()

                if r["gaps"]:
                    st.markdown("**What is missing on the page**")
                    for g in r["gaps"]:
                        st.write(f"- {g}")

                if r["rewritten_section"]:
                    st.markdown("**Answer-first rewrite**")
                    st.info(r["rewritten_section"])

                if r["suggested_headings"]:
                    st.markdown("**Suggested question-phrased headings**")
                    for h in r["suggested_headings"]:
                        st.write(f"- {h}")

                if r["faq_schema"]:
                    st.markdown("**FAQ schema — paste into the page's `<head>`**")
                    st.warning(
                        "⚠️ Before pasting this code live: make sure every question and "
                        "answer in the schema is also visible on the actual page. "
                        "Google requires the schema to match what users can see. "
                        "If any answer says [ORG TO CONFIRM], fill it in on the page first, "
                        "then update the schema to match."
                    )
                    st.code(r["faq_schema"], language="html")

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Query", "Perplexity Cited", "Perplexity Matched URL",
        "ChatGPT Cited", "ChatGPT Matched URL",
        "Readiness", "Verdict", "Gaps",
        "Rewritten Section", "Suggested Headings",
        "Perplexity Citations", "ChatGPT Citations",
    ])
    for r in results:
        writer.writerow([
            r["query"],
            r["perplexity_cited"],    r["perplexity_matched_url"],
            r["chatgpt_cited"],       r["chatgpt_matched_url"],
            r["readiness_score"],     r["verdict"],
            " | ".join(r.get("gaps") or []),
            r.get("rewritten_section") or "",
            " | ".join(r.get("suggested_headings") or []),
            " | ".join(r.get("perplexity_citations") or []),
            " | ".join(r.get("chatgpt_citations") or []),
        ])

    st.download_button(
        "⬇️ Download full results as CSV",
        data=buffer.getvalue(),
        file_name="geo_radar_results.csv",
        mime="text/csv",
    )

# ---------------------------------------------------------------------------
# HISTORY — past runs for this org
# ---------------------------------------------------------------------------
st.divider()
with st.expander("📈 Citation history for this organization"):
    try:
        history = db.get_history(org_name)
    except Exception:
        st.warning("Could not load history from database.")
        history = []
    if not history:
        st.info("No past runs found. Run an audit to start tracking citation rates over time.")
    else:
        history_rows = []
        for row in history:
            rate = round(row["cited_count"] / row["query_count"] * 100) if row["query_count"] else 0
            history_rows.append({
                "Date":          row["created_at"][:10],
                "Queries":       row["query_count"],
                "Cited":         row["cited_count"],
                "Citation rate": f"{rate}%",
            })
        st.dataframe(pd.DataFrame(history_rows), use_container_width=True, hide_index=True)
