"""
credit_engine.py — Product router + feature-derivation layer.

Implements faculty points 6, 7, 9 (and the availability half of 1 & 3):

  * Point 6/9 : a customer's PRODUCT TYPE routes to exactly ONE model
                (unsecured -> LendingClub, secured -> Home Credit proxy).
                The old 0.55*HC + 0.45*LC blend is NOT used.
  * Point 7   : the portal collects RAW answerable inputs; this module DERIVES
                the model features (dti, revol_util, LTV, ...). A derived
                feature is produced only if EVERY raw input it needs is present.
  * Point 1/3 : availability is explicit. Missing inputs stay missing (NaN) and
                are reported, never silently zero-filled. Score availability
                selects a tier, and tier selects which trained model is used.

Design evidence (see PHASE1_RESULTS.md):
  - External scores are NOT reconstructable (R^2 0.05-0.14) => a missing score
    needs a SEPARATELY TRAINED tier-0 model, not imputation.
  - Score missingness is itself predictive (OR ~ 1.3) => availability flags are
    passed to the model as features.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional
import math

# ----------------------------------------------------------------------
# Product routing
# ----------------------------------------------------------------------
UNSECURED_PRODUCTS = {
    "personal_loan": "Personal loan",
    "balance_transfer": "Credit-card balance transfer",
    "consumer_durable": "Consumer-durable loan",   # no collateral taken -> unsecured
    "education_loan": "Education loan",
}
SECURED_PRODUCTS = {
    "home_loan": "Home loan",
    "loan_against_property": "Loan against property",
    "auto_loan": "Auto loan",
}

def route_product(product_key: str) -> str:
    """Return 'unsecured' or 'secured' for a product key. Raises on unknown."""
    if product_key in UNSECURED_PRODUCTS:
        return "unsecured"
    if product_key in SECURED_PRODUCTS:
        return "secured"
    raise ValueError(
        f"Unknown product '{product_key}'. "
        f"Valid: {sorted(UNSECURED_PRODUCTS) + sorted(SECURED_PRODUCTS)}"
    )

# ----------------------------------------------------------------------
# Categorical encodings — SHARED by training and serving
# ----------------------------------------------------------------------
# Defined once here and imported by train_routed_models.py so the codes the model
# learned can never drift from the codes the portal sends.

# LendingClub `purpose` — strong signal: small_business 29.7% default vs wedding 12.2%
LC_PURPOSE_CODES = {
    "debt_consolidation": 0, "credit_card": 1, "home_improvement": 2,
    "major_purchase": 3, "medical": 4, "small_business": 5, "car": 6,
    "vacation": 7, "moving": 8, "house": 9, "wedding": 10, "educational": 11,
    "renewable_energy": 12, "other": 13,
}
# What the portal offers -> the LendingClub purpose it maps to
UI_PURPOSE_TO_LC = {
    "debt_consolidation": "debt_consolidation", "credit_card_refinance": "credit_card",
    "home_improvement": "home_improvement", "major_purchase": "major_purchase",
    "medical": "medical", "business": "small_business", "car": "car",
    "travel": "vacation", "moving": "moving", "wedding": "wedding",
    "education": "educational", "other": "other",
}

# Home Credit `NAME_INCOME_TYPE` — Unemployed 36% vs State servant 5.8% default
HC_INCOME_TYPE_CODES = {
    "Working": 0, "Commercial associate": 1, "Pensioner": 2, "State servant": 3,
    "Unemployed": 4, "Student": 5, "Businessman": 6, "Maternity leave": 7,
}
# India-relevant portal options -> Home Credit income type
UI_EMPLOYMENT_TO_HC = {
    "salaried_private": "Working",
    "government_psu": "State servant",       # lowest observed default (5.8%)
    "self_employed": "Commercial associate",
    "business_owner": "Commercial associate",  # HC 'Businessman' has only 10 rows
    "retired": "Pensioner",
    "not_working": "Unemployed",
}
# Human labels for the portal
UI_EMPLOYMENT_LABELS = {
    "salaried_private": "Salaried — private sector",
    "government_psu": "Government / PSU employee",
    "self_employed": "Self-employed / professional",
    "business_owner": "Business owner",
    "retired": "Retired / pensioner",
    "not_working": "Not currently working",
}
UI_PURPOSE_LABELS = {
    "debt_consolidation": "Debt consolidation", "credit_card_refinance": "Credit-card refinance",
    "home_improvement": "Home improvement", "major_purchase": "Major purchase",
    "medical": "Medical", "business": "Business", "car": "Car",
    "travel": "Travel", "moving": "Moving / relocation", "wedding": "Wedding",
    "education": "Education", "other": "Other",
}


def encode_purpose(ui_value):
    """Portal purpose -> LendingClub purpose code. None if not supplied."""
    if ui_value is None:
        return None
    lc = UI_PURPOSE_TO_LC.get(ui_value)
    return LC_PURPOSE_CODES.get(lc) if lc else None


def encode_employment(ui_value):
    """Portal employment type -> Home Credit income-type code. None if not supplied."""
    if ui_value is None:
        return None
    hc = UI_EMPLOYMENT_TO_HC.get(ui_value)
    return HC_INCOME_TYPE_CODES.get(hc) if hc else None


# ----------------------------------------------------------------------
# Bureau score normalisation (Point 1)
# ----------------------------------------------------------------------
# LendingClub's score is genuinely FICO. Indian bureaus use different ranges,
# so we map any of them onto the FICO 300-850 scale the LC model was trained on.
BUREAU_RANGES = {
    "FICO":      (300, 850),
    "CIBIL":     (300, 900),
    "Experian":  (300, 900),
    "Equifax":   (300, 900),
    "CRIF":      (300, 900),
}

def normalise_bureau_score(score: Optional[float], bureau: Optional[str]) -> Optional[float]:
    """Map a bureau score onto the FICO 300-850 scale. None if unavailable."""
    if score is None or bureau is None:
        return None
    if bureau not in BUREAU_RANGES:
        raise ValueError(f"Unknown bureau '{bureau}'. Valid: {sorted(BUREAU_RANGES)}")
    lo, hi = BUREAU_RANGES[bureau]
    score = max(lo, min(hi, float(score)))          # clamp to the bureau's range
    frac = (score - lo) / (hi - lo)
    f_lo, f_hi = BUREAU_RANGES["FICO"]
    return f_lo + frac * (f_hi - f_lo)


# --- currency normalisation ------------------------------------------------
# The front end collects Indian Rupees, but the models were trained on foreign
# books: LendingClub in USD, Home Credit in an anonymised currency. RAW AMOUNT
# features must therefore be converted to each model's trained scale before
# scoring. RATIO features (DTI, utilisation, LTV, credit/annuity-to-income) are
# currency-invariant and are computed from the raw Rupee figures unchanged.
# This is a documented approximation, disclosed in the UI — the model is
# foreign-calibrated, so absolute-amount effects are indicative, not exact.
INR_PER_USD = 83.0                                 # LendingClub (USD)
# Home Credit's currency is anonymised, so no true FX exists. We anchor a typical
# Indian annual income (~Rs 6,00,000) onto Home Credit's median income (~147,150
# units), preserving loan-to-income ratios. Documented approximation.
HC_AMOUNT_SCALE = 147150.0 / 600000.0              # ~0.245  (Rupees -> HC units)


def _to_usd(x):
    return None if x is None else x / INR_PER_USD


def _to_hc(x):
    return None if x is None else x * HC_AMOUNT_SCALE

# ----------------------------------------------------------------------
# Raw questionnaire answers
# ----------------------------------------------------------------------
@dataclass
class Answers:
    """Raw, human-answerable inputs. Every field may be None = 'I don't have this'."""
    # Section 0
    product: str = "personal_loan"
    # Section A - common core
    monthly_income: Optional[float] = None            # A.1
    monthly_emi_existing: Optional[float] = None      # A.2
    credit_score: Optional[float] = None              # A.4
    credit_bureau: Optional[str] = None               # A.4 (which bureau)
    additional_score: Optional[float] = None          # A.5 (anonymous extra score)
    first_credit_year: Optional[int] = None           # A.6
    loan_amount: Optional[float] = None               # A.7
    term_months: Optional[int] = None                 # A.8
    expected_roi: Optional[float] = None              # annual % the customer expects; the ONLY rate
                                                      # the affordability utility uses (no assumed rate)
    employment_type: Optional[str] = None             # A.9
    education: Optional[str] = None                   # A.10
    age: Optional[float] = None                       # A.11
    dependents: Optional[int] = None                  # A.12
    # Section B - unsecured only
    card_balance: Optional[float] = None              # B.1
    card_limit: Optional[float] = None                # B.2
    num_credit_lines: Optional[int] = None            # B.3
    enquiries_6m: Optional[int] = None                # B.4
    new_accounts_24m: Optional[int] = None            # B.5
    missed_payment_2y: Optional[bool] = None          # B.6
    has_default_record: Optional[bool] = None         # B.7
    loan_purpose: Optional[str] = None                # B.8
    housing: Optional[str] = None                     # B.9
    # Section C - secured only
    asset_type: Optional[str] = None                  # C.1
    asset_value: Optional[float] = None               # C.2
    down_payment: Optional[float] = None              # C.3
    owns_property: Optional[bool] = None              # C.4
    owns_car: Optional[bool] = None                   # C.5
    coapplicant_income: Optional[float] = None        # C.7
    enquiries_12m: Optional[int] = None               # C.8
    asset_scenario: Optional[str] = None              # 'purchase' | 'mortgage_existing'
    # Existing SECURED-loan history — asked of every customer, both tracks.
    # These come from the bureau record at training time, and the customer can
    # answer all of them directly.
    has_home_loan: Optional[bool] = None
    has_vehicle_loan: Optional[bool] = None
    secured_outstanding: Optional[float] = None       # total still owed on secured loans
    secured_ever_missed: Optional[bool] = None        # ever missed a secured EMI
    first_secured_year: Optional[int] = None          # year of first secured loan
    instalment_outstanding_pct: Optional[float] = None  # % of car/personal EMI loans left
    # Household composition
    family_total: Optional[int] = None                # total members in family
    earning_members: Optional[int] = None             # how many of them earn
    children: Optional[int] = None                    # number of children
    additional_score_type: Optional[str] = None       # which kind of extra score
    # Section D - causal probing (Point 8)
    employed_12m_continuous: Optional[bool] = None    # D.1
    job_change_6m: Optional[bool] = None              # D.2
    income_change_12m: Optional[str] = None           # D.3 increased/same/decreased
    missed_emi_6m: Optional[bool] = None              # D.4
    large_unplanned_expense: Optional[bool] = None    # D.5
    income_outlook_6m: Optional[str] = None           # D.6
    income_sources: Optional[str] = None              # D.7 single/multiple

