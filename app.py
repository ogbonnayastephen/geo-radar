"""
GEO Radar — Streamlit UI (URL-first, confidence-default SaaS).

One URL → everything auto-discovered. No forms, no key inputs.
Citation checks always run 3× samples and show confidence bands.
API keys live in env / Streamlit Secrets — users never see them.
"""

import csv
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import auth
import crawler
import db
import discover
import radar
from config import Keys
from reports import generate_pdf

# ---------------------------------------------------------------------------
# Builder's API keys — loaded once at startup, never shown to users
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
# Transfer any pending auto-fill values BEFORE widgets render.
# (Streamlit forbids writing to a session_state key that's already bound to a
# widget in the same script run — so we stage values in _pending_* keys and
# flush them here at the very top of each run, before the sidebar renders.)
# ---------------------------------------------------------------------------
if "_pending_org_name" in st.session_state:
    st.session_state["org_name"] = st.session_state.pop("_pending_org_name")
if "_pending_domains_str" in st.session_state:
    st.session_state["domains_str"] = st.session_state.pop("_pending_domains_str")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "user":            None,
    "auto_extracted":  None,   # result of auto_extract_business_info
    "audit_table":     [],     # list of {include, query, page_url}
    "audit_done":      False,
    "audit_results":   [],
    "audit_synthesis": {},
    "recheck_results": {},
    "quick_mode":      False,  # True = 1× samples; False (default) = 3×
    "stale_checked":   False,
    "stale_orgs":      [],
    "signup_pending":  False,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# Resolve current user (dev user when Supabase not configured)
_current = auth.get_current_user()
if _current and not st.session_state.user:
    st.session_state.user = _current

# ---------------------------------------------------------------------------
# Badge helpers
# ---------------------------------------------------------------------------
def _cite_badge(cited, cited_count=None, sample_count=1) -> str:
    if sample_count and sample_count > 1 and cited_count is not None:
        if cited_count >= sample_count:
            return f"✅ High ({cited_count}/{sample_count})"
        if cited_count > 0:
            label = "Likely" if cited_count / sample_count >= 0.67 else "Uncertain"
            return f"⚠️ {label} ({cited_count}/{sample_count})"
        return f"❌ Not cited (0/{sample_count})"
    return "✅ Cited" if cited else ("❌ Not cited" if cited is False else "⚠️ No key")


def citation_badge(r: dict, engine: str) -> str:
    return _cite_badge(
        r.get(f"{engine}_cited"),
        r.get(f"{engine}_cited_count"),
        r.get(f"{engine}_sample_count", 1),
    )


def google_badge(r: dict) -> str:
    if r.get("google_cited") in (True, False):
        return citation_badge(r, "google")
    err = r.get("google_error", "")
    return "⚠️ Error" if (err and "No Google API key" not in err) else "⚑ No key"


