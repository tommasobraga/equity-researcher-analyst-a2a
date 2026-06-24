# Risk Scoring Methodology
**Doc-type:** Internal Methodology | **Version:** 2.1 | **Effective Date:** March 1, 2026
**Classification:** Internal Use Only | **Owner:** Risk Committee

---

## 1. Overview

This document defines the quantitative scoring framework applied by the RiskAssessor agent to all investment candidates. Scores feed directly into portfolio construction decisions by the PortfolioManager agent.

---

## 2. Five-Dimension Scoring Framework

Each candidate is scored across five dimensions, each on a scale of 0–10. Maximum total score: 50.

### Dimension 1: Revenue Growth (0–10)

| Score | Criteria |
|---|---|
| 9–10 | Revenue growth > 25% YoY with accelerating trajectory and expanding TAM |
| 7–8 | Revenue growth 15–25% with stable or improving mix |
| 5–6 | Revenue growth 5–15%, in line with sector average |
| 3–4 | Revenue growth 0–5% or declining from cyclical peak |
| 0–2 | Revenue decline or negative organic growth |

### Dimension 2: Profitability (0–10)

| Score | Criteria |
|---|---|
| 9–10 | Gross margin > 70%, free cash flow margin > 25%, FCF/NI conversion > 100% |
| 7–8 | Gross margin 50–70%, FCF margin 15–25% |
| 5–6 | Gross margin 30–50%, FCF margin 8–15% |
| 3–4 | Gross margin < 30% or FCF margin < 8% |
| 0–2 | Negative FCF or EBITDA |

### Dimension 3: Valuation (0–10)

| Score | Criteria |
|---|---|
| 9–10 | P/E < 15× or EV/FCF < 20× relative to growth; PEG < 0.8 |
| 7–8 | PEG 0.8–1.2, EV/Sales below sector median |
| 5–6 | PEG 1.2–1.8, in-line with sector |
| 3–4 | PEG 1.8–2.5, premium to sector without clear justification |
| 0–2 | PEG > 2.5, significant valuation premium with limited growth visibility |

### Dimension 4: Momentum (0–10)

| Score | Criteria |
|---|---|
| 9–10 | +3 or more consecutive quarters of EPS beats, positive guidance revision, RSI 50–70 |
| 7–8 | +2 consecutive EPS beats, flat to positive estimate revisions |
| 5–6 | Mixed beat/miss history, neutral estimate revisions |
| 3–4 | Recent EPS miss or guidance cut |
| 0–2 | Multiple consecutive misses or significant guidance reduction |

### Dimension 5: Risk (0–10)
**Higher score = lower risk.**

| Score | Criteria |
|---|---|
| 9–10 | Net cash, low regulatory risk, diversified customer base (top-5 < 30% revenue) |
| 7–8 | Net debt < 1.5× EBITDA, manageable regulatory exposure |
| 5–6 | Moderate leverage (1.5–3× EBITDA), some concentration risk |
| 3–4 | High leverage (> 3× EBITDA) or binary regulatory outcome pending |
| 0–2 | Distressed balance sheet, existential regulatory risk, or key-man dependency |

---

## 3. Investment Horizon Adjustment

The scoring framework is calibrated for a 12-week (medium-term) horizon by default. The RiskAssessor agent adjusts emphasis based on the horizon from TaskDecomposer:

| Horizon | Emphasis |
|---|---|
| Short (≤ 8 weeks) | Momentum and near-term catalysts dominate. Valuation is secondary. |
| Medium (9–26 weeks) | Balanced weighting across all five dimensions. |
| Long (> 26 weeks) | Growth and profitability (structural moats) dominate. Short-term momentum discounted. |

---

## 4. Minimum Thresholds for Inclusion

A candidate is eligible for portfolio inclusion only if:
- Total score ≥ 30 / 50
- Risk dimension score ≥ 4 / 10
- No single dimension score of 0

Candidates with total score 25–29 may be placed on a watchlist for the following cycle.
