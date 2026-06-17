# Valutazione Architettuale — Equity Researcher A2A
**Ruolo:** AI Architect — Valutazione Enterprise
**Data:** 16 giugno 2026

---

## 1. Architettura Generale

### Struttura complessiva

Il sistema è una **pipeline sequenziale a 5 stadi** orchestrata da un singolo processo centrale. Ogni stadio è un microservizio FastAPI indipendente che espone tre endpoint (`/tasks`, `/.well-known/agent.json`, `/health`) e comunica tramite JSON-RPC 2.0 su HTTP sincrono.

```
CLI / Utente
    │
    ▼
Orchestrator (LangGraph, processo unico)
    │
    ├─► DataCollector     :8001  (OpenAI Agents SDK + LiteLLM)
    ├─► NewsSentiment      :8002  (Smolagents + LiteLLM)
    ├─► FundamentalAnalyst :8003  (BeeAI ReActAgent)
    ├─► RiskAssessor       :8004  (BeeAI ReActAgent)
    └─► ReportWriter       :8005  (Anthropic SDK direct)
```

### Componenti principali

| Componente | Tecnologia | Responsabilità |
|---|---|---|
| Orchestrator | LangGraph StateGraph | Routing, stato accumulato, error propagation |
| DataCollector | OpenAI Agents SDK | Fetch fondamentali da yfinance |
| NewsSentiment | Smolagents | RSS → news strutturate + temi |
| FundamentalAnalyst | BeeAI ReActAgent | News + fondamentali → candidati |
| RiskAssessor | BeeAI ReActAgent | Candidati → scoring + scenari |
| ReportWriter | Anthropic SDK | Report + QA pass (2 chiamate LLM) |
| shared/a2a_models.py | Pydantic | Wire format JSON-RPC condiviso |
| shared/validators.py | Python puro | Constraint check deterministico |
| shared/report.py | Jinja2 | Rendering HTML |

### Flusso del dato

```
ticker list
    │
    ▼ (DataCollector)
fondamentali yfinance (dict per ticker)
    │
    ▼ (NewsSentiment)
news RSS strutturate + temi estratti
    │
    ▼ (FundamentalAnalyst)
lista candidati con razionale
    │
    ▼ (RiskAssessor)
candidati scorati (5 dimensioni, max 50) + scenari
    │
    ▼ (ReportWriter)
report testuale italiano → QA pass → JSON strutturato + HTML
```

Tutto il payload cresce in un unico `PipelineState` (TypedDict di LangGraph). Ogni nodo aggiunge il proprio output allo stato senza sovrascrivere i precedenti. Il payload si accumula lungo la pipeline e viene passato per intero ad ogni agente successivo — **nessuna forma di streaming parziale o windowing**.

---

## 2. Orchestrazione degli Agenti

### Modello di interazione

L'orchestrazione è **centralizzata e strettamente sequenziale**: ogni nodo LangGraph si completa prima che il successivo inizi. Non esiste parallelismo, né branching condizionale, né retry a livello di grafo. Il grafo è lineare:

```
collect_data → analyze_news → analyze_fundamentals → assess_risks → write_report → END
```

### Ruolo di LangGraph

LangGraph è usato come **sequencer glorificato**: definisce l'ordine dei nodi e trasporta lo stato, ma non sfrutta nessuna delle sue feature avanzate (conditional edges, parallel branches, human-in-the-loop, checkpointing). Il grafo viene compilato una volta a startup (`_build_graph()`) e invocato con `ainvoke`. Nella pratica attuale, un semplice loop `for` con `asyncio` avrebbe lo stesso effetto.

### Punti di forza

- **Semplicità cognitiva**: il flusso è lineare, leggibile, facile da debuggare manualmente.
- **Isolamento dei fallimenti**: ogni agente può fallire indipendentemente; l'orchestratore intercetta e propaga con `status=failed`.
- **Estensibilità dichiarativa**: aggiungere un nodo o un branch richiede solo modifiche a `_build_graph()`, senza toccare la logica dei nodi.
- **Stato esplicito**: `PipelineState` come TypedDict rende il contratto di dati visibile e tipizzato.

