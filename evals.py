"""
GEO Radar — lightweight evals for the Claude audit prompt (radar.audit_page).

Run manually whenever prompts.py changes:
    python3 evals.py

No framework, no new dependency — just a fixed set of page/query test cases
run through the real audit call, checked against a few cheap assertions:
  1. Output is valid, complete JSON (no "error" key).
  2. readiness_score is 0-100.
  3. gaps is a non-empty list when the score is low.
  4. verdict/gaps never use absolute language ("never", "zero", "does not
     exist", "nowhere", "impossible") to describe something missing — the
     exact regression this file exists to catch. See prompts.py's
     "ABSENCE CLAIMS MUST BE SCOPED" rule.

Each test case costs one real Claude call — keep the list short and re-run
it whenever you touch prompts.py, not on every commit.
"""

import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

import radar
from config import Keys

_KEYS = Keys(
    anthropic=os.getenv("ANTHROPIC_API_KEY", ""),
    openai=os.getenv("OPENAI_API_KEY", ""),
    perplexity=os.getenv("PERPLEXITY_API_KEY", ""),
    google=os.getenv("GOOGLE_API_KEY", ""),
)

BANNED_ABSOLUTE_TERMS = ["never", "zero", "does not exist", "doesn't exist", "nowhere", "impossible"]

TEST_CASES = [
    {
        "name": "thin_page_no_relevant_content",
        "org_name": "Nexus Consulting",
        "query": "go-to-market strategy for B2B SaaS",
        "page_url": "https://nexusconsulting.com/about",
        "page_text": (
            "About Nexus Consulting. We are a team of five people based in Austin, Texas. "
            "Founded in 2019. Contact us at hello@nexusconsulting.com. "
            "Follow us on LinkedIn and Twitter."
        ),
    },
    {
        "name": "strong_complete_page",
        "org_name": "Nexus Consulting",
        "query": "how much does GTM consulting cost",
        "page_url": "https://nexusconsulting.com/pricing",
        "page_text": (
            "GTM Consulting Pricing. Our go-to-market engagements start at $8,000/month for a "
            "3-month minimum. This includes: weekly strategy sessions, ICP definition, channel "
            "testing across 2-3 acquisition channels, and a sales playbook. Enterprise engagements "
            "(6+ months, dedicated team) start at $15,000/month. We've helped 40+ B2B SaaS "
            "companies launch GTM motions since 2019, with an average time-to-first-customer of "
            "6 weeks. Frequently asked: Do you require a contract? Yes, minimum 3 months. What's "
            "included in onboarding? A full ICP and channel audit in week one."
        ),
    },
    {
        "name": "partial_page_missing_specifics",
        "org_name": "Nexus Consulting",
        "query": "GTM consulting for enterprise software companies",
        "page_url": "https://nexusconsulting.com/enterprise",
        "page_text": (
            "Enterprise GTM Services. We help enterprise software companies scale their go-to-market "
            "motion. Our team has deep experience working with large organizations. We offer "
            "customized strategies tailored to your needs. Contact us to learn more about how we "
            "can help your business grow."
        ),
    },
    {
        "name": "faq_style_page",
        "org_name": "Bright Legal Group",
        "query": "how much does a will cost",
        "page_url": "https://brightlegal.com/estate-planning-faq",
        "page_text": (
            "Estate Planning FAQ. How much does a basic will cost? A simple will starts at $350. "
            "A will with a trust starts at $1,200. What's included? Asset inventory, beneficiary "
            "designation, and executor appointment. How long does it take? Most wills are completed "
            "within 2 weeks of your initial consultation. Do I need a lawyer? Not legally required, "
            "but recommended for estates over $500,000."
        ),
    },
    {
        "name": "tangential_mismatch",
        "org_name": "GreenLeaf Landscaping",
        "query": "best CRM software for sales teams",
        "page_url": "https://greenleaflandscaping.com/services",
        "page_text": (
            "GreenLeaf Landscaping Services. We provide lawn care, tree trimming, seasonal cleanup, "
            "and garden design for residential and commercial properties across the metro area. "
            "Free estimates available. Licensed and insured since 2005."
        ),
    },
    {
        "name": "dense_service_page_mixed_signal",
        "org_name": "Apex Fitness Coaching",
        "query": "online personal training for beginners",
        "page_url": "https://apexfitness.com/online-coaching",
        "page_text": (
            "Online Personal Training. Apex Fitness offers 1-on-1 online coaching with certified "
            "trainers. Programs are customized to your goals, whether that's weight loss, strength, "
            "or general fitness. We use an app to track your workouts and nutrition. Coaches check "
            "in weekly. Plans range from $99-$249/month depending on check-in frequency. Over 500 "
            "clients coached since 2018."
        ),
    },
    {
        "name": "very_short_thin_content",
        "org_name": "Solstice Accounting",
        "query": "small business tax preparation services",
        "page_url": "https://solsticeaccounting.com/services",
        "page_text": "Solstice Accounting. Tax prep, bookkeeping, and payroll for small businesses.",
    },
    {
        "name": "different_vertical_home_services",
        "org_name": "Rapid Roofing Co",
        "query": "emergency roof repair near me",
        "page_url": "https://rapidroofing.com/emergency-repair",
        "page_text": (
            "Emergency Roof Repair. Rapid Roofing offers 24/7 emergency response for storm damage, "
            "leaks, and fallen trees. Average response time: 90 minutes. We work directly with your "
            "insurance company. Serving the tri-county area for over 15 years. Call our emergency "
            "line any time, day or night."
        ),
    },
]