# ----------------------------------------------------------------------
# Derivation result
# ----------------------------------------------------------------------
@dataclass
class DerivedProfile:
    track: str                                  # 'unsecured' | 'secured'
    tier: int                                   # 0,1,2 - score availability
    features: Dict[str, Any] = field(default_factory=dict)
    provided: Dict[str, bool] = field(default_factory=dict)
    missing_inputs: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Share of model features successfully derived."""
        if not self.provided:
            return 0.0
        return sum(self.provided.values()) / len(self.provided)

    def precision_label(self) -> str:
        c = self.coverage
        if self.tier >= 1 and c >= 0.80:
            return "High"
        if c >= 0.55:
            return "Medium"
        return "Low"

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _amortised_installment(principal: float, annual_rate_pct: float, months: int) -> float:
    """Standard EMI formula."""
    if months <= 0:
        return float("nan")
    r = annual_rate_pct / 100.0 / 12.0
    if r == 0:
        return principal / months
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)

def _rate_from_score(fico: Optional[float]) -> float:
    """Rough pricing curve: better score -> cheaper. Used only to derive EMI."""
    if fico is None:
        return 14.0
    if fico >= 760: return 8.0
    if fico >= 720: return 9.5
    if fico >= 680: return 11.0
    if fico >= 640: return 13.0
    if fico >= 600: return 16.0
    return 19.0


def affordability(ans: "Answers", ceiling: float = 0.50):
    """Serviceability check on the APPLIED loan — a business rule, separate from the ML risk model.

    The risk model is trained on US personal loans (max ~$40k) and cannot judge gross
    unaffordability: a very large loan falls outside its range, so its risk score saturates.
    Lenders never rely on the score alone for this — they apply a debt-to-income ceiling. This
    computes the PROPOSED (applied) DTI = (existing EMIs + the new loan's EMI) / income, a verdict,
    and the largest loan the income could comfortably service at that ceiling.
    """
    # Serviceability is a HOUSEHOLD question, so it uses combined income (applicant + co-applicant).
    # The risk model is untouched — derive() reads monthly_income only and ignores coapplicant_income.
    inc = (ans.monthly_income or 0.0) + (ans.coapplicant_income or 0.0)
    rate = ans.expected_roi   # the ONLY rate used — the utility assumes no interest rate of its own
    if inc <= 0 or not ans.loan_amount or not ans.term_months or rate is None or rate < 0:
        return None
    new_emi = _amortised_installment(ans.loan_amount, rate, int(ans.term_months))
    existing = ans.monthly_emi_existing or 0.0
    proposed_dti = (existing + new_emi) / inc
    existing_dti = existing / inc
    # largest loan whose EMI keeps total DTI at/under the ceiling
    max_new_emi = max(0.0, ceiling * inc - existing)
    r = rate / 12.0 / 100.0
    n = int(ans.term_months)
    if max_new_emi <= 0:
        max_loan = 0.0
    elif r > 0:
        max_loan = max_new_emi * (1 - (1 + r) ** (-n)) / r
    else:
        max_loan = max_new_emi * n
    # no_capacity = existing EMIs already use up (or exceed) the chosen DTI level -> no room at all.
    no_capacity = max_new_emi <= 0
    # Verdict is purely RELATIVE to the DTI level the USER supplies — we make no assumption about
    # what any lender allows. (This affordability arithmetic is entirely separate from the ML risk
    # model, which is unchanged; it neither feeds the model nor alters the risk score.)
    if proposed_dti <= ceiling:
        verdict = "within"
        note = (f"Your proposed debt-to-income of {proposed_dti*100:.0f}% is within the "
                f"{ceiling*100:.0f}% level you set.")
    elif no_capacity:
        verdict = "exceeds"
        note = (f"Your existing EMIs alone are {existing_dti*100:.0f}% of income — already at or "
                f"beyond the {ceiling*100:.0f}% level you set, so there is no room for a new loan on "
                "this income. Adding a co-applicant's income, or clearing some existing EMIs, would "
                "create capacity.")
    else:
        verdict = "exceeds"
        note = (f"Your proposed debt-to-income of {proposed_dti*100:.0f}% exceeds the "
                f"{ceiling*100:.0f}% level you set — the EMIs would take more of your income than "
                "that. A smaller loan amount or a longer term would bring it within.")
    return {"new_emi": new_emi, "existing_emi": existing, "existing_dti": existing_dti,
            "proposed_dti": proposed_dti, "verdict": verdict, "note": note,
            "no_capacity": no_capacity, "max_serviceable_loan": max_loan,
            "ceiling": ceiling, "rate_pct": rate}


def affordability_table(ans: "Answers", ceilings=(0.50, 0.60, 0.70, 0.80, 0.90, 1.00)):
    """The loan an income could service across a RANGE of debt-to-income levels — a neutral
    reference (we do not assume which level any lender applies). A higher level simply means the
    EMIs take a larger share of income; levels above 100% require EMIs beyond a single income."""
    inc = (ans.monthly_income or 0.0) + (ans.coapplicant_income or 0.0)   # combined household income
    rate = ans.expected_roi   # the ONLY rate used — the utility assumes no interest rate of its own
    if inc <= 0 or not ans.term_months or rate is None or rate < 0:
        return None
    existing = ans.monthly_emi_existing or 0.0
    r = rate / 12.0 / 100.0
    n = int(ans.term_months)
    rows = []
    for c in ceilings:
        max_new_emi = max(0.0, c * inc - existing)
        if max_new_emi <= 0:
            max_loan = 0.0
        elif r > 0:
            max_loan = max_new_emi * (1 - (1 + r) ** (-n)) / r
        else:
            max_loan = max_new_emi * n
        rows.append({"ceiling": c, "max_new_emi": max_new_emi, "max_loan": max_loan})
    return {"rate_pct": rate, "term_months": n, "existing_emi": existing, "income": inc, "rows": rows}


def _safe_div(a, b):
    """Divide only when BOTH inputs exist and denominator is non-zero."""
    if a is None or b is None:
        return None
    try:
        if float(b) == 0:
            return None
        return float(a) / float(b)
    except (TypeError, ValueError):
        return None

def _tier_from_scores(primary: Optional[float], extra: Optional[float]) -> int:
    if primary is not None and extra is not None:
        return 2
    if primary is not None:
        return 1
    return 0

# ----------------------------------------------------------------------
# The derivation layer (Point 7)
# ----------------------------------------------------------------------
def derive(ans: Answers, today: Optional[date] = None) -> DerivedProfile:
    """Turn raw questionnaire answers into a model-ready feature dict."""
    today = today or date.today()
    track = route_product(ans.product)

    fico = normalise_bureau_score(ans.credit_score, ans.credit_bureau)
    tier = _tier_from_scores(fico, ans.additional_score)

    prof = DerivedProfile(track=track, tier=tier)
    f, p = prof.features, prof.provided

    def put(name, value):
        f[name] = value
        p[name] = value is not None
        if value is None:
            prof.missing_inputs.append(name)

    # --- shared derivations -------------------------------------------------
    annual_inc = ans.monthly_income * 12 if ans.monthly_income is not None else None
    dti = _safe_div(ans.monthly_emi_existing, ans.monthly_income)   # needs BOTH
    rate = _rate_from_score(fico)
    installment = (
        _amortised_installment(ans.loan_amount, rate, ans.term_months)
        if ans.loan_amount is not None and ans.term_months else None
    )
    credit_history_years = (
        today.year - ans.first_credit_year if ans.first_credit_year else None
    )

    # --- causal probing features (Point 8), shared across tracks ------------
    # employed_12m IS trainable in both books (LC emp_length, HC DAYS_EMPLOYED);
    # the rest have no historical proxy and feed the counselling layer only.
    put("employed_12m", _b(ans.employed_12m_continuous))
    put("job_change_6m", _b(ans.job_change_6m))
    put("income_decreased_12m",
        None if ans.income_change_12m is None else int(ans.income_change_12m == "decreased"))
    put("income_increased_12m",
        None if ans.income_change_12m is None else int(ans.income_change_12m == "increased"))
    put("missed_emi_6m", _b(ans.missed_emi_6m))

    # --- existing SECURED-loan history (shared) -----------------------------
    # Holding secured credit is a positive signal (default 8.36% -> 5.57%);
    # having been overdue on it is a negative one (5.48% -> 6.80%).
    sec_years = (today.year - ans.first_secured_year) if ans.first_secured_year else None

    if track == "unsecured":
        # Raw amounts -> USD (the LC training scale); ratios stay currency-invariant.
        put("annual_inc", _to_usd(annual_inc))
        put("dti", dti * 100 if dti is not None else None)   # LC stores DTI as %
        put("fico_score", fico)
        put("loan_amnt", _to_usd(ans.loan_amount))
        put("term_months", float(ans.term_months) if ans.term_months else None)
        put("installment", _to_usd(installment))
        put("payment_to_income", _safe_div(installment, ans.monthly_income))
        put("revol_util", _pct(_safe_div(ans.card_balance, ans.card_limit)))
        put("revol_bal", _to_usd(ans.card_balance))
        put("open_acc", _n(ans.num_credit_lines))
        put("inq_last_6mths", _n(ans.enquiries_6m))
        put("acc_open_past_24mths", _n(ans.new_accounts_24m))
        put("delinq_2yrs", _b(ans.missed_payment_2y))
        put("pub_rec", _b(ans.has_default_record))
        put("credit_history_years", credit_history_years)
        put("home_mortgage", None if ans.housing is None else int(ans.housing == "paying_home_loan"))
        put("home_own", None if ans.housing is None else int(ans.housing == "own_outright"))
        # Loan purpose is a genuine LendingClub feature with a very wide spread
        # (small_business 29.7% default vs wedding 12.2%).
        put("purpose_code", encode_purpose(ans.loan_purpose))
        # LendingClub's secured-credit signals (Flag-1 fix, Aug 2026).
        # `mort_acc` (US 'number of mortgage accounts') and `mortgage_bal` (a US-dollar balance)
        # were trained on US LendingClub values. For a mortgage HOLDER, the rupee->USD balance and
        # the yes/no->1 account count do NOT transfer — they landed in a risk-ADDING region and
        # CANCELLED the protective, reliable `home_mortgage` (housing) signal, so ticking "I have a
        # home loan" wrongly nudged risk up. Fix: for a holder, leave these two mis-scaling features
        # MISSING (LightGBM uses its learned missing-branch); the mortgage-protective effect then flows
        # correctly through `home_mortgage`. Non-holders keep a clean 0. See ROUTED_MODELS / Flag 1.
        put("mort_acc", 0.0 if ans.has_home_loan is False else None)
        _mb = 0.0 if ans.has_home_loan is False else None
        put("mortgage_bal", _to_usd(_mb))
        put("mortgage_bal_to_income", _safe_div(_mb, annual_inc))
        put("il_util", ans.instalment_outstanding_pct)
    else:  # secured
        # Raw amounts -> Home Credit's (anonymised) training scale; ratios stay invariant.
        put("AMT_INCOME_TOTAL", _to_hc(annual_inc))
        put("AMT_CREDIT", _to_hc(ans.loan_amount))
        put("AMT_ANNUITY", _to_hc(installment * 12) if installment is not None else None)
        put("AMT_GOODS_PRICE", _to_hc(ans.asset_value))
        put("EXT_SOURCE_2", _unit(fico))                 # primary score slot
        put("EXT_SOURCE_3", _unit_raw(ans.additional_score))
        put("AGE_YEARS", ans.age)
        put("education_ordinal", _edu(ans.education))
        # Income type carries strong signal in Home Credit: Unemployed 36% default,
        # Working 9.6%, State servant 5.8%.
        put("income_type_code", encode_employment(ans.employment_type))
        # --- household composition -------------------------------------
        # `family_total` maps 1:1 onto Home Credit's CNT_FAM_MEMBERS.
        #
        # Dependants: people who rely on the applicant's income. Home Credit has
        # no "dependants" column, so this maps onto CNT_CHILDREN — children are
        # the dominant category of dependant in that book, and the model learned
        # dependency_ratio as children / family size. Serving the customer's
        # stated dependant count is the closest honest analogue.
        #
        # NOTE: we deliberately do NOT model a second earner by inflating income.
        # Tested and it moves risk the wrong way (HC's AMT_INCOME_TOTAL is the
        # applicant's own income, so a household figure is out of distribution).
        # Additional earners belong in the affordability layer, not the risk model.
        dependants = ans.dependents if ans.dependents is not None else ans.children
        put("CNT_CHILDREN", _n(dependants))
        put("CNT_FAM_MEMBERS", _n(ans.family_total))
        dep = None
        if ans.family_total and dependants is not None and ans.family_total > 0:
            dep = min(1.0, dependants / ans.family_total)
        put("dependency_ratio", dep)
        put("CREDIT_INCOME_RATIO", _safe_div(ans.loan_amount, annual_inc))
        put("ANNUITY_INCOME_RATIO", _safe_div(installment, ans.monthly_income))
        put("LTV", _safe_div(ans.loan_amount, ans.asset_value))
        put("own_realty", _b(ans.owns_property))
        put("own_car", _b(ans.owns_car))
        put("AMT_REQ_CREDIT_BUREAU_YEAR", _n(ans.enquiries_12m))
        # --- existing secured-loan history (bureau-derived at training time) ---
        _hm = _b(ans.has_home_loan)
        _hv = _b(ans.has_vehicle_loan)
        put("sec_has_mortgage", _hm)
        put("sec_has_car", _hv)
        put("sec_n", None if (_hm is None and _hv is None) else (_hm or 0) + (_hv or 0))
        put("sec_ever_overdue", _b(ans.secured_ever_missed))
        put("sec_history_years", sec_years)
        put("sec_debt_to_income", _safe_div(ans.secured_outstanding, annual_inc))
        # A down payment only exists when the customer is BUYING the asset. In a
        # loan-against-property the asset is already owned, so there is none.
        put("down_payment_ratio",
            _safe_div(ans.down_payment, ans.asset_value)
            if ans.asset_scenario != "mortgage_existing" else None)

    # --- availability flags are FEATURES, not just UI state (Point 3) -------
    f["score_missing"] = int(fico is None)
    p["score_missing"] = True
    f["extra_score_missing"] = int(ans.additional_score is None)
    p["extra_score_missing"] = True

    # --- notes for the UI ---------------------------------------------------
    if tier == 0:
        prof.notes.append(
            "No credit score provided - your estimate is based on your other answers, "
            "with a slightly wider margin for uncertainty."
        )
    if dti is None and (ans.monthly_income is None or ans.monthly_emi_existing is None):
        prof.notes.append(
            "Debt-to-income not computed: it needs BOTH monthly income and total "
            "monthly EMIs. It is left missing rather than half-estimated."
        )
    return prof

# --- small coercion helpers -------------------------------------------------
def _b(v):  return None if v is None else int(bool(v))
def _n(v):  return None if v is None else float(v)
def _pct(v): return None if v is None else v * 100.0

def _unit(fico):
    """Map FICO 300-850 onto the 0-1 scale the HC external scores use."""
    if fico is None:
        return None
    return max(0.0, min(1.0, (fico - 300.0) / 550.0))

def _unit_raw(score):
    """An anonymous extra score: accept 0-1 directly, else assume a 300-900 scale."""
    if score is None:
        return None
    s = float(score)
    if 0.0 <= s <= 1.0:
        return s
    return max(0.0, min(1.0, (s - 300.0) / 600.0))

_EDU_MAP = {"secondary": 1, "higher_secondary": 1, "graduate": 3, "post_graduate": 4}
def _edu(v): return None if v is None else _EDU_MAP.get(v)

# ----------------------------------------------------------------------
# Trained-model bundle + registry loader
# ----------------------------------------------------------------------
class ModelBundle:
    """Wraps a trained model saved by train_routed_models.py.

    Builds the model's input vector from a derived feature dict, using ONLY the
    features the model was trained on. Anything the customer could not supply
    stays NaN — LightGBM handles missing values natively at split time, which is
    exactly the behaviour Point 7 needs (partial questionnaires still score).
    """

    def __init__(self, payload: dict):
        self.model = payload["model"]
        self.feature_names = payload["feature_names"]
        self.oof_auc = payload.get("oof_auc")
        self.label = payload.get("label", "unknown")
        self.n_train = payload.get("n_train")

    def predict_proba(self, features: Dict[str, Any]) -> float:
        import pandas as pd
        row = {f: features.get(f, None) for f in self.feature_names}
        X = pd.DataFrame([row], columns=self.feature_names).astype(float)
        return float(self.model.predict_proba(X)[0, 1])

    def used_features(self, features: Dict[str, Any]) -> Dict[str, bool]:
        """Which of the model's inputs the customer actually supplied."""
        return {f: features.get(f) is not None for f in self.feature_names}


def load_registry(model_dir: Optional[str] = None) -> Dict[Any, "ModelBundle"]:
    """Load the four routed models into a registry keyed by (track, tier)."""
    import os, joblib
    model_dir = model_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "model_data")
    registry = {}
    for track in ("unsecured", "secured"):
        for tier in (0, 1):
            path = os.path.join(model_dir, f"{track}_tier{tier}.pkl")
            if os.path.exists(path):
                registry[(track, tier)] = ModelBundle(joblib.load(path))
    return registry


