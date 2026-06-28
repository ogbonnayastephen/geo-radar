"""
GEO Radar — Streamlit UI (SaaS mode).

API keys are loaded from environment / Streamlit Secrets and are never
shown to or entered by end users. Users authenticate with email + password
via Supabase Auth. The builder owns all API keys and charges for access.

Four-step workflow (authenticated users):
  Step 1 — Discover:  scrape Google + Reddit for real queries, Claude organizes them.
  Step 2 — Crawl:     enter homepage URL, tool maps all pages and matches queries.
  Step 3 — Review:    confirm query + page pairings, edit if needed.
  Step 4 — Run:       citation checks (Perplexity, ChatGPT, Google AI) + Claude audit + fixes.
"""

import io
import csv
import os
import time
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import radar
import discover
import crawler
import db
import auth
from config import Keys
from reports import generate_pdf

# ---------------------------------------------------------------------------
# Load builder's API keys from env — users never see or enter these
# ---------------------------------------------------------------------------
_KEYS = Keys(
    anthropic=os.getenv("ANTHROPIC_API_KEY", ""),
    openai=os.getenv("OPENAI_API_KEY", ""),
    perplexity=os.getenv("PERPLEXITY_API_KEY", ""),
    google=os.getenv("GOOGLE_API_KEY", ""),
)

db.init()

st.set_page_config(page_title="GEO Radar", page_icon="📡", layout="wide")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
for _key, _default in {
    "discovered":         {},
    "query_text":         "",
    "audit_done":         False,
    "audit_results":      [],
    "audit_synthesis":    {},
    "crawled_pages":      [],
    "page_matches":       {},
    "selected_for_crawl": [],
    "user":               None,
    "recheck_results":    {},
    "confidence_mode":    False,
    "signup_pending":     False,
}.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default

# Resolve current user (dev user returned when Supabase not configured)
_user = auth.get_current_user()
if _user and not st.session_state.user:
    st.session_state.user = _user

# ---------------------------------------------------------------------------
# Helpers — badge rendering
# ---------------------------------------------------------------------------
def _cite_badge(cited, cited_count=None, sample_count=1) -> str:
    if sample_count and sample_count > 1 and cited_count is not None:
        if cited_count >= sample_count:
            return f"✅ High ({cited_count}/{sample_count})"
        if cited_count > 0:
            label = "Likely" if cited_count / sample_count >= 0.67 else "Uncertain"
            return f"⚠️ {label} ({cited_count}/{sample_count})"
        return f"❌ Not cited (0/{sample_count})"
    if cited is None:
        return "⚠️ No key"
    return "✅ Cited" if cited else "❌ Not cited"


def citation_badge(r: dict, engine: str) -> str:
    return _cite_badge(
        r.get(f"{engine}_cited"),
        r.get(f"{engine}_cited_count"),
        r.get(f"{engine}_sample_count", 1),
    )


