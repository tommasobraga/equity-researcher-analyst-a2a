"""Data Collector agent — Anthropic SDK (native ReAct) + FastAPI, port 8001.

Receives a list of equity tickers via A2A and returns fundamentals
from a certified provider (Phase 5 pending — DEMO_MODE=true for local development).
"""
import asyncio
import json
import sys
import time
from pathlib import Path

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

_MODEL_ID = "claude-haiku-4-5-20251001"

# ------------------------------------------------------------------ #
# Tool definition + executor                                           #
# ------------------------------------------------------------------ #

_TOOLS = [
    {
        "name": "fetch_fundamentals",
        "description": (
            "Fetch fundamental data for an equity ticker (certified provider — Fase 5). "
            "Call this for each ticker individually. "
            "Input: ticker symbol, e.g. AAPL, UCG.MI, ASML.AS"
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


async def _fetch_fundamentals(input: dict) -> str:
    ticker = input["ticker"].strip().upper()
    try:
        data = await asyncio.to_thread(get_stock_fundamentals, ticker)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"ticker": ticker, "error": str(e)})


_EXECUTORS = {"fetch_fundamentals": _fetch_fundamentals}


# ------------------------------------------------------------------ #
# Prompt                                                               #
# ------------------------------------------------------------------ #

_INSTRUCTIONS = (
    "You are a financial data agent. Given a list of equity tickers, "
    "call fetch_fundamentals for EACH ticker individually and collect all results. "
    "Return ONLY a JSON array where each element is the fundamentals dict for one ticker. "
    "No prose, no markdown fences."
)


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
        demo = load_demo_response("data-collector")
        input_data_demo: dict = {}
        for part in task.message.parts:
            if hasattr(part, "data"):
                input_data_demo.update(part.data)
        input_tickers = set(input_data_demo.get("tickers", []))
        all_fundamentals = demo["data"]["fundamentals"]
        if input_tickers:
            fundamentals = [f for f in all_fundamentals if f["ticker"] in input_tickers] or all_fundamentals
        else:
            fundamentals = all_fundamentals
        result = A2ATaskResult.ok(task.id, demo["message"], data={"fundamentals": fundamentals})
        write_audit_event(make_audit_event(
            agent="DataCollector", status="demo",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000), demo_mode=True,
        ))
        log.info("agent.demo", agent="DataCollector", correlation_id=correlation_id)
        return result

    text_input = task.message.text()
    client = get_llm_client()
    try:
        raw_text = await react_loop(
            client=client,
            system=_INSTRUCTIONS,
            user_prompt=text_input,
            tools=_TOOLS,
            executors=_EXECUTORS,
            model=_MODEL_ID,
            max_tokens=1024,
        )
        output = _extract_json_array(raw_text)
        try:
            data = json.loads(output)
            a2a_result = A2ATaskResult.ok(
                task.id, "Fundamentals fetched successfully.", data={"fundamentals": data}
            )
        except json.JSONDecodeError:
            a2a_result = A2ATaskResult.ok(task.id, raw_text)
            data = []
        write_audit_event(make_audit_event(
            agent="DataCollector", status="completed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            prompt=_INSTRUCTIONS, input_text=text_input, output_text=raw_text,
        ))
        log.info("agent.completed", agent="DataCollector", correlation_id=correlation_id)
        return a2a_result
    except Exception as e:
        error_msg = str(e)
        write_audit_event(make_audit_event(
            agent="DataCollector", status="failed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            extra={"error": error_msg},
        ))
        log.error("agent.failed", agent="DataCollector", correlation_id=correlation_id, error=error_msg)
        return A2ATaskResult.fail(task.id, error_msg)


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="DataCollector A2A Agent")
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
    return {"status": "ok", "agent": "DataCollector", "port": 8001}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