# ----------------------------------------------------------------------
# Router facade
# ----------------------------------------------------------------------
class CreditRouter:
    """Routes a customer to exactly one model. No blending (Point 6)."""

    def __init__(self, registry: Optional[Dict[str, Any]] = None):
        # registry keys: ('unsecured', tier) / ('secured', tier) -> model bundle
        self.registry = registry or {}

    def model_key(self, prof: DerivedProfile):
        # tier 2 shares the tier-1 model (extra score is an input, not a new model)
        return (prof.track, 1 if prof.tier >= 1 else 0)

    def predict(self, ans: Answers) -> Dict[str, Any]:
        prof = derive(ans)
        key = self.model_key(prof)
        bundle = self.registry.get(key)
        out = {
            "product": ans.product,
            "track": prof.track,
            "tier": prof.tier,
            "model_used": f"{key[0]}_tier{key[1]}",
            "coverage": round(prof.coverage, 3),
            "precision": prof.precision_label(),
            "missing_inputs": prof.missing_inputs,
            "notes": prof.notes,
            "features": prof.features,
        }
        if bundle is None:
            out["risk"] = None
            out["error"] = f"No trained model registered for {key}"
            return out
        used = bundle.used_features(prof.features)
        out["risk"] = float(bundle.predict_proba(prof.features))
        out["model_oof_auc"] = bundle.oof_auc
        out["inputs_supplied"] = f"{sum(used.values())}/{len(used)}"
        out["model_inputs_missing"] = [f for f, ok in used.items() if not ok]
        return out


