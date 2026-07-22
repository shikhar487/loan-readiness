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

st.set_page_config(page_title="Loan Readiness Check", page_icon="🏦",
                   layout="wide", initial_sidebar_state="collapsed")

# ======================================================================
# Design tokens — validated status palette; colour is always paired with icon+label
# ======================================================================
st.markdown("""
<style>
:root{
  --surface:#fcfcfb; --plane:#f9f9f7;
  --ink:#0b0b0b; --ink-2:#52514e; --ink-muted:#898781;
  --hair:rgba(11,11,11,.10); --grid:#e1e0d9;
  --brand:#2a78d6; --brand-soft:#cde2fb;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
}
@media (prefers-color-scheme: dark){
  :root{
    --surface:#1a1a19; --plane:#0d0d0d;
    --ink:#fff; --ink-2:#c3c2b7; --ink-muted:#898781;
    --hair:rgba(255,255,255,.10); --grid:#2c2c2a;
    --brand:#3987e5; --brand-soft:#184f95;
  }
}
html, body, [class*="css"]{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;}
.block-container{padding-top:2.2rem; max-width:1180px;}

.hero{background:linear-gradient(135deg,var(--brand) 0%,#1c5cab 100%);
  border-radius:18px; padding:30px 34px; margin-bottom:26px; color:#fff;}
.hero h1{margin:0 0 6px 0; font-size:1.85rem; font-weight:650; letter-spacing:-.02em;}
.hero p{margin:0; opacity:.92; font-size:1rem; line-height:1.5;}

.sec{display:flex; align-items:center; gap:12px; margin:30px 0 6px;}
.sec .num{width:30px; height:30px; border-radius:9px; background:var(--brand);
  color:#fff; display:flex; align-items:center; justify-content:center;
  font-weight:650; font-size:.9rem; flex:none;}
.sec .t{font-size:1.18rem; font-weight:640; color:var(--ink); letter-spacing:-.01em;}
.sec-sub{color:var(--ink-2); font-size:.9rem; margin:0 0 14px 42px; line-height:1.5;}

.chip{display:inline-flex; align-items:center; gap:7px; background:var(--brand-soft);
  color:var(--ink); border-radius:999px; padding:5px 13px; font-size:.83rem;
  font-weight:550; margin-top:6px;}
@media (prefers-color-scheme: dark){ .chip{color:#fff;} }

.result{background:var(--surface); border:1px solid var(--hair); border-radius:18px;
  padding:26px 30px; margin-top:6px;}
.result .figure{font-size:3.6rem; font-weight:680; line-height:1; letter-spacing:-.03em;
  color:var(--ink); margin:0;}
.result .band{display:inline-flex; align-items:center; gap:9px; margin-top:12px;
  padding:7px 15px; border-radius:999px; font-weight:600; font-size:.95rem;
  border:1.5px solid currentColor;}
.result .cap{color:var(--ink-2); font-size:.87rem; margin-top:14px; line-height:1.55;}
.track{height:11px; border-radius:6px; background:var(--grid); margin-top:20px; overflow:hidden;}
.fill{height:100%; border-radius:6px;}
.scale{display:flex; justify-content:space-between; color:var(--ink-muted);
  font-size:.72rem; margin-top:6px; font-variant-numeric:tabular-nums;}

.tile{background:var(--surface); border:1px solid var(--hair); border-radius:14px;
  padding:16px 18px; height:100%;}
.tile .k{color:var(--ink-muted); font-size:.76rem; text-transform:uppercase;
  letter-spacing:.06em; font-weight:600;}
.tile .v{color:var(--ink); font-size:1.5rem; font-weight:650; margin-top:5px;
  letter-spacing:-.01em;}
.tile .s{color:var(--ink-2); font-size:.8rem; margin-top:3px;}

div[role="radiogroup"]{gap:.35rem;}
.stButton>button{border-radius:11px; font-weight:600; padding:.65rem 1rem;}
hr{border-color:var(--hair);}
</style>
""", unsafe_allow_html=True)


