# GEO Radar

### Enterprise-grade Answer Engine Optimization and Generative Engine Optimization audit platform

GEO Radar is an end-to-end AEO/GEO intelligence system that determines whether any organization is being cited by AI-powered answer engines — and produces the exact, page-level content interventions required to change that outcome.

**Live deployment:** [geo-radar.streamlit.app](https://geo-radar.streamlit.app)

---

## The Problem: The Collapse of Traditional Search

For three decades, search engine optimization (SEO) operated on a single premise: rank higher in a list of blue links, and users will click through to your website. That model is ending.

Artificial intelligence has restructured how information is discovered and consumed. When a buyer asks *"What is the best go-to-market strategy for a SaaS startup?"* or *"Who offers GTM consulting for B2B companies?"*, they no longer scroll through ten results. They receive a synthesized, conversational answer — generated in real time by an AI engine — with a small set of cited sources embedded within it.

The engines delivering these answers are:

- **Perplexity AI** — a dedicated AI answer engine used by tens of millions of researchers, analysts, and buyers
- **ChatGPT with web search** — OpenAI's flagship model, now capable of live web retrieval and citation
- **Google AI Overviews** — Google's AI-generated summaries appearing above organic results, powered by Gemini

In this environment, traditional SEO metrics — domain authority, keyword rankings, backlink counts — do not determine whether a business is cited. What determines citation is structural: whether a page is written so that an AI can extract a direct, specific, trustworthy answer from it. Most business websites are not. They were built for human browsing, not machine extraction.

The result is a new class of invisible businesses: organizations that rank well in traditional search but are entirely absent from AI-generated answers — and therefore absent from the research process of an entire generation of buyers.

**Answer Engine Optimization (AEO)** and **Generative Engine Optimization (GEO)** are the emerging disciplines that address this gap. GEO Radar is the operational tool that makes these disciplines executable at scale.

---

## What GEO Radar Does

GEO Radar automates the complete AEO/GEO audit workflow for any organization. In a single session, it:

1. Discovers the actual questions buyers, partners, and media are asking about an organization's category
2. Maps those queries to the specific pages on the organization's website that should be answering them
3. Checks whether each page is currently being cited by Perplexity, ChatGPT, and Google AI
4. Analyzes the structural and content gaps that explain every citation failure
5. Produces ready-to-implement fixes: rewritten content blocks, question-anchored headings, and validated FAQ schema markup
6. Synthesizes findings across all queries to identify the systemic root causes, not just per-page symptoms

The output is not a generic report. It is a prioritized, page-specific remediation plan that an organization can implement immediately.

---

## System Architecture

GEO Radar is a multi-module Python application with a Streamlit interface. Each module handles a distinct layer of the audit pipeline.

```
geo-radar/
├── app.py          # Streamlit UI — session management, results rendering, history
├── radar.py        # Core audit engine — citation checks, page analysis, synthesis
├── discover.py     # Query discovery — Google/Reddit scraping, Claude clustering
├── crawler.py      # Site mapping — BFS crawl, page-to-query matching
├── prompts.py      # Prompt templates — audit system prompt, audit user prompt
├── config.py       # Keys dataclass — safe multi-user key isolation
├── db.py           # Persistence layer — SQLite citation history
└── requirements.txt
```

### Module responsibilities

**`config.py` — Key isolation**
Defines a `Keys` dataclass that carries all API credentials through the call stack explicitly. This eliminates process-global environment mutation, making the application safe for concurrent multi-user sessions where different users hold different API keys.

**`discover.py` — Query discovery**
Scrapes Google autocomplete and Reddit search for real queries related to an organization's services and audience. Claude then clusters these into intent categories (buyer, partner, media, etc.), filtering out irrelevant noise and surfacing the queries most likely to trigger AI citations.

**`crawler.py` — Site mapping and matching**
Performs a breadth-first crawl of an organization's website (up to 60 pages by default, configurable). Uses a `deque`-based queue for O(1) traversal efficiency, with a 500-URL cap to prevent memory exhaustion on large sites. Claude then matches each discovered query to the most semantically relevant page on the site.

**`radar.py` — Citation engine and audit**
The core module. For each query-page pair:
- Calls the Perplexity Sonar API and inspects returned citation URLs
- Calls OpenAI's `gpt-4o-search-preview` model and inspects returned citation URLs
- Calls Gemini (via `google-genai`) with Google Search Grounding and inspects grounding chunk URLs
- Scrapes the matched page and up to two competitor pages currently receiving citations
- Sends page content, competitor context, and query to Claude for a structured audit
- Returns a readiness score (0–100), verdict, content gaps, rewritten answer block, suggested headings, and validated FAQ schema

**`prompts.py` — Prompt templates**
All Claude prompt text lives in this module, separate from application logic. The audit system prompt defines the AEO/GEO specialist role. The audit user prompt includes competitor context when available, instructing Claude to identify the specific structural patterns earning citations that the target page lacks.

**`db.py` — Citation history**
Persists each audit run to a local SQLite database, enabling citation rate tracking over time. Designed to be replaced with a cloud database (Supabase) for production deployments where filesystem persistence is not guaranteed.

---

## The Citation Check Methodology

### Perplexity
Uses the official Perplexity Sonar API (`sonar-pro` model). The query is submitted exactly as a user would type it. The tool inspects the `citations` array in the API response and checks whether any returned URL belongs to the organization's domain. All competitor citation URLs are collected for comparative analysis.

### ChatGPT
Uses OpenAI's `gpt-4o-search-preview` model, which performs live web retrieval before generating a response. The tool inspects the `url` field of each annotation in the response and checks for domain matches. This reflects what ChatGPT would cite if a real user asked the same question at the time of the audit.

### Google AI
Uses the `google-genai` SDK with Search Grounding enabled (`Tool(google_search=GoogleSearch())`). Gemini performs a live Google Search and grounds its response in real results. The tool inspects `grounding_metadata.grounding_chunks` for cited URLs. The Gemini API with Search Grounding is the closest available proxy to Google AI Overviews, which do not expose a direct public API. The active model is resolved dynamically at runtime via `client.models.list()` — the highest-version stable Flash model is selected automatically, ensuring the tool never fails due to model deprecation.

---

## The Audit Methodology

When a page is not being cited, GEO Radar does not merely report the failure. It explains it.

Claude analyzes the page against the six signals that drive AI citation:

| Signal | What AI engines reward |
|--------|----------------------|
| Answer-first structure | The direct answer appears in the first one to two sentences, not buried in paragraph four |
| Question-anchored headings | H2 and H3 headings phrased as the exact questions buyers ask |
| Specific evidence | Statistics, prices, timelines, named outcomes — vague claims are not cited |
| Clear business identity | Who the organization is, what it does, who it serves, where it operates |
| Extractable formatting | Short paragraphs, comparison tables, numbered steps |
| Schema markup | FAQPage, LocalBusiness, Service, or Product schema that mirrors visible page content |

Where competitor pages are currently being cited, GEO Radar scrapes up to two of them and includes their content in the audit prompt. Claude is instructed to identify the specific structural or content differences that explain why competitors are cited and the target page is not.

The output for each page includes:

- **Readiness score** — 0 to 100, how ready this page is to be cited for this specific query
- **Verdict** — one sentence identifying the single largest reason the page would or would not be cited
- **Gaps** — three specific, actionable content failures
- **Answer-first rewrite** — an 80–150 word content block, leading with the direct answer, using only facts present on the page or clearly marked for confirmation
- **Suggested headings** — two question-phrased H2 headings that would improve citation probability
- **FAQ schema** — a complete, valid JSON-LD FAQPage schema block ready to paste into the page's `<head>`, containing only answers supported by visible page content

After all per-query audits complete, a synthesis pass identifies the systemic root causes across all queries — the architectural issues that no single page fix will resolve.

---

## Installation

**Requirements:** Python 3.10 or later

```bash
git clone https://github.com/ogbonnayastephen/geo-radar.git
cd geo-radar
pip install -r requirements.txt
streamlit run app.py
```

The application opens in your browser at `http://localhost:8501`. Enter API keys in the sidebar to begin.

---

## Configuration

### Required API Keys

| Service | Purpose | Where to obtain |
|---------|---------|----------------|
| Anthropic | Query clustering, page-to-query matching, audit, synthesis | console.anthropic.com |
| OpenAI | ChatGPT citation check (`gpt-4o-search-preview`) | platform.openai.com/api-keys |
| Perplexity | Perplexity citation check (Sonar API) | perplexity.ai/settings/api |

### Optional API Key

| Service | Purpose | Where to obtain |
|---------|---------|----------------|
| Google AI Studio | Google AI citation check (Gemini + Search Grounding) | aistudio.google.com |

Keys are entered in the sidebar at session start. They are never written to disk, never logged, and exist only in memory for the duration of the browser session. Each user's keys are isolated in a `Keys` dataclass instance scoped to their session — they are never stored in process-level environment variables.

### Environment file (optional)

For local development, create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
PERPLEXITY_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here
```

The application loads this file automatically on startup via `python-dotenv`. In production, keys should be provided as Streamlit secrets or environment variables set by the hosting platform.

---

## Usage

### Step 1 — Describe the organization

In the sidebar, enter the organization name and the domains to watch for in citations (e.g., `acme.com, acmecorp.com`).

In the main form, describe:
- **Services offered** — what the organization does (e.g., *GTM consulting, sales enablement, B2B growth strategy*)
- **Audience** — who it serves (e.g., *Series A startups, B2B SaaS founders*)
- **Location** — city or region if relevant (leave blank for national or global scope)
- **Intent categories** — the three types of searchers to capture queries from (defaults: customers, partners, media)

Click **Find real queries**. The tool scrapes Google autocomplete and Reddit, then Claude organizes results into the defined categories.

### Step 2 — Select queries and crawl the site

Select the queries to audit. Click **Add selected queries and crawl my site**. Enter the organization's homepage URL and click **Crawl site and match pages**.

The crawler maps the site via breadth-first traversal and Claude automatically matches each query to the most relevant page.

### Step 3 — Review and confirm pairings

The query-to-page pairings appear in a text area. Each line follows the format:

```
query text | https://example.com/relevant-page
```

Edit any URL, add missing ones, or remove queries before running the audit.

### Step 4 — Run the audit

Click **Run audit**. The tool runs all citation checks and page audits in sequence and displays:

- **Metrics row** — total queries checked, citation rate on each platform, cited on all three
- **Results table** — per-query citation status, readiness score, and verdict
- **Strategic diagnosis** — systemic root causes and priority fixes across all queries
- **Fixes** — per-query expanders with full gap analysis, rewritten content, headings, and FAQ schema
- **CSV download** — full results exportable for client delivery or internal records

Citation history for each organization is tracked in the **Citation history** expander at the bottom of the page.

---

## Cost Reference

All API costs are billed directly to the user's own accounts. GEO Radar does not charge separately for usage.

| Operation | Approximate cost |
|-----------|-----------------|
| Discovery run (query scraping + clustering) | $0.02 – $0.05 |
| Single query audit (3 citation checks + Claude analysis) | $0.04 – $0.08 |
| 20-query audit, run once per month | $1.00 – $1.80 |
| 20-query audit, run weekly | $4.00 – $7.00 per month |

Google AI citation checks are billed to the user's Google AI Studio account separately and are generally within the free tier for typical audit volumes.

---

## Security Model

- API keys are never written to disk by the application
- Keys live exclusively in Streamlit session state for the duration of one browser session
- Each session holds its own isolated `Keys` instance — no cross-session key leakage is possible
- The SQLite history database stores audit metadata only (query text, citation outcomes, scores) — no API keys, page content, or personal data
- All outbound requests use clearly identified User-Agent strings (`GEO-Radar audit bot`) so site operators can identify and whitelist or block the crawler as appropriate

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Interface | Streamlit |
| AI audit and synthesis | Anthropic SDK — `claude-sonnet-4-6` |
| ChatGPT citation check | OpenAI SDK — `gpt-4o-search-preview` |
| Perplexity citation check | Perplexity Sonar API — `sonar-pro` |
| Google AI citation check | `google-genai` SDK — Gemini Flash with Search Grounding |
| Web scraping | Requests, BeautifulSoup4 |
| Data processing | Pandas |
| Persistence | SQLite via Python standard library |
| Environment | python-dotenv |

---

## Why AEO and GEO Are Now Business-Critical

Traditional SEO optimized for a specific artifact: position in a ranked list. The assumption was that users would evaluate that list and choose where to click. AI answer engines eliminate the list entirely. A single synthesized answer is returned, sourced from a small number of cited pages. For most queries, there are three to eight cited sources. Every other result on the web is, for that query, invisible.

The commercial consequence is not theoretical. Buyers research purchases through AI before they visit a vendor's website. Analysts query AI engines before writing reports. Journalists use AI to identify sources before making contact. If an organization's pages are not structured for AI extraction, the organization does not exist in those research workflows — regardless of how well it ranks in traditional search.

AEO addresses this at the content level: structuring pages so AI can extract a direct, specific, citable answer. GEO addresses this at the optimization level: treating AI engines as the primary distribution channel for authoritative content. GEO Radar makes both disciplines operational — not as abstract guidance, but as a systematic, auditable, executable process that any organization can run against its own website.

---

## License

MIT License. See `LICENSE` for details.
