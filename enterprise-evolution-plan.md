# Enterprise Evolution Plan — Equity Researcher A2A

## Architectural Baseline (pre-evolution state — 16 June 2026)

> This section documents the original architecture and the identified risks that guided the phase definitions. It incorporates the architectural review conducted at the enterprise kick-off.

### Original architecture (pre-migration stack)

| Component | Original technology | Current state |
|---|---|---|
| Orchestrator | LangGraph StateGraph | **Unchanged** |
| DataCollector | OpenAI Agents SDK + LiteLLM | **Migrated** → Anthropic SDK native ReAct |
| NewsSentiment | Smolagents (HuggingFace) | **Migrated** → Anthropic SDK native ReAct |
| FundamentalAnalyst | BeeAI ReActAgent | **Migrated** → Anthropic SDK native ReAct |
| RiskAssessor | BeeAI ReActAgent | **Migrated** → Anthropic SDK native ReAct |
| ReportWriter | Anthropic SDK direct | **Unchanged** |
| Fundamental data | yfinance (Yahoo Finance scraping) | **Removed** → stub NotImplementedError (Phase 5) |
| News data | RSS Reuters/Yahoo/MarketWatch + Investing.com ×2 | Investing.com **removed**, others ⚠️ to verify with Legal |

### Identified risks and current status

| Priority | Risk | Phase | Status |
|---|---|:---:|---|
| 1 | No audit trail — incompatible with financial services compliance | 1 | ✅ Resolved |
| 2 | Ports 8001-8005 open without inter-agent authentication | 2 | ✅ Resolved (HMAC-SHA256) |
| 3 | Prompt injection from RSS feeds — untrusted input passed to models | 2 | ✅ Resolved (shared/sanitize.py) |
| 4 | No structured retry, no circuit breaker, orchestrator SPOF | 3 | ✅ Resolved (tenacity + custom circuit breaker) |
| 5 | yfinance — uncertified data, not auditable, unofficial scraping | 5 | ✅ Removed early — Phase 5 provider pending |
| 6 | Growing monolithic payload — context overflow risk on large batches | 3/6 | ⚠️ Partial (windowing Phase 3) — structural solution in Phase 6 |
| 7 | Non-reproducible environment, unversioned prompts, no contract testing | 4 | ⏳ Pending |
| 8 | Unapproved LLM provider (direct ANTHROPIC_API_KEY) | 0 | ✅ Resolved (shared/llm_client.py + DEMO_MODE) |

---

## Context

The system is a working prototype with a sound architectural foundation (A2A, LangGraph, separation of concerns, deterministic guardrails). The gap toward enterprise deployment is not in the chosen patterns, but in the absence of cross-cutting layers: security, observability, auditability, resilience.

The plan is structured in **6 milestone phases ordered by priority**. It does not rewrite the architecture — it evolves it incrementally. The A2A contract remains stable throughout the plan.

---

## Technology Authorization Assessment

> **Methodological note:** this assessment is based on general enterprise criteria (vendor maturity, commercial SLAs, explicit usage licences, financial services adoption track record). It does not replace formal review by the CISO / Technology Architecture Board / internal Approved Vendor List. Key contacts: practice Security Lead, ATCI portal, myLearning Security, Accenture Technology Architecture Board.

**Legend:** ✅ Approvable without friction &nbsp;|&nbsp; ⚠️ Verify with Architecture Board &nbsp;|&nbsp; ❌ High risk — requires explicit approval or replacement

---

### Current Stack