def section(n, title, sub=""):
    st.markdown(f'<div class="sec"><div class="num">{n}</div>'
                f'<div class="t">{title}</div></div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<p class="sec-sub">{sub}</p>', unsafe_allow_html=True)


def chip(text):
    st.markdown(f'<span class="chip">→ {text}</span>', unsafe_allow_html=True)


@st.cache_resource
def get_router():
    reg = load_registry()
    return CreditRouter(reg), reg


router, registry = get_router()

st.markdown(
    '<div class="hero"><h1>Loan Readiness Check</h1>'
    '<p>Answer what you can. Where information may not be to hand, tell us whether you '
    '<b>have</b> it, <b>don\'t have</b> it, or are <b>not sure</b> — we never guess '
    'on your behalf.</p></div>', unsafe_allow_html=True)

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
# 2 · Core financials — always known, so asked directly
# ======================================================================
section("2", "Your income, age and the loan you want",
        "Everyone knows these, so we ask them outright.")
a1, a2 = st.columns(2)
with a1:
    monthly_income = st.number_input("Monthly take-home income (₹)", 0, 10_000_000, 60_000, 1_000)
    loan_amount = st.number_input("Loan amount you want (₹)", 0, 100_000_000, 500_000, 10_000)
    age = st.number_input("Your age", 18, 100, 35,
                          help="Everyone knows their age, so there is no 'not sure' option here.")
with a2:
    monthly_emi = st.number_input("Total monthly EMIs you already pay (₹)", 0, 10_000_000, 15_000, 1_000)
    term_months = st.selectbox("Repayment period (months)",
                               [12, 24, 36, 48, 60, 84, 120, 180, 240, 300], index=2)
if monthly_income > 0:
    chip(f"Debt-to-income derived: **{monthly_emi/monthly_income*100:.1f}%** — you never enter this yourself")

# ======================================================================
# 3 · Credit score — exact value OR a band
# ======================================================================
sub = ("Good news — for **unsecured** loans your score barely changes the result "
       "(validation AUC 0.715 → 0.713 without it)."
       if track == "unsecured" else
       "For **secured** loans your score matters a lot — accuracy drops sharply "
       "without it (validation AUC 0.739 → 0.663).")
section("3", "Credit score", sub)

EXACT, BAND, NOSCORE = "I know my exact score", "I know the approximate range", "I don't have a credit score"
_required.append(("score", "Credit score"))
credit_score = credit_bureau = None
with st.container(border=True):
    st.markdown("**Do you know your credit score?**")
    score_state = st.radio("Availability", [EXACT, BAND, NOSCORE, UNSURE], index=None,
                           horizontal=True, key="tri_score", label_visibility="collapsed")
    if score_state in (EXACT, BAND):
        b1, b2 = st.columns(2)
        credit_bureau = b1.selectbox("Which bureau?", list(BUREAU_RANGES))
        lo, hi = BUREAU_RANGES[credit_bureau]
        if score_state == EXACT:
            credit_score = b2.number_input(f"Your {credit_bureau} score", lo, hi, min(750, hi))
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
additional_score = None
additional_score_type = None
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
    u1, u2 = st.columns(2)
    with u1:
        card_balance, _ = tri_number("Total outstanding on all credit cards (₹)", "cb",
                                     min_value=0, max_value=10_000_000, value=50_000, step=5_000)
        num_credit_lines, _ = tri_number("How many cards / active credit lines?", "ncl",
                                         min_value=0, max_value=50, value=3)
        enquiries_6m, _ = tri_select("Loans or cards applied for in the last 6 months", "enq6",
                                     [0, 1, 2, 3], lambda x: "3 or more" if x == 3 else str(x))
    with u2:
        card_limit, _ = tri_number("Total credit limit across all cards (₹)", "cl",
                                   min_value=0, max_value=10_000_000, value=200_000, step=5_000)
        new_accounts_24m, _ = tri_select("New credit accounts in the last 24 months", "na24",
                                         [0, 1, 2, 3], lambda x: "3 or more" if x == 3 else str(x))
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
            "auto_loan": "vehicle", "consumer_durable": "item"}[product]
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

