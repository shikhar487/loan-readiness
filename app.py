"""
app_router.py — Loan Readiness portal (modern dashboard edition).

Faculty points implemented:
  6/9 : product type routes to exactly ONE model — no blending
  3   : data questions are TRI-STATE (Have it / Don't have / Not sure), compulsory
  7   : raw answerable questions in, model features derived
  1   : score availability picks the tier; exact score OR a band if unsure
  8   : Section D causal probing questions

Tri-state semantics (a modelling distinction, not just UX):
  "I have this"      -> the value entered
  "I don't have any" -> a GENUINE ZERO (no cards => balance 0, limit 0)
  "Not sure"         -> MISSING (NaN) — the model is told nothing

Run:  python run_app.py
"""
import streamlit as st
from credit_engine import (Answers, CreditRouter, load_registry,
                           UNSECURED_PRODUCTS, SECURED_PRODUCTS, BUREAU_RANGES,
                           UI_EMPLOYMENT_LABELS, UI_PURPOSE_LABELS)
from validation import validate

st.set_page_config(page_title="Loan Readiness Check", page_icon="🏦",
                   layout="wide", initial_sidebar_state="collapsed")

# ======================================================================
# Design tokens — validated status palette; colour is always paired with icon+label
# ======================================================================
st.markdown("""
<style>
/* Inter when reachable. The system stack behind it carries the design unaided if the
   app is demoed offline, so nothing here depends on the network. */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

/* ============================================================
   TOKENS
   Light only, deliberately. The theme is pinned in .streamlit/config.toml, so the
   portal no longer follows the machine's dark-mode setting — one look everywhere.
   Warm paper neutrals against a cool indigo accent; that warm/cool tension is what
   keeps it from reading as a stock Streamlit app.
   ============================================================ */
:root{
  --page:#f5f4f0; --surface:#ffffff; --plane:#faf9f6; --sink:#efede7;
  --ink:#14140f; --ink-2:#55534c; --ink-muted:#8b8880;
  --hair:#e4e2da; --hair-2:#d3d0c5; --grid:#e9e7df;

  --brand:#3352d1; --brand-2:#22369c; --brand-3:#16255f;
  --brand-soft:#e8edfc; --brand-ink:#22369c;

  --good:#0f8a4d; --warning:#b8790a; --serious:#d0691f; --critical:#c4372f;
  --good-soft:#e6f5ec; --warn-soft:#fbf2dc; --crit-soft:#fbe9e7;

  /* Elevation: soft, wide, low-opacity. One tight contact shadow plus one broad
     ambient layer reads as depth; a single hard shadow reads as a sticker. */
  --e1:0 1px 2px rgba(20,20,15,.05);
  --e2:0 1px 2px rgba(20,20,15,.05), 0 4px 12px rgba(20,20,15,.06);
  --e3:0 2px 4px rgba(20,20,15,.05), 0 12px 30px rgba(20,20,15,.09);
  --e4:0 4px 8px rgba(20,20,15,.06), 0 24px 56px rgba(20,20,15,.13);
  --ring:0 0 0 3px rgba(51,82,209,.16);

  --r-xs:8px; --r-sm:11px; --r-md:14px; --r-lg:20px; --r-xl:26px;
}

html, body, [class*="css"]{
  font-family:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
/* Two barely-there colour washes stop the page reading as flat grey. */
.stApp{
  background:
    radial-gradient(70% 45% at 12% -8%, rgba(51,82,209,.055) 0%, transparent 62%),
    radial-gradient(60% 40% at 96% 2%, rgba(208,105,31,.045) 0%, transparent 58%),
    var(--page);
  background-attachment:fixed;
}
.block-container{padding-top:1.6rem; padding-bottom:5rem; max-width:1120px;}
/* Digits align column-for-column everywhere a figure appears. */
.figure,.tile .v,.scale,.pg-count,
[data-testid="stMetricValue"],[data-testid="stTable"] td{font-variant-numeric:tabular-nums;}

/* ============================================================
   HERO
   ============================================================ */
.hero{position:relative; overflow:hidden; color:#fff; margin:0 0 10px;
  border-radius:var(--r-xl); padding:44px 46px 46px;
  background:
    radial-gradient(90% 120% at 82% -20%, rgba(120,160,255,.55) 0%, transparent 58%),
    radial-gradient(70% 100% at 8% 108%, rgba(16,32,90,.55) 0%, transparent 60%),
    linear-gradient(142deg, var(--brand) 0%, var(--brand-2) 52%, var(--brand-3) 100%);
  box-shadow:var(--e4);}
/* Faint ruled grid, masked to a corner — texture at a whisper, never a pattern. */
.hero::after{content:""; position:absolute; inset:0; pointer-events:none; opacity:.14;
  background-image:linear-gradient(rgba(255,255,255,.6) 1px, transparent 1px),
                   linear-gradient(90deg, rgba(255,255,255,.6) 1px, transparent 1px);
  background-size:52px 52px;
  -webkit-mask-image:radial-gradient(88% 78% at 80% 2%, #000 0%, transparent 70%);
          mask-image:radial-gradient(88% 78% at 80% 2%, #000 0%, transparent 70%);}
.hero>*{position:relative; z-index:1;}
.hero .eyebrow{display:inline-flex; align-items:center; gap:8px; margin-bottom:18px;
  padding:7px 15px; border-radius:999px; font-size:.7rem; font-weight:680;
  letter-spacing:.13em; text-transform:uppercase; color:#fff;
  background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.26);
  backdrop-filter:blur(8px);}
.hero h1{margin:0 0 12px 0; font-size:2.9rem; font-weight:820; letter-spacing:-.038em;
  line-height:1.03;}
.hero p{margin:0; color:rgba(255,255,255,.90); font-size:1.04rem; line-height:1.62;
  max-width:64ch;}
.hero b{color:#fff; font-weight:680;}

/* ============================================================
   PROGRESS — sticks under the viewport top so the customer always knows
   how much of a long form is left.
   ============================================================ */
.pg{position:sticky; top:0; z-index:99; margin:14px 0 4px;
  background:rgba(250,249,246,.86); backdrop-filter:blur(12px) saturate(1.5);
  border:1px solid var(--hair); border-radius:var(--r-md);
  padding:12px 18px; box-shadow:var(--e2);}
.pg-row{display:flex; align-items:center; justify-content:space-between; gap:14px;}
.pg-label{font-size:.76rem; font-weight:680; letter-spacing:.07em; text-transform:uppercase;
  color:var(--ink-muted);}
.pg-count{font-size:.86rem; font-weight:680; color:var(--ink);}
.pg-track{height:7px; border-radius:999px; background:var(--sink); margin-top:9px;
  overflow:hidden;}
.pg-fill{height:100%; border-radius:999px;
  background:linear-gradient(90deg, var(--brand) 0%, #5b7ef0 100%);
  transition:width .45s cubic-bezier(.4,0,.2,1);}
.pg-done .pg-fill{background:linear-gradient(90deg, var(--good) 0%, #35b877 100%);}

/* ============================================================
   SECTION HEADERS
   ============================================================ */
.sec{display:flex; align-items:center; gap:14px; margin:44px 0 7px;}
.sec .num{width:34px; height:34px; border-radius:11px; flex:none;
  background:linear-gradient(145deg, var(--brand) 0%, var(--brand-2) 100%);
  color:#fff; display:flex; align-items:center; justify-content:center;
  font-weight:760; font-size:.9rem;
  box-shadow:0 2px 6px rgba(51,82,209,.32), inset 0 1px 0 rgba(255,255,255,.25);}
.sec .t{font-size:1.34rem; font-weight:740; color:var(--ink); letter-spacing:-.026em;}
.sec-sub{color:var(--ink-2); font-size:.93rem; margin:0 0 18px 48px; line-height:1.65;
  max-width:76ch;}

/* ============================================================
   DERIVED-VALUE CHIP
   ============================================================ */
.chip{display:inline-flex; align-items:center; gap:8px; background:var(--brand-soft);
  color:var(--brand-ink); border:1px solid rgba(51,82,209,.16); border-radius:999px;
  padding:7px 16px; font-size:.845rem; font-weight:590; margin-top:9px;
  box-shadow:var(--e1);}

/* ============================================================
   HEADLINE RESULT — the payoff screen, so it gets the most weight.
   ============================================================ */
.result{position:relative; overflow:hidden; background:var(--surface);
  border:1px solid var(--hair); border-radius:var(--r-lg); padding:34px 36px;
  margin-top:6px; box-shadow:var(--e3);}
.result::before{content:""; position:absolute; inset:0 0 auto 0; height:4px;
  background:linear-gradient(90deg, var(--brand) 0%, #5b7ef0 55%, var(--serious) 100%);}
.result .figure{font-size:4.9rem; font-weight:840; line-height:.92; letter-spacing:-.05em;
  color:var(--ink); margin:.08em 0 0;}
.result .band{display:inline-flex; align-items:center; gap:10px; margin-top:17px;
  padding:9px 18px; border-radius:999px; font-weight:660; font-size:.93rem;
  border:1.5px solid currentColor;}
.result .cap{color:var(--ink-2); font-size:.89rem; margin-top:18px; line-height:1.62;}
.track{height:13px; border-radius:999px; background:var(--sink); margin-top:24px;
  overflow:hidden; box-shadow:inset 0 1px 3px rgba(20,20,15,.09);}
.fill{height:100%; border-radius:999px; transition:width .6s cubic-bezier(.4,0,.2,1);
  box-shadow:0 0 12px currentColor;}
.scale{display:flex; justify-content:space-between; color:var(--ink-muted);
  font-size:.735rem; margin-top:8px; font-weight:560;}

/* ============================================================
   METRIC TILES
   ============================================================ */
.tile{position:relative; background:var(--surface); border:1px solid var(--hair);
  border-radius:var(--r-md); padding:18px 20px; height:100%; box-shadow:var(--e1);
  transition:box-shadow .2s ease, transform .2s ease, border-color .2s ease;}
.tile:hover{box-shadow:var(--e3); transform:translateY(-2px); border-color:var(--hair-2);}
.tile .k{color:var(--ink-muted); font-size:.705rem; text-transform:uppercase;
  letter-spacing:.085em; font-weight:700;}
.tile .v{color:var(--ink); font-size:1.72rem; font-weight:760; margin-top:7px;
  letter-spacing:-.028em; line-height:1.12;}
.tile .s{color:var(--ink-2); font-size:.815rem; margin-top:5px; line-height:1.45;}

/* ============================================================
   VALIDATION FINDINGS
   Colour never carries the meaning on its own: every card pairs its hue with a
   bold label AND a solid left rule, so it still parses without colour vision.
   ============================================================ */
.v-issue{border:1px solid var(--hair); border-left:4px solid var(--hair);
  border-radius:var(--r-sm); padding:15px 18px; margin:10px 0; background:var(--surface);
  box-shadow:var(--e1); transition:box-shadow .18s ease, transform .18s ease;}
.v-issue:hover{box-shadow:var(--e2); transform:translateX(2px);}
.v-issue .v-label{color:var(--ink); font-weight:690; font-size:.955rem; letter-spacing:-.01em;}
.v-issue .v-detail{color:var(--ink-2); font-size:.895rem; line-height:1.62; margin-top:6px;}
.v-error{border-left-color:var(--critical);
  background:linear-gradient(96deg, var(--crit-soft) 0%, var(--surface) 38%);}
.v-warn{border-left-color:var(--warning);
  background:linear-gradient(96deg, var(--warn-soft) 0%, var(--surface) 38%);}

/* ============================================================
   STREAMLIT WIDGET CHROME
   Base colours, radii and the alert palette come from .streamlit/config.toml —
   these rules only add what the native theme cannot express. Each selector uses a
   documented data-testid; if a future Streamlit renames one, the rule simply stops
   matching and that widget falls back to themed-but-unembellished. Nothing breaks.
   ============================================================ */

/* Bordered containers. border-color on a borderless block is a no-op, so this
   reaches st.container(border=True) without touching plain layout blocks. */
[data-testid="stContainer"]{border-color:var(--hair) !important;
  background:var(--surface); transition:border-color .18s ease, box-shadow .18s ease;}
[data-testid="stContainer"]:hover{border-color:var(--hair-2) !important; box-shadow:var(--e1);}

/* Inputs and selects */
[data-testid="stNumberInput"] input,
[data-testid="stTextInput"] input,
[data-baseweb="select"]>div{
  background:var(--surface) !important; border-color:var(--hair-2) !important;
  transition:border-color .16s ease, box-shadow .16s ease;}
[data-testid="stNumberInput"] input:hover,
[data-baseweb="select"]>div:hover{border-color:var(--brand) !important;}
[data-testid="stNumberInput"] input:focus,
[data-testid="stTextInput"] input:focus,
[data-baseweb="select"]>div:focus-within{
  border-color:var(--brand) !important; box-shadow:var(--ring) !important;}
[data-testid="stWidgetLabel"] p{font-weight:590 !important; color:var(--ink) !important;
  font-size:.9rem !important; letter-spacing:-.005em;}

/* Radio groups — the most repeated control in this form, so they set the tone.
   Rendered as pills that fill when chosen, while keeping the native dot so they
   still read and behave as radios for keyboard and screen-reader users. */
[data-testid="stRadio"] div[role="radiogroup"]{gap:.5rem; flex-wrap:wrap;}
[data-testid="stRadio"] label{
  border:1px solid var(--hair); border-radius:999px; padding:7px 16px 7px 12px;
  background:var(--plane); margin:0;
  transition:border-color .16s ease, background .16s ease, box-shadow .16s ease,
             transform .16s ease;}
[data-testid="stRadio"] label:hover{border-color:var(--brand); background:var(--surface);
  transform:translateY(-1px);}
[data-testid="stRadio"] label:has(input:checked){
  border-color:var(--brand); background:var(--brand-soft);
  box-shadow:0 1px 3px rgba(51,82,209,.20);}
[data-testid="stRadio"] label:has(input:checked) p{color:var(--brand-ink); font-weight:650;}

/* Buttons */
.stButton>button{font-weight:660; padding:.78rem 1.15rem; letter-spacing:-.012em;
  transition:transform .16s ease, box-shadow .16s ease, filter .16s ease;}
.stButton>button[kind="primary"]{
  background:linear-gradient(145deg, var(--brand) 0%, var(--brand-2) 100%);
  border:none; color:#fff; box-shadow:0 2px 6px rgba(51,82,209,.30), var(--e2);}
.stButton>button[kind="primary"]:hover:not(:disabled){
  transform:translateY(-2px); filter:saturate(1.14);
  box-shadow:0 6px 18px rgba(51,82,209,.34), var(--e3);}
.stButton>button[kind="primary"]:active:not(:disabled){transform:translateY(0);}
.stButton>button:disabled{opacity:.45; box-shadow:none; filter:grayscale(.35);}

/* Alerts, expanders, metrics, tables */
[data-testid="stAlert"]{box-shadow:var(--e1); border:1px solid rgba(20,20,15,.06);}
[data-testid="stExpander"]{border-color:var(--hair) !important; overflow:hidden;
  background:var(--surface); box-shadow:var(--e1);}
[data-testid="stExpander"] summary{font-weight:600;}
[data-testid="stExpander"] summary:hover{color:var(--brand) !important;}
[data-testid="stMetric"]{background:var(--surface); border:1px solid var(--hair);
  border-radius:var(--r-md); padding:16px 18px; box-shadow:var(--e1);
  transition:box-shadow .18s ease, transform .18s ease;}
[data-testid="stMetric"]:hover{box-shadow:var(--e2); transform:translateY(-1px);}
[data-testid="stMetricLabel"] p{color:var(--ink-muted) !important; font-size:.72rem !important;
  text-transform:uppercase; letter-spacing:.075em; font-weight:700 !important;}
[data-testid="stMetricValue"]{font-weight:770; letter-spacing:-.028em;}
[data-testid="stTable"]{border-radius:var(--r-md); overflow:hidden; box-shadow:var(--e1);
  border:1px solid var(--hair);}
[data-testid="stTable"] thead th{background:var(--plane) !important;
  color:var(--ink-muted) !important; text-transform:uppercase; font-size:.71rem !important;
  letter-spacing:.07em; font-weight:700 !important; border-bottom:1px solid var(--hair) !important;}
[data-testid="stTable"] tbody tr{transition:background .14s ease;}
[data-testid="stTable"] tbody tr:hover td{background:var(--plane) !important;}

/* ============================================================
   STEPPED SECTIONS
   ============================================================ */
/* Each step is an expander; the header is the step's own title bar. */
[data-testid="stExpander"] summary{padding:14px 18px; font-size:1.02rem;}
[data-testid="stExpander"] summary p{font-weight:660 !important; letter-spacing:-.015em;}
[data-testid="stExpander"]{margin-bottom:12px;}
/* An open step is the one thing to look at, so it gets the lift. */
[data-testid="stExpander"]:has(summary[aria-expanded="true"]){
  border-color:var(--brand) !important; box-shadow:var(--e3);}
[data-testid="stExpander"]:has(summary[aria-expanded="true"]) summary p{color:var(--brand-ink) !important;}

/* Steps not yet reachable — visible so the customer can see what is coming,
   but plainly inert. */
.lockrow{display:flex; align-items:center; gap:13px; padding:14px 18px; margin-bottom:12px;
  border:1px dashed var(--hair-2); border-radius:var(--r-md); background:transparent;}
.lockn{width:26px; height:26px; border-radius:8px; flex:none; display:flex;
  align-items:center; justify-content:center; background:var(--sink);
  color:var(--ink-muted); font-weight:700; font-size:.8rem;}
.lockt{color:var(--ink-muted); font-weight:600; font-size:.97rem;}
.lockx{margin-left:auto; color:var(--ink-muted); font-size:.79rem; font-weight:520;}

/* Headings inside markdown blocks */
h5,h6{letter-spacing:-.018em; font-weight:690;}
hr{border-color:var(--hair); margin:2.4rem 0;}
::selection{background:var(--brand-soft); color:var(--brand-ink);}

/* Keep the styling, drop the movement, when the reader asks for less motion. */
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{transition-duration:.01ms !important; animation-duration:.01ms !important;}
  .tile:hover,.stButton>button[kind="primary"]:hover:not(:disabled),
  [data-testid="stRadio"] label:hover,.v-issue:hover{transform:none;}
}

/* Narrow screens: the hero's display type has to come down or it wraps badly. */
@media (max-width:640px){
  .hero{padding:30px 24px 32px; border-radius:var(--r-lg);}
  .hero h1{font-size:2.05rem;}
  .result{padding:24px 22px;}
  .result .figure{font-size:3.6rem;}
  .sec-sub{margin-left:0;}
}
</style>
""", unsafe_allow_html=True)