| Technology | Category | Status | Rationale |
|---|---|:---:|---|
| Python 3.11 | Runtime | ✅ | Enterprise de facto standard |
| FastAPI | Web framework | ✅ | Widely adopted, production-ready, native Pydantic |
| Pydantic v2 | Validation | ✅ | De facto standard for Python APIs |
| httpx | HTTP client | ✅ | Mature, tested, asyncio-native |
| JSON-RPC 2.0 | Inter-agent protocol | ✅ | Open standard, no vendor dependency |
| uv | Package manager | ⚠️ | Relatively new (2023); verify if internal policy allows alternatives to pip/poetry. Low risk but needs confirmation |
| LangGraph (LangChain) | Orchestration | ⚠️ | Widely adopted in enterprise AI, but evolves fast. Verify minimum supported version and whether LangChain Inc. is an approved vendor |
| OpenAI Agents SDK | Agent framework | ~~⚠️~~ | **Removed** — replaced by Anthropic SDK native ReAct (`shared/react.py`) |
| Smolagents (HuggingFace) | Agent framework | ~~⚠️~~ | **Removed** — replaced by Anthropic SDK native ReAct |
| BeeAI (IBM) | Agent framework | ~~⚠️~~ | **Removed** — replaced by Anthropic SDK native ReAct (also eliminates prefill / Haiku constraint) |
| Anthropic API (direct) | LLM provider | ❌ | **Not approved for any application workload**, including local development. Accenture policy explicitly prohibits personal or unmanaged `ANTHROPIC_API_KEY` usage. All LLM access must go through AWS Bedrock, Google Vertex AI, or Azure AI Foundry with IAM/service account authentication. See Phase 0 |
| AWS Bedrock (Claude) | LLM provider | ✅ | Accenture approved pattern for application workloads. Authentication via IAM role. Provisioning through CAPP (Global IT) or CMO (rest of Accenture) |
| Azure AI Foundry (Claude) | LLM provider | ✅ | Accenture approved pattern for application workloads. Consistent with Azure Key Vault (Phase 2). Authentication via Azure Managed Identity / service principal |
| Google Vertex AI (Claude) | LLM provider | ✅ | Accenture approved pattern for application workloads. Authentication via GCP service account |
| yfinance | Data source | ❌ | Unofficial Yahoo Finance scraping. No SLA, no commercial licence, uncertified data. Incompatible with financial services enterprise and MiFID II. **Removed early — stub NotImplementedError in shared/tools/yfinance_tool.py. Replace with certified provider in Phase 5** |
| RSS Reuters / Yahoo Finance / MarketWatch | Data source | ⚠️ | Public feeds without explicit data licence for commercial/analytical use. Reuters has restrictive content reuse policies. Verify with Legal before any production deployment |
| RSS Investing.com ×2 | Data source | ❌ | Investing.com has historically blocked scraping and offers no public data licence. High risk of ToS violation. **Removed early from shared/tools/rss_feed.py** |

---

### Technologies Planned by Phase

