"""
report_pdf.py — the Loan Readiness Improvement Report (PDF), the "essence" deliverable.

Renders a multi-section PDF from an Answers object + improvement_engine.analyse() output:
  1. Snapshot (readiness score, approval likelihood, products)
  2. Where you stand
  3. What's working for you / holding you back
  4. Your personalised action plan (ranked, causally-corrected)   <- the core
  5. Causal reality-check (raw vs causal, why we don't oversell)  <- the differentiator
  6. Your improvement roadmap (by effort/time)
  7. Method & disclaimers

Depends only on reportlab (kept out of the model path so serving stays light).
"""
from io import BytesIO
from datetime import date
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)

BRAND = colors.HexColor("#2a78d6")
DARK = colors.HexColor("#0b0b0b")
MUTE = colors.HexColor("#52514e")
GOOD = colors.HexColor("#0ca30c")
WARN = colors.HexColor("#d0801b")
BAD = colors.HexColor("#d03b3b")
LIGHT = colors.HexColor("#eef4fb")
HAIR = colors.HexColor("#d9d9d6")

_ss = getSampleStyleSheet()

def _st(name, **kw):
    base = kw.pop("parent", _ss["Normal"])
    return ParagraphStyle(name, parent=base, **kw)

H1 = _st("H1", fontName="Helvetica-Bold", fontSize=20, textColor=BRAND, spaceAfter=2, leading=23)
H2 = _st("H2", fontName="Helvetica-Bold", fontSize=13, textColor=DARK, spaceBefore=14, spaceAfter=6, leading=16)
BODY = _st("BODY", fontSize=10, textColor=DARK, leading=14, spaceAfter=4)
SMALL = _st("SMALL", fontSize=8.5, textColor=MUTE, leading=11)
CELL = _st("CELL", fontSize=9, textColor=DARK, leading=12)
CELLB = _st("CELLB", fontName="Helvetica-Bold", fontSize=9, textColor=DARK, leading=12)
WHITEB = _st("WHITEB", fontName="Helvetica-Bold", fontSize=9, textColor=colors.white, leading=12)


def _band(readiness):
    if readiness >= 75: return "Strong", GOOD
    if readiness >= 55: return "Good", GOOD
    if readiness >= 35: return "Fair", WARN
    return "Needs work", BAD


def _bar(readiness, width=150):
    """A simple readiness bar as a 1-row table."""
    fill = max(1, int(width * readiness / 100))
    _, col = _band(readiness)
    t = Table([[" ", " "]], colWidths=[fill, width - fill], rowHeights=[9])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), col),
        ("BACKGROUND", (1, 0), (1, 0), HAIR),
        ("LINEABOVE", (0, 0), (-1, -1), 0, colors.white),
        ("BOX", (0, 0), (-1, -1), 0, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _metric_row(items):
    """items: list of (label, value, sub). Renders as a row of stat cells."""
    cells = []
    for label, value, sub in items:
        cells.append([Paragraph(label, SMALL), Paragraph(f"<b>{value}</b>",
                     _st("bignum", fontSize=17, textColor=DARK, leading=19)),
                     Paragraph(sub, SMALL)])
    inner = [Table([[c[0]], [c[1]], [c[2]]], style=TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])) for c in cells]
    t = Table([inner], colWidths=[None] * len(inner))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.5, HAIR),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _conf_color(conf):
    return {"High": GOOD, "Medium": WARN, "Low": BAD, "Model-implied": MUTE}.get(conf, MUTE)