def section(n, title, sub=""):
    st.markdown(f'<div class="sec"><div class="num">{n}</div>'
                f'<div class="t">{title}</div></div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<p class="sec-sub">{sub}</p>', unsafe_allow_html=True)


def chip(text):
    st.markdown(f'<span class="chip">→ {text}</span>', unsafe_allow_html=True)


def takeaway(num, title, sub=""):
    """A big, bold, catchy section header for the three output sections (replaces 7/8/9)."""
    st.markdown(
        f'<div style="margin:28px 0 12px;">'
        f'<span style="display:inline-block;background:var(--brand);color:#fff;font-size:.72rem;'
        f'font-weight:700;letter-spacing:.09em;text-transform:uppercase;padding:5px 14px;'
        f'border-radius:999px;">Takeaway&nbsp;{num}</span>'
        f'<div style="font-size:1.55rem;font-weight:750;color:var(--ink);margin-top:10px;'
        f'letter-spacing:-.01em;line-height:1.15;">{title}</div>'
        + (f'<div style="color:var(--ink-2);font-size:.93rem;margin-top:3px;">{sub}</div>' if sub else '')
        + '</div>', unsafe_allow_html=True)


def chance_curve_chart(risk, track):
    """Altair chart of chance-of-loan vs default risk, marking the lender cut-off and the applicant."""
    import altair as alt, pandas as _pd, numpy as _np
    from improvement_engine import approval_chance, APPROVAL_PD50
    xs = _np.linspace(0, 0.6, 120)
    df = _pd.DataFrame({"risk": xs * 100, "chance": [approval_chance(x, track) * 100 for x in xs]})
    line = alt.Chart(df).mark_line(color="#2a78d6", strokeWidth=3).encode(
        x=alt.X("risk:Q", title="Your default risk (%)"),
        y=alt.Y("chance:Q", title="Chance of loan (%)", scale=alt.Scale(domain=[0, 100])))
    cut = APPROVAL_PD50.get(track, 0.30) * 100
    rdf = _pd.DataFrame({"x": [cut]})
    rule = alt.Chart(rdf).mark_rule(color="#e0a200", strokeDash=[5, 4], strokeWidth=2).encode(x="x:Q")
    rtext = alt.Chart(rdf).mark_text(color="#95620A", align="left", dx=6, fontSize=11, fontWeight="bold").encode(
        x="x:Q", y=alt.value(12), text=alt.value(f"lender cut-off ≈ {cut:.0f}% risk"))
    a = approval_chance(risk, track) * 100
    pdf = _pd.DataFrame({"risk": [risk * 100], "chance": [a]})
    pt = alt.Chart(pdf).mark_point(size=190, color="#d03b3b", filled=True).encode(x="risk:Q", y="chance:Q")
    ptext = alt.Chart(pdf).mark_text(color="#d03b3b", dx=9, dy=-9, fontSize=12, fontWeight="bold").encode(
        x="risk:Q", y="chance:Q", text=alt.value(f"you: {a:.0f}%"))
    return (line + rule + rtext + pt + ptext).properties(height=235)


def readiness_ceiling_chart(risk, track):
    """Readiness score vs default risk, drawn at several ceilings.

    The ceiling is the only free parameter in the readiness score, so showing the
    same applicant against a range of ceilings makes plain what it does: it sets
    how fast the 0-100 scale is used up, and nothing else. The line actually in
    use is drawn heavier; the applicant sits on it.
    """
    import altair as alt, pandas as _pd, numpy as _np
    from improvement_engine import READINESS_PD_MAX

    live = READINESS_PD_MAX.get(track, 0.60)
    ceilings = sorted({round(min(live * m, 1.0), 2) for m in (0.5, 0.75, 1.0, 1.5)})
    xmax = max(ceilings)
    xs = _np.linspace(0, xmax, 180)

    def _label(c):
        return f"{c * 100:.0f}%" + ("  (in use)" if abs(c - live) < 1e-9 else "")

    rows = [{"risk": x * 100, "score": 100 * (1 - min(x / c, 1.0)),
             "ceiling": _label(c), "c": c}
            for c in ceilings for x in xs]
    df = _pd.DataFrame(rows)

    order = [_label(c) for c in ceilings]
    # one-hue ordinal ramp (ceiling is an ordered quantity, not a category)
    ramp = ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"][:len(ceilings)]
    base = alt.Chart(df).encode(
        x=alt.X("risk:Q", title="Default risk (%)"),
        y=alt.Y("score:Q", title="Loan-readiness score", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("ceiling:N", title="Ceiling", sort=order,
                        scale=alt.Scale(domain=order, range=ramp),
                        legend=alt.Legend(orient="bottom", direction="horizontal",
                                          columns=4, labelFontSize=11, titleFontSize=11)))
    others = base.transform_filter(alt.datum.c != live).mark_line(strokeWidth=2, opacity=0.8)
    inuse = base.transform_filter(alt.datum.c == live).mark_line(strokeWidth=4)

    a = 100 * (1 - min(risk / live, 1.0))
    pdf = _pd.DataFrame({"risk": [risk * 100], "score": [a]})
    pt = alt.Chart(pdf).mark_point(size=170, color="#d03b3b", filled=True).encode(
        x="risk:Q", y="score:Q")
    ptext = alt.Chart(pdf).mark_text(color="#d03b3b", dx=9, dy=-10, fontSize=12,
                                     fontWeight="bold").encode(
        x="risk:Q", y="score:Q", text=alt.value(f"you: {a:.0f}/100"))
    return (others + inuse + pt + ptext).properties(height=235)


# ----------------------------------------------------------------------
# Indian number formatting (item 10) + product-specific tenor/ROI (item 1)
# ----------------------------------------------------------------------
def inr(n):
    """Format a number in the Indian grouping system, e.g. 12,34,56,789."""
    if n is None:
        return "—"
    n = int(round(float(n)))
    neg = n < 0
    s = str(abs(n))
    if len(s) > 3:
        last3, rest, groups = s[-3:], s[:-3], []
        while len(rest) > 2:
            groups.insert(0, rest[-2:]); rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups) + "," + last3
    return ("-" if neg else "") + "₹" + s

