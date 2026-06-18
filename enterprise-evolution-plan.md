# Piano di Evoluzione Enterprise — Equity Researcher A2A

## Baseline Architetturale (stato pre-evoluzione — 16 giugno 2026)

> Questa sezione documenta l'architettura originale e i rischi identificati che hanno guidato la definizione delle fasi. Incorpora i contenuti dell'architectural-review condotta al momento del kick-off enterprise.

### Architettura originale (stack pre-migrazione)

| Componente | Tecnologia originale | Stato attuale |
|---|---|---|
| Orchestrator | LangGraph StateGraph | **Invariato** |
| DataCollector | OpenAI Agents SDK + LiteLLM | **Migrato** → Anthropic SDK native ReAct |
| NewsSentiment | Smolagents (HuggingFace) | **Migrato** → Anthropic SDK native ReAct |
| FundamentalAnalyst | BeeAI ReActAgent | **Migrato** → Anthropic SDK native ReAct |
| RiskAssessor | BeeAI ReActAgent | **Migrato** → Anthropic SDK native ReAct |
| ReportWriter | Anthropic SDK direct | **Invariato** |
| Dati fondamentali | yfinance (scraping Yahoo Finance) | **Rimosso** → stub NotImplementedError (Fase 5) |
| Dati news | RSS Reuters/Yahoo/MarketWatch + Investing.com ×2 | Investing.com **rimosso**, altri ⚠️ da verificare con Legal |

### Rischi identificati e stato attuale

| Priorità | Rischio | Fase | Stato |
|---|---|:---:|---|
| 1 | Nessun audit trail — incompatibile con compliance financial services | 1 | ✅ Risolto |
| 2 | Porte 8001-8005 aperte senza autenticazione inter-agente | 2 | ✅ Risolto (HMAC-SHA256) |
| 3 | Prompt injection da feed RSS — input non fidato passato ai modelli | 2 | ✅ Risolto (shared/sanitize.py) |
| 4 | Nessun retry strutturato, nessun circuit breaker, SPOF orchestratore | 3 | ✅ Risolto (tenacity + circuit breaker custom) |
| 5 | yfinance — dato non certificato, non auditabile, scraping non ufficiale | 5 | ✅ Stub rimosso anticipatamente — provider Fase 5 |
| 6 | Payload monolitico crescente — rischio context overflow su batch grandi | 3/6 | ⚠️ Parzialmente (windowing Fase 3) — soluzione strutturale in Fase 6 |
| 7 | Ambiente non riproducibile, prompt non versionati, nessun contract testing | 4 | ⏳ Pendente |
| 8 | Provider LLM non approvato (ANTHROPIC_API_KEY diretta) | 0 | ✅ Risolto (shared/llm_client.py + DEMO_MODE) |

---

## Context

Il sistema è un prototipo funzionante con una base architettuale corretta (A2A, LangGraph, separazione dei concern, guardrail deterministici). Il gap verso un deployment enterprise non è nei pattern scelti, ma nella mancanza dei layer trasversali: sicurezza, osservabilità, auditabilità, resilienza.

Il piano è strutturato in **6 fasi milestone ordinate per priorità**. Non riscrive l'architettura — la evolve incrementalmente. Il contratto A2A rimane stabile per tutta la durata del piano.

---

## Valutazione Autorizzazioni Tecnologiche

