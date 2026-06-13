# GEO Radar

Automated AEO and GEO audit tool for any business, consultant, or agency.

GEO Radar finds the real questions people ask about your topic, checks whether your organization is being cited on Perplexity and ChatGPT, and produces ready-to-use content fixes for every gap — in one run.

**Live demo:** [geo-radar.streamlit.app](https://geo-radar.streamlit.app)

---

## How it works

| Step | What happens |
|------|-------------|
| 1 | Scrapes real queries from Google autocomplete and Reddit |
| 2 | Claude organizes them by your user-defined intent categories |
| 3 | Crawls your website (up to 60 pages) |
| 4 | Claude matches each query to the most relevant page |
| 5 | Perplexity checks if you are cited for each query |
| 6 | ChatGPT checks if you are cited for each query |
| 7 | Claude scores each page and writes exact content fixes |
| 8 | You get a full report with fixes ready to implement |

---

## Getting started

```bash
git clone https://github.com/ogbonnayastephen/geo-radar.git
cd geo-radar
pip install -r requirements.txt
streamlit run app.py
```

Enter your API keys in the sidebar when the app opens. Keys are never stored — they exist only for the duration of the session.

---

## API keys required

| Service | Where to get it |
|---------|----------------|
| Perplexity | perplexity.ai/settings/api |
| OpenAI | platform.openai.com/api-keys |
| Anthropic | console.anthropic.com |

Each service requires a small credit balance ($5 each gets you started; at this tool's usage level, $5 lasts several months).

---

## Cost estimate

| Usage | Estimated cost |
|-------|---------------|
| Discovery run | ~$0.02 |
| 20 queries once/month | ~$1.20 |
| 20 queries weekly | ~$4.80/month |

All costs come out of your own API accounts.

---

## Tech stack

- **UI:** Streamlit
- **AI:** Anthropic SDK (`claude-sonnet-4-6`), OpenAI SDK (`gpt-4o-search-preview`), Perplexity Sonar API
- **Scraping:** Requests, BeautifulSoup4
- **Data:** Pandas