# ---------------------------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 📡 GEO Radar")

    if st.session_state.user:
        user = st.session_state.user
        st.caption(f"👤 {user['email']}")
        if st.button("Log out", use_container_width=True):
            auth.logout()
            for k in ("auto_extracted", "audit_table", "audit_done", "audit_results",
                      "audit_synthesis", "recheck_results", "stale_checked", "stale_orgs",
                      "org_name", "domains_str"):
                st.session_state.pop(k, None)
            st.rerun()

        st.divider()
        st.subheader("Client")

        # ── Past client switcher ──────────────────────────────────────────
        try:
            past_orgs = db.get_user_orgs(user["id"])
        except Exception:
            past_orgs = []

        if past_orgs:
            org_options    = ["➕ New client"] + past_orgs
            selected_client = st.selectbox("Switch client", org_options,
                                            label_visibility="collapsed",
                                            key="client_switcher")
            if selected_client != "➕ New client":
                if st.session_state.get("_last_client") != selected_client:
                    st.session_state["_last_client"] = selected_client
                    st.session_state["org_name"]     = selected_client
                    st.rerun()

        # ── Org name & domains (auto-filled after URL analysis) ───────────
        if "org_name" not in st.session_state:
            st.session_state["org_name"] = ""
        if "domains_str" not in st.session_state:
            st.session_state["domains_str"] = ""

        st.text_input("Organization name", key="org_name",
                      placeholder="Auto-filled from your website")
        st.text_input("Your domains (comma-separated)", key="domains_str",
                      placeholder="Auto-filled from your website URL")

        st.divider()
        agency_name = st.text_input(
            "Agency name (for PDF reports)", value="GEO Radar",
            help="Appears on the PDF cover page.",
        )

        st.divider()
        st.session_state.quick_mode = st.toggle(
            "Quick check (1× per engine)",
            value=st.session_state.quick_mode,
            help="Skips confidence sampling. Faster but results can vary run-to-run.",
        )
        if st.session_state.quick_mode:
            st.caption("Single sample. Results may vary between runs.")
        else:
            st.caption("✓ Confidence mode — 3 samples · High / Likely / Uncertain.")

        st.divider()
        st.caption("**Powered by**  \nPerplexity · OpenAI · Google AI · Anthropic")

    else:
        # ── Login / Signup ────────────────────────────────────────────────
        st.caption("Sign in to run real audits on your site.")
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
                st.success("Check your email to confirm, then log in.")
                if st.button("Back to log in"):
                    st.session_state.signup_pending = False
                    st.rerun()
            else:
                su_email = st.text_input("Email", key="signup_email")
                su_pass  = st.text_input("Password (min 6 chars)", type="password", key="signup_pass")
                if st.button("Create account", type="primary", use_container_width=True):
                    if su_email and su_pass:
                        res = auth.signup(su_email, su_pass)
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
        "GEO Radar checks whether your business is cited by the three AI answer engines "
        "replacing traditional search — then gives you the **exact content fixes** to change that. "
        "Rewrite, headings, FAQ schema — ready to paste."
    )
    h1, h2, h3, h4 = st.columns(4)
    h1.markdown("**1 — Discover**  \nReal buyer questions from Google and Reddit")
    h2.markdown("**2 — Match**  \nMap every site page to the queries that should land there")
    h3.markdown("**3 — Check**  \nSee who's citing you on Perplexity, ChatGPT, and Google AI")
    h4.markdown("**4 — Fix**  \nGet a rewrite, headings, and FAQ schema for every gap")

    st.divider()
    st.markdown("#### Sample audit — Nexus Consulting")
    st.info("Sign up in the sidebar to run a real audit on your own site.", icon="ℹ️")

    _demo = [
        {
            "query": "go-to-market strategy for B2B SaaS",
            "perplexity_cited": False, "chatgpt_cited": False, "google_cited": False,
            "readiness_score": 12, "verdict": "Generic services list — no direct answer to this query.",
            "perplexity_citations": ["https://notion.so/blog/gtm-strategy", "https://hubspot.com/go-to-market"],
            "chatgpt_citations": ["https://a16z.com/go-to-market", "https://openviewpartners.com/gtm"],
            "google_citations": ["https://hbr.org/gtm-saas", "https://mckinsey.com/saas-growth"],
            "perplexity_matched_url": None, "chatgpt_matched_url": None, "google_matched_url": None,
            "perplexity_cited_count": 0, "perplexity_sample_count": 3,
            "chatgpt_cited_count": 0, "chatgpt_sample_count": 3,
            "google_cited_count": 0, "google_sample_count": 3,
            "gaps": [
                "No direct answer in the opening paragraph",
                "No statistics, timelines, or named client outcomes",
                "Missing FAQPage schema",
            ],
            "rewritten_section": "A B2B SaaS go-to-market strategy defines how you acquire your first 100 customers...",
            "suggested_headings": ["What does a B2B SaaS go-to-market strategy include?"],
            "faq_schema": "", "page_url": "https://nexusconsulting.com/services", "error": None,
        },
        {
            "query": "GTM consulting for enterprise software companies",
            "perplexity_cited": True, "chatgpt_cited": False, "google_cited": False,
            "readiness_score": 54, "verdict": "Perplexity cites the client roster. ChatGPT and Google AI find no direct answer.",
            "perplexity_citations": [],
            "chatgpt_citations": ["https://bain.com/gtm-consulting"],
            "google_citations": ["https://gartner.com/gtm-enterprise"],
            "perplexity_matched_url": "https://nexusconsulting.com/clients",
            "chatgpt_matched_url": None, "google_matched_url": None,
            "perplexity_cited_count": 2, "perplexity_sample_count": 3,
            "chatgpt_cited_count": 0, "chatgpt_sample_count": 3,
            "google_cited_count": 0, "google_sample_count": 3,
            "gaps": ["Enterprise content is buried", "Missing named enterprise client case study"],
            "rewritten_section": "Nexus Consulting specializes in GTM strategy for enterprise software companies...",
            "suggested_headings": ["How does GTM strategy differ for enterprise vs. SMB software?"],
            "faq_schema": "", "page_url": "https://nexusconsulting.com/enterprise", "error": None,
        },
        {
            "query": "how to build a sales motion for SaaS",
            "perplexity_cited": False, "chatgpt_cited": True, "google_cited": True,
            "readiness_score": 71, "verdict": "ChatGPT and Google AI cite the blog. Perplexity needs benchmark statistics.",
            "perplexity_citations": ["https://salesforce.com/blog/saas-sales-motion"],
            "chatgpt_citations": [], "google_citations": [],
            "perplexity_matched_url": None,
            "chatgpt_matched_url": "https://nexusconsulting.com/blog/saas-sales-motion",
            "google_matched_url": "https://nexusconsulting.com/blog/saas-sales-motion",
            "perplexity_cited_count": 0, "perplexity_sample_count": 3,
            "chatgpt_cited_count": 3, "chatgpt_sample_count": 3,
            "google_cited_count": 2, "google_sample_count": 3,
            "gaps": ["No benchmark statistics — Perplexity cites pages with conversion rate data"],
            "rewritten_section": "Building a SaaS sales motion starts with defining your ICP...",
            "suggested_headings": ["What are the five stages of a SaaS sales motion?"],
            "faq_schema": "", "page_url": "https://nexusconsulting.com/blog/saas-sales-motion", "error": None,
        },
    ]
    _demo_synthesis = {
        "root_causes": [
            "Pages are written for human browsing — answers are buried rather than leading",
            "No statistics, named outcomes, or specific timelines anywhere on the site",
            "FAQPage schema absent across all audited pages",
        ],
        "priority_fixes": [
            "Add a direct-answer opening paragraph to every service page",
            "Add at least two concrete data points per page",
            "Implement FAQPage schema on the top five pages",
        ],
    }

    total = len(_demo)
    dm1, dm2, dm3, dm4, dm5 = st.columns(5)
    dm1.metric("Queries checked",     total)
    dm2.metric("Cited on Perplexity", f"{sum(1 for r in _demo if r['perplexity_cited'])}/{total}")
    dm3.metric("Cited on ChatGPT",    f"{sum(1 for r in _demo if r['chatgpt_cited'])}/{total}")
    dm4.metric("Cited on Google AI",  f"{sum(1 for r in _demo if r.get('google_cited'))}/{total}")
    dm5.metric("Cited on all 3",      f"{sum(1 for r in _demo if r['perplexity_cited'] and r['chatgpt_cited'] and r.get('google_cited'))}/{total}")

    st.dataframe(pd.DataFrame([{
        "Query":      r["query"],
        "Perplexity": citation_badge(r, "perplexity"),
        "ChatGPT":    citation_badge(r, "chatgpt"),
        "Google AI":  google_badge(r),
        "Readiness":  r["readiness_score"],
        "Verdict":    r["verdict"],
    } for r in _demo]), use_container_width=True, hide_index=True)

    st.divider()
    col_rc, col_pf = st.columns(2)
    with col_rc:
        st.markdown("**Root causes**")
        for c in _demo_synthesis["root_causes"]:
            st.markdown(f"- {c}")
    with col_pf:
        st.markdown("**Priority fixes**")
        for i, f in enumerate(_demo_synthesis["priority_fixes"], 1):
            st.markdown(f"{i}. {f}")

    st.divider()
    st.markdown("👈 **Sign up or log in in the sidebar** to audit your own site.")
    st.stop()


