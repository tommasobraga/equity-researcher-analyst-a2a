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

    START(["Orchestrator API :8000"]):::infra
    START --> MODE{mode}:::term

    MODE -->|"analyze / full"| DC & NS
    MODE -->|portfolio| PL

    DC["DataCollector :8001<br/>Haiku 4.5 · ReAct"]:::haiku
    NS["NewsSentiment :8002<br/>Haiku 4.5 · ReAct"]:::haiku

    DC & NS --> FA

    FA["FundamentalAnalyst :8003<br/>Sonnet 4.6 · ReAct"]:::sonnet

    FA -->|"0 candidati"| STOP(["fail-fast"]):::term
    FA -->|"1-3 candidati"| RA

    RA["RiskAssessor :8004<br/>Sonnet 4.6 · ReAct"]:::sonnet

    RA --> RW

    RW["ReportWriter :8009<br/>Sonnet 4.6 · direct"]:::sonnet

    RW -->|"mode=analyze"| OUT_A(["Report finale"]):::infra
    RW -->|"mode=full"| PL

    PL[("portfolio.db")]:::store
    PL --> PM

    PM["PortfolioManager :8010<br/>Sonnet 4.6 · direct"]:::sonnet

    PM --> OUT_B(["Portfolio update"]):::infra
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
        LLM_P["LLM Provider<br/>bedrock · vertex · azure · local"]:::ext
        RSS_P["RSS Feeds<br/>Reuters · Yahoo · MarketWatch"]:::ext
        DATA_P["Data Provider — Fase 5<br/>Refinitiv · Bloomberg"]:::pending
    end

    subgraph ORCHESTRATOR ["Orchestrator :8000"]
        API["api.py — FastAPI"]:::orc
        GRAPH["main.py — LangGraph PipelineState"]:::orc
        API --> GRAPH
    end

    subgraph AGENTS ["Agent Layer — A2A JSON-RPC 2.0"]
        DC["DataCollector :8001<br/>Haiku 4.5 · ReAct"]:::agent
        NS["NewsSentiment :8002<br/>Haiku 4.5 · ReAct"]:::agent
        FA["FundamentalAnalyst :8003<br/>Sonnet 4.6 · ReAct"]:::agent
        RA["RiskAssessor :8004<br/>Sonnet 4.6 · ReAct"]:::agent
        RW["ReportWriter :8009<br/>Sonnet 4.6 · direct"]:::agent
        PM["PortfolioManager :8010<br/>Sonnet 4.6 · direct"]:::agent
    end

    subgraph SHARED ["Shared Library"]
        A2A["a2a_models.py"]:::shared
        REACT["react.py — react_loop()"]:::shared
        LLC["llm_client.py — get_llm_client()"]:::shared
        HMAC["hmac_auth.py — HMACMiddleware"]:::shared
        AUDIT["audit.py — write_audit_event()"]:::shared
        DEMO["demo.py — is_demo_mode()"]:::shared
        SANITIZE["sanitize.py — sanitize_rss_item()"]:::shared
        TOOLS["tools/ — yfinance + rss_feed"]:::shared
        PORT_DB["portfolio_db.py"]:::shared
        MEM["agent_memory.py"]:::shared
    end

    subgraph STORAGE ["Storage"]
        P_FILE[("portfolio.db")]:::storage
        A_FILE[("audit_*.jsonl")]:::storage
        M_FILE[("memory/*.db")]:::storage
        D_FILE[("demo/response.json")]:::storage
    end

    GRAPH -->|"A2A tasks/send"| DC & NS
    DC & NS -->|result| FA
    FA --> RA --> RW
    RW -->|"mode=full"| PM

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

    LLC --> LLM_P
    TOOLS -->|"rss_feed.py"| RSS_P
    TOOLS -->|"yfinance_tool.py"| DATA_P

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