| Technology | Phase | Category | Status | Rationale |
|---|:---:|---|:---:|---|
| `structlog` | 1 | Logging | ✅ | Mature Python library, no external vendor dependency |
| `hmac` (stdlib) | 2 | Security | ✅ | Python standard library, no additional dependency |
| `bleach` | 2 | Sanitization | ✅ | Established Python library (Mozilla), common in production |
| `azure-keyvault-secrets` | 2 | Secret management | ✅ | Azure is an Accenture-approved vendor; Key Vault is the standard solution |
| `tenacity` | 3 | Retry logic | ✅ | Mature Python library, widely used in enterprise |
| `pybreaker` | 3 | Circuit breaker | ⚠️ | Less common than Resilience4j (Java). Verify if approved or prefer custom implementation with `tenacity` |
| `langgraph-checkpoint-sqlite` | 3 (dev) | Checkpointing | ✅ | SQLite stdlib, no external service in dev |
| `langgraph-checkpoint-postgres` | 3 (prod) | Checkpointing | ✅ | Standard enterprise PostgreSQL |
| Docker / Docker Compose | 4 | Containerization | ✅ | Enterprise standard, widely adopted |
| GitHub Actions | 4 | CI/CD | ⚠️ | Verify if policy mandates Azure DevOps as CI/CD platform. Accenture client projects often require Azure DevOps |
| Azure DevOps Pipelines | 4 | CI/CD | ✅ | Preferable to GitHub Actions in Accenture contexts — Microsoft vendor, enterprise standard |
| `ruff` | 4 | Linting | ✅ | Python de facto standard, no vendor dependency |
| `mypy` | 4 | Type checking | ✅ | Standard Python, widely used in enterprise |
| Alpha Vantage | 5 | Market data | ⚠️ | Provider with official API and SLA, but verify if on approved vendor list for financial data. Free tier not suitable for production |
| Refinitiv / LSEG Data API | 5 | Market data | ✅ | EU financial services standard, certified data, guaranteed SLA, MiFID II compliant |
| Bloomberg B-PIPE | 5 | Market data | ✅ | De facto standard in financial services — if contract already exists in the organisation |
| PostgreSQL (audit DB) | 5 | Database | ✅ | Enterprise standard, widely approved |
| AWS S3 Object Lock | 5 | Log immutability | ⚠️ | Verify if AWS is the approved vendor in the deployment context (vs Azure-first policy) |
| Kubernetes | 6 | Container orchestration | ✅ | Enterprise standard, widely approved |
| Helm | 6 | K8s package manager | ✅ | De facto standard for K8s, widely adopted |
| Istio (Service Mesh) | 6 | Networking / mTLS | ⚠️ | Mature and widely adopted, but adds significant operational complexity. Verify if the practice has internal expertise or prefers Linkerd (simpler) |
| MinIO | 6 | Artifact store (self-hosted) | ⚠️ | Self-hosted S3-compatible. Prefer Azure Blob Storage if cloud-first is the mandate |
| Azure Blob Storage | 6 | Artifact store | ✅ | Azure standard, approved vendor |
| OpenTelemetry | 6 | Observability | ✅ | CNCF standard, vendor-neutral, widely adopted |
| Prometheus | 6 | Metrics | ✅ | CNCF standard, widely adopted |
| Grafana | 6 | Dashboard | ✅ | Enterprise standard for observability |
| Loki | 6 | Log aggregation | ✅ | Grafana Labs, widely adopted in cloud-native stacks |
| Argo CD | 6 | GitOps / CD | ⚠️ | Mature and CNCF-graduated, but verify if the practice prefers Azure DevOps for deployment. Alternative: Flux CD |

---

### Verification Priorities

Before starting any phase, verify in this order:

1. ~~**Cloud provider**~~ ✅ **Resolved** — `shared/llm_client.py` + `DEMO_MODE=true` for local development. For production: open ServiceNow ticket "Claude Enterprise" (Bedrock, Vertex AI, or Azure AI Foundry).
2. ~~**yfinance and RSS Investing.com**~~ ✅ **Resolved** — yfinance removed (stub NotImplementedError), Investing.com removed from rss_feed.py.
3. ~~**Smolagents / BeeAI / OpenAI Agents SDK**~~ ✅ **Resolved** — all removed, uniform Anthropic SDK native ReAct.
4. **CI/CD platform** — decide GitHub Actions vs Azure DevOps before Phase 4 to avoid pipeline migration.
5. **Infrastructure cloud provider** — confirm Azure-first vs multi-cloud before Phase 6 (impacts artifact store, secret manager, GitOps tooling choice).

---

## Intervention Phases

### Phase 0 — Enterprise Prerequisites (complete before any other phase)
**Horizon:** 1-2 weeks (depends on internal approval timelines) | **Complexity:** Organisational, not technical

**Context:** the current system uses `ANTHROPIC_API_KEY` in `.env`. This configuration **is not approved** for any Accenture application workload — including local development. Before proceeding with any technical phase, LLM access must be resolved in the approved manner.

**Interventions:**

**0a — Obtain cloud-managed access to Claude**
- Open ServiceNow ticket category "Claude Enterprise" specifying: application use case (equity research agent pipeline), frameworks used (LangGraph, Anthropic SDK native ReAct), preferred cloud provider
- Await provisioning via CAPP (Global IT) or CMO
- Expected output: IAM credentials (AWS role ARN, Azure service principal, or GCP service account) for Claude access via the assigned cloud provider