# ===========================================================================
# AUTHENTICATED APP
# ===========================================================================
user       = st.session_state.user
n_samples  = 1 if st.session_state.quick_mode else 3
agency_name = "GEO Radar"  # re-read from sidebar if it was set above

st.title("📡 GEO Radar")
st.caption("Enter your website URL — we'll discover real queries, map your pages, and check all three AI engines.")

# ---------------------------------------------------------------------------
# Stale org scan reminder (checked once per login session)
# ---------------------------------------------------------------------------
if not st.session_state.stale_checked:
    try:
        st.session_state.stale_orgs = db.get_stale_orgs(user["id"], days=14)
    except Exception:
        st.session_state.stale_orgs = []
    st.session_state.stale_checked = True

for stale in st.session_state.stale_orgs:
    st.warning(
        f"**{stale['org_name']}** hasn't been audited in {stale['days_ago']} days — "
        "AI engines may have changed. Enter the site URL below to re-audit.",
        icon="⚠️",
    )

# ---------------------------------------------------------------------------
# URL INPUT + ANALYZE BUTTON
# ---------------------------------------------------------------------------
col_url, col_btn = st.columns([4, 1])
with col_url:
    homepage_url = st.text_input(
        "Website URL",
        placeholder="https://yourwebsite.com",
        label_visibility="collapsed",
    )