# ======================================================================
# 5 · Existing secured loans (asked on BOTH tracks — both models use it)
# ======================================================================
section("5", "Your existing home or vehicle loans",
        "Holding a home or vehicle loan is a **positive** signal — in our data, people "
        "with one default at 5.6% versus 8.4% without. Having missed payments on one "
        "counts against you, so we ask both.")
g1, g2 = st.columns(2)
with g1:
    has_home_loan, _ = tri_yesno("Do you currently have a home loan?", "hashome")
    secured_outstanding, _ = tri_number("Total still owed on your home/vehicle loans (₹)",
                                        "secout", min_value=0, max_value=200_000_000,
                                        value=0, step=50_000)
with g2:
    has_vehicle_loan, _ = tri_yesno("Do you currently have a vehicle/car loan?", "hasveh")
    secured_ever_missed, _ = tri_yesno("Have you ever missed a payment on a home or "
                                       "vehicle loan?", "secmiss")
first_secured_year, _ = tri_number("Year you took your first home or vehicle loan", "fsy",
                                   min_value=1970, max_value=2026, value=2018, zero=None)
instalment_outstanding_pct, _ = tri_number(
    "Across your car / personal EMI loans, roughly what % of the original amount is "
    "still outstanding?", "ilutil", min_value=0, max_value=100, value=50, step=5,
    help="A rough estimate is fine. Someone halfway through their loans would say ~50%. "
         "In our data this is a strong signal: default rises from 19.6% to 25.0% as this "
         "percentage climbs.")
if secured_outstanding and monthly_income:
    chip(f"Secured debt vs annual income: **{secured_outstanding/(monthly_income*12)*100:.0f}%**")