**0b — Design the LLM client abstraction**
- Create `shared/llm_client.py` with factory `get_llm_client(provider: str) -> LLMClient` selected by env var `LLM_PROVIDER=bedrock|vertex|azure`
- Each agent stops directly instantiating its own LLM client — calls the factory instead
- Factory returns the client configured for the provider: `AnthropicBedrock`, `AnthropicVertex`, or Azure AI Foundry endpoint
- Remove `ANTHROPIC_API_KEY` from `.env` and all `agent.py` files
- **Note on BeeAI:** verify if `AnthropicChatModel` supports Bedrock/Vertex. If not supported, FundamentalAnalyst and RiskAssessor require framework or internal client replacement before running in cloud-managed mode

**0c — Update the local development model**
- Local development uses the same cloud credentials as the assigned provider (e.g. AWS local profile, Azure CLI login, gcloud auth application-default)
- No "dev-only" exceptions with direct API keys
- Unit tests and contract tests run with mock LLM — do not require real credentials

**0d — Demo mode for development without cloud credentials**
- Add env var `DEMO_MODE=true` read at startup in every `agent.py`
- In demo mode, `run_agent()` skips the LLM call and returns a pre-built `A2ATaskResult` with realistic but fictitious data (invented fundamentals, sample news, candidates with plausible scoring, placeholder report)
- The orchestrator, LangGraph graph, audit trail, retry, and correlation ID run normally — they are unaware that agents returned stub data
- Demo data are static files in `agents/{agent}/demo/response.json`, versioned in git — stable and reproducible
- This enables development and testing of all cross-cutting features (Phases 1-4) with a fully working end-to-end pipeline, without any cloud credentials and without violating policy
- **This is not a policy workaround**: no LLM call is made, no data leaves the local perimeter

**Key files:** `shared/llm_client.py` (new), all `agent.py` files, `.env.example` (updated)

**Verifiable outcome:** `grep -r "ANTHROPIC_API_KEY" agents/` finds no occurrences; `LLM_PROVIDER=bedrock` + local AWS credentials completes an end-to-end run; `LLM_PROVIDER=azure` completes the same run without code changes to the agents

---

### Phase 1 — Observability and Basic Audit Trail
**Horizon:** 2-3 weeks | **Complexity:** Low | **Quick win with high impact**

**Issues resolved:** no correlation ID, no audit log, unstructured stdout logging

**Interventions:**
- Add `correlation_id` (UUID v4) to `A2ATask.metadata` in `shared/a2a_models.py` — optional field, backwards-compatible
- Structure logging with `structlog` — every event includes `correlation_id`, `agent`, `model_id`, `duration_ms`, `status`, `token_usage`
- Create `shared/audit.py`: function `write_audit_event(event)` that writes to append-only JSONL at `output/audit_{date}.jsonl`. Every event includes `prompt_hash` (SHA-256 of the system prompt), `input_hash`, `output_hash`
- `prompt_hash` silently tracks changes to hardcoded prompts without explicit versioning — evolves in Phase 4
- Add aggregated `GET /health` to the orchestrator (queries all 5 `/health` endpoints in parallel via `httpx.gather`)
- Extract `usage.input_tokens` / `usage.output_tokens` tracking already exposed by the Anthropic SDK in `report-writer/agent.py`

**Key files:** `shared/a2a_models.py`, `shared/audit.py` (new), `orchestrator/main.py`, all `agent.py` files

**Verifiable outcome:** every run produces `output/audit_{date}.jsonl` with one record per agent; all logs of a run share the same `correlation_id`; `GET /orchestrator/health` returns aggregated status

---

### Phase 2 — Inter-agent Security and Secret Management
**Horizon:** 3-4 weeks | **Complexity:** Medium