def find_banned_terms(text: str) -> list[str]:
    text_lower = (text or "").lower()
    return [t for t in BANNED_ABSOLUTE_TERMS if re.search(r"\b" + re.escape(t) + r"\b", text_lower)]


def check_result(case: dict, result: dict) -> list[str]:
    """Return a list of failure descriptions; empty list means the case passed."""
    failures = []

    if result.get("error"):
        failures.append(f"error: {result['error']}")
        return failures

    score = result.get("readiness_score")
    if not isinstance(score, (int, float)) or not (0 <= score <= 100):
        failures.append(f"readiness_score invalid: {score!r}")

    gaps = result.get("gaps")
    if not isinstance(gaps, list):
        failures.append(f"gaps is not a list: {gaps!r}")
    elif isinstance(score, (int, float)) and score < 40 and not gaps:
        failures.append("low readiness_score but gaps is empty")

    verdict = result.get("verdict", "")
    if not verdict:
        failures.append("verdict is empty")

    banned_in_verdict = find_banned_terms(verdict)
    if banned_in_verdict:
        failures.append(f"absolute language in verdict: {banned_in_verdict} — {verdict!r}")

    for gap in (gaps or []):
        banned_in_gap = find_banned_terms(gap)
        if banned_in_gap:
            failures.append(f"absolute language in gap: {banned_in_gap} — {gap!r}")

    for key in ("rewritten_section", "suggested_headings", "faq_schema"):
        if key not in result:
            failures.append(f"missing key: {key}")

    return failures


def main() -> int:
    if not _KEYS.anthropic:
        print("ANTHROPIC_API_KEY is not set — cannot run evals.")
        return 1

    total_failures = 0
    n_passed = 0
    for case in TEST_CASES:
        result = radar.audit_page(
            query=case["query"],
            page_url=case["page_url"],
            page_text=case["page_text"],
            org_name=case["org_name"],
            keys=_KEYS,
        )
        failures = check_result(case, result)
        status = "PASS" if not failures else "FAIL"
        print(f"[{status}] {case['name']}")
        for f in failures:
            print(f"    - {f}")
        if not failures:
            n_passed += 1
        total_failures += len(failures)

    print()
    print(f"{n_passed}/{len(TEST_CASES)} test cases passed ({total_failures} total failure(s)).")
    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
