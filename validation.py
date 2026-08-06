"""
validation.py — Cross-field coherence checks for the portal's questionnaire.

Sits in front of credit_engine.derive(): it judges whether the RAW answers can all
be true at once, before anything is derived or scored. Pure — no Streamlit — so it
can be exercised directly from a test script.

Why this layer exists
---------------------
The tri-state widgets already guarantee every question was ANSWERED. They cannot
tell whether the answers AGREE with one another. Without this module the portal
happily scores incoherent profiles:

  * zero income and a zero loan amount still return a confident-looking PD
  * a home loan for more than the property is worth (LTV 150%) passes silently
  * a card limit of 0 with a balance outstanding drops utilisation to missing,
    and the customer is never told a key input was discarded
  * a 25-year-old can claim 31 years of credit history

Two severities
--------------
ERROR    The answers are mutually impossible, or a value the arithmetic needs is
         absent. Submission is blocked — a score computed on this would be
         meaningless, and silently discarding the bad input is worse than asking.
WARNING  Unusual but genuinely possible. Submission proceeds; the customer is told
         what looked odd, and where the request falls outside the model's trained
         range they are told that too.

Business POLICY (typical lender LTV caps, DTI comfort levels) is always a warning,
never an error — this tool does not decide any lender's rules. Only arithmetic
impossibility blocks.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class Issue:
    """One validation finding. `label` is the bold lead-in, `detail` the explanation."""
    code: str
    label: str
    detail: str


# Typical Indian-lender loan-to-value caps, by product. Advisory only (warning).
LTV_CAPS = {"home_loan": 0.90, "loan_against_property": 0.75, "auto_loan": 0.85}

# An unsecured loan far beyond this multiple of monthly income sits outside the
# LendingClub training range (max ~$40k), where the model's score saturates.
UNSECURED_INCOME_MULTIPLE_LIMIT = 50

# Age by which a loan would normally have to be repaid.
REPAYMENT_AGE_LIMIT = 70


def _num(v) -> Optional[float]:
    """Coerce to float, treating None/blank/non-numeric as absent."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate(
    *,
    track: str,
    product: str,
    new_to_credit: bool = False,
    asset_scenario: Optional[str] = None,
    today: Optional[date] = None,
    # core financials
    monthly_income: Any = None,
    coapplicant_income: Any = None,
    monthly_emi: Any = None,
    loan_amount: Any = None,
    term_months: Any = None,
    expected_roi: Any = None,
    age: Any = None,
    # unsecured
    card_balance: Any = None,
    card_limit: Any = None,
    num_credit_lines: Any = None,
    first_credit_year: Any = None,
    # secured
    asset_value: Any = None,
    down_payment: Any = None,
    family_total: Any = None,
    dependents: Any = None,
    # shared
    first_secured_year: Any = None,
) -> Tuple[List[Issue], List[Issue]]:
    """Return (errors, warnings). Errors must block submission; warnings must not."""
    today = today or date.today()
    errors: List[Issue] = []
    warnings: List[Issue] = []
    E = lambda c, l, d: errors.append(Issue(c, l, d))
    W = lambda c, l, d: warnings.append(Issue(c, l, d))

    inc = _num(monthly_income)
    co_inc = _num(coapplicant_income) or 0.0
    emi = _num(monthly_emi) or 0.0
    loan = _num(loan_amount)
    term = _num(term_months)
    roi = _num(expected_roi)
    yrs = _num(age)
    household_inc = (inc or 0.0) + co_inc

    # ------------------------------------------------------------------
    # Core financials — every downstream figure depends on these
    # ------------------------------------------------------------------
    if not inc or inc <= 0:
        E("income_zero", "Monthly income is required",
          "Enter your monthly take-home income. Debt-to-income, affordability and the "
          "risk assessment are all computed from it — with zero income none of them "
          "can be produced.")

    if not loan or loan <= 0:
        E("loan_zero", "Loan amount is required",
          "Enter the amount you want to borrow. Your EMI, debt-to-income and "
          "loan-to-value all derive from it.")

    if roi is None:
        E("roi_missing", "Expected interest rate is required",
          "Pick the rate you expect or were quoted. It is the only rate this tool "
          "uses — without it the EMI and every affordability figure are skipped "
          "entirely, and you would get a result with that whole section missing.")

    # ------------------------------------------------------------------
    # Age consistency — a credit account cannot predate the applicant's 18th year
    # ------------------------------------------------------------------
    if yrs and yrs > 0:
        earliest = today.year - int(yrs) + 18          # year the applicant turned 18
        for value, code, what in (
            (first_credit_year, "fcy_before_18", "first loan or credit card"),
            (first_secured_year, "fsy_before_18", "first home or vehicle loan"),
        ):
            y = _num(value)
            if y and y < earliest:
                E(code, f"Year of your {what} is not possible",
                  f"You entered {int(y)}, but at age {int(yrs)} you turned 18 in "
                  f"{earliest} — you could not have held credit before then. Check "
                  "either the year or your age.")

        if term and term > 0:
            age_at_end = yrs + term / 12.0
            if age_at_end > REPAYMENT_AGE_LIMIT:
                W("term_past_retirement", "This loan runs well past normal retirement age",
                  f"You would be about {age_at_end:.0f} when the final EMI is due. Most "
                  f"lenders want the loan closed by around {REPAYMENT_AGE_LIMIT}, so a "
                  "shorter tenor or a younger co-applicant may be required.")

    # ------------------------------------------------------------------
    # Debt-to-income comfort (the >=100% case is surfaced separately, up top)
    # ------------------------------------------------------------------
    if household_inc > 0 and 0.50 <= emi / household_inc < 1.0:
        W("existing_dti_high", "Your existing EMIs already take half your income",
          f"Current EMIs are {emi / household_inc * 100:.0f}% of income before this new "
          "loan is added. Lenders treat this as limited headroom, so expect a smaller "
          "sanction than the amount requested.")

    # ------------------------------------------------------------------
    # Unsecured — credit-card coherence
    # ------------------------------------------------------------------
    if track == "unsecured" and not new_to_credit:
        bal, lim = _num(card_balance), _num(card_limit)
        lines = _num(num_credit_lines)

        if bal and bal > 0 and lim == 0:
            E("limit_zero_with_balance", "Card limit cannot be zero while a balance is outstanding",
              f"You entered a balance of ₹{bal:,.0f} against a total limit of ₹0. Card "
              "utilisation — one of the strongest signals in your assessment — cannot be "
              "computed from this, so it would be dropped without telling you. Enter your "
              'total limit, or select "I don\'t have any" for the balance if you hold no cards.')

        elif bal and lim and lim > 0:
            util = bal / lim
            if util > 2.0:
                E("util_impossible", "Card balance is far above your credit limit",
                  f"A balance of ₹{bal:,.0f} against a ₹{lim:,.0f} limit is "
                  f"{util * 100:.0f}% utilisation. Values this far over the limit are "
                  "almost always the two figures entered the wrong way round — please check.")
            elif util > 1.0:
                W("util_over_limit", "Your card balance exceeds your credit limit",
                  f"That is {util * 100:.0f}% utilisation. This is possible if you are over "
                  "limit, but confirm the figures — utilisation this high weighs heavily "
                  "against you.")
            elif util > 0.90:
                W("util_very_high", "Your cards are almost fully drawn",
                  f"Utilisation of {util * 100:.0f}% is among the strongest negative signals "
                  "in the assessment. Paying this down is usually the fastest single "
                  "improvement available to you.")

        if lines == 0 and bal and bal > 0:
            E("no_lines_with_balance", "You reported no credit lines but a balance outstanding",
              f"A balance of ₹{bal:,.0f} has to sit on at least one card. Correct the number "
              'of cards, or record the balance as "I don\'t have any".')

        if loan and inc and loan > UNSECURED_INCOME_MULTIPLE_LIMIT * inc:
            W("loan_out_of_range", "This loan is very large relative to your income",
              f"₹{loan:,.0f} is over {UNSECURED_INCOME_MULTIPLE_LIMIT}× your monthly income. "
              "Unsecured lending at this multiple is outside the range this assessment was "
              "built on, so treat the risk figure as indicative and rely on the "
              "affordability check below.")

    # ------------------------------------------------------------------
    # Secured — asset, down payment and loan-to-value must agree
    # ------------------------------------------------------------------
    if track == "secured":
        val, dp = _num(asset_value), _num(down_payment)
        buying = asset_scenario == "purchase"

        ltv_broken = False
        if val and val > 0 and loan and loan > 0:
            ltv = loan / val
            if ltv > 1.0:
                ltv_broken = True
                E("ltv_over_100", "Your loan is larger than the asset securing it",
                  f"You want ₹{loan:,.0f} against an asset worth ₹{val:,.0f} — a "
                  f"loan-to-value of {ltv * 100:.0f}%. A secured loan cannot exceed the "
                  "value of its own security. Check the asset value or reduce the amount.")
            else:
                cap = LTV_CAPS.get(product)
                if cap and ltv > cap:
                    W("ltv_above_cap", "Loan-to-value is above what lenders usually allow",
                      f"Yours is {ltv * 100:.0f}%; for this product lenders typically cap "
                      f"around {cap * 100:.0f}%. You would need roughly "
                      f"₹{(loan - cap * val):,.0f} more as down payment to come within it.")

        if buying and val and val > 0 and dp is not None:
            if dp >= val:
                E("dp_covers_asset", "Your down payment already covers the whole asset",
                  f"A down payment of ₹{dp:,.0f} against a ₹{val:,.0f} asset leaves nothing "
                  "to finance. Reduce the down payment, or check the asset value.")
            elif loan and loan > 0 and not ltv_broken:
                # Suppressed when the loan already exceeds the asset — the LTV error
                # above says the same thing, and repeating it just adds noise.
                gap = (dp + loan) - val
                if abs(gap) > 0.05 * val:
                    over = gap > 0
                    W("funding_mismatch", "Down payment plus loan doesn't match the asset value",
                      f"They come to ₹{dp + loan:,.0f} against an asset worth ₹{val:,.0f} — "
                      f"{'₹{:,.0f} more than needed'.format(gap) if over else '₹{:,.0f} short'.format(-gap)}. "
                      + ("Some buyers do borrow extra for stamp duty and registration; if that "
                         "is not your intention, adjust the amount."
                         if over else
                         "Check where the remaining amount is coming from."))

        fam, dep = _num(family_total), _num(dependents)
        if fam and dep is not None and fam > 1 and dep > fam - 1:
            E("dependents_exceed_family", "More dependents than family members",
              f"You listed {int(dep)} dependents in a household of {int(fam)}. You are not "
              f"your own dependent, so at most {int(fam) - 1} of them can depend on your "
              "income.")

    return errors, warnings


def summarise(errors: List[Issue], warnings: List[Issue]) -> str:
    """One-line headline for the submit area."""
    if errors:
        n = len(errors)
        return f"{n} answer{'s' if n > 1 else ''} needs correcting before we can assess you."
    if warnings:
        n = len(warnings)
        return f"{n} thing{'s' if n > 1 else ''} to double-check — you can still continue."
    return "Everything checks out."