def inr_words(n):
    """Human words for a rupee amount: thousand / lakh / crore."""
    if not n:
        return ""
    n = float(n)
    if n >= 1e7:
        return f"{n/1e7:.2f}".rstrip("0").rstrip(".") + " crore"
    if n >= 1e5:
        return f"{n/1e5:.2f}".rstrip("0").rstrip(".") + " lakh"
    if n >= 1e3:
        return f"{n/1e3:.0f} thousand"
    return str(int(n))

# --- Auto Indian-comma-formatted rupee INPUT box (item 10) -------------------------------------
def _ind_group(digits):
    """Group a digit string in the Indian system: '10000000' -> '1,00,00,000'."""
    digits = digits.lstrip("0")
    if digits == "":
        return ""
    if len(digits) <= 3:
        return digits
    last3, rest, groups = digits[-3:], digits[:-3], []
    while len(rest) > 2:
        groups.insert(0, rest[-2:]); rest = rest[:-2]
    if rest:
        groups.insert(0, rest)
    return ",".join(groups) + "," + last3

def _clamp_rupee(key, max_value, set_note=True):
    """Pull the digits out of the box, clamp to max_value, and rewrite the DISPLAYED
    text. Returns the clamped int.

    Clamping the text matters as much as clamping the value: leaving the box reading
    99,99,99,99,999 while the app scores 1,00,00,000 invites the customer to believe
    they asked for something they did not.

    Every caller must be somewhere Streamlit still allows a write to
    st.session_state[key] — i.e. BEFORE the text_input keyed `key` is created on this
    run, or inside its on_change callback. Writing after the widget exists raises
    StreamlitAPIException, which is what used to happen here the moment anyone typed a
    figure above the cap.
    """
    digits = "".join(ch for ch in str(st.session_state.get(key, "")) if ch.isdigit())
    note = ""
    if max_value is not None and digits and int(digits) > max_value:
        digits = str(int(max_value))
        note = f"Above the maximum — set to {inr(max_value)}."
    st.session_state[key] = _ind_group(digits)
    if set_note:
        # Only the on_change callback owns the message. The pre-widget pass below runs
        # AFTER the callback has already corrected the text, so it would find the value
        # in range and blank the note before it was ever displayed — clamping the
        # customer silently, which is worse than not clamping at all.
        st.session_state[f"{key}_note"] = note
    return int(digits) if digits else 0


def _fmt_rupee_cb(key, max_value=None):
    """on_change: reformat what was typed into Indian-grouped commas, within bounds."""
    _clamp_rupee(key, max_value)


def rupee_input(label, key, default=0, help=None, max_value=None, placeholder="e.g. 5,00,000"):
    """A rupee amount box that auto-formats to the Indian comma system as you enter it."""
    if key not in st.session_state:
        st.session_state[key] = _ind_group(str(int(default))) if default else ""
    elif max_value is not None:
        # Catch any value already over the cap — pasted text, or a bound lowered since
        # it was entered. Safe here: the widget for `key` has not been created yet.
        _clamp_rupee(key, max_value, set_note=False)
    st.text_input(label, key=key, on_change=_fmt_rupee_cb, args=(key, max_value), help=help,
                  placeholder=placeholder)
    if st.session_state.get(f"{key}_note"):
        st.caption(f"⚠️ {st.session_state[f'{key}_note']}")
    digits = "".join(ch for ch in str(st.session_state.get(key, "")) if ch.isdigit())
    val = int(digits) if digits else 0
    if max_value is not None and val > max_value:
        val = int(max_value)   # value only — session_state must NOT be written past here
    return val

# Indicative Indian-market tenors & interest bands per product, from public bank data
# (SBI / HDFC / ICICI / Axis schedules and RBI aggregates, mid-2020s). The customer PICKS
# from these; the exact rate/tenor is confirmed with the lender. We assume no single value.
PRODUCT_TERMS = {
    "home_loan":             {"tenors": [60,120,180,240,300,360], "roi": (8.0, 11.5),  "t_default": 240},
    "loan_against_property": {"tenors": [60,120,180,240],         "roi": (9.0, 14.0),  "t_default": 120},
    "auto_loan":             {"tenors": [12,24,36,48,60,72,84],   "roi": (8.5, 15.0),  "t_default": 60},
    "personal_loan":         {"tenors": [12,24,36,48,60,72],      "roi": (10.5, 24.0), "t_default": 36},
    "balance_transfer":      {"tenors": [3,6,9,12,18,24],         "roi": (10.0, 20.0), "t_default": 12},
    "consumer_durable":      {"tenors": [3,6,9,12,18,24],         "roi": (0.0, 20.0),  "t_default": 12},
    "education_loan":        {"tenors": [60,84,120,180],          "roi": (8.5, 15.0),  "t_default": 84},
}
def _pt(product):
    return PRODUCT_TERMS.get(product, {"tenors": [12,24,36,48,60,84,120,180,240,300],
                                       "roi": (8.0, 24.0), "t_default": 36})
def roi_options(product):
    lo, hi = _pt(product)["roi"]
    lo = max(0.0, lo - 1.0); hi = hi + 1.0   # ±1% buffer to accommodate frequent market-rate moves
    out, x = [], lo
    while x <= hi + 1e-9:
        out.append(round(x, 2)); x += 0.5
    return out
def tenor_options(product):
    return _pt(product)["tenors"]
def tenor_label(m):
    if m % 12 == 0:
        y = m // 12
        return f"{m} months ({y} yr{'s' if y > 1 else ''})"
    return f"{m} months"


@st.cache_resource
def get_router():
    reg = load_registry()
    return CreditRouter(reg), reg


router, registry = get_router()

st.markdown(
    '<div class="hero">'
    '<div class="eyebrow">🏦 Adaptive Credit Intelligence</div>'
    '<h1>Loan Readiness Check</h1>'
    '<p>Answer what you can. Where information may not be to hand, tell us whether you '
    '<b>have</b> it, <b>don\'t have</b> it, or are <b>not sure</b> — we never guess '
    'on your behalf.</p></div>', unsafe_allow_html=True)

# The progress meter depends on the full question list, which is only complete once the
# whole form has rendered. Claim the slot here, fill it at the very end (UI_DESIGN §4.4).
_progress_slot = st.empty()

if not registry:
    st.error("No trained models found in `model_data/`. Run `python train_routed_models.py` first.")
    st.stop()

# ======================================================================
# Tri-state widgets (compulsory)
# ======================================================================
HAVE, NONE, UNSURE = "I have this", "I don't have any", "Not sure"
YES, NO, NOTSURE = "Yes", "No", "Not sure"
_required = []


def tri_number(label, key, zero=0.0, help=None, **kw):
    _required.append((key, label))
    with st.container(border=True):
        st.markdown(f"**{label}**")
        state = st.radio("Availability", [HAVE, NONE, UNSURE], index=None, horizontal=True,
                         key=f"tri_{key}", label_visibility="collapsed", help=help)
        if state == HAVE:
            return st.number_input("Enter value", key=f"val_{key}",
                                   label_visibility="collapsed", **kw), state
        if state == NONE:
            st.caption("Recorded as zero.")
            return zero, state
        if state == UNSURE:
            st.caption("Left blank — the model is told nothing rather than a guess.")
            return None, state
    return None, None


def tri_select(label, key, options, fmt=None, help=None):
    _required.append((key, label))
    with st.container(border=True):
        st.markdown(f"**{label}**")
        state = st.radio("Availability", [HAVE, NONE, UNSURE], index=None, horizontal=True,
                         key=f"tri_{key}", label_visibility="collapsed", help=help)
        if state == HAVE:
            return st.selectbox("Choose", options, key=f"val_{key}",
                                format_func=fmt or str, label_visibility="collapsed"), state
        if state in (NONE, UNSURE):
            st.caption("Left blank." if state == UNSURE else "Recorded as not applicable.")
            return None, state
    return None, None


def tri_yesno(label, key, help=None):
    _required.append((key, label))
    with st.container(border=True):
        st.markdown(f"**{label}**")
        v = st.radio("Answer", [YES, NO, NOTSURE], index=None, horizontal=True,
                     key=f"tri_{key}", label_visibility="collapsed", help=help)
        if v == YES:
            return True, v
        if v == NO:
            return False, v
        if v == NOTSURE:
            st.caption("Left blank.")
            return None, v
    return None, None


# ======================================================================
# Stepped sections
# ======================================================================
# Streamlit garbage-collects a widget's session_state once the widget has been
# absent for a full rerun — so a naive "render only the current step" wizard
# silently loses every answer the moment the customer moves two steps on
# (present after one rerun, gone after two). A collapsed expander still renders
# its children, so answers stay alive, and once unlocked a section is never
# hidden again.
_STEPS = {
    1: "What you are applying for",
    2: "Income, age and the loan you want",
    3: "Credit score",
    4: "Your credit history and details",
    5: "Home and vehicle loans",
    6: "Other loan obligations",
    7: "Recent changes in your situation",
}
_LAST_STEP = max(_STEPS)
st.session_state.setdefault("_done", {})
_bounds = {}


def _current_step():
    """The lowest step still incomplete — the one that should be open."""
    for n in sorted(_STEPS):
        if not st.session_state["_done"].get(n):
            return n
    return _LAST_STEP


def _unlocked(n):
    """A step renders once every step before it is complete."""
    return all(st.session_state["_done"].get(k) for k in range(1, n))


def _panel(n):
    """The expander for step n, open only while it is the current step."""
    done = st.session_state["_done"].get(n)
    _bounds[n] = len(_required)
    return st.expander(f"{'✓' if done else '▸'}  Step {n} of {_LAST_STEP}  ·  {_STEPS[n]}",
                       expanded=(n == _current_step()))


def _close(n, extra=True):
    """Mark the step complete when every question it registered has an answer.
    `extra` carries any requirement that is not a tri-state widget."""
    mine = _required[_bounds.get(n, 0):]
    answered = all(st.session_state.get(f"tri_{k}") is not None for k, _ in mine)
    was = bool(st.session_state["_done"].get(n))
    now = bool(answered and extra)
    st.session_state["_done"][n] = now
    # A panel's expanded/collapsed state is decided when it is created, which is
    # before this runs — so on the render where a step completes, it would stay
    # open next to the one it just unlocked. Rerunning once settles both.
    # `was != now` keeps it to a single pass: after the rerun the flag matches
    # and it stops, so this cannot loop.
    if was != now:
        st.rerun()


def _locked_strip(n):
    st.markdown(f'<div class="lockrow"><span class="lockn">{n}</span>'
                f'<span class="lockt">{_STEPS[n]}</span>'
                f'<span class="lockx">🔒 finish the step above</span></div>',
                unsafe_allow_html=True)


# Every answer a later step or the submit block might read, pre-set here. A locked
# step never runs, so without these the submit block would raise NameError on the
# first render. Each section still assigns its own values exactly as before when it
# does run — these are only the not-yet-reached defaults.
monthly_income = loan_amount = monthly_emi = coapplicant_income = 0
term_months = expected_roi = None
age = 35
credit_score = credit_bureau = additional_score = additional_score_type = None
first_credit_year = education = employment_type = None
card_balance = card_limit = num_credit_lines = None
enquiries_6m = new_accounts_24m = None
missed_payment_2y = has_default_record = None
housing = loan_purpose = None
asset_value = down_payment = owns_property = owns_car = enquiries_12m = None
asset_scenario = None
family_total = dependents = None
has_home_loan = has_vehicle_loan = None
secured_outstanding = secured_ever_missed = first_secured_year = None
instalment_outstanding_pct = None
employed_12m = missed_emi_6m = job_change_6m = large_expense = income_change = None


