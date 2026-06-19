# Equity Researcher A2A — System Description

## The Solution

**Equity Researcher A2A** is an AI-native equity research platform that automates the entire analytical cycle — from data collection to final report generation — by combining six specialized intelligent agents that collaborate through the **A2A (Agent-to-Agent)** protocol, enriched by a RAG retriever that injects internal knowledge base context into the analysis.

The system takes a list of stock tickers as input (e.g. `AAPL MSFT UCG.MI`) and, fully automatically, collects market fundamentals, processes real-time financial news, retrieves relevant internal documents, identifies the candidates with the highest potential, assesses risk across five quantitative dimensions, and generates a professional report with executive summary, base/bull/bear scenarios, and comparative scoring.

The architecture decomposes a traditional monolithic CrewAI pipeline into **6 independent FastAPI microservices** communicating via JSON-RPC 2.0 over HTTP, plus a local RAG retriever module. All agents use the **Anthropic SDK natively** with a custom ReAct loop (`shared/react.py`), without intermediate frameworks.

---

## Benefits

**Speed and scalability**
The full research pipeline — which would require hours of analytical work if done manually — completes in minutes. Each agent is an autonomous microservice: it can be scaled, replaced, or updated independently without affecting the others.

**Complete information coverage**
The system simultaneously aggregates data from heterogeneous sources — RSS feeds from Reuters, Yahoo Finance, MarketWatch, and Investing.com for news, and a certified market data provider for fundamentals (Fase 5 pending — DEMO_MODE in local dev) — ensuring that no relevant information is lost due to time constraints or limited human attention.

**Quality and reproducibility**
Every claim in the report is traceable: news items are identified by unique codes (N1, N2…) and explicitly cited for each candidate. An automated QA pass verifies calculation consistency (scoring, analyst consensus) and formal correctness before the report is delivered.

**Guardrails and risk control**
The Risk Assessor includes hardcoded conditional constraints: it refuses to produce a score if volatility data (52-week range, P/E ratio) is missing, preventing risk assessments based on incomplete data.

**Separation of concerns**
Each agent has a clearly defined area of responsibility. Changing the model, framework, or data source on a single agent requires no modifications to the others — the A2A interface is the stable contract.

**Extensibility**
The LangGraph orchestrator allows new agents (Portfolio Manager, Macro Agent, Earnings Calendar) to be added by modifying only the graph definition, without touching the logic of existing nodes.

---

## Key Uses

| Use case | Description |
|----------|-------------|
| **Automated morning briefing** | Run the pipeline at market open to receive an up-to-date report on a predefined ticker universe, with no manual intervention |
| **Pre-earnings screening** | Analyse candidates in Technology, AI, and Banking sectors ahead of earnings seasons, with company-specific investment theses and catalysts |
| **Portfolio review** | Feed an existing portfolio's tickers to get an updated risk profile assessment and analyst consensus changes |
| **Thematic research** | Identify the best stocks exposed to a specific market theme (e.g. AI, semiconductors) by cross-referencing recent news and fundamentals |
| **Architectural prototyping** | Use the project as a template for A2A multi-agent patterns: native ReAct loop, LangGraph orchestration, HMAC inter-agent auth, structured audit trail |
| **Training and education** | Study in practice the ReAct, Chain of Thought, Feedback Loop, Conditional Constraints, and Structured Output patterns on a concrete use case |

---

## Overview

## How to Start the System

```bash
# 1. Install dependencies
uv sync

# 2. Start all 6 agents (each in a separate terminal, or use start.sh)
uv run python agents/data-collector/data_collector.py       # port 8001
uv run python agents/news-sentiment/news_sentiment.py       # port 8002
uv run python agents/fundamental-analyst/fundamental_analyst.py  # port 8003
uv run python agents/risk-assessor/risk_assessor.py         # port 8004
uv run python agents/report-writer/report_writer.py         # port 8009
uv run python agents/portfolio-manager/portfolio_manager.py # port 8010

# 3. Run the pipeline
uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI --mode full

# Save report to file
uv run python orchestrator/main.py --tickers AAPL MSFT --output report.json
```

No `.env` file — env vars are injected by the platform (ECS, Lambda) or set in the shell locally. For local dev use `DEMO_MODE=true` (no LLM calls). See CLAUDE.md for the full variable reference.

