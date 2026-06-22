# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run a single agent (example: data-collector on port 8001)
uv run python agents/data-collector/data_collector.py

# Run all 6 agents (each in a separate terminal)
uv run python agents/data-collector/data_collector.py           # :8001
uv run python agents/news-sentiment/news_sentiment.py           # :8002
uv run python agents/fundamental-analyst/fundamental_analyst.py # :8003
uv run python agents/risk-assessor/risk_assessor.py             # :8004
uv run python agents/report-writer/report_writer.py             # :8009
uv run python agents/portfolio-manager/portfolio_manager.py     # :8010

# Run the Orchestrator API (director, requires all 6 agents running)
uv run python orchestrator/api.py                  # :8000

# Via CLI (bypasses orchestrator/api.py, same 3 modes)
uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI --mode full
uv run python orchestrator/main.py --tickers AAPL MSFT --mode analyze
uv run python orchestrator/main.py --mode portfolio

# Via CLI con prompt NL (task decomposition)
uv run python orchestrator/main.py --prompt "Analizza opportunità AI europee con orizzonte 3 mesi" --mode analyze
uv run python orchestrator/main.py --tickers AAPL MSFT --prompt "Confronta momentum post-earnings" --mode analyze

# Save output to file (CLI only)
uv run python orchestrator/main.py --tickers AAPL MSFT --mode analyze --output report.json

# Orchestrator API calls
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"tickers":["AAPL","MSFT"],"mode":"analyze"}'

# Con prompt NL
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"mode":"analyze","prompt":"Trova candidati nel settore semiconduttori europei"}'

curl http://localhost:8000/portfolio   # current portfolio state
curl http://localhost:8000/health      # aggregated health for all 6 agents

# Health check a running agent
curl http://localhost:8001/health

# Agent card discovery
curl http://localhost:8001/.well-known/agent.json
```

## Architecture

This is an **A2A (Agent-to-Agent)** multi-agent equity research system. The CrewAI pipeline was decomposed into 6 independent FastAPI services that communicate via **JSON-RPC 2.0 over HTTP**, orchestrated by a LangGraph director on port 8000.

### Three Workflows (selectable via `mode`)

```
mode=analyze:
  OrchestratorAPI(:8000)
    → router → data_collector(:8001) ─┐  (parallel fan-out)
             → news_sentiment(:8002)  ─┤
             → rag_retriever [local]  ─┴─► fundamental_analyst(:8003)
                                           → risk_assessor(:8004)
                                           → report_writer(:8009) → END

mode=portfolio:
  OrchestratorAPI(:8000)
    → router → portfolio_loader [SQLite] → portfolio_manager(:8010) → END

mode=full:
  OrchestratorAPI(:8000)
    → router → data_collector(:8001) ─┐  (parallel fan-out)
             → news_sentiment(:8002)  ─┤
             → rag_retriever [local]  ─┴─► fundamental_analyst(:8003)
                                           → risk_assessor(:8004)
                                           → report_writer(:8009)
                                           → portfolio_loader [SQLite]
                                           → portfolio_manager(:8010) → END
