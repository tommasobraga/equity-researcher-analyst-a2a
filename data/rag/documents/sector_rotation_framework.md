# Sector Rotation Framework
**Doc-type:** Internal Methodology | **Version:** 1.3 | **Date:** March 2026
**Classification:** Internal Use Only | **Owner:** Strategy Desk

---

## 1. Overview

This framework guides tactical asset allocation between sectors as a function of the macroeconomic cycle, earnings revision momentum, and relative valuation. It is applied by the PortfolioManager agent when adjusting sector weights.

---

## 2. Cycle Phase Model

We classify the economic cycle into four phases based on ISM Manufacturing, yield curve slope (2s10s), and credit spreads (IG OAS):

| Phase | ISM | 2s10s | IG OAS | Preferred Sectors |
|---|---|---|---|---|
| Early Recovery | Rising, <50 | Steepening | Tightening | Financials, Materials, Industrials |
| Mid Expansion | >50, rising | Flat/moderate | Tight | Technology, Consumer Discretionary, Financials |
| Late Cycle | >55, rolling over | Flat/inverting | Widening | Energy, Healthcare, Defensives |
| Recession | <50, falling | Inverted | Wide | Healthcare, Utilities, Cash |

**Current phase (Q2 2026):** Mid Expansion transitioning to Late Cycle. ISM Manufacturing at 48.3 (below 50), but Services ISM at 53.1 and labour market resilient. Yield curve (2s10s) at +28bps (uninverted since January 2026). IG OAS at 85bps (tight but off lows).

**Implication:** We are in a Late Mid Expansion environment. Our overweight in Technology/AI is consistent with the cycle phase but we are monitoring for transition signals.

---

## 3. Sector Scores — Current (April 2026)

| Sector | Cycle Score | Valuation Score | Earnings Momentum | Overall | Stance |
|---|---|---|---|---|---|
| Technology / AI | 8 | 5 | 9 | 22/30 | OVERWEIGHT |
| Semiconductors | 8 | 5 | 9 | 22/30 | OVERWEIGHT |
| European Banking | 6 | 8 | 6 | 20/30 | OVERWEIGHT |
| Healthcare | 5 | 7 | 5 | 17/30 | NEUTRAL |
| Consumer Discretionary | 5 | 5 | 5 | 15/30 | NEUTRAL |
| Industrials | 4 | 6 | 4 | 14/30 | UNDERWEIGHT |
| Energy | 3 | 6 | 3 | 12/30 | EXCLUDED* |
| Utilities | 3 | 7 | 3 | 13/30 | EXCLUDED* |

*Excluded per Investment Policy Statement regardless of cycle score.

---

## 4. Rotation Triggers

We reassess sector allocation when any of the following triggers are met:

1. **ISM Manufacturing crosses 50** (either direction) — potential phase transition
2. **Yield curve (2s10s) inverts > -20bps** — late cycle signal, reduce cyclicals
3. **IG OAS widens > 150bps** — risk-off rotation to defensives
4. **Sector earnings revisions turn negative for 2 consecutive months** — reduce that sector
5. **Relative P/E premium > 2SD above 5-year average** — trim that sector regardless of fundamentals

---

## 5. AI Sector Special Consideration

The AI investment cycle does not map cleanly onto the traditional sector rotation framework. AI infrastructure capex is relatively insensitive to the economic cycle because:
- It is driven by competitive necessity (hyperscalers cannot afford to fall behind on AI capability)
- It is funded from operating cash flows, not debt (reducing interest rate sensitivity)
- The workloads (training runs, inference) are non-discretionary once deployed

We therefore apply an "AI overlay" that allows us to maintain Technology overweight even in Late Cycle / Early Recession phases, provided:
- AI capex guidance from the top-5 hyperscalers remains positive
- Hyperscaler operating margins remain above 30%
- NVDA gross margin does not decline below 65%
