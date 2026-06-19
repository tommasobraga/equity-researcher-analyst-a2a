"""Risk Assessor agent — Anthropic SDK (native ReAct) + FastAPI, port 8004.

Receives equity candidates from Fundamental Analyst and produces:
- Base/bull/bear scenarios for each candidate
- Scoring across 5 dimensions (1-10 each, max 50 total)
- Guardrail: rejects output if volatility data (52w range, P/E) is absent.
Replaces BeeAI ReActAgent with native Anthropic SDK react_loop.
"""
import asyncio
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

from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.audit import make_audit_event, write_audit_event
from shared.demo import is_demo_mode, load_demo_response
from shared.hmac_auth import HMACMiddleware
from shared.llm_client import get_llm_client
from shared.react import react_loop
from shared.tools.yfinance_tool import get_stock_fundamentals

log = structlog.get_logger()

_MODEL_ID = "claude-sonnet-4-6"

# ------------------------------------------------------------------ #
# Conditional constraint check                                         #
# ------------------------------------------------------------------ #

def _has_volatility_data(candidate: dict) -> bool:
    fund = candidate.get("fundamentals", {})
    has_range = fund.get("52w_range") not in (None, "N/A", "None-None", "")
    has_pe = fund.get("pe_ttm") not in (None, "N/A", "")
    return has_range and has_pe


# ------------------------------------------------------------------ #
# Tool definition + executor                                           #
# ------------------------------------------------------------------ #

_TOOLS = [
    {
        "name": "check_volatility_data",
        "description": (
            "Fetch 52-week range and P/E ratio for a ticker to verify volatility data "
            "is available before scoring risk. Returns 'OK — 52w: X-Y, P/E TTM: Z' "
            "or 'MISSING: <fields>'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"}
            },
            "required": ["ticker"],
        },
    }
]


async def _check_volatility(input: dict) -> str:
    ticker = input["ticker"].strip().upper()
    try:
        data = await asyncio.to_thread(get_stock_fundamentals, ticker)
        missing = []
        if data.get("week52_low") is None or data.get("week52_high") is None:
            missing.append("52w_range")
        if data.get("pe_ttm") is None:
            missing.append("pe_ttm")
        if missing:
            return f"MISSING: {', '.join(missing)}"
        return f"OK — 52w: {data['week52_low']}-{data['week52_high']}, P/E TTM: {data['pe_ttm']}"
    except Exception as e:
        return f"ERROR: {e}"


_EXECUTORS = {"check_volatility_data": _check_volatility}


# ------------------------------------------------------------------ #
# Prompt                                                               #
# ------------------------------------------------------------------ #

_INSTRUCTIONS = """You are a CFA-aligned risk analyst. Today is {today}.

For each equity candidate:
1. Call check_volatility_data to verify that 52w range and P/E are available.
   - If MISSING for a candidate: mark that candidate with "quality": "dati_insufficienti"
     and skip the scoring for it (set all scores to 0).
   - If OK: proceed to score.
2. Produce scenario analysis and scoring.

SCORING RULES:
- Each dimension: integer 1-10.
- totale = exact arithmetic sum of the 5 dimensions (max 50).
- Be specific: reference actual company financials, product cycles, named events.
- All forward-looking dates must be AFTER {today}.

Return ONLY a JSON array (no prose, no markdown fences):
[{{
  "ticker": "X",
  "scenarios": {{
    "base": "1 sentence",
    "bull": "1 sentence",
    "bear": "1 sentence"
  }},
  "risks": {{
    "macro": "1 sentence",
    "sector": "1 sentence",
    "company": "1 sentence",
    "regulatory": "1 sentence",
    "valuation": "1 sentence"
  }},
  "falsification": "1 sentence — what would prove the thesis wrong",
  "next_checks": ["item1", "item2"],
  "quality": "alta|media|bassa|dati_insufficienti",
  "scoring": {{
    "forza_catalizzatore": 0,
    "fit_orizzonte": 0,
    "asimmetria_narrativa": 0,
    "qualita_evidenze": 0,
    "rischio_crowding": 0,
    "totale": 0
  }}
}}]"""


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