if _unlocked(1):
    with _panel(1):
        # ======================================================================
        # 1 · Product
        # ======================================================================
        section("1", "What are you applying for?",
                "Your loan type tailors the questions we ask and how your readiness is assessed.")
        allp = {**UNSECURED_PRODUCTS, **SECURED_PRODUCTS}
        product = st.selectbox("Loan type", list(allp), format_func=lambda k: allp[k],
                               label_visibility="collapsed")
        track = "unsecured" if product in UNSECURED_PRODUCTS else "secured"

        # Small track indicator only — the dataset and model accuracy are intentionally
        # NOT shown to the end user (we don't reveal which model runs behind the scenes).
        st.markdown(
            f'<div style="display:inline-flex;align-items:center;gap:9px;background:var(--surface);'
            f'border:1px solid var(--hair);border-radius:10px;padding:7px 14px;margin-top:2px;">'
            f'<span style="color:var(--ink-muted);font-size:.68rem;text-transform:uppercase;'
            f'letter-spacing:.06em;font-weight:600;">Track</span>'
            f'<span style="color:var(--ink);font-weight:650;font-size:.9rem;">{track.title()}</span>'
            f'<span style="color:var(--ink-2);font-size:.8rem;">· {allp[product]}</span>'
            f'</div>', unsafe_allow_html=True)

        # ======================================================================
        # 1b · New to credit? — asked for EVERY product, right after the loan type.
        # If "Yes", every question about existing loans / credit cards / credit score / past
        # payments is locked as "none" further down; only income, the requested loan and (for
        # secured) the asset details are then taken.
        # ======================================================================
        NEW_YES = "Yes — I am completely new to credit"
        NEW_NO  = "No — I have some credit history"
        _required.append(("newcredit", "Whether you are new to credit"))
        with st.container(border=True):
            st.markdown("### 🆕 Are you new to credit?")
            st.markdown(
                '<div style="font-size:1.05rem;line-height:1.5;color:var(--ink);">'
                'Choose <b>Yes</b> only if you have <b style="color:#a53a34;">no loan history whatsoever — '
                'no loans of any kind, and not even a credit card</b> (whether currently held or closed in '
                'the past). If you have ever held <i>any</i> loan or credit card, choose <b>No</b>.</div>',
                unsafe_allow_html=True)
            _ntc = st.radio("newcredit", [NEW_NO, NEW_YES], index=None, key="tri_newcredit",
                            label_visibility="collapsed")
        new_to_credit = (_ntc == NEW_YES)
        if new_to_credit:
            st.success("**New to credit** — every question below about existing loans, credit cards, credit "
                       "score and past payments is now locked as 'none'. Just tell us your income, the loan "
                       "you want, and (for a secured loan) the asset details. We'll assess you with the "
                       "model built for applicants with no credit file.", icon="🆕")
    _close(1)
else:
    _locked_strip(1)

if _unlocked(2):
    with _panel(2):
        # ======================================================================
        # 2 · Core financials — always known, so asked directly
        # ======================================================================
        section("2", "Your income, age and the loan you want",
                "Everyone knows these, so we ask them outright.")
        a1, a2 = st.columns(2)
        with a1:
            monthly_income = rupee_input("Monthly take-home income (₹)", "in_income", 60_000,
                                         max_value=10_000_000, placeholder="e.g. 60,000")
            loan_amount = rupee_input("Loan amount you want (₹)", "in_loan", 500_000,
                                      max_value=100_000_000, placeholder="e.g. 5,00,000")
            age = st.number_input("Your age", 18, 100, 35,
                                  help="Everyone knows their age, so there is no 'not sure' option here.")
        with a2:
            if new_to_credit:
                monthly_emi = 0
                st.text_input("Total monthly EMIs you already pay (all loans) (₹)", value="0", disabled=True,
                              help="Locked to 0 — you selected new to credit (no existing loans).")
            else:
                monthly_emi = rupee_input("Total monthly EMIs you already pay (all loans) (₹)", "in_emi",
                                          15_000, max_value=10_000_000, placeholder="e.g. 15,000",
                                          help="Add up the EMIs on ALL your current loans — home, vehicle, "
                                               "personal, consumer-durable, education, and card conversions.")
            _tenors = tenor_options(product)
            term_months = st.selectbox(
                "Repayment period", _tenors,
                index=_tenors.index(_pt(product)["t_default"]) if _pt(product)["t_default"] in _tenors else 0,
                format_func=tenor_label,
                help="Tenors Indian lenders typically offer for this product. Confirm the exact tenor with "
                     "your lender.")
            coapplicant_income = rupee_input(
                "Co-applicant's monthly income (₹, optional)", "in_coapp", 0,
                max_value=10_000_000, placeholder="optional",
                help="Applying with a co-applicant (spouse / parent / partner)? Add their monthly income. "
                     "It is used only for the affordability (debt-servicing) check — it does NOT change the "
                     "credit-risk model, which assesses you on your own profile.")

        # Change 2 — if a co-applicant income is added, remind the customer to include the co-applicant's
        # EMIs in the total-EMI figure too, so the household income is compared against household obligations.
        if coapplicant_income and coapplicant_income > 0 and not new_to_credit:
            st.warning(
                "You've added a co-applicant's income. **Please go back to 'Total monthly EMIs you already "
                "pay' above and make sure it also includes your co-applicant's existing EMIs.** The "
                "affordability check compares the household's combined income against the household's "
                "combined EMIs — including only one side would understate the true debt-to-income.", icon="⚠️")

        # Indian-format echo of the large amounts (item 10) — easier to read than a raw number box.
        _echo = []
        if loan_amount:     _echo.append(f"Loan **{inr(loan_amount)}** ({inr_words(loan_amount)})")
        if monthly_income:  _echo.append(f"income **{inr(monthly_income)}**/mo")
        if monthly_emi:     _echo.append(f"existing EMIs **{inr(monthly_emi)}**/mo")
        if _echo:
            st.caption("You entered: " + " · ".join(_echo))

        # Expected interest rate — a product-specific DROPDOWN of indicative Indian-bank rates (item 1).
        # It is the ONLY rate the affordability utility uses; the tool assumes no rate of its own.
        _roi_opts = roi_options(product)
        expected_roi = st.selectbox(
            "Expected interest rate (% per year)", _roi_opts, index=None,
            placeholder="Select the rate you expect / were quoted",
            format_func=lambda r: f"{r:.1f}%",
            help="Choose from the range Indian lenders typically offer for this product. This is the ONLY "
                 "rate the affordability estimates use — the tool assumes no rate of its own.")
        st.caption("These are indicative ranges from public Indian-bank data — pick the rate you expect or "
                   "were quoted. The affordability figures use only the value you select; confirm your exact "
                   "ROI with the lender for the most accurate results.")

        # --- Live EMI on the proposed loan (item 2) — shown up front as soon as amount + rate + tenor known.
        if loan_amount and term_months and expected_roi is not None:
            from credit_engine import _amortised_installment as _emi_fn
            _emi_top = _emi_fn(loan_amount, expected_roi, term_months)
            st.success(f"💸 **Your estimated EMI on this loan: {inr(_emi_top)}/month** — for "
                       f"{inr(loan_amount)} over {tenor_label(term_months)} at {expected_roi:.1f}% p.a. "
                       "See how it affects your debt-to-income just below.")

        # Combined household income powers the affordability / DTI serviceability view (never the risk model).
        combined_income = monthly_income + coapplicant_income
        _inc_basis = combined_income if coapplicant_income > 0 else monthly_income

        # --- Upfront guardrail: existing debt already meets/exceeds income (existing DTI >= 100%) ----------
        if monthly_emi > 0 and _inc_basis > 0 and monthly_emi >= _inc_basis:
            if coapplicant_income > 0:
                st.error(
                    f"**Your existing EMIs (₹{monthly_emi:,.0f}/mo) still meet or exceed your combined "
                    f"household income (₹{_inc_basis:,.0f}/mo).** Your current debt is already not serviceable, "
                    "so a fresh loan cannot be added on top. You would need to reduce existing EMIs, or add "
                    "more income, before applying for a new loan.", icon="🚫")
            else:
                st.error(
                    f"**Your existing EMIs (₹{monthly_emi:,.0f}/mo) already meet or exceed your monthly income "
                    f"(₹{monthly_income:,.0f}/mo)** — your current debt is more than your income can service, so "
                    "a fresh loan cannot be added on top. **Add a co-applicant and enter their monthly income "
                    "above** (or reduce your existing EMIs) before applying for a new loan.", icon="🚫")

        if _inc_basis > 0:
            _basis_lbl = "combined household income" if coapplicant_income > 0 else "income"
            _extra = f"; combined income ₹{_inc_basis:,.0f}/mo" if coapplicant_income > 0 else ""
            chip(f"Existing debt-to-income derived: **{monthly_emi/_inc_basis*100:.1f}%** — "
                 f"your current EMIs over {_basis_lbl} (you never enter this yourself){_extra}")
            if loan_amount and term_months and expected_roi is not None:
                from credit_engine import _amortised_installment
                _new_emi = _amortised_installment(loan_amount, expected_roi, term_months)
                _existing_dti = monthly_emi / _inc_basis * 100
                _prop_dti = (monthly_emi + _new_emi) / _inc_basis * 100
                # Existing debt is manageable but the requested loan makes the proposed position unaffordable
                # (proposed DTI >= 100% => the new EMI alone would exceed income). Flag it in red, and be
                # explicit that the risk advisory/report reflect the existing position, not this outsized loan.
                if _prop_dti >= 100 and _existing_dti < 100:
                    st.error(
                        f"⚠️ **Proposed debt-to-income of {_prop_dti:.0f}% is very high.** Your existing debt is "
                        f"low ({_existing_dti:.1f}%), but the EMI on this loan (~₹{_new_emi:,.0f}/mo at "
                        f"{expected_roi:.2f}% p.a.) would take far more than your whole income. **Please note:** "
                        "the risk assessment and improvement plan you receive after completing the form reflect "
                        "your **existing debt and current credit position only** — a loan of this size is not "
                        "treated as serviceable, and its affordability is assessed separately in the "
                        "serviceability check below. To borrow an amount like this you would need to add a "
                        "co-applicant's income, choose a longer term, or request a smaller amount.", icon="⚠️")
                else:
                    chip(f"Proposed debt-to-income after this loan (approx.): **{_prop_dti:.0f}%** — existing "
                         f"EMIs plus this loan's estimated EMI of ~₹{_new_emi:,.0f}/mo at {expected_roi:.2f}% "
                         "p.a. (the rate you entered).")
            elif loan_amount and term_months:
                chip("Enter your expected interest rate above to estimate this loan's EMI, your proposed "
                     "debt-to-income, and the amount your income could service.")
    _close(2, (expected_roi is not None))
else:
    _locked_strip(2)

if _unlocked(3):
    with _panel(3):
        # ======================================================================
        # 3 · Credit score — exact value OR a band
        # ======================================================================
        sub = ("Your credit score is a key, reliable input that lenders trust — please "
               "enter it if you know it. If you are genuinely new to credit with no score, "
               "we will switch to a model built for that case."
               if track == "unsecured" else
               "Your credit score is essential for a secured loan assessment — please "
               "enter it. Without it the estimate is far less reliable.")
        credit_score = credit_bureau = None
        additional_score = additional_score_type = None
        if new_to_credit:
            section("3", "Credit score", "Skipped — you told us you're new to credit.")
            with st.container(border=True):
                st.caption("🔒 Locked — new to credit means no credit score yet. We'll use the no-score "
                           "model built for exactly this case.")
        else:
            section("3", "Credit score", sub)
            EXACT, BAND, NOSCORE = "I know my exact score", "I know the approximate range", "I don't have a credit score"
            _required.append(("score", "Credit score"))
            with st.container(border=True):
                st.markdown("**Do you know your credit score?**")
                score_state = st.radio("Availability", [EXACT, BAND, NOSCORE, UNSURE], index=None,
                                       horizontal=True, key="tri_score", label_visibility="collapsed")
                if score_state in (EXACT, BAND):
                    b1, b2 = st.columns(2)
                    # Only FICO is genuinely in the training data (LendingClub). Any other bureau
                    # score is accepted and RESCALED by its published range onto the FICO 300-850
                    # scale the model was trained on.
                    BUREAU_CHOICES = {
                        "FICO (300–850) — the model's trained scale": "FICO",
                        "Another bureau — CIBIL / Experian / Equifax / CRIF (300–900)*": "CIBIL",
                    }
                    label = b1.selectbox("Which score do you have?", list(BUREAU_CHOICES))
                    credit_bureau = BUREAU_CHOICES[label]
                    lo, hi = BUREAU_RANGES[credit_bureau]
                    if score_state == EXACT:
                        credit_score = b2.number_input("Your score", lo, hi, min(750, hi))
                    else:
                        bands, v = [], lo
                        while v < hi:
                            top = min(v + 40, hi)
                            bands.append((v, top))
                            v = top
                        pick = b2.selectbox("Approximate range", bands,
                                            format_func=lambda t: f"{t[0]} – {t[1]}",
                                            index=len(bands) - 4 if len(bands) > 4 else 0)
                        credit_score = (pick[0] + pick[1]) / 2
                        st.caption(f"We'll use the midpoint of your band ({credit_score:.0f}). "
                                   "A band is far better than leaving it blank.")
                    if credit_bureau != "FICO":
                        st.caption("\\* Rescaled to the model's trained FICO range (300–850) by the "
                                   "bureau's published min–max. This assumes rank-equivalence across "
                                   "bureaus — a documented approximation, since the model is trained on FICO.")
                elif score_state == NOSCORE:
                    st.caption("New to credit — we'll use the no-score model built for exactly this case.")
                elif score_state == UNSURE:
                    st.caption("We'll use the no-score model and widen the confidence band.")

            # --- additional lender score: pick the TYPE, or say it's unavailable ----
            NOT_AVAILABLE = "Not available with me"
            SCORE_TYPES = ["Lender's own internal score", "Alternative-data / fintech score",
                           "Account-aggregator based score", "Telecom or utility score",
                           "Other lender score", NOT_AVAILABLE]
            _required.append(("extra_type", "Additional lender score"))
            with st.container(border=True):
                st.markdown("**Any additional score a lender has given you?**")
                st.caption("These are lender-specific and not published by any bureau — the dataset "
                           "behind this model keeps them anonymous, so we cannot name them.")
                additional_score_type = st.selectbox(
                    "Type of score", SCORE_TYPES, index=None, placeholder="Select one…",
                    key="tri_extra_type", label_visibility="collapsed")
                if additional_score_type and additional_score_type != NOT_AVAILABLE:
                    additional_score = st.number_input(
                        "Score value", 0.0, 900.0, 0.0, 1.0,
                        help="Enter on whatever scale the lender gave you (0–1 or 300–900).")
                    if additional_score == 0.0:
                        additional_score = None
    _close(3)