```

**Conditional edges:** fail-fast after `fundamental_analyst` if no candidates (skips risk_assessor, report_writer, portfolio branch). Routing is deterministic today (LLM-ready: functions receive full `PipelineState`, replace body with `react_loop()` call when cloud provider available).

The orchestrator (`orchestrator/main.py`) uses **LangGraph** (`StateGraph`). `PipelineState` (TypedDict) carries accumulated data across nodes. `orchestrator/api.py` wraps the pipeline as a FastAPI service on port 8000.

### A2A Protocol

`shared/a2a_models.py` defines the full wire format:
- **`JsonRpcRequest`** — wraps every call: `method="tasks/send"`, params contain an `A2ATask`
- **`A2ATask`** — `id` + `message` (list of `TextPart` and/or `DataPart`)
- **`A2ATaskResult`** — `id` + `status` (`completed|failed|working`) + `message`
- Structured data travels as `DataPart(data={key: value})` inside the message parts
- Use `A2ATaskResult.ok()` / `A2ATaskResult.fail()` factory methods in agents

### Agent anatomy

Every agent follows the same pattern:
1. `run_agent(task: A2ATask) -> A2ATaskResult` — core logic, called by the FastAPI handler
2. `POST /tasks` — receives `JsonRpcRequest`, validates method, delegates to `run_agent`
3. `GET /.well-known/agent.json` — serves the Agent Card for discovery
4. `GET /health` — liveness check

### Models in use

| Agent | Framework | Model | Port |
|---|---|---|---|
| DataCollector | Anthropic SDK (`shared/react.py`) | `claude-haiku-4-5-20251001` | 8001 |
| NewsSentiment | Anthropic SDK (`shared/react.py`) | `claude-haiku-4-5-20251001` | 8002 |
| FundamentalAnalyst | Anthropic SDK (`shared/react.py`) | `claude-sonnet-4-6` | 8003 |
| RiskAssessor | Anthropic SDK (`shared/react.py`) | `claude-sonnet-4-6` | 8004 |
| ReportWriter | Anthropic SDK direct | `claude-sonnet-4-6` (report + QA) | 8009 |
| PortfolioManager | Anthropic SDK direct | `claude-sonnet-4-6` | 8010 |

### Shared tools

- `shared/tools/yfinance_tool.py` — `get_stock_fundamentals(ticker)` / `get_stock_fundamentals_text(ticker)`. **Stub only** — yfinance removed (unofficial scraping, no commercial licence, MiFID II incompatible). Functions raise `NotImplementedError`. Certified provider integration (Refinitiv LSEG / Bloomberg B-PIPE / Alpha Vantage enterprise) planned for **Phase 5**. In `DEMO_MODE=true` these functions are never called.
- `shared/tools/rss_feed.py` — `fetch_rss_news()` reads RSS feeds (Reuters, Yahoo Finance, MarketWatch) with retry logic. Commercial licence to be verified in Phase 5.
- `shared/portfolio_db.py` — `init_db()` / `load_portfolio_state()` / `save_portfolio_state()`: SQLite persistence for the fictional portfolio (`output/portfolio.db`). Initial seed 100,000 USD. Natural upgrade to PostgreSQL in Phase 5/6.
- `data/rag/documents/` — 11 synthetic documents (investment policy, sector notes, scoring methodology, macro context, watchlist). Replace with real internal documentation when available.

### Native ReAct loop

All 4 agents with tool use (DataCollector, NewsSentiment, FundamentalAnalyst, RiskAssessor) implement the ReAct pattern (Reason → Act → Observe) directly with the Anthropic SDK tool_use, without intermediate frameworks. The logic lives in `shared/react.py` (`react_loop()`). Each `stop_reason="tool_use"` is the ACT, tool execution is the OBSERVE, `stop_reason="end_turn"` is the final response. ReportWriter does not use tool use — two sequential direct calls (report + QA).

### Shared utilities

- `shared/llm_client.py` — `get_llm_client()`: singleton factory for the LLM client; reads `LLM_PROVIDER` (local|bedrock|vertex|azure)
- `shared/react.py` — `react_loop()`: native Anthropic SDK ReAct loop, used by all agents with tool use
- `shared/audit.py` — `write_audit_event()` / `make_audit_event()`: append-only JSONL audit trail
- `shared/demo.py` — `is_demo_mode()` / `load_demo_response()`: demo mode without LLM calls
- `shared/hmac_auth.py` — `HMACMiddleware` + `sign_request()`: inter-agent authentication
- `shared/secrets.py` — `get_secret()`: provider-agnostic secret factory (local/azure/aws)
- `shared/sanitize.py` — `sanitize_rss_item()`: RSS input sanitization against prompt injection
- `shared/rag_retriever.py` — `retrieve_context(query_terms, top_k)`: TF-IDF retrieval on documents in `data/rag/documents/`. Parallel node in the orchestrator (fan-out alongside DataCollector and NewsSentiment). Output injected into FundamentalAnalyst prompt as `rag_context`. Stable public interface: upgrade to embedding-based (Bedrock Titan) without orchestrator changes.
- `shared/llm_judge.py` — `run_judge()`: independent LLM grounding check. Runs after ReportWriter; receives original source material (news, fundamentals, RAG context) and returns a `JudgmentResult` (verdict: PASS/WARN/FAIL, grounding_score 0-100). FAIL triggers conservative mode in PortfolioManager. If `grounding_score < JUDGE_SCORE_THRESHOLD`, the portfolio branch is skipped (report flagged as non-publishable).
- `shared/models.py` — Pydantic models: `Report` (full report schema with `model_validate()` enforcement in ReportWriter), `TaskDecomposition` (NL prompt decomposition output), `Scoring`, `Candidato`, `Correction` and related.
- `shared/validators.py` — `validate(report)`: deterministic output constraints (no UK stocks, no crypto, no directives, citation format, score range). `validate_tickers(tickers)`: input guardrail — rejects LSE (`.L`), crypto keywords, invalid format before the pipeline starts.

### Orchestrator internals

- `orchestrator/main.py` — `run_pipeline(tickers, mode, interactive, prompt)`: LangGraph entry point. First node is `node_task_decomposer` (no-op if `prompt` is None). Compiles the graph with `_build_graph_builder()` on every call (to allow correct checkpointing). `PipelineState` TypedDict fields: `run_id`, `mode`, `tickers`, `fundamentals`, `news`, `themes`, `candidates`, `risk_assessment`, `report`, `executive_summary`, `qa_verdict`, `degraded`, `portfolio_state`, `portfolio_result`, `rag_context`, `judgment`, `ticker_history`, `previous_runs`, `user_prompt`, `task_decomposition`.
- `orchestrator/api.py` — FastAPI on port 8000. `POST /research` (accepts `tickers`, `mode`, `prompt`), `GET /portfolio`, `GET /health`. Routes requests to `run_pipeline()`.

### Domain constraints (hardcoded in agent prompts)

- Universe: US and EU equities only (UK/LSE excluded)
- Excluded sectors: energy, utilities, real estate, REITs, consumer staples, industrials, airlines, crypto/DeFi/Web3
- Priority sectors: Technology, AI, Software, Semiconductors, Banking, Financial Services
- Final report language: **Italian**

### Report Writer internals

Two-step process in `run_agent`:
1. Generate full report with `=== SINTESI ESECUTIVA ===` and `=== JSON ===` sections
2. Run QA pass on the same output; QA model responds with `QA: [APPROVATO|CORRETTO]`

The JSON schema embedded in `_REPORT_SCHEMA` defines the canonical output structure (candidates with 5-dimension scoring summing to max 50, analyst consensus, scenarios, risks, falsification trigger).

## Environment

There is no `.env` file — environment variables are injected by the platform (ECS, Lambda) or set in the shell locally. Do not use `ANTHROPIC_API_KEY` directly: pattern not approved for Accenture workloads.

### Local development (demo mode — no LLM calls)

```powershell
$env:DEMO_MODE = "true"
uv run python agents/data-collector/data_collector.py
```

### Production (AWS Bedrock)

```
DEMO_MODE=false
LLM_PROVIDER=bedrock
AWS_REGION=eu-west-1        # region assigned by ServiceNow ticket
```
Credentials managed by the IAM role on the compute resource — no secrets in config.

### Available variables

| Variable | Values | Default | Notes |
|---|---|---|---|
| `DEMO_MODE` | `true\|false` | `false` | `true` = no LLM calls, data from `agents/*/demo/response.json` |
| `LLM_PROVIDER` | `local\|bedrock\|vertex\|azure` | `local` | `local` requires `ANTHROPIC_API_KEY` (personal testing only) |
| `AWS_REGION` | e.g. `eu-west-1` | `us-east-1` | only if `LLM_PROVIDER=bedrock` |
| `VERTEX_REGION` | e.g. `europe-west4` | `us-east5` | only if `LLM_PROVIDER=vertex` |
| `VERTEX_PROJECT_ID` | GCP project ID | — | required if `LLM_PROVIDER=vertex` |
| `A2A_SHARED_SECRET` | 32-byte hex | — | inter-agent HMAC; middleware disabled if absent |
| `SECRET_PROVIDER` | `local\|azure\|aws` | `local` | provider for `shared/secrets.py` |
| `JUDGE_SCORE_THRESHOLD` | integer 0–100 | `60` | grounding score minimum — below this the portfolio branch is skipped |
| `MAX_NEWS_PAYLOAD` | integer | `15` | max news items passed to FundamentalAnalyst |
| `MAX_CANDIDATES_PAYLOAD` | integer | `3` | max candidates passed to RiskAssessor |
