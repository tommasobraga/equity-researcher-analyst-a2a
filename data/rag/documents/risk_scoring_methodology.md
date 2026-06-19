# Metodologia di Scoring del Rischio — Framework Interno
**Versione:** 2.1 | **Data:** Gennaio 2026 | **Classificazione:** Uso interno — confidenziale

---

## Principi Generali

Lo scoring di rischio è strutturato su **5 dimensioni** indipendenti, ciascuna con punteggio da 1 a 10. Il punteggio totale massimo è **50 punti**. Questo framework è lo strumento primario per la classificazione della qualità di un'opportunità di investimento.

**Soglie di qualità:**
- **Alta qualità:** ≥ 40/50
- **Media qualità:** 30–39/50
- **Bassa qualità:** < 30/50

**Regola BUY:** score ≥ 35 E qualità alta o media
**Regola SELL:** qualità bassa O dati insufficienti per scoring affidabile

---

## Le 5 Dimensioni

### 1. Solidità Fondamentale (max 10)
Valuta la salute finanziaria strutturale dell'azienda.

| Punteggio | Criteri |
|---|---|
| 9–10 | P/E < 25, EPS in crescita > 15% YoY, margini in espansione, bilancio solido |
| 7–8 | P/E 25–35, crescita EPS 5–15%, margini stabili |
| 5–6 | P/E 35–50, crescita EPS < 5% o marginale, qualche pressione margini |
| 3–4 | P/E > 50 o negativo, EPS piatto o in calo, margini in contrazione |
| 1–2 | Fondamentali deteriorati, perdite operative, rischio going concern |

### 2. Momentum e Trend di Mercato (max 10)
Valuta la posizione del titolo nel range di prezzo e il momentum relativo.

| Punteggio | Criteri |
|---|---|
| 9–10 | Prezzo nel 80–100% del 52w range, trend positivo, volume in crescita |
| 7–8 | Prezzo nel 60–80% del 52w range, momentum moderatamente positivo |
| 5–6 | Prezzo nel 40–60% del range, lateralizzazione |
| 3–4 | Prezzo nel 20–40% del range, trend negativo |
| 1–2 | Prezzo nel bottom 20% del range, forte pressione ribassista |

### 3. Sentiment Analisti (max 10)
Valuta il consenso e le revisioni delle stime degli analisti sell-side.

| Punteggio | Criteri |
|---|---|
| 9–10 | > 70% buy rating, revisioni stime al rialzo negli ultimi 90 giorni |
| 7–8 | 50–70% buy, stime stabili o leggermente positive |
| 5–6 | 30–50% buy, consensus neutro, stime miste |
| 3–4 | < 30% buy, revisioni al ribasso prevalenti |
| 1–2 | Maggioranza sell, tagli significativi alle stime |

### 4. Rischio Macro e Settoriale (max 10)
Valuta l'esposizione dell'azienda a fattori di rischio sistemici e settoriali.

| Punteggio | Criteri |
|---|---|
| 9–10 | Settore in crescita strutturale, bassa ciclicità, nessun rischio regolatorio imminente |
| 7–8 | Settore positivo, alcuni rischi macro gestibili |
| 5–6 | Settore neutro o in transizione, rischi macro moderati |
| 3–4 | Settore sotto pressione, rischi regolatori o macro significativi |
| 1–2 | Settore in crisi, rischi esistenziali (disruption, regolamentazione ostile) |

### 5. Qualità del Management e Governance (max 10)
Valuta il track record e la credibilità del management.

| Punteggio | Criteri |
|---|---|
| 9–10 | Management con track record eccellente, guidance centrata, ESG solido |
| 7–8 | Management affidabile, guidance generalmente rispettata |
| 5–6 | Management adeguato, alcune mancate guidance, governance nella norma |
| 3–4 | Turnover recente C-suite, guidance mancata > 2 volte, governance carente |
| 1–2 | Red flag governance (contenzioso, frodi, conflitti di interesse) |

---

## Guardrail Obbligatori

Il processo di scoring **non può essere completato** se mancano:
- Prezzo corrente e 52-week high/low (dimensione 2)
- P/E ratio (dimensione 1)
- Almeno 3 coperture analisti attive (dimensione 3)

In caso di dati mancanti: output classificato come **DATI INSUFFICIENTI**, posizione automaticamente candidata a SELL se già in portafoglio.

---

## Scenari

Per ogni candidato con score ≥ 30 devono essere prodotti tre scenari:
- **Base:** evoluzione più probabile, assunzioni moderate
- **Bull:** catalizzatori positivi si materializzano (es. beat earnings, M&A, re-rating settoriale)
- **Bear:** rischi principali si concretizzano (es. miss guidance, deterioramento macro, regulatory hit)