**Issues resolved:** ports 8001-8005 open without authentication, API keys in `.env`, prompt injection from RSS

**Interventions:**
- **HMAC-SHA256 authentication:** add `X-A2A-Signature` header (HMAC on body + timestamp) and `X-A2A-Timestamp` to A2A calls. FastAPI verification middleware in every agent. Anti-replay window of 30 seconds. No additional infrastructure — shared secret from secret manager
- **Secret Manager:** create `shared/secrets.py` with factory `get_secret(key)`. In dev: reads from `.env` via `python-dotenv`. In production: reads from Azure Key Vault (`azure-keyvault-secrets`) or AWS Secrets Manager (`boto3`). Selected by env var `SECRET_PROVIDER=local|azure|aws`. Remove all direct `os.getenv("ANTHROPIC_API_KEY")` from `agent.py` files
- **RSS Sanitization:** create `shared/sanitize.py` with `sanitize_rss_item(title, summary)` — HTML strip (`bleach`), truncation to maximum lengths, control character removal. Apply in `shared/tools/rss_feed.py` before text reaches prompts
- IP allowlist documented: ports 8001-8005 accessible only from the orchestrator (Docker Compose internal network in Phase 4, NetworkPolicy in Phase 6)

**Technologies:** `hmac` stdlib, `bleach`, `azure-keyvault-secrets`

**Key files:** `shared/secrets.py` (new), `shared/sanitize.py` (new), `shared/tools/rss_feed.py`, `orchestrator/main.py`, all `agent.py` files

**Verifiable outcome:** `POST /tasks` without `X-A2A-Signature` → HTTP 401; `grep -r "os.getenv" agents/` finds no direct API keys; RSS contaminated with HTML tags arrives sanitized at the agent

---

### Phase 3 — Resilience, Structured Retry and Checkpointing
**Horizon:** 4-6 weeks | **Complexity:** Medium

**Issues resolved:** no checkpointing, orchestrator SPOF (partial), no circuit breaker, hardcoded `asyncio.sleep(90)`, monolithic payload (partial)

**Interventions:**
- **LangGraph Checkpointing:** pass `SqliteSaver` (dev) or `PostgresSaver` (prod) to `StateGraph.compile()` in `_build_graph()`. The `run_id` from Phase 1 becomes the `thread_id`. Crash at stage 4 → restart from stage 4 with same `run_id`
- **Structured retry:** replace hardcoded `asyncio.sleep(90)` with `@retry(wait=wait_exponential(min=10, max=120), stop=stop_after_attempt(5))` via `tenacity`. Define typed exceptions `RateLimitError`, `AgentTimeoutError`, `AgentUnavailableError` in `shared/exceptions.py`
- **Circuit Breaker:** `pybreaker` on `send_task_with_retry` in the orchestrator. Circuit opens after 3 transient errors in 5 minutes — fails immediately instead of waiting for timeout
- **Payload Windowing:** configurable `MAX_NEWS_PAYLOAD` and `MAX_CANDIDATES_PAYLOAD` constants. NewsSentiment returns top-N news by relevance. Structural solution in Phase 6 (artifact store)
- **Graceful Degradation:** conditional edge in LangGraph — if NewsSentiment fails, continues with `news=[]` and methodological note; if DataCollector fails on a ticker, that ticker is marked as `data_unavailable` and the others proceed

**Technologies:** `langgraph-checkpoint-sqlite` (dev), `langgraph-checkpoint-postgres` (prod), `tenacity`, `pybreaker`

**Key files:** `orchestrator/main.py`, `shared/exceptions.py` (new)

**Verifiable outcome:** kill orchestrator process at stage 3 → restart with same `run_id` resumes from stage 4; unreachable agent opens circuit breaker after 3 failures; pipeline completes with NewsSentiment offline, producing report with degradation note

---

### Phase 4 — Containerization and CI/CD Pipeline
**Horizon:** 6-8 weeks | **Complexity:** Medium-High

