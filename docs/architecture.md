# Architecture — Equity Researcher A2A

## 1. Workflow Diagram

I tre workflow selezionabili via `mode`. Ogni nodo include modello LLM, tool esposti e scope funzionale.

```mermaid
flowchart TD
    classDef haiku  fill:#dbeafe,stroke:#2563eb,color:#1e3a5f
    classDef sonnet fill:#ede9fe,stroke:#7c3aed,color:#2e1065
    classDef infra  fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef store  fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef term   fill:#f1f5f9,stroke:#94a3b8,color:#334155

    START(["Orchestrator API<br/>FastAPI · LangGraph · :8000"]):::infra
    START --> MODE{mode?}:::term

    MODE -->|analyze / full| DC & NS
    MODE -->|portfolio| PL

    DC["DataCollector · :8001<br/>Haiku 4.5 · ReAct<br/>tool: fetch_fundamentals<br/>────────────────────<br/>Recupera fondamentali equity per ticker:<br/>P/E, EPS, price, 52w range, consensus analisti.<br/>Provider certificato (Refinitiv/Bloomberg) — Fase 5 pending.<br/>In DEMO_MODE carica dati fittizi da response.json."]:::haiku

    NS["NewsSentiment · :8002<br/>Haiku 4.5 · ReAct<br/>tool: read_financial_rss<br/>────────────────────<br/>Legge RSS da Reuters, Yahoo Finance, MarketWatch.<br/>Sanitizza gli item (anti prompt-injection).<br/>Classifica le notizie in macro-temi di mercato."]:::haiku

    DC & NS --> FA

    FA["FundamentalAnalyst · :8003<br/>Sonnet 4.6 · ReAct<br/>tool: fetch_fundamentals<br/>────────────────────<br/>Incrocia macro-temi di mercato con i fondamentali.<br/>Universo: US e EU equities (escluso UK/LSE).<br/>Settori esclusi: energy, utilities, REIT, consumer staples, crypto.<br/>Priorita: Tech, AI, Software, Semiconduttori, Banking.<br/>Seleziona fino a 3 candidati con tesi d'investimento."]:::sonnet

    FA -->|"0 candidati"| STOP(["fail-fast<br/>skip RiskAssessor e ReportWriter"]):::term
    FA -->|"1-3 candidati"| RA

    RA["RiskAssessor · :8004<br/>Sonnet 4.6 · ReAct<br/>tool: check_volatility_data<br/>────────────────────<br/>Verifica la presenza di dati di volatilita (52w range + P/E).<br/>Guardrail: rifiuta output se i dati mancano.<br/>Scoring su 5 dimensioni (max 10 ciascuna, totale max 50).<br/>Produce scenari base / bull / bear per ogni candidato."]:::sonnet

    RA --> RW

    RW["ReportWriter · :8009<br/>Sonnet 4.6 · API diretta — 2 chiamate sequenziali<br/>────────────────────<br/>Step 1 — genera report in italiano:<br/>  - Sintesi esecutiva (max 10 righe, neutro, no buy/sell)<br/>  - JSON strutturato (scoring, scenari, rischi, consensus)<br/>Step 2 — QA pass interno sullo stesso output.<br/>QA verdict: APPROVATO o CORRETTO."]:::sonnet

    RW -->|"mode=analyze"| OUT_A(["Report finale<br/>(executive summary + JSON)"]):::infra
    RW -->|"mode=full"| PL

    PL[("portfolio.db<br/>SQLite · seed 100.000 USD")]:::store
    PL --> PM

    PM["PortfolioManager · :8010<br/>Sonnet 4.6 · API diretta<br/>────────────────────<br/>mode=full:      riceve candidates + risk + report,<br/>                decide BUY / SELL / HOLD per ogni ticker.<br/>mode=portfolio: riceve solo stato portafoglio,<br/>                produce P&L estimate + sector exposure review.<br/>Regole BUY: score >= 35 AND qualita alta/media.<br/>Regole SELL: qualita bassa o dati insufficienti.<br/>Sizing: 10% cash disponibile, cap 20% per singola posizione."]:::sonnet

    PM --> OUT_B(["Portfolio update<br/>(trades + P&L review)"]):::infra
```

---

## 2. Component Diagram

Dipendenze strutturali tra layer: Orchestrator, Agenti, Shared Library, Storage ed Esterni.