else:
    _locked_strip(3)

if _unlocked(4):
    with _panel(4):
        # ======================================================================
        # 4 · Track-specific
        # ======================================================================
        card_balance = card_limit = num_credit_lines = None
        enquiries_6m = new_accounts_24m = None
        missed_payment_2y = has_default_record = None
        housing = employment_type = loan_purpose = None
        asset_value = down_payment = owns_property = owns_car = enquiries_12m = None
        first_credit_year = education = None
        asset_scenario = None
        family_total = dependents = None

        if track == "unsecured":
            section("4", "Your credit cards and history",
                    'If you have no credit cards at all, choose "I don\'t have any" — that is '
                    "different from not knowing the number.")

            # New-to-credit is decided once, up top (item 19). When set, all credit-history questions
            # are pre-set to "none" and skipped; only housing and purpose are asked here.
            if new_to_credit:
                card_balance = card_limit = 0.0
                num_credit_lines = enquiries_6m = new_accounts_24m = 0
                missed_payment_2y = has_default_record = False
                first_credit_year = None
                with st.container(border=True):
                    st.caption("🔒 Locked (new to credit): no credit cards · no loans · no missed payments · "
                               "no defaults · no credit history yet. Just tell us your housing and purpose.")
                h1, h2 = st.columns(2)
                with h1:
                    housing, _ = tri_select("Your housing situation", "house",
                                            ["own_outright", "paying_home_loan", "rented", "with_family"],
                                            lambda x: x.replace("_", " ").title())
                with h2:
                    loan_purpose, _ = tri_select(
                        "Purpose of the loan", "purpose", list(UI_PURPOSE_LABELS),
                        lambda x: UI_PURPOSE_LABELS[x])
            else:
                # Item 3/9 — do you use credit cards? "No" pre-fills and skips the card questions.
                _required.append(("hascards", "Whether you use credit cards"))
                with st.container(border=True):
                    st.markdown("**Do you currently use any credit cards?**")
                    _hc = st.radio("cards", [YES, NO, NOTSURE], index=None, key="tri_hascards",
                                   horizontal=True, label_visibility="collapsed")
                _no_cards = (_hc == NO)
                if _no_cards:
                    card_balance = card_limit = 0.0
                    num_credit_lines = 0
                    st.caption("No credit cards — card balance, limit and count are recorded as zero and "
                               "the follow-up card questions are skipped.")
                u1, u2 = st.columns(2)
                with u1:
                    if not _no_cards:
                        card_balance, _ = tri_number("Total outstanding on all credit cards (₹)", "cb",
                                                     min_value=0, max_value=10_000_000, value=50_000, step=5_000)
                        num_credit_lines, _ = tri_number("How many cards / active credit lines?", "ncl",
                                                         min_value=0, max_value=50, value=3)
                    enquiries_6m, _ = tri_select("Loans or cards applied for in the last 6 months", "enq6",
                                                 [0, 1, 2, 3], lambda x: "3 or more" if x == 3 else str(x))
                with u2:
                    if not _no_cards:
                        card_limit, _ = tri_number("Total credit limit across all cards (₹)", "cl",
                                                   min_value=0, max_value=10_000_000, value=200_000, step=5_000)
                    new_accounts_24m, _ = tri_select(
                        "New loans or credit cards you OPENED in the last 24 months", "na24",
                        [0, 1, 2, 3], lambda x: "3 or more" if x == 3 else str(x),
                        help="Count every brand-new credit account you actually opened in the last 2 years — "
                             "any new loan (home, auto, personal, consumer-durable, education) or a new credit "
                             "card. Do NOT count: applications/enquiries that didn't open, accounts opened "
                             "earlier, or accounts you've since closed.")
                    housing, _ = tri_select("Your housing situation", "house",
                                            ["own_outright", "paying_home_loan", "rented", "with_family"],
                                            lambda x: x.replace("_", " ").title())
                if card_balance and card_limit:
                    chip(f"Card utilisation derived: **{card_balance/card_limit*100:.1f}%**")
                v1, v2 = st.columns(2)
                with v1:
                    missed_payment_2y, _ = tri_yesno("Any payment missed by 30+ days in the last 2 years?", "mp2y")
                with v2:
                    has_default_record, _ = tri_yesno("Any default, write-off or legal/recovery case?", "defrec")
                w1, w2 = st.columns(2)
                with w1:
                    first_credit_year, _ = tri_number("Year you took your first loan or card", "fcy",
                                                      min_value=1970, max_value=2026, value=2015, zero=None)
                with w2:
                    loan_purpose, _ = tri_select(
                        "Purpose of the loan", "purpose", list(UI_PURPOSE_LABELS),
                        lambda x: UI_PURPOSE_LABELS[x],
                        help="Purpose matters a lot: in the training data, business loans default "
                             "at 29.7% versus 12.2% for weddings.")
        else:
            # ---- security structure: buying it, or pledging something already owned? ----
            PURCHASE, MORTGAGE = ("I am buying this asset with the loan (it becomes the security)",
                                  "I already own the asset and will mortgage it to raise funds")
            # Loan against property is definitionally a mortgage of an owned asset.
            if product == "loan_against_property":
                asset_scenario = "mortgage_existing"
                scenario_label = MORTGAGE
            else:
                _required.append(("scenario", "How the security is arranged"))
                with st.container(border=True):
                    st.markdown("**How is the security arranged?**")
                    scenario_label = st.radio("Scenario", [PURCHASE, MORTGAGE], index=None,
                                              key="tri_scenario", label_visibility="collapsed")
                    asset_scenario = ("purchase" if scenario_label == PURCHASE
                                      else "mortgage_existing" if scenario_label == MORTGAGE else None)

            # ---- heading + wording adapt to product AND scenario ----
            NOUN = {"home_loan": "property", "loan_against_property": "property",
                    "auto_loan": "vehicle"}[product]
            if asset_scenario == "mortgage_existing":
                head = f"The {NOUN} you will mortgage"
                sub4 = (f"Enter details of the {NOUN} you **already own** and will pledge as security "
                        "to raise these funds. No down payment applies.")
                value_label = f"Current market value of the {NOUN} you own (₹)"
            else:
                head = f"The {NOUN} you plan to purchase"
                if product == "home_loan":
                    sub4 = ("Enter details of the **house or flat you intend to buy** — this property "
                            "will itself be the security for the loan.")
                else:
                    sub4 = (f"Enter details of the {NOUN} you intend to buy — it will be "
                            "hypothecated/mortgaged as security for the loan.")
                value_label = f"Purchase price / market value of the {NOUN} (₹)"
            section("4", head, sub4)

            s1, s2 = st.columns(2)
            with s1:
                asset_value, _ = tri_number(value_label, "av", min_value=0, max_value=500_000_000,
                                            value=6_000_000, step=100_000, zero=None)
                owns_property, _ = tri_yesno("Do you already own a house or property?", "ownprop")
                education, _ = tri_select("Highest education", "edu",
                                          ["secondary", "higher_secondary", "graduate", "post_graduate"],
                                          lambda x: x.replace("_", " ").title())
            with s2:
                if asset_scenario == "mortgage_existing":
                    st.container(border=True).info(
                        "No down payment applies — you already own this asset.", icon="ℹ️")
                    down_payment = None
                else:
                    down_payment, _ = tri_number("Down payment you can make (₹)", "dp",
                                                 min_value=0, max_value=500_000_000,
                                                 value=1_500_000, step=50_000)
                owns_car, _ = tri_yesno("Do you own a car?", "owncar")
                if new_to_credit:
                    enquiries_12m = 0
                    st.caption("🔒 Bureau enquiries locked to 0 — you selected new to credit.")
                else:
                    enquiries_12m, _ = tri_select("Bureau enquiries in the last 12 months", "enq12",
                                                  [0, 1, 2, 3], lambda x: "3 or more" if x == 3 else str(x))
            if asset_value:
                chip(f"Loan-to-value derived: **{loan_amount/asset_value*100:.1f}%**")

            # ---- household composition ----
            st.markdown("##### Your household")
            st.caption("Family size and how many people depend on your income both affect "
                       "how much of your earnings is already committed.")
            f1, f2 = st.columns(2)
            with f1:
                family_total = st.number_input("Total members in your family", 1, 15, 3,
                                               help="Everyone living in your household, "
                                                    "including yourself.")
            with f2:
                dependents = st.number_input("How many of them are your dependents?", 0, 15, 1)

            with st.expander("Who counts as a dependent?"):
                st.markdown("""
        **A dependent is anyone who relies on your income for their living expenses and
        does not earn enough to support themselves.**

        **✅ Count them as a dependent**
        - Children under 18, or older children still studying
        - A spouse or partner who does not earn
        - Elderly parents or in-laws you financially support
        - Any family member with no independent income (for example, someone unable to work)

        **❌ Do not count**
        - **Yourself** — you are the applicant, not your own dependent
        - A spouse, parent or sibling who **earns and supports themselves**
        - Relatives you do not financially support, even if they live with you
        - Domestic staff or tenants

        *Example:* a family of 4 — you (earning), your spouse (earning), and two school-going
        children — has **4 total members** and **2 dependents**.
        """)

            if dependents > family_total - 1 and family_total > 1:
                st.warning("Dependents cannot exceed your family size minus yourself — "
                           "remember not to count yourself as a dependent.", icon="⚠️")
            if family_total:
                chip(f"Dependency ratio derived: **{min(dependents/family_total,1)*100:.0f}%** "
                     f"of your household depends on your income")
    _close(4)
else:
    _locked_strip(4)

