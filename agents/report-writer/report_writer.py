"""Report Writer agent — direct Anthropic API + FastAPI, port 8009.

Produces the final report in Italian: executive summary + structured JSON.
Includes an internal QA pass before returning the output.
Maps to report_writer + qa_reviewer from the original CrewAI pipeline.
"""
import copy
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pydantic import ValidationError

from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.audit import make_audit_event, write_audit_event
from shared.demo import is_demo_mode, load_demo_response
from shared.hmac_auth import HMACMiddleware
from shared.llm_client import get_llm_client
from shared.models import Report

log = structlog.get_logger()

_MODEL_REPORT = "claude-sonnet-4-6"
_MODEL_QA = "claude-sonnet-4-6"

# ------------------------------------------------------------------ #
# Prompts                                                              #
# ------------------------------------------------------------------ #

_REPORT_SYSTEM = """Sei un analista di ricerca azionaria senior. Produci report in italiano professionale.

Il report ha DUE sezioni obbligatorie, separate esattamente da questi separatori:

=== SINTESI ESECUTIVA ===
(massimo 10 righe, tono neutro, nessuna direttiva buy/sell)
Focus su ciò che è specifico e differenziante per ogni candidato.

=== JSON ===
(JSON valido che rispetta esattamente lo schema fornito)

REGOLE:
- Tutto il testo in italiano
- Cita gli ID notizia per ogni affermazione (N1, N2...)
- Nessun numero inventato o data nel passato
- scoring.totale = somma esatta delle 5 dimensioni (max 50)
- data_analisi = {today}"""

_REPORT_SCHEMA = """{
  "data_analisi": "YYYY-MM-DD",
  "universo": "US e EU equities",
  "temi": [
    {
      "tema_id": "T1",
      "titolo": "string",
      "perche_ora": "string",
      "evidenze": ["N1"],
      "indicatori_da_monitorare": ["item"]
    }
  ],
  "candidati": [
    {
      "rank": 1,
      "ticker": "string",
      "azienda": "string",
      "mercato": "US|EU",
      "tema": "T1",
      "tesi": "string",
      "catalizzatore": "string",
      "orizzonte_settimane": "string",
      "scenari": {"base": "", "bull": "", "bear": ""},
      "rischi": {"macro": "", "settore": "", "azienda": "", "regolatorio": "", "valutazione": ""},
      "trigger_falsificazione": "string",
      "prossime_verifiche": ["item"],
      "evidenze_citate": ["N1"],
      "rating_qualita": "alta|media|bassa",
      "scoring": {
        "forza_catalizzatore": 0,
        "fit_orizzonte": 0,
        "asimmetria_narrativa": 0,
        "qualita_evidenze": 0,
        "rischio_crowding": 0,
        "totale": 0
      },
      "consenso_analisti": {
        "totale_analisti": 0,
        "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0,
        "giudizio_sintetico": "string",
        "target_medio": "string"
      }
    }
  ],
  "candidati_esclusi": [{"ticker": "string", "motivo_esclusione": "string"}],
  "nota_metodologica": "string"
}"""

_QA_SYSTEM = """Sei un revisore QA di report di ricerca azionaria. Oggi è {today}.

Controlla:
1. Conformità schema JSON
2. Ogni affermazione cita un ID notizia
3. Nessuna direttiva buy/sell esplicita
4. scoring.totale = somma esatta (ogni dimensione 1-10, max 50)
5. consenso_analisti compilato per ogni candidato
6. Tutto il testo in italiano corretto
7. Date future coerenti (tutte dopo {today})

Rispondi SOLO con:
QA: [APPROVATO|CORRETTO] — una riga di verdetto, max 3 frasi.

Se ci sono correzioni numeriche (scoring, consensus):
=== CORREZIONI ===
[{{"ticker": "X", "field": "scoring.totale", "value": 31, "motivo": "reason"}}]

Non riprodurre il report. Non correggere testi liberi."""


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

def _call_claude(system: str, user: str, model: str, max_tokens: int) -> tuple[str, dict]:
    """Returns (text, token_usage) where token_usage = {"input": N, "output": N}."""
    response = get_llm_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    usage = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    }
    return response.content[0].text, usage


