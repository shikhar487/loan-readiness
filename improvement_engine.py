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
from datetime import date
from typing import Optional, List, Dict, Any
from credit_engine import Answers, CreditRouter


def _score_howto(ans: Answers) -> List[str]:
    """Turn 'improve your credit score' into CONCRETE, personalised steps grounded in the
    same behaviours the model measures (which are exactly the drivers of a FICO/CIBIL score:
    payment history ~35%, amounts owed ~30%, history length ~15%, new credit ~10%, mix ~10%).
    Never suggests immutable factors (age, dependents). Only surfaces steps relevant to THIS
    applicant, so the advice is specific rather than generic."""
    steps: List[str] = []

    # 1) Repayment history — the single biggest factor
    if ans.missed_payment_2y or ans.has_default_record is True:
        s = ("Fix and protect your repayment record — the biggest single factor in a CIBIL score. "
             "Pay every EMI and card bill on time from now on (set auto-pay)")
        if ans.has_default_record is True:
            s += ", and clear or formally dispute any default / write-off / settled account on your report."
        else:
            s += " so the recent missed payment is followed by a clean streak."
        steps.append(s)
    else:
        steps.append("Keep paying every EMI and credit-card bill on time — set auto-pay so you never "
                     "miss a due date. Repayment history is the single biggest score factor.")

    # 2) Credit utilisation — the fastest-moving factor
    if ans.card_balance and ans.card_limit and ans.card_balance > 0.30 * ans.card_limit:
        util = 100 * ans.card_balance / ans.card_limit
        steps.append(f"Bring your credit-card usage from ~{util:.0f}% down below 30% of your limit "
                     "(pay down balances, or ask for a limit increase without spending more) — "
                     "utilisation is the fastest-moving score factor.")
    else:
        steps.append("Keep total credit-card usage under 30% of your limit every month — low, steady "
                     "utilisation is one of the strongest positives for the score.")

    # 3) Recent credit-seeking / enquiries
    if ans.enquiries_6m and ans.enquiries_6m > 0:
        steps.append(f"Avoid new loan or card applications for the next 6 months so your "
                     f"{int(ans.enquiries_6m)} recent hard enquiry(ies) age off — each fresh "
                     "application dips the score briefly.")
    else:
        steps.append("Space out any new credit applications — apply only when you genuinely need it, "
                     "since each hard enquiry temporarily lowers the score.")

    # 4) Credit age & mix / overall debt load
    if ans.instalment_outstanding_pct and ans.instalment_outstanding_pct > 40:
        steps.append("Pay down your car / personal EMI loans so less of the original amount is "
                     "outstanding — a lower overall debt load helps the score.")
    elif ans.first_credit_year and (date.today().year - ans.first_credit_year) < 5:
        steps.append("Keep your oldest card or loan open and active — a longer credit history and a "
                     "healthy mix of loan types lift the score gradually.")
    else:
        steps.append("Don't close your oldest card, keep a healthy mix of credit types, and check "
                     "your bureau (CIBIL) report once a year for errors you can dispute.")
    return steps

BASE = os.path.dirname(os.path.abspath(__file__))