> **Nota metodologica:** questa valutazione è basata su criteri generali enterprise (maturità del vendor, presenza di SLA commerciali, licenze d'uso esplicite, track record di adozione in financial services). Non sostituisce la verifica formale con il CISO / Technology Architecture Board / Approved Vendor List interna. I referenti da coinvolgere sono: Security Lead di practice, portale ATCI, myLearning Security, Technology Architecture Board Accenture.

**Legenda:** ✅ Approvabile senza frizioni &nbsp;|&nbsp; ⚠️ Da verificare con Architecture Board &nbsp;|&nbsp; ❌ Alto rischio — richiede approvazione esplicita o sostituzione

---

### Stack Attuale

| Tecnologia | Categoria | Stato | Motivazione |
|---|---|:---:|---|
| Python 3.11 | Runtime | ✅ | Standard de facto enterprise |
| FastAPI | Web framework | ✅ | Ampiamente adottato, produzione-ready, Pydantic nativo |
| Pydantic v2 | Validazione | ✅ | Standard de facto per Python API |
| httpx | HTTP client | ✅ | Maturo, testato, asyncio-native |
| JSON-RPC 2.0 | Protocollo inter-agente | ✅ | Standard aperto, nessuna dipendenza vendor |
| uv | Package manager | ⚠️ | Relativamente nuovo (2023); verificare se la policy interna ammette alternative a pip/poetry. Rischio basso ma da confermare |
| LangGraph (LangChain) | Orchestrazione | ⚠️ | Ampiamente adottato in enterprise AI, ma evolve rapidamente. Verificare versione minima supportata e se LangChain Inc. è vendor approvato |
| OpenAI Agents SDK | Framework agente | ~~⚠️~~ | **Rimosso** — sostituito da Anthropic SDK native ReAct (`shared/react.py`) |
| Smolagents (HuggingFace) | Framework agente | ~~⚠️~~ | **Rimosso** — sostituito da Anthropic SDK native ReAct |
| BeeAI (IBM) | Framework agente | ~~⚠️~~ | **Rimosso** — sostituito da Anthropic SDK native ReAct (eliminato anche il vincolo prefill / Haiku) |
| Anthropic API (diretta) | LLM provider | ❌ | **Non approvato per nessun workload applicativo**, incluso lo sviluppo locale. La policy Accenture vieta esplicitamente l'uso di `ANTHROPIC_API_KEY` personali o non gestite. Tutti gli accessi LLM devono passare per AWS Bedrock, Google Vertex AI, o Azure AI Foundry con autenticazione IAM/service account. Vedi Fase 0 |
| AWS Bedrock (Claude) | LLM provider | ✅ | Pattern approvato Accenture per application workloads. Autenticazione via IAM role. Provisioning tramite CAPP (Global IT) o CMO (resto Accenture) |
| Azure AI Foundry (Claude) | LLM provider | ✅ | Pattern approvato Accenture per application workloads. Coerente con Azure Key Vault (Fase 2). Autenticazione via Azure Managed Identity / service principal |
| Google Vertex AI (Claude) | LLM provider | ✅ | Pattern approvato Accenture per application workloads. Autenticazione via GCP service account |
| yfinance | Fonte dati | ❌ | Scraping non ufficiale di Yahoo Finance. Nessun SLA, nessuna licenza d'uso commerciale, dati non certificati. Incompatibile con financial services enterprise e con MiFID II. **Rimosso anticipatamente — stub NotImplementedError in shared/tools/yfinance_tool.py. Sostituzione con provider certificato in Fase 5** |
| RSS Reuters / Yahoo Finance / MarketWatch | Fonte dati | ⚠️ | Feed pubblici senza licenza dati esplicita per uso commerciale/analitico. Reuters ha policy restrittive sul riuso dei contenuti. Da verificare con Legal prima di un deployment production |
| RSS Investing.com ×2 | Fonte dati | ❌ | Investing.com ha storicamente bloccato scraping e non offre licenza dati pubblica. Alto rischio di violazione ToS. **Rimosso anticipatamente da shared/tools/rss_feed.py** |

---

### Tecnologie Pianificate per Fase

| Tecnologia | Fase | Categoria | Stato | Motivazione |
|---|:---:|---|:---:|---|
| `structlog` | 1 | Logging | ✅ | Libreria Python matura, nessuna dipendenza vendor esterna |
| `hmac` (stdlib) | 2 | Sicurezza | ✅ | Libreria standard Python, nessuna dipendenza aggiuntiva |
| `bleach` | 2 | Sanitizzazione | ✅ | Libreria Python consolidata (Mozilla), uso comune in produzione |
| `azure-keyvault-secrets` | 2 | Secret management | ✅ | Azure è vendor approvato Accenture; Key Vault è la soluzione standard |
| `tenacity` | 3 | Retry logic | ✅ | Libreria Python matura, ampiamente usata in enterprise |
| `pybreaker` | 3 | Circuit breaker | ⚠️ | Meno diffuso di alternativa Resilience4j (Java). Verificare se approvato o preferire implementazione custom con `tenacity` |
| `langgraph-checkpoint-sqlite` | 3 (dev) | Checkpointing | ✅ | SQLite stdlib, nessun servizio esterno in dev |
| `langgraph-checkpoint-postgres` | 3 (prod) | Checkpointing | ✅ | PostgreSQL standard enterprise |
| Docker / Docker Compose | 4 | Containerizzazione | ✅ | Standard enterprise, ampiamente adottato |
| GitHub Actions | 4 | CI/CD | ⚠️ | Verificare se la policy prevede Azure DevOps come piattaforma obbligatoria per CI/CD. In Accenture i progetti client spesso richiedono Azure DevOps |
| Azure DevOps Pipelines | 4 | CI/CD | ✅ | Preferibile a GitHub Actions in contesti Accenture — vendor Microsoft, standard enterprise |
| `ruff` | 4 | Linting | ✅ | Standard de facto Python, nessuna dipendenza vendor |
| `mypy` | 4 | Type checking | ✅ | Standard Python, ampiamente usato in enterprise |
| Alpha Vantage | 5 | Dati di mercato | ⚠️ | Provider con API ufficiale e SLA, ma da verificare se è in approved vendor list per dati finanziari. Free tier non adatto a produzione |
| Refinitiv / LSEG Data API | 5 | Dati di mercato | ✅ | Standard financial services EU, dati certificati, SLA garantito, compatibile MiFID II |
| Bloomberg B-PIPE | 5 | Dati di mercato | ✅ | Standard de facto in financial services — se contratto già presente nell'organizzazione |
| PostgreSQL (audit DB) | 5 | Database | ✅ | Standard enterprise, ampiamente approvato |
| AWS S3 Object Lock | 5 | Immutabilità log | ⚠️ | Verificare se AWS è vendor approvato nel contesto di deployment (vs Azure-first policy) |
| Kubernetes | 6 | Orchestrazione container | ✅ | Standard enterprise, ampiamente approvato |
| Helm | 6 | Package manager K8s | ✅ | Standard de facto per K8s, ampiamente adottato |
| Istio (Service Mesh) | 6 | Networking / mTLS | ⚠️ | Maturo e ampiamente adottato, ma aggiunge complessità operativa significativa. Verificare se la practice ha competenze interne o se preferire Linkerd (più semplice) |
| MinIO | 6 | Artifact store (self-hosted) | ⚠️ | Self-hosted S3-compatible. Preferire Azure Blob Storage se cloud-first è il mandato |
| Azure Blob Storage | 6 | Artifact store | ✅ | Standard Azure, vendor approvato |
| OpenTelemetry | 6 | Osservabilità | ✅ | Standard CNCF, vendor-neutral, ampiamente adottato |
| Prometheus | 6 | Metriche | ✅ | Standard CNCF, ampiamente adottato |
| Grafana | 6 | Dashboard | ✅ | Standard enterprise per observability |
| Loki | 6 | Log aggregation | ✅ | Grafana Labs, ampiamente adottato in stack cloud-native |
| Argo CD | 6 | GitOps / CD | ⚠️ | Maturo e CNCF-graduated, ma verificare se la practice preferisce Azure DevOps per il deployment. Alternativa: Flux CD |

---

### Priorità di Verifica

Prima di avviare qualsiasi fase, verificare nell'ordine:

1. ~~**Cloud provider**~~ ✅ **Risolto** — `shared/llm_client.py` + `DEMO_MODE=true` in sviluppo locale. Per produzione: aprire ticket ServiceNow "Claude Enterprise" (Bedrock, Vertex AI, o Azure AI Foundry).
2. ~~**yfinance e RSS Investing.com**~~ ✅ **Risolto** — yfinance rimosso (stub NotImplementedError), Investing.com rimosso da rss_feed.py.
3. ~~**Smolagents / BeeAI / OpenAI Agents SDK**~~ ✅ **Risolto** — tutti rimossi, Anthropic SDK native ReAct uniforme.
4. **CI/CD platform** — decidere GitHub Actions vs Azure DevOps prima di Fase 4 per non dover migrare pipeline.
5. **Cloud provider per infrastruttura** — confermare Azure-first vs multi-cloud prima di Fase 6 (impatta scelta artifact store, secret manager, GitOps tool).

---

## Fasi di Intervento

### Fase 0 — Prerequisiti Enterprise (da completare prima di qualsiasi altra fase)
**Orizzonte:** 1-2 settimane (dipende dai tempi di approvazione interni) | **Complessità:** Organizzativa, non tecnica

**Contesto:** il sistema attuale usa `ANTHROPIC_API_KEY` in `.env`. Questa configurazione **non è approvata** per nessun workload applicativo Accenture — incluso lo sviluppo locale. Prima di procedere con qualsiasi fase tecnica è necessario risolvere l'accesso LLM nel modo approvato.

**Interventi:**

**0a — Ottenere accesso cloud-managed a Claude**
- Aprire ticket ServiceNow categoria "Claude Enterprise" specificando: use case applicativo (equity research agent pipeline), framework usati (LangGraph, Anthropic SDK native ReAct), cloud provider preferito
- Attendere provisioning tramite CAPP (Global IT) o CMO
- Output atteso: credenziali IAM (AWS role ARN, Azure service principal, o GCP service account) per accesso a Claude tramite il cloud provider assegnato

**0b — Progettare l'astrazione del client LLM**
- Creare `shared/llm_client.py` con factory `get_llm_client(provider: str) -> LLMClient` selezionata da env var `LLM_PROVIDER=bedrock|vertex|azure`
- Ogni agente smette di istanziare direttamente il proprio client LLM — chiama la factory
- La factory restituisce il client configurato per il provider: `AnthropicBedrock`, `AnthropicVertex`, o Azure AI Foundry endpoint
- Eliminare `ANTHROPIC_API_KEY` da `.env` e da tutti gli `agent.py`
- **Nota su BeeAI:** verificare se `AnthropicChatModel` supporta Bedrock/Vertex. Se non supportato, FundamentalAnalyst e RiskAssessor richiedono sostituzione del framework o del client interno prima di poter girare in modalità cloud-managed

**0c — Aggiornare il modello di sviluppo locale**
- Lo sviluppo locale usa le stesse credenziali cloud del provider assegnato (es. AWS profile locale, Azure CLI login, gcloud auth application-default)
- Nessuna eccezione "solo per dev locale" con API key diretta
- I test unitari e i contract test girano con mock LLM — non richiedono credenziali reali

**0d — Demo mode per sviluppo senza credenziali cloud**
- Aggiungere env var `DEMO_MODE=true` letta a startup in ogni `agent.py`
- In demo mode, `run_agent()` salta la chiamata LLM e restituisce un `A2ATaskResult` pre-confezionato con dati realistici ma fittizi (fondamentali inventati, news di esempio, candidati con scoring plausibile, report di placeholder)
- L'orchestratore, il grafo LangGraph, l'audit trail, il retry, il correlation ID girano normalmente — non sanno che gli agenti hanno restituito dati stub
- I dati demo sono file statici in `agents/{agent}/demo/response.json`, versionati in git — stabili e riproducibili
- Questo consente di sviluppare e testare tutte le feature trasversali (Fasi 1-4) con una pipeline end-to-end funzionante, senza nessuna credenziale cloud e senza violare policy
- **Non è un workaround alla policy**: nessuna chiamata LLM viene effettuata, nessun dato esce dal perimetro locale

**File principali:** `shared/llm_client.py` (nuovo), tutti gli `agent.py`, `.env.example` (aggiornato)

**Outcome verificabile:** `grep -r "ANTHROPIC_API_KEY" agents/` non trova occorrenze; `LLM_PROVIDER=bedrock` + credenziali AWS locali completa una run end-to-end; `LLM_PROVIDER=azure` completa la stessa run senza modifiche al codice degli agenti

---

### Fase 1 — Osservabilità e Audit Trail di Base
**Orizzonte:** 2-3 settimane | **Complessità:** Bassa | **Quick win ad alto impatto**

**Criticità risolte:** nessun correlation ID, nessun audit log, logging stdout non strutturato

**Interventi:**
- Aggiungere `correlation_id` (UUID v4) a `A2ATask.metadata` in `shared/a2a_models.py` — campo opzionale, retrocompatibile
- Strutturare il logging con `structlog` — ogni evento include `correlation_id`, `agent`, `model_id`, `duration_ms`, `status`, `token_usage`
- Creare `shared/audit.py`: funzione `write_audit_event(event)` che scrive in JSONL append-only su `output/audit_{date}.jsonl`. Ogni evento include `prompt_hash` (SHA-256 del system prompt), `input_hash`, `output_hash`
- Il `prompt_hash` traccia silenziosamente le modifiche ai prompt hardcoded senza versioning esplicito — evolve in Fase 4
- Aggiungere `GET /health` aggregato all'orchestratore (interroga i 5 `/health` in parallelo via `httpx.gather`)
- Estrarre il tracking `usage.input_tokens` / `usage.output_tokens` già esposto dall'Anthropic SDK in `report-writer/agent.py`

**File principali:** `shared/a2a_models.py`, `shared/audit.py` (nuovo), `orchestrator/main.py`, tutti gli `agent.py`

**Outcome verificabile:** ogni run produce `output/audit_{date}.jsonl` con un record per agente; tutti i log di una run condividono lo stesso `correlation_id`; `GET /orchestrator/health` ritorna stato aggregato

---

### Fase 2 — Sicurezza Inter-agente e Secret Management
**Orizzonte:** 3-4 settimane | **Complessità:** Media

**Criticità risolte:** porte 8001-8005 aperte senza autenticazione, API key in `.env`, prompt injection da RSS

**Interventi:**
- **Autenticazione HMAC-SHA256:** aggiungere header `X-A2A-Signature` (HMAC su body + timestamp) e `X-A2A-Timestamp` alle chiamate A2A. Middleware FastAPI di verifica in ogni agente. Finestra anti-replay di 30 secondi. Zero infrastruttura aggiuntiva — shared secret da secret manager
- **Secret Manager:** creare `shared/secrets.py` con factory `get_secret(key)`. In dev: legge da `.env` via `python-dotenv`. In produzione: legge da Azure Key Vault (`azure-keyvault-secrets`) o AWS Secrets Manager (`boto3`). Selezionato da env var `SECRET_PROVIDER=local|azure|aws`. Eliminare tutti gli `os.getenv("ANTHROPIC_API_KEY")` diretti dagli `agent.py`
- **RSS Sanitization:** creare `shared/sanitize.py` con `sanitize_rss_item(title, summary)` — strip HTML (`bleach`), troncamento a lunghezze massime, rimozione caratteri di controllo. Applicare in `shared/tools/rss_feed.py` prima che il testo raggiunga i prompt
- IP allowlist documentata: porte 8001-8005 accessibili solo dall'orchestratore (Docker Compose network interno in Fase 4, NetworkPolicy in Fase 6)

**Tecnologie:** `hmac` stdlib, `bleach`, `azure-keyvault-secrets`

**File principali:** `shared/secrets.py` (nuovo), `shared/sanitize.py` (nuovo), `shared/tools/rss_feed.py`, `orchestrator/main.py`, tutti gli `agent.py`

**Outcome verificabile:** `POST /tasks` senza `X-A2A-Signature` → HTTP 401; `grep -r "os.getenv" agents/` non trova API key dirette; RSS contaminato con tag HTML arriva sanificato all'agente

---

### Fase 3 — Resilienza, Retry Strutturato e Checkpointing
**Orizzonte:** 4-6 settimane | **Complessità:** Media

**Criticità risolte:** nessun checkpointing, SPOF orchestratore (parziale), nessun circuit breaker, `asyncio.sleep(90)` hardcoded, payload monolitico (parziale)

**Interventi:**
- **LangGraph Checkpointing:** passare `SqliteSaver` (dev) o `PostgresSaver` (prod) a `StateGraph.compile()` in `_build_graph()`. Il `run_id` di Fase 1 diventa il `thread_id`. Crash a stadio 4 → ripartenza da stadio 4 con stesso `run_id`
- **Retry strutturato:** sostituire `asyncio.sleep(90)` hardcoded con `@retry(wait=wait_exponential(min=10, max=120), stop=stop_after_attempt(5))` via `tenacity`. Definire eccezioni tipizzate `RateLimitError`, `AgentTimeoutError`, `AgentUnavailableError` in `shared/exceptions.py`
- **Circuit Breaker:** `pybreaker` su `send_task_with_retry` nell'orchestratore. Apertura del circuito dopo 3 errori transienti in 5 minuti — fallisce immediatamente invece di aspettare il timeout
- **Payload Windowing:** costanti `MAX_NEWS_PAYLOAD` e `MAX_CANDIDATES_PAYLOAD` configurabili. Il NewsSentiment restituisce top-N news per rilevanza. Soluzione strutturale completa in Fase 6 (artifact store)
- **Graceful Degradation:** conditional edge in LangGraph — se NewsSentiment fallisce, prosegue con `news=[]` e nota metodologica; se DataCollector fallisce su un ticker, quel ticker viene marcato come `data_unavailable` e gli altri procedono

**Tecnologie:** `langgraph-checkpoint-sqlite` (dev), `langgraph-checkpoint-postgres` (prod), `tenacity`, `pybreaker`

**File principali:** `orchestrator/main.py`, `shared/exceptions.py` (nuovo)

**Outcome verificabile:** kill del processo orchestratore a stadio 3 → riavvio con stesso `run_id` riprende da stadio 4; agente irraggiungibile apre il circuit breaker dopo 3 fallimenti; pipeline completa con NewsSentiment offline producendo report con nota di degradation

---

### Fase 4 — Containerizzazione e CI/CD Pipeline
**Orizzonte:** 6-8 settimane | **Complessità:** Media-Alta

**Criticità risolte:** ambiente non riproducibile, prompt non versionati, nessun contract testing A2A

**Interventi:**
- **Dockerfile per agente** in ogni `agents/{agent}/Dockerfile`. Immagine base `python:3.11-slim`. Immagine orchestratore separata. Gestione della dipendenza da `sys.path.insert()` presenti negli `agent.py` (da risolvere con packaging corretto)
- **Docker Compose** al root: 5 agenti + orchestratore + PostgreSQL (checkpointing) su rete interna `agents-net`. Porte 8001-8005 non esposte sull'host
- **CI/CD Pipeline** (GitHub Actions o Azure DevOps): lint (`ruff`), type check (`mypy` su `shared/`), smoke tests offline, contract tests A2A
- **Contract tests A2A** in `tests/test_contracts.py`: verifica che ogni agent card rispetti schema `AgentCard`, che `tasks/send` sia implementato, che il wire format corrisponda a `A2ATaskResult` — girano senza LLM reali via `httpx.MockTransport`
- **Prompt come file:** spostare i system prompt hardcoded (`_REPORT_SYSTEM`, `_QA_SYSTEM`, `_INSTRUCTIONS`) da stringhe in `agent.py` a file `agents/{agent}/prompts/system.md`. L'`agent.py` legge a startup. Il `prompt_hash` di Fase 1 diventa SHA-256 del file — tracciabile con `git log`
- **Versioning del protocollo A2A:** campo `version: "1.0"` in `JsonRpcRequest` e `AgentCard`. CI verifica coerenza con `pyproject.toml`

**Tecnologie:** Docker, Docker Compose, GitHub Actions / Azure DevOps, `ruff`, `mypy`, `schemathesis`

**File principali:** `Dockerfile` per agente (nuovi), `docker-compose.yml` (nuovo), `.github/workflows/ci.yml` (nuovo), `agents/{agent}/prompts/system.md` (nuovi)

**Outcome verificabile:** `docker compose up` avvia tutto senza config manuale; PR che rompe il wire format A2A non passa CI; modifica a `system.md` produce `prompt_hash` diverso nel log di audit

---

### Fase 5 — Data Layer Certificato e Compliance MiFID II/MAR
**Orizzonte:** 2-4 mesi | **Complessità:** Alta

**Criticità risolte:** dipendenza yfinance (dato non certificato, scraping non ufficiale), audit trail incompleto per MiFID II

**Interventi:**
- **Astrazione Market Data Provider:** `shared/market_data/provider.py` con interfaccia `MarketDataProvider` e metodi `get_fundamentals(ticker)`, `get_price(ticker)`. Implementazioni: `YFinanceProvider` (dev), `AlphaVantageProvider` (validazione), `RefinitivProvider` (produzione). Selezione via `MARKET_DATA_PROVIDER` env var. Il `DataCollector` e `FundamentalAnalyst` chiamano solo l'interfaccia
- **Data Lineage:** ogni `FundamentalsResult` include `source_provider`, `source_timestamp`, `source_record_id`, `certifiable: bool`. Incluso nell'audit log per ogni candidato analizzato
- **Audit Log su PostgreSQL:** migrare da JSONL a tabella `audit_events` append-only (nessun UPDATE/DELETE garantito da trigger DB o policy). Indici su `correlation_id`, `run_id`, `agent`, `timestamp`. Alternativa immutabilità certificata: AWS S3 Object Lock in COMPLIANCE mode
- **Registro dei Prompt:** tabella `prompt_versions` (`prompt_id`, `agent`, `content_hash`, `content`, `created_at`, `created_by`, `change_rationale`). Il `prompt_hash` nel log referenzia questa tabella. CI aggiorna il registro automaticamente ad ogni modifica ai file `prompts/system.md`
- **Firma artefatti output:** SHA-256 di ogni `report_{timestamp}.html` e `raw_{timestamp}.json` registrato nell'audit log al momento della generazione in `shared/report.py`

**Provider dati raccomandati:**
- Alpha Vantage — free tier per validazione, ~$50/mese enterprise
- Refinitiv Eikon Data API — standard financial services EU, SLA garantito, dati certificati per uso regolamentato
- Bloomberg B-PIPE — se contratto già presente nell'organizzazione

**Sequenza minima per MiFID II (se il driver è la compliance):** Fase 1 → Fase 5 → Fase 2. Le fasi 3, 4, 6 sono operative ma non prerequisiti diretti di conformità normativa.

> **Nota operativa — stato attuale (giugno 2026):** in `DEMO_MODE=true` nessuna fonte dati esterna viene chiamata — questa fase non è bloccante per lo sviluppo. L'esplorazione organizzativa è in corso in spare time.

**Percorso organizzativo (da esplorare):**

Tutti i provider dati e news devono essere licenziati prima di qualsiasi deployment production o demo a stakeholder client. Il percorso:

1. **Verificare approved vendor list** — controllare su portale ATCI se Refinitiv LSEG o Bloomberg sono già vendor approvati in Accenture. Se sì, si bypassa il processo di approvazione e si va direttamente al provisioning.
2. **Technology Architecture Board** — se il vendor non è in lista, aprire richiesta di approvazione con use case (equity research pipeline, financial services, MiFID II). Referenti: Security Lead della practice + Technology Architecture Board.
3. **Legal / Compliance** — valutazione clausole di redistribuzione dei contenuti news (Reuters Connect incluso in Refinitiv ha restrizioni su output a terzi).
4. **Contratto / provisioning** — Refinitiv LSEG è preferibile come scelta unica: copre fondamentali (sostituisce yfinance stub) **e** news Reuters (sostituisce i feed RSS) con un solo contratto e SLA certificato MiFID II.

**Perché i feed RSS attuali non sono enterprise-ready:**
- Reuters RSS: uso commerciale/analitico richiede licenza Reuters Connect
- Yahoo Finance RSS / MarketWatch RSS: ToS vietano uso automatizzato a fini commerciali
- Investing.com ×2: già rimosso (ToS violazione esplicita)

**File principali:** `shared/market_data/provider.py` (nuovo), `shared/audit.py` (migrazione a DB), `shared/tools/yfinance_tool.py` (refactoring), `shared/report.py`

**Outcome verificabile:** `MARKET_DATA_PROVIDER=alphavantage` completa con `data_lineage.source_provider: "alphavantage"` nel log; query SQL su `audit_events` restituisce tutti i run con una specifica versione del prompt

---

### Fase 6 — Kubernetes, Alta Disponibilità e Scalabilità Orizzontale
**Orizzonte:** 4-6 mesi | **Complessità:** Alta

**Criticità risolte:** SPOF orchestratore (completo), payload monolitico (completo), scalabilità orizzontale

**Interventi:**
- **Helm Chart** per ogni agente: `Deployment` (replicas: 2), `Service`, `HorizontalPodAutoscaler`. `ExternalSecret` da Azure Key Vault via External Secrets Operator
- **Orchestratore come Kubernetes Job:** non è un servizio always-on ma un `Job` triggered da API o `CronJob`. Ogni run è un Pod indipendente — SPOF eliminato
- **Artifact Store:** `shared/artifact_store.py` con `put_artifact(run_id, key, data) -> str` e `get_artifact(ref) -> Any`. Il `PipelineState` porta solo riferimenti (`news_ref`, `fundamentals_ref`), non i dati. Dati in MinIO (self-hosted, S3-compatible) o Azure Blob Storage. Risolve il payload monolitico in modo architetturale
- **Service Mesh (Istio):** mTLS inter-agente automatico, distributed tracing (Jaeger), circuit breaking infrastrutturale. L'autenticazione HMAC di Fase 2 può essere deprecata
- **OpenTelemetry completo:** strumentare chiamate A2A come span con attributi `correlation_id`, `model.id`, `token.*`. Export verso Jaeger (traces) + Prometheus (metrics) + Loki (logs)
- **Dashboard Grafana:** latenza P50/P95/P99 per agente, token usage, pipeline success rate, uptime

**Tecnologie:** Kubernetes, Helm, Istio, MinIO / Azure Blob Storage, OpenTelemetry, Jaeger, Prometheus, Grafana, Loki, Argo CD

**File principali:** `helm/` (nuovo), `shared/artifact_store.py` (nuovo), `orchestrator/main.py` (estrazione artifact refs)

**Outcome verificabile:** nodo K8s in drain durante una run → Job rischedulato, riprende dal checkpoint; trace Jaeger mostra 5 span agente con latenze per `correlation_id`; `PipelineState` < 10KB anche con 50 news in input

---

## Riepilogo

| Fase | Nome | Complessità | Orizzonte |
|------|------|-------------|-----------|
| 1 | Osservabilità e Audit Trail di Base | Bassa | 2-3 settimane |
| 2 | Sicurezza Inter-agente e Secret Management | Media | 3-4 settimane |
| 3 | Resilienza, Retry e Checkpointing | Media | 4-6 settimane |
| 4 | Containerizzazione e CI/CD | Media-Alta | 6-8 settimane |
| 5 | Data Layer Certificato e Compliance MiFID II | Alta | 2-4 mesi |
| 6 | Kubernetes, HA e Scalabilità Orizzontale | Alta | 4-6 mesi |

**Nota architetturale:** tutti e 5 gli agenti usano Anthropic SDK nativo (`react_loop()` dove serve tool use, chiamata diretta per ReportWriter). BeeAI, OpenAI Agents SDK e Smolagents rimossi — dipendenze ridotte, pattern uniforme, nessun vincolo di prefill.

---

## Testing Strategy (trasversale)

- **Fase 1-2:** unit test su `shared/audit.py`, `shared/sanitize.py`, `shared/secrets.py` — deterministici, coverage > 90%, nessun LLM
- **Fase 3:** resilience test con mock server che simula crash e rate limit — verifica circuit breaker e checkpointing
- **Fase 4:** contract test A2A in CI su mock server — wire format, agent card schema, versioning protocollo
- **Fase 5:** golden dataset (3-5 run con input fisso) — verifica proprietà strutturali dell'output, non il testo
- **Fase 6:** load test con K6/Locust — 10 run concorrenti, latenza P95, comportamento checkpointer sotto carico

---

## Evoluzione futura dell'Orchestratore — da Sequencer a Reasoning Agent

L'orchestratore attuale è un sequencer glorificato: esegue i 5 nodi in ordine fisso indipendentemente dall'input, senza capacità di ragionamento su cosa fare e perché. Il grafo LangGraph è lineare e non sfrutta nessuna delle sue feature avanzate.

L'obiettivo a lungo termine è trasformarlo in un **orchestratore reasoning-first**: un agente che riceve l'input dell'utente, valuta il contesto, e decide autonomamente quali agenti coinvolgere, in quale ordine, e con quale profondità di analisi.

- **Routing dinamico basato sull'input:** se l'utente specifica un singolo ticker con tesi già formata, l'orchestratore può saltare NewsSentiment e FundamentalAnalyst e delegare direttamente a RiskAssessor. Se riceve un tema di mercato senza ticker, può avviare un loop di discovery prima di procedere con l'analisi fondamentale.
- **Parallelismo controllato dal reasoning:** DataCollector e NewsSentiment sono logicamente indipendenti — un orchestratore intelligente li eseguirebbe in parallelo (`asyncio.gather`) invece di in serie, riducendo la latenza della pipeline del 30-40%.
- **Loop di raffinamento:** se il FundamentalAnalyst identifica meno di 2 candidati validi, l'orchestratore può decidere autonomamente di rieseguire NewsSentiment con un focus diverso, o di espandere l'universo dei ticker, prima di procedere al reporting.
- **Tool calling come primitiva:** ogni agente A2A diventa un tool nel senso LLM — l'orchestratore li chiama con `tool_use` tramite il cloud provider (Bedrock/Vertex/Azure AI Foundry), e il modello decide la sequenza ottimale in base al contesto accumulato nel `PipelineState`.
- **Human-in-the-loop:** LangGraph supporta `interrupt_before` su qualsiasi nodo — l'orchestratore reasoning può fermarsi dopo FundamentalAnalyst, presentare i candidati all'analista umano per validazione, e riprendere solo dopo conferma.

Questo intervento è architetturalmente separato dalle Fasi 1-6 (non è un prerequisito né un blocco) e richiede che la Fase 0b (astrazione `shared/llm_client.py`) sia completata per avere un client cloud-managed su cui il reasoning possa girare. Il punto di ingresso naturale è dopo la Fase 3 (checkpointing disponibile per supportare i loop di raffinamento) e prima della Fase 6 (prima di scalare, ottimizzare la logica).

---

## Verifica end-to-end del piano completato

Al termine di tutte le fasi:
1. `docker compose up` (o `kubectl apply -f helm/`) avvia il sistema completo
2. `uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI` completa con successo
3. L'audit log mostra per ogni agente: `correlation_id`, `prompt_hash`, `model_id`, `token_usage`, `data_lineage.source_provider`
4. `POST /tasks` senza firma HMAC → HTTP 401
5. Kill del processo orchestratore a metà run → riavvio con stesso `run_id` riprende dal checkpoint
6. Trace Jaeger per il `correlation_id` mostra la sequenza completa dei 5 agenti