def _extract_section(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx == -1:
        return ""
    after = text[idx + len(marker):].strip()
    # Stop at next === marker
    next_marker = after.find("\n===")
    if next_marker != -1:
        after = after[:next_marker]
    return after.strip()


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        return text[start:end]
    return text


async def run_agent(task: A2ATask) -> A2ATaskResult:
    correlation_id = task.metadata.get("correlation_id")
    t0 = time.monotonic()

    if is_demo_mode():
        demo = load_demo_response("report-writer")
        input_data_demo: dict[str, Any] = {}
        for part in task.message.parts:
            if hasattr(part, "data"):
                input_data_demo.update(part.data)
        input_tickers = {c["ticker"] for c in input_data_demo.get("candidates", [])}
        report = copy.deepcopy(demo["data"]["report"])
        if input_tickers:
            filtered = [c for c in report["candidati"] if c["ticker"] in input_tickers]
            if filtered:
                report["candidati"] = filtered
        n = len(report["candidati"])
        tickers_str = ", ".join(c["ticker"] for c in report["candidati"])
        summary = (
            f"Il report identifica {n} candidati equity ({tickers_str}) "
            f"dalla pipeline di analisi. "
            + demo["data"]["executive_summary"].split(". ", 1)[-1]
            if ". " in demo["data"]["executive_summary"] else
            f"Il report identifica {n} candidati equity ({tickers_str})."
        )
        result = A2ATaskResult.ok(
            task.id,
            summary,
            data={
                "report": report,
                "executive_summary": summary,
                "qa_verdict": demo["data"]["qa_verdict"],
            },
        )
        write_audit_event(make_audit_event(
            agent="ReportWriter", status="demo",
            correlation_id=correlation_id, model_id=_MODEL_REPORT,
            duration_ms=int((time.monotonic() - t0) * 1000), demo_mode=True,
        ))
        log.info("agent.demo", agent="ReportWriter", correlation_id=correlation_id)
        return result

    input_data: dict[str, Any] = {}
    for part in task.message.parts:
        if hasattr(part, "data"):
            input_data.update(part.data)

    today = date.today().isoformat()
    candidates = input_data.get("candidates", [])
    risk_assessment = input_data.get("risk_assessment", [])
    news = input_data.get("news", [])
    themes = input_data.get("themes", [])
    prev_runs_ctx = input_data.get("previous_runs_context", "")
    gate_feedback = input_data.get("gate_feedback", "")
    research_focus = input_data.get("research_focus", "")
    rationale = input_data.get("rationale", "")

    user_prompt = (
        (f"RAGIONAMENTO DEL PIANIFICATORE:\n{rationale}\n\n---\n\n" if rationale else
         f"FOCUS DELLA RICERCA: {research_focus}\n\n---\n\n" if research_focus else "")
        + (f"GATE VALIDATION FEEDBACK — fix these errors in your response:\n{gate_feedback}\n\n---\n\n" if gate_feedback else "")
        + (f"CONTESTO RUN PRECEDENTI:\n{prev_runs_ctx}\n\n---\n\n" if prev_runs_ctx else "")
        + f"Oggi è {today}.\n\n"
        f"NOTIZIE:\n{json.dumps(news, ensure_ascii=False)}\n\n"
        f"TEMI:\n{json.dumps(themes, ensure_ascii=False)}\n\n"
        f"CANDIDATI:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"VALUTAZIONE RISCHI:\n{json.dumps(risk_assessment, ensure_ascii=False)}\n\n"
        f"SCHEMA JSON TARGET:\n{_REPORT_SCHEMA}\n\n"
        "Produci il report completo con le due sezioni."
    )

    try:
        report_raw, usage_report = _call_claude(
            system=_REPORT_SYSTEM.format(today=today),
            user=user_prompt,
            model=_MODEL_REPORT,
            max_tokens=8000,
        )

        sintesi = _extract_section(report_raw, "=== SINTESI ESECUTIVA ===")
        json_raw = _extract_section(report_raw, "=== JSON ===")
        json_clean = _extract_json(json_raw)

        qa_input = f"REPORT DA REVISIONARE:\n{report_raw}"
        qa_output, usage_qa = _call_claude(
            system=_QA_SYSTEM.format(today=today),
            user=qa_input,
            model=_MODEL_QA,
            max_tokens=2048,
        )

        try:
            report_dict = json.loads(json_clean)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ReportWriter JSON parse failed: {exc}") from exc

        try:
            report_obj = Report.model_validate(report_dict)
            report_dict = report_obj.model_dump()
        except ValidationError as exc:
            raise ValueError(
                f"ReportWriter schema violation — {exc.error_count()} error(s):\n"
                + "\n".join(f"  • {e['loc']}: {e['msg']}" for e in exc.errors())
            ) from exc

        total_usage = {
            "input": usage_report["input"] + usage_qa["input"],
            "output": usage_report["output"] + usage_qa["output"],
        }
        write_audit_event(make_audit_event(
            agent="ReportWriter", status="completed",
            correlation_id=correlation_id, model_id=_MODEL_REPORT,
            duration_ms=int((time.monotonic() - t0) * 1000),
            prompt=_REPORT_SYSTEM, input_text=user_prompt, output_text=report_raw,
            token_usage=total_usage,
            extra={"qa_verdict": qa_output[:80]},
        ))
        log.info("agent.completed", agent="ReportWriter", correlation_id=correlation_id,
                 tokens_in=total_usage["input"], tokens_out=total_usage["output"])

        return A2ATaskResult.ok(
            task.id,
            sintesi,
            data={"report": report_dict, "executive_summary": sintesi, "qa_verdict": qa_output},
        )
    except Exception as e:
        write_audit_event(make_audit_event(
            agent="ReportWriter", status="failed",
            correlation_id=correlation_id, model_id=_MODEL_REPORT,
            duration_ms=int((time.monotonic() - t0) * 1000),
            extra={"error": str(e)},
        ))
        log.error("agent.failed", agent="ReportWriter", correlation_id=correlation_id, error=str(e))
        return A2ATaskResult.fail(task.id, str(e))


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="ReportWriter A2A Agent")
app.add_middleware(HMACMiddleware)

_WELL_KNOWN = Path(__file__).parent / ".well-known" / "agent.json"


@app.get("/.well-known/agent.json")
async def agent_card():
    return FileResponse(_WELL_KNOWN, media_type="application/json")


@app.post("/tasks")
async def receive_task(rpc: JsonRpcRequest) -> JSONResponse:
    if rpc.method != "tasks/send":
        resp = JsonRpcResponse.fail(-32601, f"Method not found: {rpc.method}", rpc.id)
        return JSONResponse(resp.model_dump(), status_code=404)
    try:
        task = A2ATask(**rpc.params)
    except Exception as e:
        resp = JsonRpcResponse.fail(-32602, f"Invalid params: {e}", rpc.id)
        return JSONResponse(resp.model_dump(), status_code=422)

    result = await run_agent(task)
    return JSONResponse(JsonRpcResponse.ok(result.model_dump(), rpc.id).model_dump())


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "ReportWriter", "port": 8009}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8009, log_level="info")
