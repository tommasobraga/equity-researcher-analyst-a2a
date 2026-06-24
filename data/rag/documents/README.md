# RAG Documents — Synthetic Demo Corpus

**All documents in this directory are entirely fictional and created for demonstration purposes only.**

They are not real financial research, not official company filings, and not investment advice. All figures, ratings, price targets, quotes, and financial data are invented and do not reflect actual company performance or analyst opinions.

## Purpose

These documents simulate the types of source material that a real deployment would ingest:

| Document type | Real-world equivalent |
|---|---|
| `*_10k_*` | SEC Form 10-K annual filings |
| `*_annual_report_*` | IFRS annual reports (EU companies) |
| `*_earnings_*_transcript` | Earnings call transcripts |
| `*_analyst_initiation_*` | Sell-side equity research initiation reports |
| `sector_note_*` | Internal sector research notes |
| `investment_policy_statement` | Internal investment policy |
| `risk_scoring_methodology` | Internal risk framework |
| `valuation_framework_*` | Internal valuation methodology |
| `esg_exclusion_policy` | Internal ESG policy |
| `macro_context_*` | Internal macro strategy notes |
| `sector_rotation_framework` | Internal tactical allocation framework |
| `coverage_universe_watchlist` | Internal coverage list |

## Real deployment

In production these files would be replaced with actual licensed source material (SEC EDGAR filings, Bloomberg/Refinitiv data, internal research) ingested via an embedding pipeline (Bedrock Titan → pgvector). The `shared/rag_retriever.py` interface is stable and does not need to change when the corpus is replaced.

---

*This project is a technical showcase. Nothing here constitutes financial advice.*