**Issues resolved:** non-reproducible environment, unversioned prompts, no A2A contract testing

**Interventions:**
- **Dockerfile per agent** in each `agents/{agent}/Dockerfile`. Base image `python:3.11-slim`. Separate orchestrator image. Handle the `sys.path.insert()` dependency in `agent.py` files (resolve with proper packaging)
- **Docker Compose** at root: 5 agents + orchestrator + PostgreSQL (checkpointing) on internal `agents-net` network. Ports 8001-8005 not exposed on host
- **CI/CD Pipeline** (GitHub Actions or Azure DevOps): lint (`ruff`), type check (`mypy` on `shared/`), offline smoke tests, A2A contract tests
- **A2A contract tests** in `tests/test_contracts.py`: verify every agent card complies with `AgentCard` schema, `tasks/send` is implemented, wire format matches `A2ATaskResult` — runs without real LLM via `httpx.MockTransport`
- **Prompts as files:** move hardcoded system prompts (`_REPORT_SYSTEM`, `_QA_SYSTEM`, `_INSTRUCTIONS`) from strings in `agent.py` to files `agents/{agent}/prompts/system.md`. `agent.py` reads at startup. The `prompt_hash` from Phase 1 becomes SHA-256 of the file — trackable with `git log`
- **A2A protocol versioning:** `version: "1.0"` field in `JsonRpcRequest` and `AgentCard`. CI verifies consistency with `pyproject.toml`

**Technologies:** Docker, Docker Compose, GitHub Actions / Azure DevOps, `ruff`, `mypy`, `schemathesis`

**Key files:** `Dockerfile` per agent (new), `docker-compose.yml` (new), `.github/workflows/ci.yml` (new), `agents/{agent}/prompts/system.md` (new)

**Verifiable outcome:** `docker compose up` starts everything without manual config; PR that breaks the A2A wire format fails CI; change to `system.md` produces a different `prompt_hash` in the audit log

---

### Phase 5 — Certified Data Layer and MiFID II/MAR Compliance
**Horizon:** 2-4 months | **Complexity:** High

**Issues resolved:** yfinance dependency (uncertified data, unofficial scraping), incomplete audit trail for MiFID II

**Interventions:**
- **Market Data Provider abstraction:** `shared/market_data/provider.py` with `MarketDataProvider` interface and methods `get_fundamentals(ticker)`, `get_price(ticker)`. Implementations: `YFinanceProvider` (dev), `AlphaVantageProvider` (validation), `RefinitivProvider` (production). Selection via `MARKET_DATA_PROVIDER` env var. DataCollector and FundamentalAnalyst call the interface only
- **Data Lineage:** every `FundamentalsResult` includes `source_provider`, `source_timestamp`, `source_record_id`, `certifiable: bool`. Included in the audit log for every candidate analysed
- **Audit Log on PostgreSQL:** migrate from JSONL to append-only `audit_events` table (no UPDATE/DELETE guaranteed by DB trigger or policy). Indexes on `correlation_id`, `run_id`, `agent`, `timestamp`. Alternative certified immutability: AWS S3 Object Lock in COMPLIANCE mode
- **Prompt Registry:** table `prompt_versions` (`prompt_id`, `agent`, `content_hash`, `content`, `created_at`, `created_by`, `change_rationale`). The `prompt_hash` in the log references this table. CI updates the registry automatically on every change to `prompts/system.md` files
- **Output artifact signing:** SHA-256 of every `report_{timestamp}.html` and `raw_{timestamp}.json` recorded in the audit log at generation time in `shared/report.py`

**Recommended data providers:**
- Alpha Vantage — free tier for validation, ~$50/month enterprise
- Refinitiv Eikon Data API — EU financial services standard, guaranteed SLA, certified data for regulated use
- Bloomberg B-PIPE — if contract already exists in the organisation

**Minimum sequence for MiFID II (if compliance is the driver):** Phase 1 → Phase 5 → Phase 2. Phases 3, 4, 6 are operational but not direct compliance prerequisites.