def google_badge(r: dict) -> str:
    if r.get("google_cited") is True:
        return citation_badge(r, "google")
    if r.get("google_cited") is False:
        return citation_badge(r, "google")
    err = r.get("google_error", "")
    if err and "No Google API key" not in err:
        return "⚠️ Error"
    return "⚑ No key"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 📡 GEO Radar")

    if st.session_state.user:
        user = st.session_state.user
        st.caption(f"👤 {user['email']}")
        if st.button("Log out", use_container_width=True):
            auth.logout()
            st.rerun()

        st.divider()
        st.subheader("Organization")

        # ── Agency client switcher ────────────────────────────────────────
        try:
            past_orgs = db.get_user_orgs(user["id"])
        except Exception:
            past_orgs = []

        if past_orgs:
            org_options = ["➕ New client"] + past_orgs
            selected_client = st.selectbox("Switch client", org_options,
                                            label_visibility="collapsed")
            _org_default = "" if selected_client == "➕ New client" else selected_client
        else:
            _org_default = ""

        org_name = st.text_input("Organization name", value=_org_default)
        domains_raw = st.text_input(
            "Your domains (comma-separated)",
            value="yourdomain.com",
            help="Used to detect when a citation points to you. No https needed.",
        )
        target_domains = [d.strip() for d in domains_raw.split(",") if d.strip()]

        st.divider()
        agency_name = st.text_input(
            "Agency name (for PDF reports)",
            value="GEO Radar",
            help="Appears on the cover page of downloaded PDF reports.",
        )

        st.divider()
        st.session_state.confidence_mode = st.toggle(
            "Confidence mode",
            value=st.session_state.confidence_mode,
            help=(
                "Runs each citation check 3 times and shows a confidence level "
                "(High / Likely / Uncertain) instead of a single yes/no. "
                "Increases API cost ~3×."
            ),
        )
        if st.session_state.confidence_mode:
            st.caption("⚠️ Each query makes 9 citation API calls (3 engines × 3 samples).")

        st.divider()
        st.caption("**Powered by**  \nPerplexity · OpenAI · Google AI · Anthropic")

    else:
        # ── Login / Signup form ───────────────────────────────────────────
        st.caption("Sign in to run real audits on your own site.")
        tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

        with tab_login:
            login_email = st.text_input("Email", key="login_email")
            login_pass  = st.text_input("Password", type="password", key="login_pass")
            if st.button("Log in", type="primary", use_container_width=True):
                if login_email and login_pass:
                    res = auth.login(login_email, login_pass)
                    if res.get("user"):
                        st.session_state.user = res["user"]
                        st.rerun()
                    else:
                        st.error(res.get("error", "Login failed."))
                else:
                    st.warning("Enter your email and password.")

        with tab_signup:
            if st.session_state.signup_pending:
                st.success("Check your email to confirm your account, then log in.")
                if st.button("Back to log in"):
                    st.session_state.signup_pending = False
                    st.rerun()
            else:
                signup_email = st.text_input("Email", key="signup_email")
                signup_pass  = st.text_input(
                    "Password (min 6 characters)", type="password", key="signup_pass"
                )
                if st.button("Create account", type="primary", use_container_width=True):
                    if signup_email and signup_pass:
                        res = auth.signup(signup_email, signup_pass)
                        if res.get("user"):
                            st.session_state.user = res["user"]
                            st.rerun()
                        elif res.get("needs_confirmation"):
                            st.session_state.signup_pending = True
                            st.rerun()
                        else:
                            st.error(res.get("error", "Sign up failed."))
                    else:
                        st.warning("Enter your email and password.")

# ---------------------------------------------------------------------------
# AUTH GATE — logged-out visitors see the demo landing page
# ---------------------------------------------------------------------------
if not st.session_state.user:

    st.title("📡 GEO Radar")
    st.subheader("AI Visibility Audit — Are you being cited by ChatGPT, Perplexity, and Google AI?")
    st.markdown(
        "GEO Radar checks whether your business is being cited by the three AI answer engines "
        "that are replacing traditional search — then gives you the **exact content fixes** "
        "to change that. No dashboards to interpret. The rewrite, the headings, and the "
        "FAQ schema — ready to paste."
    )

    h1, h2, h3, h4 = st.columns(4)
    h1.markdown("**1 — Discover**  \nFind the real questions buyers, partners, and media ask about your category")
    h2.markdown("**2 — Match**  \nCrawl your site and map each query to your most relevant page")
    h3.markdown("**3 — Check**  \nSee whether Perplexity, ChatGPT, and Google AI cite you for each query")
    h4.markdown("**4 — Fix**  \nGet a rewritten content block, question-phrased headings, and FAQ schema for every gap")

    st.divider()
    st.markdown("#### Sample audit — Nexus Consulting (B2B strategy firm)")
    st.info("This is sample output. Sign up in the sidebar to run a real audit on your own site.", icon="ℹ️")

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
            "perplexity_cited_count": 0, "perplexity_sample_count": 1,
            "chatgpt_cited_count": 0, "chatgpt_sample_count": 1,
            "google_cited_count": 0, "google_sample_count": 1,
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
            "verdict": "Perplexity cites the client roster page, but ChatGPT and Google AI find no direct answer.",
            "perplexity_citations": [],
            "chatgpt_citations": ["https://bain.com/gtm-consulting", "https://mckinsey.com/enterprise-software"],
            "google_citations": ["https://gartner.com/gtm-enterprise"],
            "perplexity_matched_url": "https://nexusconsulting.com/clients",
            "chatgpt_matched_url": None, "google_matched_url": None,
            "perplexity_cited_count": 1, "perplexity_sample_count": 1,
            "chatgpt_cited_count": 0, "chatgpt_sample_count": 1,
            "google_cited_count": 0, "google_sample_count": 1,
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
            "verdict": "ChatGPT and Google AI cite the blog post, but Perplexity favors pages with embedded benchmark statistics.",
            "perplexity_citations": ["https://salesforce.com/blog/saas-sales-motion"],
            "chatgpt_citations": [],
            "google_citations": [],
            "perplexity_matched_url": None,
            "chatgpt_matched_url": "https://nexusconsulting.com/blog/saas-sales-motion",
            "google_matched_url": "https://nexusconsulting.com/blog/saas-sales-motion",
            "perplexity_cited_count": 0, "perplexity_sample_count": 1,
            "chatgpt_cited_count": 1, "chatgpt_sample_count": 1,
            "google_cited_count": 1, "google_sample_count": 1,
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

    _rows = [
        {
            "Query":      r["query"],
            "Perplexity": citation_badge(r, "perplexity"),
            "ChatGPT":    citation_badge(r, "chatgpt"),
            "Google AI":  google_badge(r),
            "Readiness":  r["readiness_score"],
            "Verdict":    r["verdict"],
        }
        for r in _demo
    ]
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

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

    st.markdown("### Fixes")
    _r = _demo[0]
    with st.expander(
        f"{_r['query']}   |   "
        f"Perplexity {citation_badge(_r, 'perplexity')}   "
        f"ChatGPT {citation_badge(_r, 'chatgpt')}   "
        f"Google AI {google_badge(_r)}   ·   readiness {_r['readiness_score']}/100",
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
    st.markdown("👈 **Sign up or log in in the sidebar** to run a real audit.")
    st.stop()

# ===========================================================================
# AUTHENTICATED APP — only reaches here when user is logged in
# ===========================================================================

user = st.session_state.user

st.title("📡 GEO Radar")
st.caption(
    "Find real queries → crawl your site → check ChatGPT, Perplexity, and Google AI → "
    "get Claude's exact fixes to make your pages citable."
)

n_samples = 3 if st.session_state.get("confidence_mode") else 1

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
        location = st.text_input("City or region (leave blank if global)", value="")

    st.markdown("**Intent categories** — who is searching for you?")
    cat_col1, cat_col2, cat_col3 = st.columns(3)
    with cat_col1:
        cat1 = st.text_input("Category 1", value="customers")
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
                keys=_KEYS,
            )

        progress_placeholder.empty()

        if result.get("error"):
            st.error(f"Discovery failed: {result['error']}")
        else:
            SKIP_KEYS = {"error", "raw_count", "seeds_used"}
            st.session_state.discovered = {
                k: v for k, v in result.items()
                if k not in SKIP_KEYS and isinstance(v, list)
            }
            st.success(
                f"Found {result.get('raw_count', 0)} real queries from Google and Reddit. "
                "Claude organized the best ones below."
            )

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