# ----------------------------------------------------------------------
# Per-case counterintuitive-result self-check (runtime disclosure)
# ----------------------------------------------------------------------
# The routed models are gradient-boosted trees with NO monotonic constraint, and
# income/loan enter as raw US-dollar features on top of the correct affordability
# ratios. So for SOME individual profiles a change that "should" lower risk can
# nudge the estimate the other way by a small amount (all observed cases <= ~2.5pp;
# see the counterintuitive sweep in REPORT_DISCLOSURES §4.5). Rather than hide that,
# we probe the customer's OWN profile at prediction time and, if a lever behaves
# counterintuitively for THEM, hand the front end an exact, plain-English disclosure.

# Each lever: (field, human name, direction the customer would try to IMPROVE it,
# the multipliers/steps to probe in that improving direction).
_MONO_LEVERS = [
    ("monthly_income", "income",       "up",   "mult", (1.25, 1.5, 2.0)),
    ("loan_amount",    "loan amount",  "down", "mult", (0.8, 0.6, 0.5)),
    ("credit_score",   "credit score", "up",   "add",  (20.0, 40.0)),
]
# Fire the disclosure only above this size, so imperceptible tree-boundary noise
# (typically <= 0.2pp, e.g. the secured curves) never clutters the screen.
_MONO_MIN_PP = 0.3


