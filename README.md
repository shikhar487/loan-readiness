---
title: Loan Readiness Check
emoji: 🏦
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# Loan Readiness Check

Customer-facing credit-readiness portal for the capstone *From Predictive
Underwriting to Adaptive Credit Intelligence*.

## What it does

- **Routes by product type** — a secured vs unsecured choice sends the applicant to
  exactly one model (LendingClub for unsecured, Home-Credit-proxy for secured). No
  blending.
- **Availability-aware** — every optional input is tri-state (*have it / don't have any
  / not sure*); nothing is silently defaulted, and the report adapts to what the
  customer actually provides.
- **Derives features** — the form asks raw answerable questions (monthly income, EMIs,
  one credit score) and computes `dti`, `revol_util`, `LTV`, etc.
- **Score tiers** — works with an exact score, an approximate band, or no score at all
  (a separately trained tier-0 model, with an honest wider confidence band).

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit portal (the routed questionnaire + result) |
| `credit_engine.py` | Product router, derivation layer, model registry |
| `model_data/*.pkl` | Four trained LightGBM models (unsecured/secured × tier-0/1) |
| `requirements.txt` | Runtime dependencies |

The `.pkl` models are produced by `Routed_Models_Capstone.ipynb`; drop refreshed
copies into `model_data/` to update the deployed models.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

*Note: this Space runs the portal only. Model training and the report analyses
(logistic-regression tables, fairness, LIME/SHAP) live in the Colab notebook.*