```mermaid
graph TB
    classDef agent   fill:#ede9fe,stroke:#7c3aed,color:#2e1065
    classDef shared  fill:#dbeafe,stroke:#2563eb,color:#1e3a5f
    classDef storage fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef ext     fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef orc     fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef pending fill:#fee2e2,stroke:#dc2626,color:#7f1d1d

    subgraph EXTERNAL ["Esterni"]
        LLM_P["LLM Provider<br/>bedrock / vertex / azure / local<br/>legge LLM_PROVIDER env var"]:::ext
        RSS_P["RSS Feeds<br/>Reuters · Yahoo Finance · MarketWatch<br/>licenza commerciale da verificare — Fase 5"]:::ext
        DATA_P["Data Provider<br/>Refinitiv LSEG / Bloomberg B-PIPE<br/>Alpha Vantage enterprise — Fase 5 pending"]:::pending
    end

    subgraph ORCHESTRATOR ["Orchestrator · :8000"]
        API["api.py — FastAPI<br/>POST /research<br/>GET /portfolio<br/>GET /health"]:::orc
        GRAPH["main.py — LangGraph StateGraph<br/>PipelineState TypedDict<br/>run_id · mode · tickers · fundamentals · news<br/>themes · candidates · risk_assessment · report<br/>portfolio_state · portfolio_result · degraded"]:::orc
        API --> GRAPH
    end

    subgraph AGENTS ["Agent Layer — A2A JSON-RPC 2.0 over HTTP"]
        DC["DataCollector :8001<br/>Haiku 4.5 · ReAct"]:::agent
        NS["NewsSentiment :8002<br/>Haiku 4.5 · ReAct"]:::agent
        FA["FundamentalAnalyst :8003<br/>Sonnet 4.6 · ReAct"]:::agent
        RA["RiskAssessor :8004<br/>Sonnet 4.6 · ReAct"]:::agent
        RW["ReportWriter :8009<br/>Sonnet 4.6 · direct"]:::agent
        PM["PortfolioManager :8010<br/>Sonnet 4.6 · direct"]:::agent
    end

    subgraph SHARED ["Shared Library"]
        A2A["a2a_models.py<br/>JsonRpcRequest · A2ATask<br/>A2ATaskResult · ok() / fail()"]:::shared
        REACT["react.py<br/>react_loop()<br/>ReAct nativo Anthropic SDK<br/>stop_reason: tool_use vs end_turn"]:::shared
        LLC["llm_client.py<br/>get_llm_client() singleton<br/>factory per provider"]:::shared
        HMAC["hmac_auth.py<br/>HMACMiddleware<br/>sign_request()"]:::shared
        AUDIT["audit.py<br/>write_audit_event()<br/>JSONL append-only"]:::shared
        DEMO["demo.py<br/>is_demo_mode()<br/>load_demo_response()"]:::shared
        SANITIZE["sanitize.py<br/>sanitize_rss_item()<br/>anti prompt-injection RSS"]:::shared
        TOOLS["tools/<br/>yfinance_tool.py — stub (Fase 5)<br/>rss_feed.py — retry logic"]:::shared
        PORT_DB["portfolio_db.py<br/>init_db()<br/>load / save portfolio_state"]:::shared
        MEM["agent_memory.py<br/>SQLite per-agent memory<br/>Fase A+B"]:::shared
    end

    subgraph STORAGE ["Storage"]
        P_FILE[("portfolio.db")]:::storage
        A_FILE[("logs/audit_*.jsonl")]:::storage
        M_FILE[("output/memory/*.db")]:::storage
        D_FILE[("agents/*/demo/<br/>response.json")]:::storage
    end

    %% Orchestrator → Agents (A2A calls)
    GRAPH -->|"A2A tasks/send"| DC & NS
    DC & NS -->|result| FA
    FA --> RA --> RW
    RW -->|"mode=full"| PM

    %% Agents → Shared (ReAct only for 4 tool-use agents)
    DC & NS & FA & RA --> REACT
    DC & NS & FA & RA & RW & PM --> LLC
    DC & NS & FA & RA & RW & PM --> HMAC
    DC & NS & FA & RA & RW & PM --> AUDIT
    DC & NS & FA & RA & RW & PM --> DEMO
    DC & FA & RA --> TOOLS
    NS --> TOOLS
    NS --> SANITIZE
    PM & GRAPH --> PORT_DB
    DC & NS & FA & RA & RW & PM --> MEM

    %% Shared → External
    LLC --> LLM_P
    TOOLS -->|"rss_feed.py"| RSS_P
    TOOLS -->|"yfinance_tool.py (stub)"| DATA_P

    %% Shared → Storage
    AUDIT --> A_FILE
    PORT_DB --> P_FILE
    MEM --> M_FILE
    DEMO --> D_FILE
```

---

## Note evolutive

| Layer | Stato | Prossimi step |
|---|---|---|
| Data Provider | stub — `NotImplementedError` | Fase 5: Refinitiv LSEG o Bloomberg B-PIPE |
| RSS Feeds | operativo | Fase 5: verifica licenza commerciale |
| LLM Provider | `local` (test) / `bedrock` (prod) | Valutare Vertex per EU data residency |
| Storage | SQLite (`portfolio.db`) | Fase 5/6: upgrade a PostgreSQL |
| Agent Memory | SQLite per-agent (Fase A+B) | Fase futura: vector store per RAG |
| Auth | HMAC inter-agente opzionale | Fase 5: mutual TLS o API gateway |
| Orchestrator | LangGraph deterministico | LLM-ready: sostituire body nodi con `react_loop()` |