def _mono_message(field, worst_pp, at_value, base_risk_pct, new_risk_pct):
    """Plain-English, case-specific explanation for one counterintuitive lever."""
    d = f"{worst_pp:.1f}"
    if field == "monthly_income":
        return {
            "lever": "income",
            "title": "Why earning more nudged your risk slightly up",
            "body": (
                f"For your profile, if your monthly income rose to about "
                f"₹{at_value:,.0f}, the model's estimated default risk edges **up** by "
                f"~{d} points (from {base_risk_pct:.1f}% to {new_risk_pct:.1f}%) instead of down. "
                "This is a known quirk, not a judgement that earning more is bad:\n\n"
                "- The model learned from **US** loan records, and your income band sits in a "
                "**thinly-covered part** of that data, so its estimate wobbles a little across "
                "nearby incomes rather than moving smoothly.\n"
                "- What genuinely matters — **how comfortably your income covers the EMI** — still "
                "improves with more income; you can see that in the affordability figures in "
                "Takeaway 3.\n"
                f"- The wobble is **small (~{d} points), within the model's margin of error**. We "
                "never treat 'earn less' as advice."),
        }
    if field == "loan_amount":
        return {
            "lever": "loan amount",
            "title": "Why asking for a smaller loan nudged your risk slightly up",
            "body": (
                f"For your profile, dropping the loan to about ₹{at_value:,.0f} moves the model's "
                f"estimated risk **up** by ~{d} points (from {base_risk_pct:.1f}% to "
                f"{new_risk_pct:.1f}%) instead of down. Why:\n\n"
                "- In the training data, **very small personal loans were charged off slightly more "
                "often** than mid-sized ones, so the model carries a mild bias in that direction.\n"
                "- A smaller loan still genuinely **lowers your EMI and repayment burden** — that "
                "real benefit shows up in the affordability figures in Takeaway 3, which the model "
                "does not touch.\n"
                f"- The effect is **small (~{d} points)** and does **not** reduce the size of loan "
                "you can afford."),
        }
    return {
        "lever": "credit score",
        "title": "A small quirk in how your credit score moved the estimate",
        "body": (
            f"For your profile, a higher credit score moved the model's estimated risk **up** by "
            f"~{d} points (from {base_risk_pct:.1f}% to {new_risk_pct:.1f}%) over a small range "
            "instead of down. This is a minor non-smoothness in the model near your inputs; the "
            "overall relationship between a better score and lower risk still holds, and the effect "
            f"is small (~{d} points, within the model's margin)."),
    }