---

## General Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATOR                              │
│                   LangGraph StateGraph v3                         │
│                                                                   │
│  PipelineState (TypedDict) — accumulated data                     │
│                                                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                          │
│  │  [1]     │ │  [2]     │ │  [RAG]   │  ← parallel fan-out      │
│  │Data      │ │News      │ │RAG       │                           │
│  │Collector │ │Sentiment │ │Retriever │                           │
│  │:8001     │ │:8002     │ │[local]   │                           │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘                          │
│       └────────────┴────────────┘                                 │
│                        ↓ fan-in                                   │
│               ┌────────────────┐                                  │
│               │  [3] Fundmt.   │                                  │
│               │  Analyst :8003 │                                  │
│               └───────┬────────┘                                  │
│                       ↓                                           │
│               ┌───────────────┐   ┌────────────────┐             │
│               │ [4] Risk      │ → │ [5] Report     │             │
│               │ Assessor :8004│   │ Writer  :8009  │             │
│               └───────────────┘   └───────┬────────┘             │
│                                           ↓ (mode=full)          │
│                              ┌────────────────────┐              │
│                              │ [6] Portfolio Mgr  │              │
│                              │ :8010              │              │
│                              └────────────────────┘              │
└──────────────────────────────────────────────────────────────────┘
              ↑  Communication via JSON-RPC 2.0 over HTTP  ↑
```

The pipeline uses **parallel fan-out**: DataCollector, NewsSentiment, and RAGRetriever run concurrently and fan-in at FundamentalAnalyst. The orchestrator uses **LangGraph** (`StateGraph`) — adding or reconfiguring branches only requires modifying `_build_graph_builder()` in `orchestrator/main.py`, without touching node logic.

---

## The A2A Protocol

All messages between the orchestrator and agents follow **JSON-RPC 2.0**:

```json
// Request (Orchestrator → Agent)
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "params": {
    "id": "task-uuid",
    "message": {
      "role": "user",
      "parts": [
        {"type": "text",  "text": "Fetch data for AAPL, MSFT"},
        {"type": "data",  "data": {"candidates": [...]}}
      ]
    }
  },
  "id": 1
}