### Punti di debolezza

- **Nessun parallelismo**: DataCollector e NewsSentiment sono logicamente indipendenti ma eseguiti in serie. Per pipeline su molti ticker, questo moltiplica la latenza.
- **Nessun retry a livello di grafo**: se un agente restituisce `status=failed`, la pipeline si ferma. Non esiste logica di retry, fallback o degraded mode.
- **Stato monolitico crescente**: il payload passato agli agenti finali include tutti i dati degli stadi precedenti. Su molti ticker o news feed abbondanti, si rischia di superare i context window dei modelli.
- **Nessun checkpointing**: se il processo orchestratore crasha a stadio 4, tutta la pipeline riparte da zero. Non c'è persistenza intermedia.
- **Single point of failure**: l'orchestratore è un singolo processo Python. Non esiste HA, load balancing, o supervisione.

---

## 3. Layer di Comunicazione

### A2A (JSON-RPC 2.0 su HTTP)

Il protocollo è ben definito in `shared/a2a_models.py`: `JsonRpcRequest` → `A2ATask` (con `TextPart` e `DataPart`) → `A2ATaskResult`. Le factory `ok()` e `fail()` standardizzano le risposte.

La comunicazione è **sincrona e bloccante**: l'orchestratore chiama `httpx.AsyncClient.post()` con timeout di 120 secondi e attende la risposta prima di procedere. Non esiste publish/subscribe, code di messaggi, o callback asincrono.

### Limitazioni

**Osservabilità**
- Nessun correlation ID tra le chiamate: non è possibile tracciare una richiesta end-to-end attraverso i 5 agenti.
- Nessuna metrica esposta (latenza per agente, token usage, tasso di errore).
- Il logging è su stdout non strutturato. In produzione, questo rende impossibile aggregare e correlare i log in sistemi come ELK o Datadog.
- Nessun distributed tracing (OpenTelemetry o equivalente).

**Scalabilità**
- Il pattern request/response sincrono non scala orizzontalmente: se si vogliono processare 50 ticker in parallelo, servono 50 istanze dell'orchestratore, ognuna con la propria connessione verso i 5 agenti.
- Il payload A2A non ha paginazione o chunking: per batch grandi, la serializzazione JSON di tutto lo stato diventa un collo di bottiglia.
- Non c'è back-pressure: se un agente è lento o sovraccarico, l'orchestratore aspetta silenziosamente fino al timeout.

**Controllo**
- Nessuna versioning dell'API: se un agente evolve il proprio schema, l'orchestratore non ha modo di negoziare la versione.
- Nessuna autenticazione tra agenti: le chiamate HTTP interne non portano token, header di autenticazione, o mTLS. Chiunque abbia accesso alla rete può chiamare `/tasks` direttamente.
- Nessun circuit breaker: un agente degradato non viene isolato, la pipeline si blocca sul timeout.

**Gestione degli errori**
- Il meccanismo `A2ATaskResult.fail()` propaga il messaggio di errore ma non il tipo strutturato. Non è possibile distinguere tra un errore transitorio (rate limit, timeout di rete) e un errore permanente (dato non disponibile) per implementare retry selettivi.
- La gestione delle eccezioni nei singoli agenti è difesa (try/except globale), ma non distingue tra errori recuperabili e non recuperabili.

---

## 4. Dipendenze Esterne

### Fonti dati

| Fonte | Tipo | Dati | Rischi |
|---|---|---|---|
| **yfinance** | Libreria Python (scraping Yahoo Finance) | Fondamentali, prezzi storici, info ticker | Alto |
| **Reuters RSS** | Feed pubblico | News finanziarie | Medio |
| **Yahoo Finance RSS** | Feed pubblico | News mercati | Medio |
| **MarketWatch RSS** | Feed pubblico | News mercati | Medio |
| **Investing.com RSS ×2** | Feed pubblico | News, forex | Medio-Alto |
| **Anthropic API** | API commerciale | LLM inference | Medio |