> **Operational note — current state (June 2026):** in `DEMO_MODE=true` no external data sources are called — this phase is not blocking for development. Organisational exploration is ongoing in spare time.

**Organisational path (to explore):**

All data and news providers must be licensed before any production deployment or client stakeholder demo. The path:

1. **Check approved vendor list** — verify on ATCI portal whether Refinitiv LSEG or Bloomberg are already approved vendors in Accenture. If yes, skip the approval process and go directly to provisioning.
2. **Technology Architecture Board** — if the vendor is not on the list, open an approval request with use case (equity research pipeline, financial services, MiFID II). Contacts: practice Security Lead + Technology Architecture Board.
3. **Legal / Compliance** — evaluate news content redistribution clauses (Reuters Connect bundled in Refinitiv has restrictions on output to third parties).
4. **Contract / provisioning** — Refinitiv LSEG is preferable as a single choice: covers both fundamentals (replaces yfinance stub) **and** Reuters news (replaces RSS feeds) with one contract and certified MiFID II SLA.

**Why current RSS feeds are not enterprise-ready:**
- Reuters RSS: commercial/analytical use requires a Reuters Connect licence
- Yahoo Finance RSS / MarketWatch RSS: ToS prohibit automated use for commercial purposes
- Investing.com ×2: already removed (explicit ToS violation)

**Key files:** `shared/market_data/provider.py` (new), `shared/audit.py` (migration to DB), `shared/tools/yfinance_tool.py` (refactoring), `shared/report.py`

**Verifiable outcome:** `MARKET_DATA_PROVIDER=alphavantage` completes with `data_lineage.source_provider: "alphavantage"` in the log; SQL query on `audit_events` returns all runs with a specific prompt version

---

### Phase 6 — Kubernetes, High Availability and Horizontal Scalability
**Horizon:** 4-6 months | **Complexity:** High

**Issues resolved:** orchestrator SPOF (complete), monolithic payload (complete), horizontal scalability

**Interventions:**
- **Helm Chart** for each agent: `Deployment` (replicas: 2), `Service`, `HorizontalPodAutoscaler`. `ExternalSecret` from Azure Key Vault via External Secrets Operator
- **Orchestrator as Kubernetes Job:** not an always-on service but a `Job` triggered by API or `CronJob`. Each run is an independent Pod — SPOF eliminated
- **Artifact Store:** `shared/artifact_store.py` with `put_artifact(run_id, key, data) -> str` and `get_artifact(ref) -> Any`. The `PipelineState` carries only references (`news_ref`, `fundamentals_ref`), not the data. Data in MinIO (self-hosted, S3-compatible) or Azure Blob Storage. Resolves the monolithic payload architecturally
- **Service Mesh (Istio):** automatic inter-agent mTLS, distributed tracing (Jaeger), infrastructure-level circuit breaking. Phase 2 HMAC authentication can be deprecated
- **Full OpenTelemetry:** instrument A2A calls as spans with attributes `correlation_id`, `model.id`, `token.*`. Export to Jaeger (traces) + Prometheus (metrics) + Loki (logs)
- **Grafana Dashboard:** P50/P95/P99 latency per agent, token usage, pipeline success rate, uptime

**Technologies:** Kubernetes, Helm, Istio, MinIO / Azure Blob Storage, OpenTelemetry, Jaeger, Prometheus, Grafana, Loki, Argo CD

**Key files:** `helm/` (new), `shared/artifact_store.py` (new), `orchestrator/main.py` (artifact ref extraction)

**Verifiable outcome:** K8s node drain during a run → Job rescheduled, resumes from checkpoint; Jaeger trace shows 5 agent spans with latencies for `correlation_id`; `PipelineState` < 10KB even with 50 news in input

---

## Summary