if _unlocked(5):
    with _panel(5):
        # ======================================================================
        # 5 · Existing secured loans (asked on BOTH tracks — both models use it)
        # ======================================================================
        section("5", "Your existing home &amp; vehicle loans (secured loans)",
                "Holding a home or vehicle loan is a <b>positive</b> signal — in our data, people with one "
                "default at 5.6% versus 8.4% without. We ask about each loan type separately; the follow-up "
                "questions for a loan appear only if you have it.")

        has_home_loan = has_vehicle_loan = None
        if new_to_credit:
            has_home_loan = has_vehicle_loan = False
            secured_outstanding = 0.0
            secured_ever_missed = False
            first_secured_year = None
            with st.container(border=True):
                st.caption("🔒 Locked (new to credit): no home or vehicle loan.")
        else:
            # Ask about each secured loan type in its OWN block, so the customer answers specifically for
            # that loan. The per-type answers are combined afterwards into the model's secured-history
            # inputs, so the risk model receives exactly the same values as before (no model change).
            home_owed = home_year = home_missed = None
            veh_owed = veh_year = veh_missed = None
            c_home, c_veh = st.columns(2)
            with c_home:
                st.markdown("**🏠 Home loan**")
                has_home_loan, _ = tri_yesno("Do you currently have a home loan?", "hashome")
                if has_home_loan is True:
                    home_owed, _ = tri_number("Amount still owed on your home loan (₹)", "homeowed",
                                              min_value=0, max_value=200_000_000, value=0, step=50_000)
                    home_missed, _ = tri_yesno("Ever missed a payment on your home loan?", "homemiss")
                    home_year, _ = tri_number("Year you took your home loan", "homeyr",
                                              min_value=1970, max_value=2026, value=2018, zero=None)
            with c_veh:
                st.markdown("**🚗 Vehicle / car loan**")
                has_vehicle_loan, _ = tri_yesno("Do you currently have a vehicle / car loan?", "hasveh")
                if has_vehicle_loan is True:
                    veh_owed, _ = tri_number("Amount still owed on your vehicle loan (₹)", "vehowed",
                                             min_value=0, max_value=50_000_000, value=0, step=25_000)
                    veh_missed, _ = tri_yesno("Ever missed a payment on your vehicle loan?", "vehmiss")
                    veh_year, _ = tri_number("Year you took your vehicle loan", "vehyr",
                                             min_value=1970, max_value=2026, value=2020, zero=None)
            # combine per-type answers into the model's secured-history inputs (model contract unchanged)
            if (has_home_loan is True) or (has_vehicle_loan is True):
                _owes = [x for x in (home_owed, veh_owed) if x is not None]
                secured_outstanding = sum(_owes) if _owes else None
                _missed = [m for m in (home_missed, veh_missed) if m is not None]
                secured_ever_missed = (any(_missed) if _missed else None)
                _years = [y for y in (home_year, veh_year) if y]
                first_secured_year = min(_years) if _years else None
                if secured_outstanding and monthly_income:
                    chip(f"Secured debt vs annual income: **{secured_outstanding/(monthly_income*12)*100:.0f}%**")
            else:
                secured_outstanding = 0.0
                secured_ever_missed = False
                first_secured_year = None
                if (has_home_loan is False) and (has_vehicle_loan is False):
                    st.caption("No home or vehicle loan — recorded as none; the follow-up questions are skipped.")
    _close(5)
else:
    _locked_strip(5)

if _unlocked(6):
    with _panel(6):
        # ======================================================================
        # 6 · Your other loan obligations (item 8 — separate from home/vehicle)
        # ======================================================================
        section("6", "Your other loan obligations",
                "This is about your <b>instalment loans of every kind put together</b> — not just the "
                "home/vehicle loans above.")
        if new_to_credit:
            instalment_outstanding_pct = 0
            with st.container(border=True):
                st.caption("🔒 Locked (new to credit): no other loan obligations.")
        else:
            instalment_outstanding_pct, _ = tri_number(
                "Across ALL your instalment loans combined — car, personal, consumer-durable and "
                "education loans (but NOT your home loan) — roughly what % of the total originally "
                "borrowed is still outstanding?", "ilutil", min_value=0, max_value=100, value=50, step=5,
                help="A rough estimate is fine. Someone about halfway through repaying these loans would say "
                     "~50%; someone who just took them, ~90%; nearly finished, ~10%. In our data this is a "
                     "strong signal — default rises from 19.6% to 25.0% as this percentage climbs.")
    _close(6)
else:
    _locked_strip(6)

if _unlocked(7):
    with _panel(7):
        # ======================================================================
        # 7 · Causal probing
        # ======================================================================
        section("7", "Recent changes in your situation",
                "These separate a temporary setback from a lasting one. Our causal analysis showed "
                "these drive genuine risk — not just correlation.")
        d1, d2 = st.columns(2)
        with d1:
            employed_12m, _ = tri_yesno("Continuously employed for the last 12 months?", "emp12")
            missed_emi_6m, _ = tri_yesno("Missed any EMI or card payment in the last 6 months?", "me6")
            employment_type, _ = tri_select(
                "Employment type", "emptype", list(UI_EMPLOYMENT_LABELS),
                lambda x: UI_EMPLOYMENT_LABELS[x],
                help="Strong signal in the training data: government/PSU employees default "
                     "at 5.8% versus 9.6% for private salaried.")
        with d2:
            job_change_6m, _ = tri_yesno("Changed jobs in the last 6 months?", "jc6")
            large_expense, _ = tri_yesno("Any large unplanned expense recently?", "lue")
            # Asked as a Yes/No fact first, then the direction. The tri-state vocabulary
            # does not fit a question about change: "I don't have any" captioned itself
            # "Recorded as not applicable", which is meaningless — everyone has an income
            # trajectory, even a flat one.
            #
            # The value handed to the model is unchanged — still exactly one of
            # "increased" / "same" / "decreased" / None:
            #     No             -> "same"      (a real answer, not a missing one)
            #     Yes + which way-> "increased" / "decreased"
            #     Not sure       -> None        (model told nothing, per §3A)
            _required.append(("incchg", "Whether your income changed in the last 12 months"))
            with st.container(border=True):
                st.markdown("**Has your income changed in the last 12 months?**")
                _chg = st.radio("Income change", [YES, NO, NOTSURE], index=None, horizontal=True,
                                key="tri_incchg", label_visibility="collapsed")
                if _chg == YES:
                    # Registered only on "Yes", so the step cannot complete until the
                    # direction is picked — the same conditional-registration pattern
                    # the card questions use.
                    _required.append(("incchg_dir", "Which way your income moved"))
                    income_change = st.selectbox(
                        "Which way?", ["increased", "decreased"], index=None,
                        format_func=lambda x: x.title(), key="tri_incchg_dir",
                        placeholder="Increased or decreased?")
                elif _chg == NO:
                    income_change = "same"
                    st.caption("Recorded as no change over the last 12 months.")
                elif _chg == NOTSURE:
                    income_change = None
                    st.caption("Left blank — the model is told nothing rather than a guess.")
    _close(7)
else:
    _locked_strip(7)

# ======================================================================
# Submit
# ======================================================================
st.divider()

# Steps still to go. `_required` only ever contains questions from steps that have
# actually rendered, so the question counts below describe what the customer can see —
# not the whole form. This line carries the rest.
_steps_left = [n for n in sorted(_STEPS) if not st.session_state["_done"].get(n)]
if _steps_left:
    st.info(f"**{len(_steps_left)} step{'s' if len(_steps_left) > 1 else ''} to go.** "
            f"Next up: {_STEPS[_steps_left[0]]}.", icon="📋")

unanswered = [lbl for key, lbl in _required if st.session_state.get(f"tri_{key}") is None]

# --- Fill the progress meter claimed under the hero (UI_DESIGN §3.7 / §4.4) ------------
# It counts STEPS, not questions: `_required` only holds questions from steps that have
# rendered, so a question-based count would read 100% the moment step 1 was done.
_total = _LAST_STEP
_done = sum(1 for n in _STEPS if st.session_state["_done"].get(n))
_pct = _done / _total * 100
_progress_slot.markdown(
    f'<div class="pg{" pg-done" if _done == _total else ""}">'
    f'<div class="pg-row"><span class="pg-label">'
    f'{"All steps complete" if _done == _total else f"Step {min(_done + 1, _total)} of {_total} — {_STEPS[_current_step()]}"}'
    f'</span><span class="pg-count">{_done} / {_total}</span></div>'
    f'<div class="pg-track"><div class="pg-fill" style="width:{_pct:.1f}%"></div></div>'
    f'</div>', unsafe_allow_html=True)

if unanswered:
    st.warning(f"**{len(unanswered)} question(s) still need an answer.** Pick one option for each "
               '— including "Not sure", which is a valid answer we handle properly.')
    with st.expander("Show what's outstanding"):
        for lbl in unanswered:
            st.markdown(f"- {lbl}")

# --- cross-field coherence (validation.py) --------------------------------------
# Completeness is checked above: every question was ANSWERED. This checks the answers
# AGREE with one another — a loan bigger than the asset securing it, a card balance with
# no limit, credit history predating the applicant's 18th birthday. Errors block;
# warnings never do. See validation.py for why business policy is only ever a warning.
errors, warns = validate(
    track=track, product=product, new_to_credit=new_to_credit,
    asset_scenario=asset_scenario,
    monthly_income=monthly_income, coapplicant_income=coapplicant_income,
    monthly_emi=monthly_emi, loan_amount=loan_amount, term_months=term_months,
    expected_roi=expected_roi, age=age,
    card_balance=card_balance, card_limit=card_limit,
    num_credit_lines=num_credit_lines, first_credit_year=first_credit_year,
    asset_value=asset_value, down_payment=down_payment,
    family_total=family_total, dependents=dependents,
    first_secured_year=first_secured_year)

# Held back until the form is finished. On a fresh page it is empty by definition, and
# flagging "income is required" before the customer has started reads as the tool
# shouting at them. The button is disabled by `unanswered`/`_steps_left` anyway, so
# nothing is lost by waiting — and `errors` still gates the button below regardless.
if not unanswered and not _steps_left:
    if errors:
        st.error(f"**{len(errors)} answer{'s' if len(errors) > 1 else ''} "
                 f"need{'' if len(errors) > 1 else 's'} correcting before we can assess you.** "
                 "These can't all be true at once, so a result based on them would be misleading.",
                 icon="🚫")
        for i in errors:
            st.markdown(
                f'<div class="v-issue v-error"><div class="v-label">{i.label}</div>'
                f'<div class="v-detail">{i.detail}</div></div>', unsafe_allow_html=True)

    if warns:
        st.warning(f"**{len(warns)} thing{'s' if len(warns) > 1 else ''} worth double-checking.** "
                   "You can continue — we just want you to see these first.", icon="⚠️")
        for i in warns:
            st.markdown(
                f'<div class="v-issue v-warn"><div class="v-label">{i.label}</div>'
                f'<div class="v-detail">{i.detail}</div></div>', unsafe_allow_html=True)

    if not errors and not warns:
        st.success("Everything checks out — your answers are consistent.", icon="✅")

go = st.button("Check my loan readiness", type="primary", use_container_width=True,
               disabled=bool(unanswered) or bool(errors) or bool(_steps_left))