def _extract_json_array(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start != -1 and end > start:
        return text[start:end]
    return text


async def run_agent(task: A2ATask) -> A2ATaskResult:
    correlation_id = task.metadata.get("correlation_id")
    t0 = time.monotonic()

    if is_demo_mode():
        demo = load_demo_response("risk-assessor")
        input_data_demo: dict[str, Any] = {}
        for part in task.message.parts:
            if hasattr(part, "data"):
                input_data_demo.update(part.data)
        input_tickers = {c["ticker"] for c in input_data_demo.get("candidates", [])}
        all_ra = demo["data"]["risk_assessment"]
        all_candidates = demo["data"]["candidates"]
        if input_tickers:
            risk_assessment = [r for r in all_ra if r["ticker"] in input_tickers] or all_ra
            candidates = [c for c in all_candidates if c["ticker"] in input_tickers] or all_candidates
        else:
            risk_assessment = all_ra
            candidates = all_candidates
        result = A2ATaskResult.ok(
            task.id,
            f"Risk assessment complete for {len(risk_assessment)} candidate(s).",
            data={"risk_assessment": risk_assessment, "candidates": candidates},
        )
        write_audit_event(make_audit_event(
            agent="RiskAssessor", status="demo",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000), demo_mode=True,
        ))
        log.info("agent.demo", agent="RiskAssessor", correlation_id=correlation_id)
        return result

    input_data: dict[str, Any] = {}
    for part in task.message.parts:
        if hasattr(part, "data"):
            input_data.update(part.data)

    candidates = input_data.get("candidates", [])
    if not candidates:
        return A2ATaskResult.fail(task.id, "No candidates received from FundamentalAnalyst.")

    today = date.today().isoformat()
    candidates_json = json.dumps(candidates, ensure_ascii=False)

    risk_history_parts = [
        snip for snip in input_data.get("risk_history", {}).values() if snip
    ]
    history_block = "\n\n".join(risk_history_parts)

    gate_feedback = input_data.get("gate_feedback", "")
    user_prompt = (
        (f"GATE VALIDATION FEEDBACK — fix these issues in your response:\n{gate_feedback}\n\n---\n\n" if gate_feedback else "")
        + (f"HISTORICAL RISK CONTEXT FROM PREVIOUS RUNS:\n{history_block}\n\n---\n\n" if history_block else "")
        + f"EQUITY CANDIDATES:\n{candidates_json}\n\n"
        "Now perform the risk assessment for each candidate."
    )

    client = get_llm_client()
    try:
        raw_text = await react_loop(
            client=client,
            system=_INSTRUCTIONS.format(today=today),
            user_prompt=user_prompt,
            tools=_TOOLS,
            executors=_EXECUTORS,
            model=_MODEL_ID,
        )
        output = _extract_json_array(raw_text)
        try:
            risk_data = json.loads(output)
            a2a_result = A2ATaskResult.ok(
                task.id,
                f"Risk assessment complete for {len(risk_data)} candidate(s).",
                data={"risk_assessment": risk_data, "candidates": candidates},
            )
        except json.JSONDecodeError:
            a2a_result = A2ATaskResult.ok(task.id, raw_text)
            risk_data = []
        write_audit_event(make_audit_event(
            agent="RiskAssessor", status="completed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            prompt=_INSTRUCTIONS, input_text=candidates_json, output_text=raw_text,
            extra={"assessed_count": len(risk_data)},
        ))
        log.info("agent.completed", agent="RiskAssessor", correlation_id=correlation_id,
                 assessed_count=len(risk_data))
        return a2a_result
    except Exception as e:
        error_msg = str(e)
        write_audit_event(make_audit_event(
            agent="RiskAssessor", status="failed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            extra={"error": error_msg},
        ))
        log.error("agent.failed", agent="RiskAssessor", correlation_id=correlation_id,
                  error=error_msg)
        return A2ATaskResult.fail(task.id, error_msg)


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="RiskAssessor A2A Agent")
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
    return {"status": "ok", "agent": "RiskAssessor", "port": 8004}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004, log_level="info")