| Phase | Name | Complexity | Horizon |
|------|------|-------------|---------|
| 1 | Observability and Basic Audit Trail | Low | 2-3 weeks |
| 2 | Inter-agent Security and Secret Management | Medium | 3-4 weeks |
| 3 | Resilience, Retry and Checkpointing | Medium | 4-6 weeks |
| 4 | Containerization and CI/CD | Medium-High | 6-8 weeks |
| 5 | Certified Data Layer and MiFID II Compliance | High | 2-4 months |
| 6 | Kubernetes, HA and Horizontal Scalability | High | 4-6 months |

**Architectural note:** all 6 agents use the native Anthropic SDK (`react_loop()` where tool use is needed, direct call for ReportWriter). BeeAI, OpenAI Agents SDK and Smolagents removed — reduced dependencies, uniform pattern, no prefill constraint.

---

## Testing Strategy (cross-cutting)

- **Phases 1-2:** unit tests on `shared/audit.py`, `shared/sanitize.py`, `shared/secrets.py` — deterministic, coverage > 90%, no LLM
- **Phase 3:** resilience tests with mock server simulating crashes and rate limits — verifies circuit breaker and checkpointing
- **Phase 4:** A2A contract tests in CI on mock server — wire format, agent card schema, protocol versioning
- **Phase 5:** golden dataset (3-5 runs with fixed input) — verifies structural properties of output, not free text
- **Phase 6:** load testing with K6/Locust — 10 concurrent runs, P95 latency, checkpointer behaviour under load

---

## Future Orchestrator Evolution — from Sequencer to Reasoning Agent

The current orchestrator is a glorified sequencer: it executes the 6 nodes in fixed order regardless of input, with no capacity to reason about what to do and why. The LangGraph graph is linear and exploits none of its advanced features.

The long-term objective is to transform it into a **reasoning-first orchestrator**: an agent that receives user input, evaluates context, and autonomously decides which agents to involve, in what order, and with what depth of analysis.

- **Input-driven dynamic routing:** if the user specifies a single ticker with an already-formed thesis, the orchestrator can skip NewsSentiment and FundamentalAnalyst and delegate directly to RiskAssessor. If it receives a market theme without tickers, it can start a discovery loop before proceeding with fundamental analysis.
- **Reasoning-controlled parallelism:** DataCollector and NewsSentiment are logically independent — an intelligent orchestrator would run them in parallel (`asyncio.gather`) instead of in series, reducing pipeline latency by 30-40%.
- **Refinement loop:** if FundamentalAnalyst identifies fewer than 2 valid candidates, the orchestrator can autonomously decide to re-run NewsSentiment with a different focus, or expand the ticker universe, before proceeding to reporting.
- **Tool calling as a primitive:** each A2A agent becomes a tool in the LLM sense — the orchestrator calls them with `tool_use` via the cloud provider (Bedrock/Vertex/Azure AI Foundry), and the model decides the optimal sequence based on context accumulated in `PipelineState`.
- **Human-in-the-loop:** LangGraph supports `interrupt_before` on any node — the reasoning orchestrator can pause after FundamentalAnalyst, present candidates to the human analyst for validation, and resume only after confirmation.

This intervention is architecturally separate from Phases 1-6 (it is neither a prerequisite nor a blocker) and requires Phase 0b (`shared/llm_client.py` abstraction) to be complete in order to have a cloud-managed client on which reasoning can run. The natural entry point is after Phase 3 (checkpointing available to support refinement loops) and before Phase 6 (before scaling, optimise the logic).

---

## End-to-end verification of the completed plan

At the end of all phases:
1. `docker compose up` (or `kubectl apply -f helm/`) starts the complete system
2. `uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI` completes successfully
3. The audit log shows for each agent: `correlation_id`, `prompt_hash`, `model_id`, `token_usage`, `data_lineage.source_provider`
4. `POST /tasks` without HMAC signature → HTTP 401
5. Kill orchestrator process mid-run → restart with same `run_id` resumes from checkpoint
6. Jaeger trace for `correlation_id` shows the complete sequence of all 6 agents
