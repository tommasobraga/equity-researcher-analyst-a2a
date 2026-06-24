# Valuation Framework — Technology & Software
**Doc-type:** Internal Methodology | **Version:** 2.0 | **Date:** March 2026
**Classification:** Internal Use Only | **Owner:** Research Desk Global Tech

---

## 1. Overview

This document defines the valuation methodology applied to technology and software companies in our coverage universe. Standard accounting metrics (P/E, EV/EBITDA) are insufficient for high-growth tech companies due to heavy capitalised R&D, stock-based compensation (SBC), and deferred revenue dynamics.

---

## 2. Primary Valuation Metrics by Subsector

### Cloud / SaaS
**Primary:** EV/NTM Revenue, Rule of 40 (Revenue Growth % + FCF Margin %)
**Secondary:** EV/FCF (non-GAAP), Price/NTM FCF

Rule of 40 benchmarks:
- Best-in-class: > 60 (e.g., MSFT Azure ~65, Cloudflare ~55)
- Acceptable: 40–60
- Requires explanation: < 40

### Semiconductors
**Primary:** EV/EBITDA (non-GAAP), P/NTM Earnings (non-GAAP)
**Secondary:** EV/Sales, Price/Book (for asset-heavy fabs)

Note: NVDA's non-GAAP excludes $4.2B annual SBC — use non-GAAP consistently but disclose GAAP gap.

### Semiconductor Equipment
**Primary:** EV/EBITDA, Order Book / Revenue (backlog coverage ratio)
**Secondary:** EV/FCF, P/E

ASML backlog of €48.3B at 1.6× annual revenue provides exceptional revenue visibility — a premium to spot EV/EBITDA is justified.

### European Banks
**Primary:** Price/Tangible Book Value (P/TBV), RoTE vs Cost of Equity spread
**Secondary:** Dividend yield, buyback yield (total capital return yield)

UCG trades at 1.2× TBV with 20.1% RoTE vs ~12% CoE (Bloomberg consensus). At peers' 1.0–1.1× TBV and lower RoTE, UCG's premium is justified.

---

## 3. Non-GAAP Adjustments — Standard Policy

We apply the following adjustments consistently across all coverage:

| Item | Treatment |
|---|---|
| Stock-based compensation (SBC) | Excluded from non-GAAP EPS; disclosed separately as % of revenue |
| Amortisation of acquired intangibles | Excluded |
| Restructuring charges (if non-recurring) | Excluded with disclosure |
| Litigation settlements > $200M | Excluded with disclosure |
| Unrealised gains/losses on equity investments | Excluded |

**SBC as % of revenue (FY2025):** MSFT 2.3%, NVDA 3.1%, AAPL 1.9%, ASML 1.8%.

---

## 4. DCF Framework

For terminal value estimation, we use the Gordon Growth Model:

**TV = FCF_{n+1} / (WACC - g)**

Standard assumptions:
- Terminal growth rate (g): 3.0% (in line with long-run nominal GDP)
- WACC: sector-adjusted (Tech large-cap: 9.0–10.5%; EU Banks: 11.0–12.5%)

**Sensitivity tables** are mandatory for any DCF-based price target. Minimum: 3×3 matrix (WACC ± 50bps, terminal growth ± 50bps).

---

## 5. Peer Comparables — Current Multiples (April 2026)

| Company | EV/NTM Sales | EV/NTM EBITDA | P/NTM FCF | RoTE |
|---|---|---|---|---|
| MSFT | 11.2× | 24.8× | 32.1× | 42% |
| NVDA | 18.4× | 27.3× | 35.6× | 68% |
| AAPL | 7.8× | 22.1× | 28.4× | 147%* |
| ASML | 9.6× | 21.3× | 26.8× | 34% |
| UCG.MI | 1.8× revenue | — | — | 20.1% |

*AAPL RoTE inflated by negative book equity from buybacks.

---

## 6. Valuation Red Flags

Automatic review required if:
- Forward P/E > 60× without > 30% EPS growth
- EV/Sales > 20× without clear path to > 30% FCF margin
- PEG > 3.0×
- Any company trading at > 10× its 5-year average P/E multiple without structural change justification