### Valutazione dei rischi per fonte

**yfinance** è il rischio più critico:
- Non è un'API ufficiale: Yahoo Finance non garantisce SLA, struttura dei dati, o continuità del servizio. È uno scraper mascherato da libreria.
- I dati non sono certificati né auditabili: non esiste una fonte autorevole a cui ricondurre i fondamentali restituiti.
- In ambito financial services, l'uso di dati non certificati per raccomandazioni (anche solo informali) espone a rischi di compliance significativi (MiFID II, MAR).
- Il timeout di 15 secondi per ticker è un workaround, non una soluzione: non esiste retry logic strutturata né fallback.

**RSS feed:**
- Feed pubblici senza autenticazione: nessuna garanzia di disponibilità o di qualità del dato.
- Nessuna deduplicazione strutturata delle news tra feed diversi.
- Nessun controllo sulla freschezza del dato: una news di 3 giorni fa viene trattata come breaking news.
- Investing.com ha storicamente limitato o bloccato scraping.

**Anthropic API:**
- Dipendenza da un singolo provider LLM senza fallback.
- Nessuna gestione strutturata del rate limiting (il retry con backoff esponenziale è implementato solo parzialmente in alcuni agenti).
- Token usage non monitorato: nessun budget o alert sulla spesa API.

---

## 5. Rischi Enterprise

### Sicurezza dei dati
- **Nessuna autenticazione inter-agente**: le porte 8001-8005 sono HTTP plain, senza token o mTLS. In un ambiente condiviso (anche una VPC), qualsiasi processo può inviare task arbitrari.
- **Nessuna sanitizzazione dell'input**: il contenuto delle news RSS (testo esterno non fidato) viene passato direttamente come prompt ai modelli LLM senza filtri. Un attore malevolo che controllasse un feed RSS potrebbe tentare prompt injection.
- **API key in `.env`**: nessun riferimento a secret management (Vault, AWS Secrets Manager, Azure Key Vault). In produzione questo è inaccettabile.
- **Log su stdout**: se il log cattura payload di richiesta/risposta, dati di mercato sensibili o contenuti dei report vanno su stdout non protetto.

### Compliance (Financial Services)
- **MiFID II / MAR**: qualsiasi sistema che produce o contribuisce a produrre raccomandazioni di investimento deve essere auditabile, con traccia di chi ha deciso cosa e su quali basi. Questo sistema non ha traccia persistente delle decisioni dei modelli.
- **Dati non certificati**: l'uso di yfinance e RSS pubblici come base per analisi finanziarie non soddisfa i requisiti di data lineage e data quality tipici dei framework di compliance bancaria.
- **Output non deterministico**: i report generati da LLM variano tra run. Senza versioning degli output e audit trail, non è possibile dimostrare cosa il sistema ha raccomandato in un momento specifico.
- **Lingua del report**: il report è in italiano per design, ma i dati di input sono in inglese. La catena di traduzione implicita fatta dall'LLM non è controllata né verificata.

### Auditabilità
- **Nessun audit log**: non esiste un log strutturato e persistente di ogni invocazione della pipeline, degli input/output per agente, dei modelli usati, e delle versioni dei prompt.
- **Prompt non versionati**: i system prompt sono hardcoded nelle `agent.py`. Una modifica al prompt cambia silenziosamente il comportamento del sistema senza traccia.
- **Nessun model versioning**: se Anthropic aggiorna un modello sottostante, l'output del sistema cambia senza che nessun alert venga emesso.

### Monitoring e Logging
- Nessuna metrica di business (quanti ticker processati, tasso di successo, latenza media pipeline).
- Nessun alerting su failure rate o degrado della qualità dell'output.
- Nessun health check aggregato: i singoli `/health` degli agenti non sono aggregati in una vista sistemica.
- Nessuna dashboard operativa.