// Response (Agent → Orchestrator)
{
  "jsonrpc": "2.0",
  "result": {
    "id": "task-uuid",
    "status": "completed",
    "message": {
      "role": "agent",
      "parts": [
        {"type": "text", "text": "Fundamentals fetched."},
        {"type": "data", "data": {"fundamentals": [...]}}
      ]
    }
  },
  "id": 1
}
```

Messages can contain **text** parts (free-form text) and **data** parts (structured dictionaries). Structured data always travels as `DataPart`. Pydantic models are defined in `shared/a2a_models.py`.

Each agent also exposes:
- `GET /.well-known/agent.json` — Agent Card for discovery
- `GET /health` — liveness check

---

## The 5 Agents in Detail

### [1] Data Collector — port 8001
**Framework:** Anthropic SDK native ReAct | **Model:** `claude-haiku-4-5-20251001`

Receives the ticker list from the orchestrator, calls `fetch_fundamentals` individually for each one, and returns a JSON array of fundamentals. Market data comes from a certified provider (Fase 5 pending — stub raises `NotImplementedError` in non-demo mode).

Data returned per ticker: current price, P/E TTM, forward P/E, EPS TTM, 52-week range, market cap, average analyst target, analyst count, recommendation, buy/hold/sell breakdown, sector.

### [2] News & Sentiment — port 8002
**Framework:** Anthropic SDK native ReAct | **Model:** `claude-haiku-4-5-20251001`

Reads 6 financial RSS feeds (Reuters, Yahoo Finance, MarketWatch, Investing.com) via `read_financial_rss`. Selects the 10–12 most relevant articles for priority sectors, assigns each a unique ID (N1, N2, …), and clusters them into 3–4 macro market themes.

**Included sectors:** Technology, AI, Software, Semiconductors, Banking, Financial Services.  
**Excluded sectors:** energy, utilities, real estate, REITs, consumer staples, industrials, airlines, crypto/DeFi/Web3.

Output: JSON object `{"news": [...], "themes": [...]}`.

### [3] Fundamental Analyst — port 8003
**Framework:** Anthropic SDK native ReAct | **Model:** `claude-sonnet-4-6`

Receives news, themes, and pre-fetched fundamentals from the previous step. Identifies up to 3 equity candidates that best fit the market themes, calls `fetch_fundamentals` to verify and enrich data, and builds a company-specific investment thesis (not just macro commentary).

Output: JSON array of candidates with ticker, thesis, catalyst, supporting news IDs, fundamentals, and analyst consensus.

### [4] Risk Assessor — port 8004
**Framework:** Anthropic SDK native ReAct with Conditional Constraints | **Model:** `claude-sonnet-4-6`

Receives the candidates identified by the Fundamental Analyst. For each candidate:

1. **Guardrail** — calls `check_volatility_data` to verify that the 52-week range and P/E are available. If missing: the candidate receives `"quality": "insufficient_data"` and all scores are set to 0.
2. **Scenarios** — produces company-specific base/bull/bear analysis.
3. **Scoring** — evaluates 5 dimensions from 1 to 10 (maximum 50 total):
   - `catalyst_strength` — strength of the specific catalyst
   - `horizon_fit` — consistency with the investment time horizon
   - `narrative_asymmetry` — upside vs downside narrative potential
   - `evidence_quality` — quality and specificity of supporting evidence
   - `crowding_risk` — risk of crowded positioning

### [5] Report Writer — port 8009
**Framework:** Anthropic SDK direct | **Model:** `claude-sonnet-4-6` (report + QA)

Produces the final report in two steps:

**Step 1 — Report generation** (`max_tokens=16000`):
- Receives candidates, risk assessment, news, and themes
- Produces a document with two marked sections:
  - `=== EXECUTIVE SUMMARY ===` — maximum 10 lines, neutral tone, no buy/sell directives
  - `=== JSON ===` — full structure according to `_REPORT_SCHEMA`

**Step 2 — QA review** (same model, `max_tokens=2048`):
- Checks: JSON schema compliance, news citations, no explicit buy/sell, scoring correctness, Italian language, consistent dates
- Responds with `QA: [APPROVED|CORRECTED]` and optionally `=== CORRECTIONS ===`

---

## Roadmap

| Version | Description |
|---------|-------------|
| v3 orchestrator | LangGraph v3 active — parallel fan-out (DataCollector + NewsSentiment + RAGRetriever), circuit breaker, checkpointing, graceful degradation |
| RAG v1 | TF-IDF keyword retrieval on `data/rag/documents/` (11 synthetic docs). Upgrade path: embedding-based with Bedrock Titan on pgvector/ChromaDB |
| Fase 5 | Certified data provider (Refinitiv LSEG / Bloomberg B-PIPE), RSS commercial license verification, PostgreSQL upgrade |
| Future agents | Earnings Calendar, Macro Agent |

---

## Problems This Application Solves

Traditional equity research suffers from three structural problems that Equity Researcher A2A directly addresses.

**The cost of analytical time.** A complete analysis of three or four stocks — gathering news, reading fundamentals, building a thesis, running scenario analysis, writing the report — requires a human analyst between two and four hours of focused work. The system reduces this to minutes, freeing the professional for higher-judgment activities: validating theses, comparing against their own market view, and making the final decision.

**The fragmentation of information sources.** The data relevant to an investment decision is scattered: prices and fundamentals on financial platforms, news on RSS feeds and specialist sites, analyst consensus on proprietary terminals. Tracking all these sources in parallel is impossible without dedicated tooling, and the risk is making decisions on incomplete information. The system automatically aggregates all these sources into a single coherent cycle, ensuring the picture is complete.

**The lack of structure and traceability in informal analysis.** Many stock assessments remain as disorganised notes, messaging chats, or improvised spreadsheets — hard to review, impossible to compare over time, and lacking any format that makes it possible to understand why a thesis proved right or wrong. Equity Researcher A2A produces a structured and repeatable output: every claim cites its source (news code N1, N2…), every candidate has a score across five dimensions, and every thesis explicitly includes a falsification trigger — the condition that, if it occurred, would invalidate the thesis itself. This makes analysis reviewable, comparable, and useful for building a more rigorous decision-making process over time.
