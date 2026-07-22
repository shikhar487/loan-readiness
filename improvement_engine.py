"""
improvement_engine.py — the "essence" layer.

Given a customer's answers and the routed model, it produces a ranked, causally-corrected
action plan: for each ACTIONABLE lever (never age/gender/dependents), how much the customer's
approval chance improves if they make that realistic change — discounted by the lever's
SHAP-Causal Correction Factor (SCCF) from the DML analysis, so we never oversell a change the
raw model overstates.

Outputs feed both the on-screen plan and the PDF report.
"""
from __future__ import annotations
import os, json, math, copy, dataclasses
from typing import Optional, List, Dict, Any
from credit_engine import Answers, CreditRouter

BASE = os.path.dirname(os.path.abspath(__file__))

# ---- SCCF causal factors (from dml_analysis.py) ---------------------------
def _load_sccf():
    p = os.path.join(BASE, "model_data", "causal_sccf.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"unsecured": {}, "secured": {}}
SCCF = _load_sccf()

# Approval-likelihood mapping: logistic in PD centred on a track "borderline" PD.
# At PD = centre, approval = 50%; lower PD -> higher approval.
APPROVAL_CENTRE = {"unsecured": 0.25, "secured": 0.12}
APPROVAL_STEEP = 12.0

def approval_chance(pd_val: float, track: str) -> float:
    c = APPROVAL_CENTRE[track]
    return 1.0 / (1.0 + math.exp(APPROVAL_STEEP * (pd_val - c)))

def readiness_score(pd_val: float) -> int:
    """0-100, aligned to the portal's 0-0.6 risk gauge."""
    return int(round(100 * (1 - min(pd_val / 0.6, 1.0))))

# ---- product eligibility (illustrative PD cutoffs) ------------------------
PRODUCTS = {
    "unsecured": [
        ("Personal loan", 0.30), ("Education loan", 0.35), ("Balance transfer", 0.20),
    ],
    "secured": [
        ("Auto loan", 0.15), ("Home loan", 0.10), ("Loan against property", 0.14),
        ("Consumer-durable loan", 0.20),
    ],
}
def products_qualified(pd_val: float, track: str):
    return [name for name, cut in PRODUCTS[track] if pd_val <= cut]

# ---- immutable factors (shown as context, NEVER suggested as changes) -----
IMMUTABLE = ["age", "gender", "dependents", "family_total", "education",
             "credit_history_years", "first_secured_year"]

# ---------------------------------------------------------------------------
# Actionable levers. Each `apply` returns a modified copy of Answers reflecting a
# realistic improving change. `sccf_key` links to the DML factor (None = behavioural
# lever with no causal estimate -> shown as "model-implied").
# ---------------------------------------------------------------------------
def _rep(ans, **kw):
    return dataclasses.replace(ans, **kw)

def _unsecured_levers():
    return [
        dict(key="utilisation", track="unsecured", sccf_key="revol_util",
             title="Pay your credit-card balances below 30%",
             detail="Bring total card usage under 30% of your limit.",
             effort="Medium", timeframe="1-3 months",
             applicable=lambda a: a.card_balance and a.card_limit and a.card_balance > 0.30 * a.card_limit,
             apply=lambda a: _rep(a, card_balance=0.30 * a.card_limit)),
        dict(key="dti", track="unsecured", sccf_key="dti",
             title="Reduce your monthly EMIs (pay off / consolidate a loan)",
             detail="Lower your existing EMI burden by about 10% of income.",
             effort="Medium", timeframe="3-6 months",
             applicable=lambda a: a.monthly_emi_existing and a.monthly_income and a.monthly_emi_existing > 0.05 * a.monthly_income,
             apply=lambda a: _rep(a, monthly_emi_existing=max(0, a.monthly_emi_existing - 0.10 * a.monthly_income))),
        dict(key="score", track="unsecured", sccf_key="fico_score",
             title="Improve your credit score by ~50 points",
             detail="On-time payments and low usage over 6-12 months.",
             effort="Long", timeframe="6-12 months",
             applicable=lambda a: a.credit_score is not None and a.credit_score < 800,
             apply=lambda a: _rep(a, credit_score=min((a.credit_score or 0) + 50, 900))),
        dict(key="enquiries", track="unsecured", sccf_key="inq_last_6mths",
             title="Avoid new loan/card applications for 6 months",
             detail="Let recent hard enquiries age off your file.",
             effort="Quick", timeframe="6 months (just wait)",
             applicable=lambda a: a.enquiries_6m and a.enquiries_6m > 0,
             apply=lambda a: _rep(a, enquiries_6m=0)),
        dict(key="instalment", track="unsecured", sccf_key="il_util",
             title="Pay down your car / personal EMI loans",
             detail="Reduce how much of those loans is still outstanding.",
             effort="Medium", timeframe="3-9 months",
             applicable=lambda a: a.instalment_outstanding_pct and a.instalment_outstanding_pct > 40,
             apply=lambda a: _rep(a, instalment_outstanding_pct=max(0, a.instalment_outstanding_pct - 30))),
        dict(key="default", track="unsecured", sccf_key=None,
             title="Clear or dispute a default / derogatory mark",
             detail="Settle or dispute any default, write-off or legal record.",
             effort="Medium", timeframe="1-3 months",
             applicable=lambda a: a.has_default_record is True,
             apply=lambda a: _rep(a, has_default_record=False)),
        dict(key="loan_amount", track="unsecured", sccf_key=None,
             title="Request a smaller loan amount",
             detail="Ask for about 15% less to ease the repayment burden.",
             effort="Quick", timeframe="immediate",
             applicable=lambda a: a.loan_amount and a.loan_amount > 50000,
             apply=lambda a: _rep(a, loan_amount=a.loan_amount * 0.85)),
    ]

def _secured_levers():
    return [
        dict(key="ltv", track="secured", sccf_key="LTV",
             title="Increase your down payment (lower loan-to-value)",
             detail="A bigger down payment reduces how much you borrow against the asset.",
             effort="Medium", timeframe="varies",
             applicable=lambda a: a.asset_value and a.loan_amount and a.loan_amount > 0.6 * a.asset_value,
             apply=lambda a: _rep(a, loan_amount=max(0.1 * a.asset_value, a.loan_amount - 0.10 * a.asset_value))),
        dict(key="emi", track="secured", sccf_key="ANNUITY_INCOME_RATIO",
             title="Reduce your existing monthly EMIs",
             detail="Lower other obligations to free up repayment capacity.",
             effort="Medium", timeframe="3-6 months",
             applicable=lambda a: a.monthly_emi_existing and a.monthly_income and a.monthly_emi_existing > 0.05 * a.monthly_income,
             apply=lambda a: _rep(a, monthly_emi_existing=max(0, a.monthly_emi_existing - 0.10 * a.monthly_income))),
        dict(key="borrow_less", track="secured", sccf_key="CREDIT_INCOME_RATIO",
             title="Borrow a smaller amount relative to income",
             detail="Request about 10% less.",
             effort="Quick", timeframe="immediate",
             applicable=lambda a: a.loan_amount and a.loan_amount > 100000,
             apply=lambda a: _rep(a, loan_amount=a.loan_amount * 0.90)),
        dict(key="score", track="secured", sccf_key="EXT_SOURCE_2",
             title="Improve your credit score",
             detail="Build score through on-time payments over 6-12 months.",
             effort="Long", timeframe="6-12 months",
             applicable=lambda a: a.credit_score is not None and a.credit_score < 800,
             apply=lambda a: _rep(a, credit_score=min((a.credit_score or 0) + 50, 900))),
        dict(key="enquiries", track="secured", sccf_key="AMT_REQ_CREDIT_BUREAU_YEAR",
             title="Avoid new credit enquiries",
             detail="Hold off on other applications for a few months.",
             effort="Quick", timeframe="a few months",
             applicable=lambda a: a.enquiries_12m and a.enquiries_12m > 0,
             apply=lambda a: _rep(a, enquiries_12m=0)),
    ]

def _sccf_for(track, key):
    if key is None:
        return None
    return SCCF.get(track, {}).get(key, {}).get("sccf")

def _confidence_badge(sccf):
    if sccf is None:
        return "Model-implied"        # no causal estimate (behavioural / secured gap)
    if sccf >= 0.50:
        return "High"
    if sccf >= 0.30:
        return "Medium"
    return "Low"

# ---------------------------------------------------------------------------
def analyse(ans: Answers, router: CreditRouter) -> Dict[str, Any]:
    base = router.predict(ans)
    track = base["track"]
    pd0 = base["risk"]
    levers = _unsecured_levers() if track == "unsecured" else _secured_levers()

    plan = []
    for lv in levers:
        try:
            if not lv["applicable"](ans):
                continue
            new_ans = lv["apply"](ans)
            pd_new = router.predict(new_ans)["risk"]
        except Exception:
            continue
        raw_drop = pd0 - pd_new                      # >0 = risk reduced (good)
        sccf = _sccf_for(track, lv["sccf_key"])
        # causal-corrected risk reduction
        causal_drop = raw_drop * sccf if sccf is not None else raw_drop
        # Readiness Score (0-100) is the headline "loan-readiness" metric: it moves
        # proportionally to risk reduction and does not saturate like approval chance.
        readiness_gain = readiness_score(pd0 - causal_drop) - readiness_score(pd0)
        appr_uplift = approval_chance(pd0 - causal_drop, track) - approval_chance(pd0, track)
        plan.append({
            "key": lv["key"], "title": lv["title"], "detail": lv["detail"],
            "effort": lv["effort"], "timeframe": lv["timeframe"],
            "raw_risk_drop_pp": round(raw_drop * 100, 2),
            "causal_risk_drop_pp": round(causal_drop * 100, 2),
            "readiness_gain_pts": readiness_gain,
            "approval_uplift_pp": round(appr_uplift * 100, 1),
            "sccf": sccf, "confidence": _confidence_badge(sccf),
            "sign_flip": bool(sccf is not None and raw_drop * causal_drop < 0),
        })

    # rank by causal readiness gain (desc); keep material ones (>= 1 readiness point)
    plan = [p for p in plan if p["readiness_gain_pts"] >= 1]
    plan.sort(key=lambda p: p["readiness_gain_pts"], reverse=True)

    # cumulative "if you did the top 3" (sequential re-scoring, with causal discount
    # applied to the combined raw reduction using an average SCCF of the applied levers)
    top3_keys = [p["key"] for p in plan[:3]]
    cum_ans, applied, sccfs = ans, [], []
    for lv in levers:
        if lv["key"] in top3_keys and lv["applicable"](cum_ans):
            cum_ans = lv["apply"](cum_ans)
            applied.append(lv["key"])
            s = _sccf_for(track, lv["sccf_key"])
            sccfs.append(s if s is not None else 1.0)
    pd_raw_after = router.predict(cum_ans)["risk"] if applied else pd0
    avg_sccf = (sum(sccfs) / len(sccfs)) if sccfs else 1.0
    pd_after = pd0 - (pd0 - pd_raw_after) * min(avg_sccf, 1.0)   # causal-discounted

    return {
        "track": track,
        "pd_now": pd0,
        "pd_after_top3": pd_after,
        "readiness_now": readiness_score(pd0),
        "readiness_after_top3": readiness_score(pd_after),
        "approval_now_pct": round(approval_chance(pd0, track) * 100, 1),
        "approval_after_pct": round(approval_chance(pd_after, track) * 100, 1),
        "products_now": products_qualified(pd0, track),
        "products_after": products_qualified(pd_after, track),
        "products_all": [n for n, _ in PRODUCTS[track]],
        "plan": plan,
        "immutable_note": ("Age, dependents, family size and credit-history length affect "
                           "your result but are not things we ask you to change."),
    }