def counterintuitive_disclosures(router: "CreditRouter", ans: "Answers"):
    """Probe the customer's OWN profile and return plain-English disclosures for any
    lever that behaves counterintuitively for them. Empty list = nothing to disclose.

    Cheap: a handful of extra predict() calls on perturbed copies of `ans`.
    """
    import dataclasses
    base = router.predict(ans).get("risk")
    if base is None:
        return []
    base_pct = base * 100.0
    out = []
    for field, _name, direction, kind, steps in _MONO_LEVERS:
        cur = getattr(ans, field, None)
        if cur is None or cur <= 0:
            continue
        worst = None  # (delta_pp, perturbed_value, new_pct)
        for s in steps:
            if kind == "mult":
                val = cur * s
            else:  # additive step, in the improving direction
                val = cur + s if direction == "up" else cur - s
            if field == "credit_score":
                val = min(val, 850.0)   # keep score in range
            if val <= 0:
                continue
            r = router.predict(dataclasses.replace(ans, **{field: val})).get("risk")
            if r is None:
                continue
            delta = r * 100.0 - base_pct   # >0 means risk went UP (the wrong way when improving)
            if delta >= _MONO_MIN_PP and (worst is None or delta > worst[0]):
                worst = (delta, val, r * 100.0)
        if worst is not None:
            out.append(_mono_message(field, worst[0], worst[1], base_pct, worst[2]))
    return out