with col_btn:
    analyze_btn = st.button("🔍 Analyze my site", type="primary", use_container_width=True)

if st.session_state.auto_extracted:
    ex = st.session_state.auto_extracted
    st.caption(
        f"**{ex.get('org_name', '')}** · {ex.get('services', '')} · "
        f"Audience: {ex.get('audience', '')} · "
        f"Domains: {', '.join(ex.get('domains', []))}"
    )
    if st.button("↺ Start over", help="Clear and analyze a different URL"):
        for k in ("auto_extracted", "audit_table", "audit_done", "audit_results",
                  "audit_synthesis", "recheck_results", "org_name", "domains_str"):
            st.session_state.pop(k, None)
        st.rerun()

# ---------------------------------------------------------------------------
# ANALYZE: auto-extract → parallel discover + crawl → Claude match
# ---------------------------------------------------------------------------
if analyze_btn and homepage_url:
    for k in ("auto_extracted", "audit_table", "audit_done", "audit_results",
              "audit_synthesis", "recheck_results"):
        st.session_state.pop(k, None)

    url = homepage_url if homepage_url.startswith("http") else f"https://{homepage_url}"

    with st.spinner("Reading your homepage..."):
        extracted = crawler.auto_extract_business_info(url, _KEYS)

    st.session_state.auto_extracted         = extracted
    st.session_state["_pending_org_name"]   = extracted.get("org_name", "")
    st.session_state["_pending_domains_str"] = ", ".join(extracted.get("domains", []))

    org_name = extracted.get("org_name", "")
    services = extracted.get("services", "")
    audience = extracted.get("audience", "")

    st.success(f"Found **{org_name}** — {services}. Discovering queries and mapping your site...")

    def _run_discover():
        return discover.discover_queries(
            org_name=org_name,
            services=services,
            audience=audience,
            location="",
            categories=["customers", "partners", "media"],
            keys=_KEYS,
        )

    def _run_crawl():
        return crawler.build_page_map(url)

    with st.spinner("Finding real queries + mapping your site in parallel (~30 sec)..."):
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_d = pool.submit(_run_discover)
            fut_c = pool.submit(_run_crawl)
            discover_result = fut_d.result()
            pages           = fut_c.result()

    all_queries: list[str] = []
    for cat, qs in discover_result.items():
        if cat not in ("error", "raw_count", "seeds_used") and isinstance(qs, list):
            all_queries.extend(qs)

    if not all_queries:
        st.warning("No queries discovered — check your internet connection and try again.")
        st.stop()

    with st.spinner(f"Matching {len(all_queries)} queries to {len(pages)} pages..."):
        matches = crawler.match_queries_to_pages(all_queries, pages, org_name, _KEYS)

    st.session_state.audit_table = [
        {"include": True, "query": q, "page_url": matches.get(q, "")}
        for q in all_queries
    ]
    st.rerun()

