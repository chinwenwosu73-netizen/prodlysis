"""Report generation: Markdown and PDF export of an analysis."""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .metrics import METRIC_DEFS

# Prodlysis brand palette
PINK = colors.HexColor("#EC4899")
LIGHT_PINK = colors.HexColor("#FCE7F3")
CARD = colors.HexColor("#FFF7FB")
BORDER = colors.HexColor("#F4D3E4")
GREEN = colors.HexColor("#22C55E")
AMBER = colors.HexColor("#F59E0B")
RED = colors.HexColor("#EF4444")
TEXT = colors.HexColor("#1F2937")


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def build_markdown(analysis, previous_label="Previous", current_label="Current"):
    """Build a Markdown report from an analysis dict."""
    md = []
    md.append("# Prodlysis UX Comparison Report\n")

    md.append(f"**Periods:** {previous_label} vs {current_label}\n")
    if analysis.get("health") is not None:
        md.append(f"**UX Health Score:** {analysis['health']}/100"
                  f" (Δ {analysis.get('health_delta', 0):+d} points)\n")
    md.append(f"**Generated:** {_now_str()}\n")

    # Executive summary
    md.append("\n## Executive Summary\n")
    md.append(analysis.get("summary") or "No summary available.\n")

    # Metrics comparison table
    md.append("\n## Metrics Comparison\n")
    md.append("| Metric | Previous | Current | Change | Status |")
    md.append("| --- | --- | --- | --- | --- |")
    for m in analysis.get("metrics", []):
        md.append(
            f"| {m['label']} | {m['previous_display']} | {m['current_display']} "
            f"| {m['change_display']} | {m['status'].title()} |"
        )

    # Findings
    md.append("\n## Findings\n")
    for f in analysis.get("findings", []):
        md.append(f"### {f['title']}\n")
        for e in f.get("evidence", []):
            md.append(f"- {e}")
        if f.get("cause"):
            md.append(f"\n**Likely cause:** {f['cause']}")
        if f.get("confidence") is not None:
            md.append(f"\n**Confidence:** {f['confidence']}%\n")

    # Recommendations
    md.append("\n## Recommendations\n")
    for i, r in enumerate(analysis.get("recommendations", []), 1):
        md.append(f"{i}. **[{r.get('priority', 'Medium')}]** {r['text']}")

    # Next steps
    md.append("\n## Next Steps\n")
    for i, r in enumerate(analysis.get("next_steps", analysis.get("recommendations", [])), 1):
        md.append(f"{i}. {r['text'] if isinstance(r, dict) else r}")

    return "\n".join(md)


def _now_str():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def build_pdf_bytes(analysis, previous_label="Previous", current_label="Current"):
    """Build an A4 PDF report and return its bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Prodlysis UX Comparison Report",
        author="Prodlysis",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ProdTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, textColor=TEXT, spaceAfter=2,
    )
    sub_style = ParagraphStyle(
        "ProdSub", parent=styles["Normal"], fontSize=10, textColor=PINK,
        spaceAfter=10,
    )
    h2 = ParagraphStyle(
        "ProdH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=13, textColor=TEXT, spaceBefore=12, spaceAfter=4,
    )
    body = ParagraphStyle(
        "ProdBody", parent=styles["Normal"], fontSize=9.5, leading=13,
        textColor=TEXT,
    )
    bullet = ParagraphStyle(
        "ProdBullet", parent=body, leftIndent=10, bulletIndent=0,
    )

    story = []
    story.append(Paragraph("PRODLYSIS", title_style))
    story.append(Paragraph("AI-Powered UX Analysis &amp; Product Insights", sub_style))
    story.append(Paragraph(f"Periods: {previous_label} vs {current_label} &nbsp;·&nbsp; Generated {_now_str()}", sub_style))

    # Health score
    story.append(Paragraph("Executive Summary", h2))
    if analysis.get("health") is not None:
        story.append(Paragraph(
            f"UX Health Score: <b>{analysis['health']}/100</b> "
            f"(Δ {analysis.get('health_delta', 0):+d} points)",
            body,
        ))
    story.append(Spacer(1, 3))
    story.append(Paragraph(analysis.get("summary") or "No summary available.", body))

    # Metrics table
    story.append(Paragraph("Metrics Comparison", h2))
    header = ["Metric", "Previous", "Current", "Change", "Status"]
    rows = [header]
    for m in analysis.get("metrics", []):
        rows.append([
            m["label"], m["previous_display"], m["current_display"],
            m["change_display"], m["status"].title(),
        ])
    if len(rows) > 1:
        t = Table(rows, colWidths=[60 * mm, 32 * mm, 32 * mm, 28 * mm, 24 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PINK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 1), (-1, -1), CARD),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t)

    # Findings
    story.append(Paragraph("Findings", h2))
    for f in analysis.get("findings", []):
        story.append(Paragraph(f"<b>{f['title']}</b>", body))
        for e in f.get("evidence", []):
            story.append(Paragraph(f"• {e}", bullet))
        if f.get("cause"):
            story.append(Paragraph(f"<b>Likely cause:</b> {f['cause']}", bullet))
        if f.get("confidence") is not None:
            story.append(Paragraph(f"<b>Confidence:</b> {f['confidence']}%", bullet))
        story.append(Spacer(1, 5))

    # Recommendations
    story.append(Paragraph("Recommendations", h2))
    for i, r in enumerate(analysis.get("recommendations", []), 1):
        priority = r.get("priority", "Medium")
        story.append(Paragraph(f"{i}. <b>[{priority}]</b> {r['text']}", bullet))

    # Next steps
    story.append(Paragraph("Next Steps", h2))
    for i, r in enumerate(analysis.get("next_steps", analysis.get("recommendations", [])), 1):
        text = r["text"] if isinstance(r, dict) else r
        story.append(Paragraph(f"{i}. {text}", bullet))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
