"""
GEO Radar — PDF audit report generator.

Produces a branded, client-ready PDF using reportlab.
Usage: pdf_bytes = generate_pdf(org_name, results, synthesis, prepared_by)
Returns bytes suitable for st.download_button(mime="application/pdf").
"""

from io import BytesIO
from datetime import datetime


INDIGO   = "#6366F1"
DARK     = "#111827"
MID_GRAY = "#6B7280"
LIGHT_BG = "#F9FAFB"
BORDER   = "#E5E7EB"


def generate_pdf(
    org_name: str,
    results: list[dict],
    synthesis: dict | None,
    prepared_by: str = "GEO Radar",
) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Table, TableStyle,
        Spacer, HRFlowable, PageBreak, KeepTogether,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=0.9 * inch,
    )

    styles = getSampleStyleSheet()
    indigo = colors.HexColor(INDIGO)
    dark   = colors.HexColor(DARK)
    gray   = colors.HexColor(MID_GRAY)
    lt_bg  = colors.HexColor(LIGHT_BG)
    border = colors.HexColor(BORDER)

    def ps(name, **kw):
        return ParagraphStyle(name, parent=styles["Normal"], **kw)

    title_s  = ps("GT", fontSize=28, textColor=indigo, leading=34, spaceAfter=4)
    sub_s    = ps("GS", fontSize=18, textColor=dark,   leading=24, spaceAfter=6)
    meta_s   = ps("GM", fontSize=10, textColor=gray,   spaceAfter=2)
    h1_s     = ps("H1", fontSize=16, textColor=indigo, fontName="Helvetica-Bold",
                   spaceBefore=14, spaceAfter=6)
    h2_s     = ps("H2", fontSize=12, textColor=dark,   fontName="Helvetica-Bold",
                   spaceBefore=10, spaceAfter=4)
    body_s   = ps("BD", fontSize=10, textColor=dark,   leading=14, spaceAfter=3)
    small_s  = ps("SM", fontSize=9,  textColor=gray,   leading=12, spaceAfter=2)
    quote_s  = ps("QT", fontSize=10, textColor=dark,   leading=14, spaceAfter=3,
                   leftIndent=12, borderPad=4,
                   backColor=colors.HexColor("#EEF2FF"))

    def hr():
        return HRFlowable(width="100%", thickness=0.5, color=border, spaceAfter=8, spaceBefore=8)

    def tbl_style(header_color=indigo):
        return TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), header_color),
            ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
            ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [lt_bg, colors.white]),
            ("GRID",         (0, 0), (-1, -1), 0.5, border),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("PADDING",      (0, 0), (-1, -1), 6),
        ])

    def badge(cited):
        if cited is True:
            return "✓ Cited"
        if cited is False:
            return "✗ Not cited"
        return "—"

    story = []

    # ── COVER PAGE ──────────────────────────────────────────────────────────
    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph("AI Visibility Audit Report", title_s))
    story.append(Paragraph(org_name, sub_s))
    story.append(HRFlowable(width="100%", thickness=3, color=indigo, spaceAfter=12))
    story.append(Paragraph(
        f"Prepared by <b>{prepared_by}</b>  ·  {datetime.now().strftime('%B %d, %Y')}",
        meta_s,
    ))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(
        "This report audits whether your business is cited by the three leading AI answer engines — "
        "Perplexity, ChatGPT, and Google AI — and delivers the exact content fixes needed to change your visibility.",
        ps("Intro", fontSize=10, textColor=gray, leading=14),
    ))
    story.append(PageBreak())

    # ── EXECUTIVE SUMMARY ────────────────────────────────────────────────────
    story.append(Paragraph("Executive Summary", h1_s))

    valid      = [r for r in results if r.get("perplexity_cited") is not None
                                     or r.get("chatgpt_cited") is not None]
    total      = len(valid)
    perp_cited = sum(1 for r in valid if r.get("perplexity_cited"))
    gpt_cited  = sum(1 for r in valid if r.get("chatgpt_cited"))
    goog_cited = sum(1 for r in valid if r.get("google_cited"))
    all_cited  = sum(1 for r in valid if (r.get("perplexity_cited") and
                                           r.get("chatgpt_cited") and
                                           r.get("google_cited")))

    def pct(n):
        return f"{round(n / total * 100)}%" if total else "0%"

    metrics_data = [
        ["Metric", "Result"],
        ["Queries audited",         str(total)],
        ["Cited on Perplexity",     f"{perp_cited}/{total} ({pct(perp_cited)})"],
        ["Cited on ChatGPT",        f"{gpt_cited}/{total} ({pct(gpt_cited)})"],
        ["Cited on Google AI",      f"{goog_cited}/{total} ({pct(goog_cited)})"],
        ["Cited on all 3 engines",  f"{all_cited}/{total} ({pct(all_cited)})"],
    ]
    metrics_tbl = Table(metrics_data, colWidths=[3.5 * inch, 3.5 * inch])
    metrics_tbl.setStyle(tbl_style())
    story.append(metrics_tbl)
    story.append(Spacer(1, 0.3 * inch))

    # ── STRATEGIC DIAGNOSIS ──────────────────────────────────────────────────
    if synthesis and not synthesis.get("error"):
        story.append(Paragraph("Strategic Diagnosis", h1_s))
        story.append(Paragraph(
            "Systemic root causes across all queries — not per-page symptoms.", small_s
        ))
        story.append(Spacer(1, 0.1 * inch))

        story.append(Paragraph("Root Causes", h2_s))
        for cause in (synthesis.get("root_causes") or []):
            story.append(Paragraph(f"• {cause}", body_s))

        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("Priority Fixes (Highest Impact First)", h2_s))
        for i, fix in enumerate((synthesis.get("priority_fixes") or []), 1):
            story.append(Paragraph(f"{i}.  {fix}", body_s))

        story.append(Spacer(1, 0.3 * inch))

    # ── QUERY RESULTS TABLE ──────────────────────────────────────────────────
    story.append(Paragraph("Query Results", h1_s))

    rows = [["Query", "Perplexity", "ChatGPT", "Google AI", "Score", "Verdict"]]
    for r in results:
        score = f"{r['readiness_score']}/100" if r.get("readiness_score") is not None else "—"
        rows.append([
            Paragraph(r.get("query", ""), ps("QC", fontSize=9)),
            badge(r.get("perplexity_cited")),
            badge(r.get("chatgpt_cited")),
            badge(r.get("google_cited")),
            score,
            Paragraph((r.get("verdict") or "")[:100], ps("VC", fontSize=9)),
        ])

    results_tbl = Table(
        rows,
        colWidths=[2.1 * inch, 0.85 * inch, 0.75 * inch, 0.85 * inch, 0.6 * inch, 1.85 * inch],
    )
    results_tbl.setStyle(tbl_style())
    story.append(results_tbl)

    # ── DETAILED FIXES ───────────────────────────────────────────────────────
    needs_fixes = [r for r in results if not r.get("error") and r.get("gaps")]
    if needs_fixes:
        story.append(PageBreak())
        story.append(Paragraph("Detailed Fix Recommendations", h1_s))
        story.append(Paragraph(
            "For each query where gaps were identified, GEO Radar provides a specific content "
            "rewrite, question-phrased headings, and FAQ schema. Apply these to the matched page "
            "to improve AI citation probability.",
            small_s,
        ))

        for r in needs_fixes:
            block = []
            block.append(Paragraph(r.get("query", ""), h2_s))
            block.append(Paragraph(
                f"Page: {r.get('page_url') or '—'}  ·  "
                f"Perplexity {badge(r.get('perplexity_cited'))}  "
                f"ChatGPT {badge(r.get('chatgpt_cited'))}  "
                f"Google AI {badge(r.get('google_cited'))}  "
                f"Score {r.get('readiness_score', '—')}/100",
                small_s,
            ))

            if r.get("verdict"):
                block.append(Paragraph(f"<b>Assessment:</b> {r['verdict']}", body_s))

            if r.get("gaps"):
                block.append(Paragraph("<b>Content gaps:</b>", body_s))
                for g in r["gaps"]:
                    block.append(Paragraph(f"• {g}", body_s))

            if r.get("rewritten_section"):
                block.append(Paragraph("<b>Answer-first rewrite:</b>", body_s))
                block.append(Paragraph(r["rewritten_section"], quote_s))

            if r.get("suggested_headings"):
                block.append(Paragraph("<b>Suggested question-phrased headings:</b>", body_s))
                for h in (r["suggested_headings"] or []):
                    block.append(Paragraph(f"• {h}", body_s))

            block.append(hr())
            story.append(KeepTogether(block[:6]))  # keep heading + first few items together
            story.extend(block[6:])

    doc.build(story)
    return buf.getvalue()