# ---------------------------------------------------------------------------
# REVIEW TABLE — editable before running the audit
# ---------------------------------------------------------------------------
if st.session_state.audit_table and not st.session_state.audit_done:
    org_name = st.session_state.get("org_name", "")

    st.divider()
    st.subheader("Review queries and matched pages")
    st.caption(
        "Edit any URL, uncheck rows to skip, or add rows manually. "
        "Then click **Run audit**."
    )

    df        = pd.DataFrame(st.session_state.audit_table)
    edited_df = st.data_editor(
        df,
        column_config={
            "include":  st.column_config.CheckboxColumn("Run?",         default=True, width="small"),
            "query":    st.column_config.TextColumn("Query",            width="large"),
            "page_url": st.column_config.TextColumn("Matched page URL", width="large"),
        },
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="audit_table_editor",
    )

    n_selected = int(edited_df["include"].sum()) if "include" in edited_df.columns else len(edited_df)
    run_col, info_col = st.columns([2, 5])
    with run_col:
        run_btn = st.button(
            f"🚀 Run audit ({n_selected} quer{'y' if n_selected == 1 else 'ies'})",
            type="primary",
            disabled=n_selected == 0,
        )
    with info_col:
        if not st.session_state.quick_mode:
            st.caption(f"Confidence mode — 3 samples × {n_selected} queries × 3 engines = {n_selected * 9} citation checks.")
        else:
            st.caption(f"Quick check — 1 sample × {n_selected} queries × 3 engines = {n_selected * 3} checks.")

    if run_btn:
        target_domains = [
            d.strip()
            for d in st.session_state.get("domains_str", "").split(",")
            if d.strip()
        ]
        if not org_name:
            st.warning("Organization name is missing — it should be in the sidebar.")
            st.stop()
        if not target_domains:
            st.warning("Add at least one domain in the sidebar (should be auto-filled).")
            st.stop()

        rows = edited_df[edited_df["include"] == True].to_dict("records")
        if not rows:
            st.warning("Select at least one query to audit.")
            st.stop()

        st.session_state.recheck_results = {}
        results  = []
        progress = st.progress(0.0, text="Starting...")

        for i, row in enumerate(rows):
            q        = str(row.get("query", "")).strip()
            page_url = str(row.get("page_url", "")).strip()
            if not q:
                continue
            label = f"({i+1}/{len(rows)}) {q}"
            if not st.session_state.quick_mode:
                label += " · 3× confidence"
            progress.progress(i / len(rows), text=label)
            results.append(radar.run_audit(q, page_url, target_domains, org_name, _KEYS, n_samples))
            time.sleep(0.2)

        progress.progress(1.0, text="Running synthesis...")
        synthesis = radar.synthesize_results(results, org_name, _KEYS)

        st.session_state.audit_results   = results
        st.session_state.audit_synthesis = synthesis
        st.session_state.audit_done      = True

        try:
            db.save_run(org_name, results,
                        synthesis if not synthesis.get("error") else None,
                        user_id=user.get("id"))
        except Exception:
            pass

        st.rerun()

# ---------------------------------------------------------------------------
# RESULTS
# ---------------------------------------------------------------------------
def _show_recheck_panel(before: dict, after: dict) -> None:
    score_b = before.get("readiness_score") or 0
    score_a = after.get("readiness_score")  or 0
    delta   = score_a - score_b
    st.markdown("---")
    st.markdown("**Fix check results**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Readiness", f"{score_a}/100", delta=f"{delta:+d} pts",
              delta_color="normal" if delta >= 0 else "inverse")

    def _em(col, label, b, a):
        col.metric(label,
                   "✅" if a else ("❌" if a is False else "—"),
                   delta="↑ improved" if (not b) and a else None,
                   delta_color="normal" if (not b) and a else "off")

    _em(c2, "Perplexity", before.get("perplexity_cited"), after.get("perplexity_cited"))
    _em(c3, "ChatGPT",    before.get("chatgpt_cited"),    after.get("chatgpt_cited"))
    _em(c4, "Google AI",  before.get("google_cited"),     after.get("google_cited"))

    improved = any([
        (not before.get("perplexity_cited")) and after.get("perplexity_cited"),
        (not before.get("chatgpt_cited"))    and after.get("chatgpt_cited"),
        (before.get("google_cited") is False) and after.get("google_cited"),
    ])
    if improved:
        st.success("Fix confirmed — at least one engine now cites you for this query.")
    else:
        st.info(
            "Not yet cited. Re-index times: ChatGPT hours, Google AI 1–2 days, Perplexity 2–7 days. "
            "Try re-checking tomorrow."
        )