if go:
    ans = Answers(
        product=product, monthly_income=monthly_income or None,
        coapplicant_income=coapplicant_income or None,
        expected_roi=expected_roi or None,
        monthly_emi_existing=monthly_emi, credit_score=credit_score,
        credit_bureau=credit_bureau, additional_score=additional_score,
        additional_score_type=additional_score_type,
        first_credit_year=int(first_credit_year) if first_credit_year else None,
        loan_amount=loan_amount or None, term_months=term_months,
        employment_type=employment_type, education=education, age=age,
        card_balance=card_balance, card_limit=card_limit,
        num_credit_lines=num_credit_lines, enquiries_6m=enquiries_6m,
        new_accounts_24m=new_accounts_24m, missed_payment_2y=missed_payment_2y,
        has_default_record=has_default_record, loan_purpose=loan_purpose,
        housing=housing, asset_value=asset_value, down_payment=down_payment,
        owns_property=owns_property, owns_car=owns_car, enquiries_12m=enquiries_12m,
        asset_scenario=asset_scenario, family_total=family_total,
        dependents=dependents,
        employed_12m_continuous=employed_12m, job_change_6m=job_change_6m,
        income_change_12m=income_change, missed_emi_6m=missed_emi_6m,
        large_unplanned_expense=large_expense,
        has_home_loan=has_home_loan, has_vehicle_loan=has_vehicle_loan,
        secured_outstanding=secured_outstanding,
        secured_ever_missed=secured_ever_missed,
        first_secured_year=int(first_secured_year) if first_secured_year else None,
        instalment_outstanding_pct=instalment_outstanding_pct)

    res = router.predict(ans)
    risk = res["risk"]
    track = res["track"]
    from improvement_engine import approval_chance, readiness_score
    approval_pct = approval_chance(risk, track) * 100.0
    readiness = readiness_score(risk, track)
    # HEADLINE is the chance of getting the loan (approval likelihood). Bands are on approval.
    if approval_pct >= 75:
        band, icon, col = "Strong — likely to be approved", "●", "var(--good)"
    elif approval_pct >= 50:
        band, icon, col = "Fair — approvable with the right lender", "▲", "var(--warning)"
    elif approval_pct >= 25:
        band, icon, col = "Guarded — improvements needed first", "◆", "var(--serious)"
    else:
        band, icon, col = "Low — significant improvements needed", "■", "var(--critical)"

    # Compute affordability + the plan up front so the three output sections can be ordered freely.
    import math
    from credit_engine import affordability, affordability_table
    from improvement_engine import analyse, READINESS_PD_MAX, APPROVAL_PD50
    from report_pdf import build_report_pdf
    aff = affordability(ans, ceiling=1.0) if (monthly_income and loan_amount and term_months) else None
    plan_res = analyse(ans, router, afford=aff)
    pd_max = READINESS_PD_MAX.get(track, 0.60)
    _baserate = 20 if track == "unsecured" else 8   # typical default rate for the track (for the explainer)
    _cut = APPROVAL_PD50.get(track, 0.30)           # PD at which approval is 50/50 — the lender cut-off
    _k = math.log(99.0) / _cut                      # curve steepness (approval ≈ 99% at zero risk)

    takeaway(1, "Your loan-readiness verdict", "where you stand today — and exactly how we got there")
    r1, r2 = st.columns([0.44, 0.56])
    with r1:
        st.markdown(f"""
<div class="result">
  <div style="color:var(--ink-muted);font-size:.76rem;text-transform:uppercase;
       letter-spacing:.06em;font-weight:600;">Chance of getting this loan</div>
  <p class="figure">{approval_pct:.0f}%</p>
  <div class="band" style="color:{col};"><span>{icon}</span><span>{band}</span></div>
  <div class="track"><div class="fill" style="width:{approval_pct:.1f}%;background:{col};"></div></div>
  <div class="scale"><span>0%</span><span>50%</span><span>100%</span></div>
  <div class="cap">For a <b>{track}</b> loan
    {"." if res['tier'] >= 1 else ", assessed without a credit score."}</div>
</div>""", unsafe_allow_html=True)

        # -- the curve that produced the number, directly beneath it -------------
        st.markdown('<div style="font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;'
                    'color:var(--ink-muted);font-weight:700;margin:10px 0 2px;">'
                    'How we got it — the curve &amp; the lender cut-off</div>', unsafe_allow_html=True)
        st.altair_chart(chance_curve_chart(risk, track), use_container_width=True)
        st.caption(f"Dashed line = a typical lender's approval cut-off. Red dot = you. "
                   f"Your **{risk*100:.1f}%** risk → a **{approval_pct:.0f}%** chance of this loan.")
        st.markdown(
            '<div style="color:var(--ink-2);font-size:.82rem;line-height:1.5;margin-top:2px;">'
            '<b>How to read it:</b>'
            '<ul style="margin:4px 0 0 0;padding-left:18px;">'
            '<li>Lower risk → higher chance of the loan.</li>'
            "<li>The chance drops fastest right around the lender's cut-off.</li>"
            "<li>Well below the cut-off you're fairly safe; above it, approval falls away quickly.</li>"
            '</ul></div>', unsafe_allow_html=True)
        with st.expander("Show the exact formula"):
            st.caption(
                f"The curve is `chance = 1 / (1 + e^(k × (risk − cut-off)))`. Two numbers set it, "
                f"neither fitted to data: the **midpoint** is the lender cut-off ({_cut*100:.0f}%), "
                f"where the chance is exactly 50%; the **steepness** k comes from making a zero-risk "
                f"applicant read ~99% — so k = ln(99) ÷ {_cut*100:.0f}% ≈ **{_k:.1f}**. In the "
                f"standard sigmoid form `1/(1+e^−(α+β·risk))`, α = ln 99 ≈ {math.log(99):.2f} and "
                f"β = −k ≈ {-_k:.1f} (negative because chance falls as risk rises).")
    with r2:
        t1, t2 = st.columns(2)
        t1.markdown(f'<div class="tile"><div class="k">Loan-readiness score</div>'
                    f'<div class="v">{readiness}/100</div>'
                    f'<div class="s">how strong your profile is</div></div>',
                    unsafe_allow_html=True)
        t2.markdown(f'<div class="tile"><div class="k">Confidence</div>'
                    f'<div class="v">{res["precision"]}</div>'
                    f'<div class="s">based on how much you told us</div></div>',
                    unsafe_allow_html=True)
        st.write("")
        t3, t4 = st.columns(2)
        t3.markdown(f'<div class="tile"><div class="k">Details provided</div>'
                    f'<div class="v" style="font-size:1.15rem">{res["inputs_supplied"]}</div>'
                    f'<div class="s">questions answered</div></div>', unsafe_allow_html=True)
        _loan = f"₹{loan_amount:,.0f}" if loan_amount else "—"
        t4.markdown(f'<div class="tile"><div class="k">Loan requested</div>'
                    f'<div class="v" style="font-size:1.15rem">{_loan}</div>'
                    f'<div class="s">over {term_months} months</div></div>', unsafe_allow_html=True)

        # -- the arithmetic behind the score, directly beneath its own tiles -----
        st.markdown(
            '<div style="border:1px solid var(--hair);border-radius:10px;padding:13px 15px;'
            'background:var(--surface);margin-top:14px;">'
            '<div style="font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;'
            'color:var(--ink-muted);font-weight:700;">Loan-readiness score — the maths for you</div>'
            f'<div style="font-family:ui-monospace,Consolas,monospace;font-size:.98rem;color:var(--ink);'
            f'margin-top:9px;line-height:2.0;">100 &times; ( 1 &minus; <b>risk</b> &divide; <b>ceiling</b> )<br>'
            f'= 100 &times; ( 1 &minus; {risk:.3f} &divide; {pd_max:.2f} )<br>'
            f'= <b style="color:var(--brand);font-size:1.2rem;">{readiness} / 100</b></div>'
            f'<div style="color:var(--ink-2);font-size:.82rem;margin-top:9px;line-height:1.5;">'
            f'<b>What the {pd_max*100:.0f}% ceiling is</b> — the risk level where the score hits 0/100:'
            f'<ul style="margin:4px 0 0 0;padding-left:18px;">'
            f'<li>Set at ~{pd_max*100/_baserate:.0f}&times; the ~{_baserate}% average default rate for '
            f'{track} loans.</li>'
            f'<li>At {pd_max*100:.0f}% risk a decline is near-certain — a fair floor for the scale.</li>'
            f'<li>Lower risk &rarr; higher score. It <b>measures your profile</b>; it is <b>not</b> an '
            f'approval cut-off.</li>'
            f'</ul></div></div>',
            unsafe_allow_html=True)

        st.markdown('<div style="font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;'
                    'color:var(--ink-muted);font-weight:700;margin:12px 0 2px;">'
                    'What the ceiling actually does — the same risk, scored on four scales</div>',
                    unsafe_allow_html=True)
        st.altair_chart(readiness_ceiling_chart(risk, track), use_container_width=True)
        _readings = " · ".join(
            f"<b>{100*(1-min(risk/c,1.0)):.0f}/100</b> at {c*100:.0f}%"
            for c in sorted({round(min(pd_max*m, 1.0), 2) for m in (0.5, 0.75, 1.0, 1.5)}))
        st.markdown(
            '<div style="color:var(--ink-2);font-size:.82rem;line-height:1.5;">'
            '<ul style="margin:0;padding-left:18px;">'
            '<li>Every line is the <b>same</b> score formula — just a different ceiling.</li>'
            '<li>Higher ceiling → everyone scores higher; lower ceiling → everyone scores lower.</li>'
            f'<li>At your {risk*100:.1f}% risk you would read {_readings}. We use '
            f'<b>{pd_max*100:.0f}%</b> (the heavy line).</li>'
            "<li>The ceiling changes the <b>score only</b> — never your chance of the loan (that comes "
            'from the curve on the left).</li>'
            '</ul></div>', unsafe_allow_html=True)

    # -- the two thresholds are different numbers doing different jobs ----------
    st.markdown(
        f'<div style="border:1px solid var(--hair);border-left:4px solid var(--brand);'
        f'border-radius:10px;padding:13px 16px;background:var(--surface);margin-top:14px;">'
        f'<div style="font-weight:700;color:var(--ink);font-size:.97rem;">'
        f'Two different thresholds — and why both are needed</div>'
        f'<div style="color:var(--ink-2);font-size:.9rem;line-height:1.55;margin-top:7px;">'
        f'Two numbers, two different jobs — don\'t confuse them (and neither is the ~{_baserate}% '
        f'average default rate they\'re built from):</div>'
        f'<div style="display:flex;gap:14px;margin-top:11px;flex-wrap:wrap;">'
        f'  <div style="flex:1 1 240px;border:1px solid var(--hair);border-radius:8px;padding:10px 12px;">'
        f'    <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;'
        f'         color:var(--ink-muted);font-weight:700;">Average default rate — ~{_baserate}%</div>'
        f'    <div style="color:var(--ink-2);font-size:.86rem;margin-top:5px;line-height:1.45;">'
        f'      Reality of the book: ~{_baserate} in 100 {track} borrowers default. Just the anchor '
        f'      the other two are set from — it decides nothing itself.'
        f'    </div></div>'
        f'  <div style="flex:1 1 240px;border:1px solid var(--hair);border-radius:8px;padding:10px 12px;">'
        f'    <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;'
        f'         color:#95620A;font-weight:700;">Lender cut-off — {_cut*100:.0f}%</div>'
        f'    <div style="color:var(--ink-2);font-size:.86rem;margin-top:5px;line-height:1.45;">'
        f'      A <b>decision</b> line: past this risk a typical lender stops approving. It sets your '
        f'      <b>chance of the loan</b> (the left curve).'
        f'    </div></div>'
        f'  <div style="flex:1 1 240px;border:1px solid var(--hair);border-radius:8px;padding:10px 12px;">'
        f'    <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;'
        f'         color:var(--brand);font-weight:700;">Readiness ceiling — {pd_max*100:.0f}%</div>'
        f'    <div style="color:var(--ink-2);font-size:.86rem;margin-top:5px;line-height:1.45;">'
        f'      A <b>measuring</b> scale: the risk where the 0–100 score bottoms out. It sets your '
        f'      <b>readiness score</b> — nothing else.'
        f'    </div></div>'
        f'</div>'
        f'<div style="color:var(--ink-2);font-size:.88rem;line-height:1.5;margin-top:11px;">'
        f'<b>Why keep both?</b>'
        f'<ul style="margin:4px 0 0 0;padding-left:18px;">'
        f'<li>They answer different questions — approval is a lender\'s yes/no; readiness is how '
        f'strong your profile is.</li>'
        f'<li>Example: right at the {_cut*100:.0f}% cut-off your approval chance is 50/50, but your '
        f'readiness is still {100*(1-min(_cut/pd_max,1.0)):.0f}/100 — borderline today ≠ unlendable.</li>'
        f'<li>One cut-off can\'t do both: at {_cut*100:.0f}% every borderline applicant would read '
        f'0/100 (unfair, hides progress); at {pd_max*100:.0f}% we\'d claim likely approval at risks no '
        f'lender accepts.</li>'
        f'</ul></div></div>',
        unsafe_allow_html=True)
    st.write("")
    st.markdown(
        '<div style="border:1px solid var(--hair);border-radius:10px;padding:12px 15px;'
        'background:var(--surface);color:var(--ink-2);font-size:.93rem;line-height:1.6;margin-top:8px;">'
        'Both numbers are produced from the details you provided above, which run through an '
        '<b style="color:var(--ink);">extensive credit-risk model backed by a large database of over '
        '2 million historical loan records</b>. The EMI and affordability figures in '
        '<b style="color:var(--ink);">Takeaway&nbsp;3</b> are separate arithmetic on only the amount, '
        'rate and tenor you enter &mdash; the model plays no part in those.</div>', unsafe_allow_html=True)
    st.write("")

    for note in res["notes"]:
        st.info(note, icon="ℹ️")

    if res["tier"] == 0 and track == "secured":
        st.error("**Precision note** — for a secured loan, not having a credit score reduces "
                 "how precise this estimate can be. Getting your score and re-running will give "
                 "a more reliable assessment.", icon="⚠️")
    elif res["tier"] == 0:
        st.info("You didn't provide a credit score — for a personal loan its impact is smaller than "
                "for a secured loan, because your other answers already carry much of the signal. A "
                "verified score still sharpens the estimate, so add it if you can.", icon="ℹ️")

    st.caption("This is an indicative readiness estimate based on the information you provided. "
               "The more complete your answers, the more precise it is — an incomplete form "
               "tends to read as higher risk.")
    st.caption(f"For transparency: the underlying model estimate is a **{risk*100:.1f}% probability "
               "of default (PD)** for this profile — the figures above are derived from it. The full "
               "breakdown is at the end of your downloadable report.")

    # -- per-case counterintuitive-result disclosure -------------------------
    # If a lever behaves the "wrong" way for THIS specific profile (e.g. earning
    # more or borrowing less nudges the estimate up), say so plainly, with the
    # exact numbers and the honest reason — never leave it as a silent surprise.
    from credit_engine import counterintuitive_disclosures
    _disc = counterintuitive_disclosures(router, ans)
    if _disc:
        st.markdown(
            '<div style="border-left:4px solid var(--warning);background:var(--surface);'
            'border-radius:8px;padding:11px 15px;margin-top:6px;">'
            '<b style="color:var(--ink);">A couple of results here may look counterintuitive '
            '— here is exactly why, for your profile.</b><br>'
            '<span style="color:var(--ink-2);font-size:.9rem;">Our model is honest about its own '
            'quirks. For the specific details you entered, the changes below move the risk estimate '
            'in a surprising direction. Click each to understand it in plain English.</span></div>',
            unsafe_allow_html=True)
        for _d in _disc:
            with st.expander("ℹ️  " + _d["title"]):
                st.markdown(_d["body"])
        st.write("")

    # ==================================================================
    # TAKEAWAY 2 · the improvement plan — the punch that drives this app
    # ==================================================================
    takeaway(2, "Your route to “yes”",
             "the changes that move the needle — every impact causally corrected")
    st.markdown(
        "Ranked by how much each change lifts your **chance of getting the loan** — and every impact "
        "is **causally corrected**, so we only push changes that genuinely move the needle "
        "(not ones the raw model overstates). We only suggest things you can actually change.")

    # Affordability-driven headline — promoted ABOVE the behavioural plan. For an out-of-capacity
    # request this is THE recommendation, and one the risk model cannot produce (it saturates on
    # amounts outside its training range). Pure arithmetic on the customer's own figures.
    _aa = plan_res.get("afford_action")
    if _aa and _aa.get("no_capacity"):
        st.error(
            f"**Your existing EMIs already use up your repayment capacity.** Your current EMIs alone "
            f"are about **{_aa['existing_dti_pct']}%** of your income — leaving no room to add a new "
            "loan on this income. To create capacity:", icon="🚫")
        st.markdown(
            "- **Add a co-applicant and include their monthly income** — this raises the household "
            "income the loan is serviced from, or\n"
            "- **Clear or reduce some existing EMIs** first, or\n"
            "- **Choose a longer repayment term** so each EMI is smaller.")
        st.caption("This is an affordability (debt-servicing) check — separate from the credit-risk "
                   "score above. It uses only your own figures and does not change the risk model. "
                   "Full detail in Takeaway 3 below.")
    elif _aa:
        st.error(
            f"**This loan is larger than your income can comfortably service.** The EMI on "
            f"₹{_aa['requested']:,.0f} would put your total debt-to-income at about "
            f"**{_aa['proposed_dti_pct']}%** — beyond your income. The most this income could service "
            f"over the same term is about **₹{_aa['max_serviceable_loan']:,.0f}** "
            f"(≈ ₹{_aa['shortfall']:,.0f} less). Strongest options:", icon="🎯")
        st.markdown(
            f"- **Borrow up to about ₹{_aa['max_serviceable_loan']:,.0f}** over this term, or\n"
            f"- **Choose a longer repayment term** to lower the monthly EMI, or\n"
            f"- **Add a co-applicant's income**, or raise income before applying.")
        st.caption("This is an affordability (debt-servicing) check — separate from the credit-risk "
                   "score above. It uses only your own figures and does not change the risk model. "
                   "Full detail in Takeaway 3 below.")

    # Coverage indicator — reframes 'not sure' answers from a silent failure into a clear signal.
    if plan_res.get("inputs_supplied"):
        st.caption(f"Recommendations based on {plan_res['inputs_supplied']} model inputs "
                   f"· input coverage ~{plan_res.get('coverage_pct', 0)}%. More complete answers "
                   f"give more specific advice.")

    if plan_res["plan"]:
        pg1, pg2, pg3 = st.columns(3)
        pg1.markdown(f'<div class="tile"><div class="k">Chance of loan now</div>'
                     f'<div class="v">{plan_res["approval_now_pct"]:.0f}%</div>'
                     f'<div class="s">readiness {plan_res["readiness_now"]}/100</div></div>',
                     unsafe_allow_html=True)
        pg2.markdown(f'<div class="tile"><div class="k">If you act on the top 3</div>'
                     f'<div class="v" style="color:var(--good);">{plan_res["approval_after_pct"]:.0f}%</div>'
                     f'<div class="s">readiness {plan_res["readiness_after_top3"]}/100 · '
                     f'{len(plan_res["products_now"])} → {len(plan_res["products_after"])} products</div></div>',
                     unsafe_allow_html=True)
        pg3.markdown(f'<div class="tile"><div class="k">Most you can reach</div>'
                     f'<div class="v" style="color:var(--accent);">{plan_res["approval_best_pct"]:.0f}%</div>'
                     f'<div class="s">acting on everything in your control</div></div>',
                     unsafe_allow_html=True)
        # Honest ceiling note (items 13/18): what's practical vs out of reach.
        _bp = plan_res["approval_best_pct"]
        if _bp < 90:
            st.caption(
                f"Based on the details you've shared — and holding constant the factors that can't be "
                f"altered right now, such as your **age** and **how long you've held credit** — the "
                f"most this profile reaches on its own is about **{_bp}%**. You can push beyond this: "
                f"a **higher income of your own** lifts it directly (income is one of the factors our "
                f"model scores), and **adding a co-applicant or any additional household income** "
                f"improves the debt-to-income the affordability check works on — both raise how much "
                f"loan you can comfortably service, and your chance of getting it.")
        else:
            st.caption(f"Based on the details you've shared, you can realistically reach about "
                       f"**{_bp}%** by acting on the changes below — close to the practical maximum for "
                       "this profile.")
        st.write("")
        _cc = {"High": "var(--good)", "Medium": "var(--warning)",
               "Low": "var(--critical)", "Model-implied": "var(--ink-muted)"}
        for i, p in enumerate(plan_res["plan"], 1):
            st.markdown(
                f'<div style="border:1px solid var(--hair);border-radius:11px;padding:12px 16px;'
                f'margin-bottom:8px;background:var(--surface);">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-weight:640;color:var(--ink);">{i}. {p["title"]}</span>'
                f'<span style="font-weight:680;color:var(--good);font-size:1.05rem;">+{p["readiness_gain_pts"]} pts</span>'
                f'</div>'
                f'<div style="color:var(--ink-2);font-size:.85rem;margin-top:3px;">{p["detail"]} '
                f'· {p["effort"]}, {p["timeframe"]} '
                f'· <span style="color:{_cc.get(p["confidence"],"var(--ink-muted)")};">● {p["confidence"]} confidence</span></div>'
                f'</div>', unsafe_allow_html=True)
            if p.get("how_steps"):
                with st.expander(f"↳ How to actually raise your score — {len(p['how_steps'])} steps for your profile"):
                    for s in p["how_steps"]:
                        st.markdown(f"- {s}")

        with st.expander("🔬 The causal reality-check — why our advice is different"):
            st.markdown(
                "Most credit tools show what *correlates* with approval. We use **double/debiased "
                "machine learning** to estimate what each change *causally* does, so we never "
                "oversell a lever the raw model overstates.")
            import pandas as _pd
            tbl = _pd.DataFrame([{
                "Change": p["title"],
                "Raw model says": f"−{p['raw_risk_drop_pp']:.1f}pp risk",
                "Causally-corrected": ("not causally tested" if p["confidence"] == "Model-implied"
                                       else f"−{p['causal_risk_drop_pp']:.1f}pp risk"),
                "Confidence": p["confidence"],
            } for p in plan_res["plan"]])
            st.dataframe(tbl, hide_index=True, use_container_width=True)
    else:
        fb = plan_res.get("fallback") or {"kind": "generic"}
        if fb["kind"] == "affordability":
            st.info("The main thing to address here is the **loan size versus your income** "
                    "(shown above). Once the amount is within what your income can service, "
                    "re-run to surface behavioural improvements too.", icon="ℹ️")
        elif fb["kind"] == "coverage":
            st.warning("**We can't suggest specific improvements yet — a few key answers are "
                       "missing.** You left these as 'not sure' or skipped them, and each one "
                       "unlocks part of the plan:", icon="📝")
            for m in fb.get("missing", []):
                st.markdown(f"- {m}")
            st.caption("Add these and re-run — the more complete your answers, the more specific "
                       "the recommendations become.")
        elif fb["kind"] == "strong":
            st.success("On the details you provided, we didn't find a material weakness to improve "
                       "— your profile already reads as low-risk on what we can see.", icon="✅")
        else:
            st.info("From the details you provided, we didn't find high-impact changes to suggest. "
                    "Answering more of the optional questions may reveal opportunities.")

    # ==================================================================
    # TAKEAWAY 3 · can your income service this loan? (affordability)
    # ==================================================================
    takeaway(3, "Can your income service this loan?",
             "a separate affordability check — your credit-risk verdict above is unchanged")
    if aff:
        st.caption(f"Figures are computed at the interest rate you entered — "
                   f"**{aff['rate_pct']:.2f}% per year**. Confirm your applicable rate with the lender "
                   "for the most accurate results; the model assumes no rate of its own.")
        a1, a2, a3 = st.columns(3)
        a1.metric("Your EMI on this loan", f"₹{aff['new_emi']:,.0f}/mo")
        a2.metric("Proposed debt-to-income", f"{aff['proposed_dti']*100:.0f}%",
                  help="Your existing EMIs plus this loan's EMI, as a share of income. (The DTI the "
                       "risk model uses is existing obligations only — this is an additional figure, "
                       "it does not change the model.)")
        a3.metric("Largest loan under 100% DTI", f"₹{aff['max_serviceable_loan']:,.0f}",
                  help="The largest loan whose EMI keeps your proposed debt-to-income under 100% "
                       "(i.e. total EMIs stay within your income), at the interest rate you entered.")
        _pdti = aff["proposed_dti"] * 100
        if _pdti < 100:
            st.success(
                f"Your proposed debt-to-income on this loan is **{_pdti:.0f}%** — within 100%, so your "
                "income can service the EMIs (with some left over). Lower is more comfortable.", icon="✅")
        else:
            st.warning(
                f"**At 100% debt-to-income your entire declared income would be spent on EMIs — which "
                f"most lenders will not approve.** Your proposed DTI on this loan is **{_pdti:.0f}%**. "
                f"Aim for a **loan amount (or longer tenor, or a co-applicant's income) that keeps your "
                f"proposed DTI below 100%** — the largest loan that fits is about "
                f"**₹{aff['max_serviceable_loan']:,.0f}** over this term.", icon="⚠️")
        at = affordability_table(ans)
        if at:
            import pandas as _pd
            st.markdown("**Loan serviceable at different debt-to-income levels**")
            _df = _pd.DataFrame([{
                "DTI level": f"{int(row['ceiling']*100)}%",
                "Max EMI room (₹/mo)": f"₹{row['max_new_emi']:,.0f}",
                "Max loan serviceable": f"₹{row['max_loan']:,.0f}",
            } for row in at["rows"]])
            st.table(_df)
            st.caption("A higher level means the EMIs take a larger share of your income. "
                       "**100% means your entire income goes to EMIs** — most lenders will not approve "
                       "that, so aim to keep your proposed DTI comfortably below it. Lower levels leave "
                       "more of your income free after the EMI.")
    elif expected_roi is None:
        st.info("Enter your **expected interest rate** in section 2 above to see this loan's EMI, your "
                "proposed debt-to-income, and the largest amount your income could service. The tool "
                "does not assume a rate — it uses only the one you provide.", icon="ℹ️")
    else:
        st.info("Enter your income, the loan amount and the repayment period above to see the "
                "affordability check.", icon="ℹ️")

    pdf_bytes = build_report_pdf(ans, plan_res, applicant_name="Applicant",
                                 product_label=allp[product])
    st.download_button("📄 Download my full improvement report (PDF)", data=pdf_bytes,
                       file_name="loan_readiness_report.pdf", mime="application/pdf",
                       type="primary", use_container_width=True)

    st.caption(
        "Note on amounts: you enter figures in ₹, but the models are trained on international "
        "credit books (US and an anonymised foreign market). Rupee amounts are converted to "
        "each model's trained scale before scoring; ratios like debt-to-income and utilisation "
        "are currency-independent. Because the underlying loan and income distributions differ "
        "from India, this is an indicative readiness estimate, not a lending decision.")