homepage_url = st.text_input("Homepage URL", placeholder="https://yourwebsite.com")
crawl_btn    = st.button("🕷️ Crawl site and match pages", type="primary")

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
                keys=_KEYS,
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
            if url and not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            items.append((q, url))
        else:
            items.append((line, ""))
    return items


def _show_recheck_panel(before: dict, after: dict) -> None:
    """Render a before/after comparison panel for a proof-of-fix recheck."""
    score_before = before.get("readiness_score") or 0
    score_after  = after.get("readiness_score")  or 0
    delta        = score_after - score_before

    st.markdown("---")
    st.markdown("**Fix check results**")

    col_score, col_perp, col_gpt, col_goog = st.columns(4)
    col_score.metric(
        "Readiness score",
        f"{score_after}/100",
        delta=f"{delta:+d} pts",
        delta_color="normal" if delta >= 0 else "inverse",
    )

    def _engine_metric(col, label, before_val, after_val):
        b = "✅" if before_val else ("❌" if before_val is False else "—")
        a = "✅" if after_val  else ("❌" if after_val  is False else "—")
        improved = (not before_val) and after_val
        col.metric(label, a, delta="↑ improved" if improved else None,
                   delta_color="normal" if improved else "off")

    _engine_metric(col_perp, "Perplexity",
                   before.get("perplexity_cited"), after.get("perplexity_cited"))
    _engine_metric(col_gpt,  "ChatGPT",
                   before.get("chatgpt_cited"),    after.get("chatgpt_cited"))
    _engine_metric(col_goog, "Google AI",
                   before.get("google_cited"),     after.get("google_cited"))

    any_improved = any([
        (not before.get("perplexity_cited")) and after.get("perplexity_cited"),
        (not before.get("chatgpt_cited"))    and after.get("chatgpt_cited"),
        (before.get("google_cited") is False) and after.get("google_cited"),
    ])
    if any_improved:
        st.success("Fix confirmed — at least one engine now cites you for this query.")
    else:
        st.info(
            "Not yet cited on any engine. AI engines re-crawl at different speeds: "
            "ChatGPT is usually fastest (hours), Google AI takes hours–2 days, "
            "Perplexity may take 2–7 days. Try re-checking tomorrow."
        )


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

    # Clear old recheck results when running a new audit
    st.session_state.recheck_results = {}

    results  = []
    progress = st.progress(0.0, text="Starting...")

    for i, (query, page_url) in enumerate(queries):
        label = f"Checking ({i+1}/{len(queries)}): {query}"
        if st.session_state.confidence_mode:
            label += " (3× confidence samples)"
        progress.progress(i / len(queries), text=label)
        result = radar.run_audit(query, page_url, target_domains, org_name, _KEYS, n_samples)
        results.append(result)
        time.sleep(0.3)

    progress.progress(1.0, text="Running synthesis...")
    synthesis = radar.synthesize_results(results, org_name, _KEYS)
    st.session_state.audit_results   = results
    st.session_state.audit_synthesis = synthesis
    st.session_state.audit_done      = True
    db.save_run(
        org_name,
        results,
        synthesis if not synthesis.get("error") else None,
        user_id=user.get("id"),
    )

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
    all_cited  = sum(1 for r in valid if (r.get("perplexity_cited")
                                          and r.get("chatgpt_cited")
                                          and r.get("google_cited")))

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
            "Perplexity": citation_badge(r, "perplexity"),
            "ChatGPT":    citation_badge(r, "chatgpt"),
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
        for i, r in enumerate(needs_fixes):
            score       = f"  ·  readiness {r['readiness_score']}/100" if r["readiness_score"] else ""
            google_part = (
                f"   Google AI {google_badge(r)}"
                if r.get("google_cited") is not None or r.get("google_error")
                else ""
            )
            label = (
                f"{r['query']}   |   "
                f"Perplexity {citation_badge(r, 'perplexity')}   "
                f"ChatGPT {citation_badge(r, 'chatgpt')}"
                f"{google_part}{score}"
            )
            recheck_key = f"recheck_{i}"

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
                            st.caption("Google key not configured.")
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
                        "Fill in any [ORG TO CONFIRM] placeholders on the page first."
                    )
                    st.code(r["faq_schema"], language="html")

                # ── Proof-of-fix recheck ──────────────────────────────────
                st.divider()
                st.markdown("**Applied the fix? Re-check this page.**")
                st.caption(
                    "After updating your page with the rewrite and schema above, "
                    "click below to re-run just this query and see if citations improved."
                )
                if st.button("🔄 Re-check after fix", key=f"btn_{recheck_key}",
                             use_container_width=False):
                    with st.spinner(f"Re-checking '{r['query']}'..."):
                        new_result = radar.recheck_single(
                            r["query"], r.get("page_url", ""),
                            target_domains, org_name, _KEYS, n_samples,
                        )
                    before_snap = {k: v for k, v in r.items()}
                    st.session_state.recheck_results[recheck_key] = {
                        "before": before_snap,
                        "after":  new_result,
                    }
                    qr_id = r.get("query_result_id")
                    if qr_id:
                        try:
                            db.save_fix_attempt(qr_id, before_snap, new_result)
                        except Exception:
                            pass

                if recheck_key in st.session_state.recheck_results:
                    rc = st.session_state.recheck_results[recheck_key]
                    _show_recheck_panel(rc["before"], rc["after"])

    # ── CSV download ────────────────────────────────────────────────────────
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Query", "Perplexity Cited", "Perplexity Matched URL",
        "ChatGPT Cited", "ChatGPT Matched URL",
        "Google AI Cited", "Google AI Matched URL",
        "Readiness", "Verdict", "Gaps",
        "Rewritten Section", "Suggested Headings",
    ])
    for r in results:
        writer.writerow([
            r["query"],
            r["perplexity_cited"],    r["perplexity_matched_url"],
            r["chatgpt_cited"],       r["chatgpt_matched_url"],
            r.get("google_cited"),    r.get("google_matched_url"),
            r["readiness_score"],     r["verdict"],
            " | ".join(r.get("gaps") or []),
            r.get("rewritten_section") or "",
            " | ".join(r.get("suggested_headings") or []),
        ])

    st.divider()
    dl_csv, dl_pdf = st.columns(2)

    with dl_csv:
        st.download_button(
            "⬇️ Download results as CSV",
            data=buffer.getvalue(),
            file_name=f"geo_radar_{org_name.lower().replace(' ', '_')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with dl_pdf:
        try:
            pdf_bytes = generate_pdf(org_name, results, synthesis, agency_name)
            st.download_button(
                "⬇️ Download PDF report",
                data=pdf_bytes,
                file_name=f"geo_radar_{org_name.lower().replace(' ', '_')}_report.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as e:
            st.caption(f"PDF generation error: {e}")

# ---------------------------------------------------------------------------
# HISTORY — past runs for this org
# ---------------------------------------------------------------------------
st.divider()
with st.expander("📈 Citation history for this organization"):
    try:
        history = db.get_history(org_name, user_id=user.get("id"))
    except Exception:
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