if st.session_state.audit_done and st.session_state.audit_results:
    results        = st.session_state.audit_results
    synthesis      = st.session_state.audit_synthesis
    org_name       = st.session_state.get("org_name", "")
    target_domains = [
        d.strip()
        for d in st.session_state.get("domains_str", "").split(",")
        if d.strip()
    ]

    st.divider()
    st.subheader("Results")

    valid      = [r for r in results if r.get("perplexity_cited") is not None or r.get("chatgpt_cited") is not None]
    total      = len(valid)
    perp_cited = sum(1 for r in valid if r.get("perplexity_cited"))
    gpt_cited  = sum(1 for r in valid if r.get("chatgpt_cited"))
    goog_cited = sum(1 for r in valid if r.get("google_cited"))
    all_cited  = sum(1 for r in valid if r.get("perplexity_cited") and r.get("chatgpt_cited") and r.get("google_cited"))

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Queries checked",     total)
    m2.metric("Cited on Perplexity", f"{perp_cited}/{total}")
    m3.metric("Cited on ChatGPT",    f"{gpt_cited}/{total}")
    m4.metric("Cited on Google AI",  f"{goog_cited}/{total}")
    m5.metric("Cited on all 3",      f"{all_cited}/{total}")

    st.dataframe(pd.DataFrame([{
        "Query":      r["query"],
        "Perplexity": citation_badge(r, "perplexity"),
        "ChatGPT":    citation_badge(r, "chatgpt"),
        "Google AI":  google_badge(r),
        "Readiness":  r.get("readiness_score") if r.get("readiness_score") is not None else "—",
        "Verdict":    r.get("verdict") if not r.get("error") else f"⚠️ {r['error']}",
    } for r in results]), use_container_width=True, hide_index=True)

    if synthesis and not synthesis.get("error"):
        st.divider()
        st.markdown("### Strategic diagnosis")
        st.caption("Root causes across all queries — not per-page symptoms.")
        col_rc, col_pf = st.columns(2)
        with col_rc:
            st.markdown("**Root causes**")
            for c in synthesis.get("root_causes", []):
                st.markdown(f"- {c}")
        with col_pf:
            st.markdown("**Priority fixes**")
            for i, f in enumerate(synthesis.get("priority_fixes", []), 1):
                st.markdown(f"{i}. {f}")

    needs_fixes = [r for r in results if not r.get("error") and (
        not r.get("perplexity_cited") or not r.get("chatgpt_cited") or r.get("google_cited") is False
    )]

    if needs_fixes:
        st.markdown("### Fixes")
        for i, r in enumerate(needs_fixes):
            score_part  = f"  ·  readiness {r['readiness_score']}/100" if r.get("readiness_score") else ""
            google_part = (f"   Google AI {google_badge(r)}"
                           if r.get("google_cited") is not None or r.get("google_error") else "")
            label = (
                f"{r['query']}   |   "
                f"Perplexity {citation_badge(r, 'perplexity')}   "
                f"ChatGPT {citation_badge(r, 'chatgpt')}"
                f"{google_part}{score_part}"
            )
            recheck_key = f"rc_{i}"

            with st.expander(label):
                cp, cg, cgg = st.columns(3)
                with cp:
                    st.markdown("**Perplexity**")
                    if r.get("perplexity_matched_url"):
                        st.success(f"Cited: {r['perplexity_matched_url']}")
                    elif r.get("perplexity_citations"):
                        st.error("Not citing you. Currently citing:")
                        for c in r["perplexity_citations"][:4]:
                            st.write(f"- {c}")
                    else:
                        st.warning("No citations returned.")

                with cg:
                    st.markdown("**ChatGPT**")
                    if r.get("chatgpt_matched_url"):
                        st.success(f"Cited: {r['chatgpt_matched_url']}")
                    elif r.get("chatgpt_citations"):
                        st.error("Not citing you. Currently citing:")
                        for c in r["chatgpt_citations"][:4]:
                            st.write(f"- {c}")
                    else:
                        st.warning("No citations returned.")

                with cgg:
                    st.markdown("**Google AI**")
                    if r.get("google_matched_url"):
                        st.success(f"Cited: {r['google_matched_url']}")
                    elif r.get("google_cited") is None:
                        err = r.get("google_error", "")
                        st.error(f"Error: {err}") if err else st.caption("Google key not configured.")
                    elif r.get("google_citations"):
                        st.error("Not citing you. Currently citing:")
                        for c in r["google_citations"][:4]:
                            st.write(f"- {c}")
                    else:
                        st.warning("No citations returned.")

                st.divider()
                if r.get("gaps"):
                    st.markdown("**What is missing on the page**")
                    for g in r["gaps"]:
                        st.write(f"- {g}")
                if r.get("rewritten_section"):
                    st.markdown("**Answer-first rewrite**")
                    st.info(r["rewritten_section"])
                if r.get("suggested_headings"):
                    st.markdown("**Suggested question-phrased headings**")
                    for h in r["suggested_headings"]:
                        st.write(f"- {h}")
                if r.get("faq_schema"):
                    st.markdown("**FAQ schema — paste into `<head>`**")
                    st.warning(
                        "Before pasting live: every schema question/answer must also be "
                        "visible on the page. Fill in [ORG TO CONFIRM] placeholders first."
                    )
                    st.code(r["faq_schema"], language="html")

                st.divider()
                st.markdown("**Applied the fix? Re-check this page.**")
                st.caption("After publishing your changes, click below to see if citations improved.")
                if st.button("🔄 Re-check after fix", key=f"btn_{recheck_key}"):
                    with st.spinner(f"Re-checking '{r['query']}'..."):
                        new_result = radar.recheck_single(
                            r["query"], r.get("page_url", ""),
                            target_domains, org_name, _KEYS, n_samples,
                        )
                    st.session_state.recheck_results[recheck_key] = {
                        "before": dict(r), "after": new_result,
                    }
                    qr_id = r.get("query_result_id")
                    if qr_id:
                        try:
                            db.save_fix_attempt(qr_id, dict(r), new_result)
                        except Exception:
                            pass

                if recheck_key in st.session_state.recheck_results:
                    rc = st.session_state.recheck_results[recheck_key]
                    _show_recheck_panel(rc["before"], rc["after"])

    # ── Downloads ─────────────────────────────────────────────────────────
    st.divider()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Query", "Perplexity", "Perplexity URL", "ChatGPT", "ChatGPT URL",
        "Google AI", "Google AI URL", "Readiness", "Verdict", "Gaps",
        "Rewritten Section", "Suggested Headings",
    ])
    for r in results:
        writer.writerow([
            r["query"],
            r.get("perplexity_cited"), r.get("perplexity_matched_url"),
            r.get("chatgpt_cited"),    r.get("chatgpt_matched_url"),
            r.get("google_cited"),     r.get("google_matched_url"),
            r.get("readiness_score"),  r.get("verdict"),
            " | ".join(r.get("gaps") or []),
            r.get("rewritten_section") or "",
            " | ".join(r.get("suggested_headings") or []),
        ])

    slug = (org_name or "audit").lower().replace(" ", "_")
    dl_csv, dl_pdf = st.columns(2)
    with dl_csv:
        st.download_button(
            "⬇️ Download CSV",
            data=buf.getvalue(),
            file_name=f"geo_radar_{slug}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with dl_pdf:
        try:
            pdf_bytes = generate_pdf(org_name, results, synthesis, agency_name)
            st.download_button(
                "⬇️ Download PDF report",
                data=pdf_bytes,
                file_name=f"geo_radar_{slug}_report.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as e:
            st.caption(f"PDF error: {e}")

# ---------------------------------------------------------------------------
# CITATION RATE HISTORY — trend chart + table
# ---------------------------------------------------------------------------
st.divider()
org_name_for_history = st.session_state.get("org_name", "")

with st.expander(
    "📈 Citation rate over time"
    + (f" — {org_name_for_history}" if org_name_for_history else "")
):
    if not org_name_for_history:
        st.info("Run an audit to start tracking citation rates over time.")
    else:
        try:
            history = db.get_history(org_name_for_history, limit=20, user_id=user.get("id"))
        except Exception:
            history = []

        if not history:
            st.info("No past runs for this org yet.")
        else:
            chart_rows = []
            for row in reversed(history):  # oldest first
                rate = round(row["cited_count"] / row["query_count"] * 100) if row["query_count"] else 0
                chart_rows.append({"Date": row["created_at"][:10], "Citation rate %": rate})

            chart_df = pd.DataFrame(chart_rows).set_index("Date")
            st.line_chart(chart_df, use_container_width=True)

            table_rows = []
            for row in history:
                rate = round(row["cited_count"] / row["query_count"] * 100) if row["query_count"] else 0
                table_rows.append({
                    "Date":          row["created_at"][:10],
                    "Queries":       row["query_count"],
                    "Cited":         row["cited_count"],
                    "Citation rate": f"{rate}%",
                })
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