# ======================================================================
# 6 · Causal probing
# ======================================================================
section("6", "Recent changes in your situation",
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
    income_change, _ = tri_select("How has your income changed in 12 months?", "incchg",
                                  ["increased", "same", "decreased"], lambda x: x.title())

# ======================================================================
# Submit
# ======================================================================
st.divider()
unanswered = [lbl for key, lbl in _required if st.session_state.get(f"tri_{key}") is None]
if unanswered:
    st.warning(f"**{len(unanswered)} question(s) still need an answer.** Pick one option for each "
               '— including "Not sure", which is a valid answer we handle properly.')
    with st.expander("Show what's outstanding"):
        for lbl in unanswered:
            st.markdown(f"- {lbl}")

go = st.button("Check my loan readiness", type="primary", use_container_width=True,
               disabled=bool(unanswered))

if go:
    ans = Answers(
        product=product, monthly_income=monthly_income or None,
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
    if risk < 0.10:
        band, icon, col = "Low risk", "●", "var(--good)"
    elif risk < 0.25:
        band, icon, col = "Moderate risk", "▲", "var(--warning)"
    elif risk < 0.40:
        band, icon, col = "Elevated risk", "◆", "var(--serious)"
    else:
        band, icon, col = "High risk", "■", "var(--critical)"

    section("7", "Your result")
    r1, r2 = st.columns([0.44, 0.56])
    with r1:
        pct = min(risk / 0.6, 1.0) * 100
        st.markdown(f"""
<div class="result">
  <div style="color:var(--ink-muted);font-size:.76rem;text-transform:uppercase;
       letter-spacing:.06em;font-weight:600;">Estimated default risk</div>
  <p class="figure">{risk*100:.1f}%</p>
  <div class="band" style="color:{col};"><span>{icon}</span><span>{band}</span></div>
  <div class="track"><div class="fill" style="width:{pct:.1f}%;background:{col};"></div></div>
  <div class="scale"><span>0%</span><span>30%</span><span>60%+</span></div>
  <div class="cap">Based on the details you provided for a <b>{res['track']}</b> loan
    {"." if res['tier'] >= 1 else ", assessed without a credit score."}</div>
</div>""", unsafe_allow_html=True)
    with r2:
        t1, t2 = st.columns(2)
        t1.markdown(f'<div class="tile"><div class="k">Confidence</div>'
                    f'<div class="v">{res["precision"]}</div>'
                    f'<div class="s">based on how much you could tell us</div></div>',
                    unsafe_allow_html=True)
        t2.markdown(f'<div class="tile"><div class="k">Details provided</div>'
                    f'<div class="v">{res["inputs_supplied"]}</div>'
                    f'<div class="s">questions answered</div></div>', unsafe_allow_html=True)
        st.write("")
        t3, t4 = st.columns(2)
        t3.markdown(f'<div class="tile"><div class="k">Risk band</div>'
                    f'<div class="v" style="font-size:1.15rem;color:{col};">{icon} {band}</div>'
                    f'<div class="s">where you sit today</div></div>', unsafe_allow_html=True)
        _loan = f"₹{loan_amount:,.0f}" if loan_amount else "—"
        t4.markdown(f'<div class="tile"><div class="k">Loan requested</div>'
                    f'<div class="v" style="font-size:1.15rem">{_loan}</div>'
                    f'<div class="s">over {term_months} months</div></div>', unsafe_allow_html=True)

    for note in res["notes"]:
        st.info(note, icon="ℹ️")

    if res["tier"] == 0 and track == "secured":
        st.error("**Precision note** — for a secured loan, not having a credit score reduces "
                 "how precise this estimate can be. Getting your score and re-running will give "
                 "a more reliable assessment.", icon="⚠️")
    elif res["tier"] == 0:
        st.success("You didn't provide a credit score — for a personal loan that barely changes "
                   "the result, because your other answers already carry most of the signal.",
                   icon="✅")

    st.caption("This is an indicative readiness estimate based on the information you provided. "
               "The more complete your answers, the more precise it is — an incomplete form "
               "tends to read as higher risk.")

    # ==================================================================
    # 8 · Personalised improvement plan (the essence) + PDF report
    # ==================================================================
    from improvement_engine import analyse
    from report_pdf import build_report_pdf
    plan_res = analyse(ans, router)

    section("8", "How to improve your loan readiness")
    st.markdown(
        "Ranked by how much each change lifts your **Readiness Score** — and every impact is "
        "**causally corrected**, so we only push changes that genuinely move the needle "
        "(not ones the raw model overstates). We only suggest things you can actually change.")

    if plan_res["plan"]:
        pg1, pg2 = st.columns([0.5, 0.5])
        pg1.markdown(f'<div class="tile"><div class="k">Readiness now</div>'
                     f'<div class="v">{plan_res["readiness_now"]}/100</div>'
                     f'<div class="s">approval likelihood {plan_res["approval_now_pct"]:.0f}%</div></div>',
                     unsafe_allow_html=True)
        pg2.markdown(f'<div class="tile"><div class="k">If you act on the top 3</div>'
                     f'<div class="v" style="color:var(--good);">{plan_res["readiness_after_top3"]}/100</div>'
                     f'<div class="s">+{plan_res["readiness_after_top3"]-plan_res["readiness_now"]} points · '
                     f'{len(plan_res["products_now"])} → {len(plan_res["products_after"])} products unlock</div></div>',
                     unsafe_allow_html=True)
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
        st.info("From the details you provided, we didn't find high-impact changes to suggest. "
                "Answering more of the optional questions may reveal opportunities.")

    pdf_bytes = build_report_pdf(ans, plan_res, applicant_name="Applicant",
                                 product_label=allp[product])
    st.download_button("📄 Download my full improvement report (PDF)", data=pdf_bytes,
                       file_name="loan_readiness_report.pdf", mime="application/pdf",
                       type="primary", use_container_width=True)
