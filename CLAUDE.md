# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run a single agent (example: data-collector on port 8001)
uv run python agents/data-collector/agent.py

# Run all 5 agents (each in a separate terminal)
uv run python agents/data-collector/agent.py      # :8001
uv run python agents/news-sentiment/agent.py      # :8002
uv run python agents/fundamental-analyst/agent.py # :8003
uv run python agents/risk-assessor/agent.py       # :8004
uv run python agents/report-writer/agent.py       # :8009

# Run the full pipeline (requires all agents running)
uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI

# Save output to file
uv run python orchestrator/main.py --tickers AAPL MSFT --output report.json

# Health check a running agent
curl http://localhost:8001/health

# Agent card discovery
curl http://localhost:8001/.well-known/agent.json
```

## Architecture

This is an **A2A (Agent-to-Agent)** multi-agent equity research system. The CrewAI pipeline was decomposed into 5 independent FastAPI services that communicate via **JSON-RPC 2.0 over HTTP**.

### Pipeline (sequential, orchestrated)

```
Orchestrator
  → [1] DataCollector     :8001  Anthropic SDK ReAct fetch fundamentals from yfinance
  → [2] NewsSentiment     :8002  Anthropic SDK ReAct RSS feeds → JSON news + themes
  → [3] FundamentalAnalyst:8003  Anthropic SDK ReAct news+themes+fundamentals → candidates
  → [4] RiskAssessor      :8004  Anthropic SDK ReAct candidates → scoring + scenarios
  → [5] ReportWriter      :8005  Anthropic SDK direct final Italian report + QA pass
```

The orchestrator (`orchestrator/main.py`) uses **LangGraph** (`StateGraph`). Each pipeline step is a node; `PipelineState` (TypedDict) carries accumulated data across nodes. The graph is compiled once at module load (`_build_graph()`) and invoked with `_graph.ainvoke(initial_state)`. Adding conditional edges, parallel branches, or retry loops only requires modifying `_build_graph()` — node logic stays untouched.

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

| Agent | Framework | Model |
|---|---|---|
| DataCollector | Anthropic SDK (`shared/react.py`) | `claude-haiku-4-5-20251001` |
| NewsSentiment | Anthropic SDK (`shared/react.py`) | `claude-haiku-4-5-20251001` |
| FundamentalAnalyst | Anthropic SDK (`shared/react.py`) | `claude-sonnet-4-6` |
| RiskAssessor | Anthropic SDK (`shared/react.py`) | `claude-sonnet-4-6` |
| ReportWriter | Anthropic SDK direct | `claude-sonnet-4-6` (report) + `claude-sonnet-4-6` (QA) |

### Shared tools

- `shared/tools/yfinance_tool.py` — `get_stock_fundamentals(ticker)` / `get_stock_fundamentals_text(ticker)`. Wraps yfinance with a 15s per-ticker timeout via `ThreadPoolExecutor`.
- `shared/tools/rss_feed.py` — `fetch_rss_news()` reads 6 RSS feeds (Reuters, Yahoo Finance, MarketWatch, Investing.com × 2) with retry logic.

### ReAct loop nativo

Tutti e 4 gli agenti con tool use (DataCollector, NewsSentiment, FundamentalAnalyst, RiskAssessor) implementano il pattern ReAct (Reason → Act → Observe) direttamente con l'Anthropic SDK tool_use, senza framework intermedi. La logica è in `shared/react.py` (`react_loop()`). Ogni `stop_reason="tool_use"` è l'ACT, l'esecuzione del tool è l'OBSERVE, `stop_reason="end_turn"` è la risposta finale. ReportWriter non usa tool use — due chiamate dirette sequenziali (report + QA).

### Shared utilities

- `shared/llm_client.py` — `get_llm_client()`: factory singleton per il client LLM; legge `LLM_PROVIDER` (local|bedrock|vertex|azure)
- `shared/react.py` — `react_loop()`: ReAct loop nativo Anthropic SDK, usato da tutti gli agenti con tool use
- `shared/audit.py` — `write_audit_event()` / `make_audit_event()`: audit trail JSONL append-only
- `shared/demo.py` — `is_demo_mode()` / `load_demo_response()`: demo mode senza chiamate LLM
- `shared/hmac_auth.py` — `HMACMiddleware` + `sign_request()`: autenticazione inter-agente
- `shared/secrets.py` — `get_secret()`: factory secret provider-agnostic (local/azure/aws)
- `shared/sanitize.py` — `sanitize_rss_item()`: sanitizzazione input RSS anti prompt-injection

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

Non esiste un file `.env` — le variabili d'ambiente sono iniettate dalla piattaforma (ECS, Lambda) o settate nella shell in locale. Non usare `ANTHROPIC_API_KEY` diretta: pattern non approvato per workload Accenture.

### Sviluppo locale (demo mode — nessuna chiamata LLM)

```powershell
$env:DEMO_MODE = "true"
uv run python agents/data-collector/agent.py
```

### Produzione (AWS Bedrock)

```
DEMO_MODE=false
LLM_PROVIDER=bedrock
AWS_REGION=eu-west-1        # region assegnata dal ticket ServiceNow
```
Credenziali gestite dal ruolo IAM sulla risorsa compute — nessun segreto in config.

### Variabili disponibili

| Variabile | Valori | Default | Note |
|---|---|---|---|
| `DEMO_MODE` | `true\|false` | `false` | `true` = nessuna chiamata LLM, dati da `agents/*/demo/response.json` |
| `LLM_PROVIDER` | `local\|bedrock\|vertex\|azure` | `local` | `local` richiede `ANTHROPIC_API_KEY` (solo test personali) |
| `AWS_REGION` | es. `eu-west-1` | `us-east-1` | solo se `LLM_PROVIDER=bedrock` |
| `VERTEX_REGION` | es. `europe-west4` | `us-east5` | solo se `LLM_PROVIDER=vertex` |
| `VERTEX_PROJECT_ID` | GCP project ID | — | obbligatorio se `LLM_PROVIDER=vertex` |
| `A2A_SHARED_SECRET` | hex 32 byte | — | HMAC inter-agente; se assente il middleware è disabilitato |
| `SECRET_PROVIDER` | `local\|azure\|aws` | `local` | provider per `shared/secrets.py` |