def build_report_pdf(ans, res, applicant_name="Applicant", product_label=""):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Loan Readiness Improvement Report")
    S = []
    r_now, r_aft = res["readiness_now"], res["readiness_after_top3"]
    band_txt, band_col = _band(r_now)

    # ---- header ----
    S.append(Paragraph("Loan Readiness Improvement Report", H1))
    S.append(Paragraph(f"{applicant_name} &nbsp;·&nbsp; {product_label or res['track'].title()} "
                       f"&nbsp;·&nbsp; {date.today():%d %b %Y}", SMALL))
    S.append(Spacer(1, 4))
    S.append(HRFlowable(width="100%", thickness=1, color=BRAND, spaceAfter=8))

    # ---- snapshot ----
    S.append(_metric_row([
        ("READINESS SCORE", f"{r_now}/100", f"{band_txt}"),
        ("APPROVAL LIKELIHOOD", f"{res['approval_now_pct']:.0f}%", "indicative"),
        ("PRODUCTS YOU QUALIFY FOR", f"{len(res['products_now'])}/{len(res['products_all'])}",
         "today"),
        ("IF YOU ACT ON TOP 3", f"{r_aft}/100", f"+{r_aft - r_now} points"),
    ]))
    S.append(Spacer(1, 6))
    S.append(Paragraph("Your readiness today", SMALL))
    S.append(_bar(r_now))
    S.append(Spacer(1, 2))
    S.append(Paragraph(f"<font color='#52514e' size=8>0</font>"
                       f"{'&nbsp;' * 60}<font color='#52514e' size=8>100</font>", SMALL))

    # ---- where you stand ----
    S.append(Paragraph("Where you stand today", H2))
    verdict = {
        "Strong": "You present as a strong applicant. Most products are within reach.",
        "Good": "You are close to approval for several products. A few changes will strengthen you.",
        "Fair": "You are on the borderline. The plan below shows the fastest ways to improve.",
        "Needs work": "There is meaningful room to improve. Focus on the highest-impact actions first.",
    }[band_txt]
    S.append(Paragraph(verdict, BODY))
    prod_rows = [[Paragraph("Product", CELLB), Paragraph("Today", CELLB),
                  Paragraph("After your plan", CELLB)]]
    for p in res["products_all"]:
        now = "Yes" if p in res["products_now"] else "-"
        aft = "Yes" if p in res["products_after"] else "-"
        prod_rows.append([Paragraph(p, CELL),
                          Paragraph(f"<font color='{GOOD.hexval() if now=='Yes' else '#898781'}'>{now}</font>", CELL),
                          Paragraph(f"<font color='{GOOD.hexval() if aft=='Yes' else '#898781'}'>{aft}</font>", CELL)])
    pt = Table(prod_rows, colWidths=[240, 90, 120])
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("BOX", (0, 0), (-1, -1), 0.5, HAIR), ("LINEBELOW", (0, 0), (-1, 0), 0.5, HAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    S.append(pt)

    # ---- action plan (core) ----
    S.append(Paragraph("Your personalised action plan", H2))
    S.append(Paragraph("Ranked by how much each change improves your readiness. The impact is "
                       "<b>causally corrected</b> — we discount changes that the raw model "
                       "overstates, so you focus effort where it genuinely counts.", BODY))
    rows = [[Paragraph("#", WHITEB), Paragraph("Action you can take", WHITEB),
             Paragraph("Readiness gain", WHITEB), Paragraph("Effort", WHITEB),
             Paragraph("Confidence", WHITEB)]]
    for i, p in enumerate(res["plan"], 1):
        rows.append([
            Paragraph(str(i), CELL),
            Paragraph(f"<b>{p['title']}</b><br/><font size=8 color='#52514e'>{p['detail']} "
                      f"({p['timeframe']})</font>", CELL),
            Paragraph(f"<b>+{p['readiness_gain_pts']}</b> pts", CELL),
            Paragraph(p["effort"], CELL),
            Paragraph(f"<font color='{_conf_color(p['confidence']).hexval()}'>&#9679;</font> "
                      f"{p['confidence']}", CELL),
        ])
    at = Table(rows, colWidths=[16, 250, 70, 55, 80], repeatRows=1)
    at.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("BOX", (0, 0), (-1, -1), 0.5, HAIR), ("INNERGRID", (0, 0), (-1, -1), 0.3, HAIR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    S.append(at)
    if not res["plan"]:
        S.append(Paragraph("No high-impact changes were identified from the details provided.", BODY))

    # ---- causal reality-check (differentiator) ----
    S.append(Paragraph("The causal reality-check", H2))
    S.append(Paragraph("Most tools tell you what <i>correlates</i> with approval. We go further: "
                       "using double/debiased machine learning, we estimate what each change "
                       "<i>causally</i> does, so we never oversell a lever the raw model overstates.", BODY))
    cr = [[Paragraph("Change", WHITEB), Paragraph("Raw model says", WHITEB),
           Paragraph("Causally-corrected", WHITEB), Paragraph("Confidence", WHITEB)]]
    for p in res["plan"]:
        if p["confidence"] == "Model-implied":
            corr = "not causally tested"
        else:
            corr = f"-{p['causal_risk_drop_pp']:.1f}pp risk"
        cr.append([Paragraph(p["title"], CELL),
                   Paragraph(f"-{p['raw_risk_drop_pp']:.1f}pp risk", CELL),
                   Paragraph(corr, CELL),
                   Paragraph(f"<font color='{_conf_color(p['confidence']).hexval()}'>&#9679;</font> "
                             f"{p['confidence']}", CELL)])
    crt = Table(cr, colWidths=[220, 100, 110, 75], repeatRows=1)
    crt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4a3aa7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("BOX", (0, 0), (-1, -1), 0.5, HAIR), ("INNERGRID", (0, 0), (-1, -1), 0.3, HAIR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    S.append(crt)
    S.append(Spacer(1, 3))
    S.append(Paragraph("<b>Confidence</b> reflects how much of the raw effect survives causal "
                       "adjustment (the SHAP-Causal Correction Factor): "
                       "<font color='#0ca30c'>&#9679;</font> High and "
                       "<font color='#d0801b'>&#9679;</font> Medium levers are causally reliable; "
                       "<font color='#d03b3b'>&#9679;</font> Low levers are discounted; "
                       "<font color='#52514e'>&#9679;</font> Model-implied levers (e.g. clearing a "
                       "default) are behavioural and shown as-is.", SMALL))

    # ---- roadmap ----
    S.append(Paragraph("Your improvement roadmap", H2))
    order = {"Quick": 0, "Medium": 1, "Long": 2}
    seq = sorted(res["plan"], key=lambda p: order.get(p["effort"], 3))
    rm = [[Paragraph("When", WHITEB), Paragraph("Do this", WHITEB),
           Paragraph("Running readiness", WHITEB)]]
    run = r_now
    for p in seq:
        run = min(100, run + p["readiness_gain_pts"])
        when = {"Quick": "Now - 1 month", "Medium": "1 - 6 months",
                "Long": "6 - 12 months"}.get(p["effort"], "later")
        rm.append([Paragraph(when, CELL), Paragraph(p["title"], CELL),
                   Paragraph(f"<b>{run}/100</b>", CELL)])
    rmt = Table(rm, colWidths=[110, 270, 100], repeatRows=1)
    rmt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GOOD),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("BOX", (0, 0), (-1, -1), 0.5, HAIR), ("INNERGRID", (0, 0), (-1, -1), 0.3, HAIR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    S.append(rmt)

    # ---- context / immutable ----
    S.append(Spacer(1, 6))
    S.append(Paragraph("Factors we did <b>not</b> ask you to change", H2))
    S.append(Paragraph(res["immutable_note"], BODY))

    # ---- method & disclaimers ----
    S.append(Paragraph("How we worked this out", H2))
    S.append(Paragraph(
        "Your readiness is scored by a machine-learning model trained on historical lending "
        "outcomes, chosen for your product type. Each action's impact is measured by re-scoring "
        "your profile with that one change, then <b>causally corrected</b> using double/debiased "
        "machine learning so the numbers reflect real effect, not mere correlation.", BODY))
    S.append(Paragraph(
        "<b>Important:</b> this is an indicative loan-readiness estimate to help you improve your "
        "profile - it is not a lending decision or a guarantee of approval. Estimates are more "
        "precise when your answers are complete. Your data is used only to generate this report. "
        "Causal factors for the secured track are indicative where a causal estimate was not "
        "available.", SMALL))

    doc.build(S)
    buf.seek(0)
    return buf.getvalue()