# ---- SCCF causal factors (from dml_analysis.py) ---------------------------
def _load_sccf():
    p = os.path.join(BASE, "model_data", "causal_sccf.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"unsecured": {}, "secured": {}}
SCCF = _load_sccf()

# ---------------------------------------------------------------------------
# Score/approval mappings — TRACK-AWARE, recalibrated July 2026.
#
# Two separate customer-facing numbers, derived from the same calibrated PD:
#   * Readiness score (0-100): "how strong is your profile" — a smooth distance
#     from a track-specific worst-case PD. The denominator is set per track
#     (~3x the base default rate) so the score is sensitive in the range that
#     actually occurs on that track (unsecured PDs run ~4-48%, secured ~1-25%).
#   * Approval likelihood (%): "chance a lender says yes" — a logistic centred on
#     a track "borderline" PD (approval = 50% there). Steepness is set so a
#     genuinely low-risk applicant reaches ~99% (the OLD curve capped secured at
#     ~81%, which read as broken for a near-riskless borrower).
# ---------------------------------------------------------------------------
READINESS_PD_MAX = {"unsecured": 0.60, "secured": 0.30}   # PD that scores 0/100
APPROVAL_PD50    = {"unsecured": 0.30, "secured": 0.14}    # PD where approval = 50%

def _approval_steepness(track: str) -> float:
    # steepness k such that approval at PD=0 is ~0.99  ->  k = ln(99) / pd50
    return math.log(99.0) / APPROVAL_PD50[track]

def approval_chance(pd_val: float, track: str) -> float:
    c = APPROVAL_PD50.get(track, 0.30)
    k = _approval_steepness(track if track in APPROVAL_PD50 else "unsecured")
    return 1.0 / (1.0 + math.exp(k * (pd_val - c)))

def readiness_score(pd_val: float, track: str = "unsecured") -> int:
    """0-100 profile-strength score, scaled to the track's PD range."""
    pd_max = READINESS_PD_MAX.get(track, 0.60)
    return int(round(100 * (1 - min(pd_val / pd_max, 1.0))))

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
             title="Aim to improve your credit score — this has the biggest impact",
             detail="Work towards a higher score with on-time payments and low usage over 6-12 months.",
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
             title="Aim to improve your credit score — this has the biggest impact",
             detail="Work towards a higher score with on-time payments over 6-12 months.",
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
# Which KEY answerable inputs the customer left blank ("not sure" / skipped).
# Reported in plain language so a thin form explains itself instead of failing
# silently. These are the inputs that unlock the improvement levers above.
# ---------------------------------------------------------------------------
def _missing_answers(ans: Answers, track: str) -> List[str]:
    common = [
        (ans.credit_score is None,                              "your credit score"),
        (ans.monthly_emi_existing is None,                      "your total existing monthly EMIs"),
        (ans.first_credit_year is None,                         "the year of your first loan or card"),
    ]
    if track == "unsecured":
        items = common + [
            (ans.card_balance is None or ans.card_limit is None, "your credit-card balance and total limit"),
            (ans.enquiries_6m is None,                          "loan/card applications in the last 6 months"),
            (ans.missed_payment_2y is None,                     "whether you missed any payment in 2 years"),
            (ans.has_default_record is None,                    "whether you have any default / write-off record"),
        ]
    else:
        items = common + [
            (ans.asset_value is None,                           "the market value of the asset"),
            (ans.enquiries_12m is None,                         "credit enquiries in the last 12 months"),
            (ans.owns_property is None,                         "whether you already own property"),
            (ans.owns_car is None,                              "whether you own a car"),
        ]
    return [label for cond, label in items if cond]

# ---------------------------------------------------------------------------
def analyse(ans: Answers, router: CreditRouter, afford: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`afford` is the (optional) result of credit_engine.affordability(), computed against the
    DTI level the CUSTOMER chose. When the requested loan is not serviceable on the stated income,
    it becomes the HEADLINE recommendation — the risk model saturates on out-of-range amounts and
    cannot express gross unaffordability on its own, so we surface this arithmetic result instead."""
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
        readiness_gain = readiness_score(pd0 - causal_drop, track) - readiness_score(pd0, track)
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
            # 'Improve your score' is decomposed into concrete, personalised behavioural
            # steps (the actual drivers of a credit score) rather than a generic instruction.
            "how_steps": _score_howto(ans) if lv["key"] == "score" else [],
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

    # -- best PRACTICAL ceiling: apply EVERY applicable actionable lever (items 13/18) ------
    # This is the most a customer could reach by acting on things in their control; the gap
    # from here to 100 is "impractical" — driven by immutable factors (age, history length) or
    # simply beyond typical approval. Age/gender/dependents are never levers, so they stay fixed.
    best_ans, best_sccfs = ans, []
    for lv in levers:
        try:
            if lv["applicable"](best_ans):
                s = _sccf_for(track, lv["sccf_key"])
                nxt = lv["apply"](best_ans)
                if router.predict(nxt)["risk"] < router.predict(best_ans)["risk"]:  # only if it helps
                    best_ans = nxt
                    best_sccfs.append(s if s is not None else 1.0)
        except Exception:
            continue
    pd_best_raw = router.predict(best_ans)["risk"]
    best_avg_sccf = (sum(best_sccfs) / len(best_sccfs)) if best_sccfs else 1.0
    pd_best = pd0 - (pd0 - pd_best_raw) * min(best_avg_sccf, 1.0)
    approval_best_pct = round(approval_chance(pd_best, track) * 100)
    readiness_best = readiness_score(pd_best, track)

    # -- affordability-driven headline action (arithmetic, NOT the risk model) --------
    # If the requested loan cannot be serviced at the customer's chosen DTI level, that is the
    # single most useful recommendation — and one the saturated risk model cannot produce.
    afford_action = None
    if (afford and afford.get("verdict") == "exceeds"
            and ans.loan_amount and afford.get("max_serviceable_loan", 0) < ans.loan_amount):
        afford_action = {
            "requested": float(ans.loan_amount),
            "max_serviceable_loan": float(afford["max_serviceable_loan"]),
            "proposed_dti_pct": round(afford["proposed_dti"] * 100),
            "existing_dti_pct": round(afford.get("existing_dti", 0.0) * 100),
            "ceiling_pct": round(afford["ceiling"] * 100),
            "new_emi": float(afford["new_emi"]),
            "term_months": int(ans.term_months) if ans.term_months else None,
            "shortfall": float(ans.loan_amount - afford["max_serviceable_loan"]),
            "no_capacity": bool(afford.get("no_capacity")),
        }

    # -- completeness / coverage summary (Point 3) -----------------------------------
    missing_answers = _missing_answers(ans, track)
    coverage_pct = int(round((base.get("coverage") or 0) * 100))

    # -- always give a verdict: never a blank screen ---------------------------------
    # Priority: unaffordable request > missing-inputs prompt > genuinely-strong profile.
    fallback = None
    if not plan:
        if afford_action:
            fallback = {"kind": "affordability"}
        elif missing_answers:
            fallback = {"kind": "coverage", "missing": missing_answers}
        else:
            fallback = {"kind": "strong"}   # nothing left to improve on what we know

    return {
        "track": track,
        "pd_now": pd0,
        "pd_after_top3": pd_after,
        "pd_best": pd_best,
        "readiness_now": readiness_score(pd0, track),
        "readiness_after_top3": readiness_score(pd_after, track),
        "readiness_best": readiness_best,
        "approval_now_pct": round(approval_chance(pd0, track) * 100, 1),
        "approval_after_pct": round(approval_chance(pd_after, track) * 100, 1),
        "approval_best_pct": approval_best_pct,
        "products_now": products_qualified(pd0, track),
        "products_after": products_qualified(pd_after, track),
        "products_all": [n for n, _ in PRODUCTS[track]],
        "plan": plan,
        "afford_action": afford_action,
        "coverage_pct": coverage_pct,
        "inputs_supplied": base.get("inputs_supplied"),
        "missing_answers": missing_answers,
        "fallback": fallback,
        "immutable_note": ("Age, dependents, family size and credit-history length affect "
                           "your result but are not things we ask you to change."),
    }
