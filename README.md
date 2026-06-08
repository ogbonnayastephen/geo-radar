# 📡 AEO Radar

Check whether ChatGPT and Perplexity cite your organization for the queries
that matter — then get Claude's exact fixes to make your pages citable.

Built for nonprofits who can't afford a $2,000–$10,000/month AEO agency retainer.
Runs on three AI APIs. Costs under $5/month at typical NGO usage.

---

## What it does

**Step 1 — Discover real queries**
Scrapes Google autocomplete and Reddit for questions real people actually type.
Claude clusters and prioritizes them by intent: people seeking help, donors, volunteers.
No keyword research needed. The team just picks from a list.

**Step 2 — Review and add page URLs**
Selected queries appear in a text area. Add the page you want cited after a `|`.

**Step 3 — Run the audit**
For each query:
- Perplexity checks if you're cited in its AI search results.
- ChatGPT checks if you're cited in its web search results.
- If you're missing from either, Claude reads your page and returns:
  - A readiness score (0–100)
  - The specific gaps on your page
  - An answer-first rewrite ready to paste in
  - FAQ schema code ready to paste into your website's `<head>`

---

## File map

```
aeo-radar/
├── app.py            Streamlit UI — discovery, audit input, results, CSV export
├── radar.py          Citation checks (Perplexity + ChatGPT) + Claude audit
├── discover.py       Query discovery — Google scraping + Reddit + Claude clustering
├── prompts.py        Claude prompt templates (tune output quality here)
├── requirements.txt  Python dependencies
├── .env.example      Copy to .env and add your API keys
├── .gitignore        Keeps .env and secrets out of git
└── README.md         This file
```

---

## Setup (15 minutes)

### 1. Get your three API keys

**Perplexity** — https://www.perplexity.ai/settings/api
**OpenAI (ChatGPT)** — https://platform.openai.com/api-keys
**Anthropic (Claude)** — https://console.anthropic.com

You will need to add a small credit balance to OpenAI ($5) and Anthropic ($5).
Both are pay-as-you-go. No subscription required.

### 2. Install Python (if you don't have it)

Go to python.org/downloads and install Python 3.11 or higher.
On Windows: check "Add Python to PATH" during installation.

### 3. Set up the project

```bash
# Open a terminal and navigate to the project folder
cd path/to/aeo-radar

# Install dependencies
pip install -r requirements.txt

# Add your API keys
cp .env.example .env
# Open .env in any text editor and paste your three keys in
```

### 4. Run it

```bash
streamlit run app.py
```

Your browser opens automatically at http://localhost:8501.

---

## Deploy free (so the NGO team can use it without your laptop)

1. Push the folder to a GitHub repo. Do NOT push .env — .gitignore handles this.
2. Go to share.streamlit.io, connect your GitHub repo, point it at app.py.
3. In app Settings → Secrets, paste:

```toml
PERPLEXITY_API_KEY = "your_key"
OPENAI_API_KEY     = "your_key"
ANTHROPIC_API_KEY  = "your_key"
```

4. Click Deploy. Streamlit gives you a public URL the whole team can use.
   Hosting is free. The team only pays for API usage.

---

## Monthly cost

| Usage | Cost/month |
|---|---|
| Discovery run (once) | ~$0.02 |
| 20 queries, once a month | ~$1.20 |
| 20 queries, weekly | ~$4.80 |
| Streamlit hosting | $0 |

All costs are pay-as-you-go. No contracts, no minimums.

---

## Honest limitations

- **Google AI Overviews**: Google has no public API. The tool cannot verify live
  Google AIO citations. It audits your page's *readiness* for Google instead.
- **Citation guarantees**: No tool can force an AI to cite you. This improves
  the probability significantly. Citations are ultimately each platform's call.
- **Publishing**: The tool generates fixes. A human still pastes them into the website.
