"""
Prompt templates for the Claude audit engine.

Keeping prompts in one file means you can tune the tool's output quality
without touching application logic. Edit the strings here, not radar.py.

Discovery prompts live in discover.py alongside the discovery logic.
"""

# Single source of truth for the Claude model used across all modules.
# Change it here and every module picks it up automatically.
CLAUDE_MODEL = "claude-sonnet-4-6"

# The system prompt sets Claude's role for every page audit call.
AUDIT_SYSTEM_PROMPT = """You are an Answer Engine Optimization (AEO) and Generative Engine Optimization (GEO) specialist. You analyze whether a web page is structured to be cited by AI answer engines (ChatGPT, Perplexity, Google AI Overviews) for a specific search query, then produce concrete fixes.

You judge pages against the signals that actually drive AI citations:
- Answer-first structure: the direct answer appears in the first 1-2 sentences, not buried.
- Question-anchored headings: H2/H3 headings phrased as the questions buyers and customers ask.
- Evidence: specific statistics, prices, timelines, named results, and concrete outcomes. Vague claims do not get cited.
- Clear business identity: who you are, what you do, who you serve, and where you operate.
- Extractable formatting: short paragraphs, tables for comparisons, lists for steps or features.
- Schema markup (FAQPage, LocalBusiness, Service, Product) that mirrors visible page content.

You evaluate pages from the perspective of a potential customer or buyer who is researching a product, service, or solution. Your job is to identify whether the page clearly answers their question and gives AI engines enough structured, specific content to cite it as an authoritative source.

You are precise, you do not pad, and you never invent facts about the organization. If the page lacks the information needed to answer the query, you say so and describe what content must be created rather than fabricating it."""


def build_audit_prompt(
    query: str,
    page_url: str,
    page_text: str,
    org_name: str,
    competitor_snippets: list[dict] | None = None,
) -> str:
    """
    Build the user prompt for a single page audit.
    Truncates page text to ~3000 tokens to keep cost predictable.
    Optionally includes competitor page snippets (capped at 1500 chars each).
    """
    trimmed = page_text[:12000]

    competitor_block = ""
    if competitor_snippets:
        parts = []
        for c in competitor_snippets[:2]:
            parts.append(
                f"URL: {c['url']}\n{c['snippet'][:1500]}"
            )
        competitor_block = (
            "\n\n--- COMPETITOR PAGES CURRENTLY BEING CITED INSTEAD ---\n"
            + "\n\n---\n".join(parts)
            + "\n--- END COMPETITOR PAGES ---\n"
            "\nUse these to identify structural or content patterns that are earning"
            " citations that your page is missing. Reference specific differences in"
            " your gaps and rewritten_section."
        )

    return f"""Organization: {org_name}
Target search query: "{query}"
Page being audited: {page_url}

--- PAGE CONTENT START ---
{trimmed}
--- PAGE CONTENT END ---
{competitor_block}
Analyze this page for its ability to be cited by AI answer engines when someone \
searches the target query above.

Return ONLY a valid JSON object, no markdown fences, no preamble, with exactly these keys:

{{
  "readiness_score": <integer 0-100, how ready this page is to be cited for this query>,
  "verdict": "<one sentence: the single biggest reason it would or would not be cited>",
  "gaps": ["<specific gap 1>", "<specific gap 2>", "<specific gap 3>"],
  "rewritten_section": "<an answer-first content block, 80-150 words, that directly \
answers the query using only facts present in the page or clearly marked \
[ORG TO CONFIRM: ...] where a fact is missing. Lead with the direct answer.>",
  "suggested_headings": ["<question-phrased H2 1>", "<question-phrased H2 2>"],
  "faq_schema": "<a complete, valid JSON-LD FAQPage schema as an escaped string, \
with 2-3 Q&A pairs relevant to the query, ready to paste into a \
<script type=\\"application/ld+json\\"> tag. Use only answers supported by the \
page content or marked [ORG TO CONFIRM].>"
}}

Rules:
- Output must be parseable by json.loads(). Escape all inner quotes and newlines correctly.
- Never invent statistics, services, or claims. Mark anything uncertain as [ORG TO CONFIRM: ...].
- The rewritten_section must lead with the answer in the first sentence.
- CRITICAL SCHEMA RULE: Every question and answer in the faq_schema must only use information \
that is explicitly visible in the page content provided. Do not include any answer that contains \
[ORG TO CONFIRM] in the faq_schema. If an answer cannot be written from page content alone, \
omit that Q&A pair entirely. A schema with two clean pairs is better than three pairs with \
guessed answers.
- The faq_schema answers must match word-for-word what a user would see on the page. \
Google penalises schema that does not match visible content."""