### Gestione dei Fallimenti
- Un singolo agente che fallisce blocca l'intera pipeline senza possibilità di ripresa parziale.
- Non esiste dead letter queue per i task falliti.
- Non esiste graceful degradation: se il NewsSentiment agent non è disponibile, il sistema non può procedere anche se i dati RSS non fossero strettamente necessari per alcuni ticker.
- Il timeout fisso di 120 secondi per agente è arbitrario e non adattivo.

---

## 6. Sintesi

### Punti di forza

- **Architettura modulare e comprensibile**: la separazione in microservizi con contratto A2A standardizzato è la scelta giusta. Ogni agente è deployabile e testabile indipendentemente.
- **Contratto wire ben definito**: `a2a_models.py` con Pydantic garantisce validazione strutturata dei messaggi — base solida per evoluzioni future.
- **Separazione della logica di dominio**: `shared/validators.py` implementa constraint deterministici separati dall'LLM. Questo pattern (LLM + guardrail deterministici) è corretto e maturo.
- **Estensibilità del grafo**: LangGraph è la scelta giusta per l'orchestrazione — il valore si manifesterà quando si aggiungeranno branch paralleli e retry condizionali.
- **Multi-framework by design**: l'uso intenzionale di framework diversi per agente diverso ha valore pedagogico e dimostra che il protocollo A2A è framework-agnostic.
- **QA pass integrato**: il doppio passaggio LLM in ReportWriter (generazione + QA) è un pattern corretto per aumentare l'affidabilità dell'output.

### Principali rischi architetturali

1. **Nessuna autenticazione inter-agente** — superficie di attacco aperta su tutte le porte interne.
2. **Dipendenza critica da yfinance** — dato finanziario non certificato, non auditabile, da un'API non ufficiale.
3. **Nessun audit trail** — incompatibile con qualsiasi framework di compliance financial services.
4. **Single point of failure sull'orchestratore** — nessuna HA, nessun checkpointing, nessuna ripresa parziale.
5. **Payload monolitico crescente** — rischio di context overflow e latenza non lineare su batch grandi.
6. **Prompt injection da feed RSS** — input non fidato passato direttamente ai modelli senza sanitizzazione.

### Top 5 priorità per l'evoluzione enterprise

| Priorità | Area | Intervento |
|---|---|---|
| **1** | **Auditabilità e Compliance** | Implementare un audit log strutturato e persistente (ogni invocazione: timestamp, agente, modello, versione prompt, hash input/output). Prerequisito non negoziabile per financial services. |
| **2** | **Sicurezza inter-agente** | Aggiungere autenticazione alle chiamate A2A (JWT o mTLS). Spostare la gestione dei secret su un secret manager. Sanitizzare gli input RSS prima di passarli ai prompt. |
| **3** | **Resilienza e gestione dei fallimenti** | Introdurre retry con backoff esponenziale e circuit breaker (Tenacity o equivalente) a livello di orchestratore. Aggiungere checkpointing LangGraph per ripresa parziale. Definire una strategia di graceful degradation. |
| **4** | **Osservabilità** | Aggiungere correlation ID end-to-end, log strutturati (JSON), metriche per agente (latenza, token usage, error rate) esposte via OpenTelemetry. |
| **5** | **Dati certificati** | Sostituire yfinance con un provider dati ufficiale (Bloomberg API, Refinitiv, Alpha Vantage certificato) con SLA, data lineage, e licenza d'uso esplicita per uso finanziario. |

---

*Il sistema ha una base architettuale solida e corretta nelle sue scelte fondamentali (A2A, LangGraph, separazione dei concern). Il gap rispetto a un deployment enterprise non è nel design pattern ma nella mancanza dei layer trasversali: sicurezza, osservabilità, auditabilità, e resilienza — tutti risolvibili in modo incrementale senza riscrivere l'architettura core.*
