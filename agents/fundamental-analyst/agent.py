"""Fundamental Analyst agent — Anthropic SDK (ReAct nativo) + FastAPI, porta 8003.

Riceve news/temi dal News & Sentiment e fondamentali dal Data Collector,
identifica fino a 3 candidati equity con tesi d'investimento specifica.
Sostituisce BeeAI ReActAgent con react_loop nativo Anthropic SDK.
"""
import asyncio
import json
import sys
import time
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
from shared.tools.yfinance_tool import get_stock_fundamentals_text

log = structlog.get_logger()

_MODEL_ID = "claude-sonnet-4-6"
_client = get_llm_client()

# ------------------------------------------------------------------ #
# Tool definition + executor                                           #
# ------------------------------------------------------------------ #

_TOOLS = [
    {
        "name": "fetch_fundamentals",
        "description": (
            "Fetch real fundamental data for a stock ticker from yfinance. "
            "Use this for each candidate to get price, P/E, EPS, 52-week range, "
            "analyst target and consensus. Input: ticker symbol, e.g. AAPL, UCG.MI, ASML.AS"
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
    return await asyncio.to_thread(get_stock_fundamentals_text, input["ticker"])


_EXECUTORS = {"fetch_fundamentals": _fetch_fundamentals}


# ------------------------------------------------------------------ #
# Prompt                                                               #
# ------------------------------------------------------------------ #

_INSTRUCTIONS = """You are a fundamental equity analyst for US and EU markets (UK/LSE excluded).

Given news items and market themes, your job is to:
1. Identify up to 3 equity candidates that best fit the themes.
2. For each candidate call fetch_fundamentals to get real data.
3. Build a company-specific investment thesis (not just macro commentary).

SECTOR EXCLUSIONS — reject any candidate in:
energy, utilities, real estate, REITs, consumer staples, industrials,
airlines, crypto, DeFi, Web3.

PRIORITY SECTORS: Technology, AI, Software, Semiconductors, Banking,
Financial Services, Investment Banking, Private Equity, Asset Management.

Return ONLY a JSON array (no prose, no markdown fences):
[{
  "ticker": "X",
  "company": "Full Name",
  "market": "US|EU",
  "theme_id": "T1",
  "thesis": "3-4 sentences, company-specific",
  "catalyst": "2 sentences, specific trigger and timeline",
  "news_ids": ["N1", "N2"],
  "fundamentals": {
    "price": "X", "pe_ttm": "X", "eps": "X",
    "52w_range": "X-X", "analyst_target": "X"
  },
  "analyst_consensus": {
    "total_analysts": 0, "strong_buy": 0, "buy": 0,
    "hold": 0, "sell": 0, "strong_sell": 0,
    "recommendation_key": "X", "recommendation_mean": "X",
    "giudizio_sintetico": "X"
  }
}]"""


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
        demo = load_demo_response("fundamental-analyst")
        result = A2ATaskResult.ok(task.id, demo["message"], data=demo["data"])
        write_audit_event(make_audit_event(
            agent="FundamentalAnalyst", status="demo",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000), demo_mode=True,
        ))
        log.info("agent.demo", agent="FundamentalAnalyst", correlation_id=correlation_id)
        return result

    input_data: dict[str, Any] = {}
    for part in task.message.parts:
        if hasattr(part, "data"):
            input_data.update(part.data)

    news_text = json.dumps(input_data.get("news", []), ensure_ascii=False)
    themes_text = json.dumps(input_data.get("themes", []), ensure_ascii=False)
    fundamentals_hint = json.dumps(input_data.get("fundamentals", []), ensure_ascii=False)

    user_prompt = (
        f"NEWS ITEMS:\n{news_text}\n\n"
        f"MARKET THEMES:\n{themes_text}\n\n"
        f"PRE-FETCHED FUNDAMENTALS (use as starting point, verify with tool if needed):\n{fundamentals_hint}\n\n"
        "Now identify the best candidates and return the JSON array."
    )

    try:
        raw_text = await react_loop(
            client=_client,
            system=_INSTRUCTIONS,
            user_prompt=user_prompt,
            tools=_TOOLS,
            executors=_EXECUTORS,
            model=_MODEL_ID,
        )
        output = _extract_json_array(raw_text)
        try:
            candidates = json.loads(output)
            a2a_result = A2ATaskResult.ok(
                task.id,
                f"Identified {len(candidates)} equity candidate(s).",
                data={"candidates": candidates},
            )
        except json.JSONDecodeError:
            a2a_result = A2ATaskResult.ok(task.id, raw_text)
            candidates = []
        write_audit_event(make_audit_event(
            agent="FundamentalAnalyst", status="completed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            prompt=_INSTRUCTIONS, input_text=user_prompt, output_text=raw_text,
            extra={"candidate_count": len(candidates)},
        ))
        log.info("agent.completed", agent="FundamentalAnalyst", correlation_id=correlation_id,
                 candidate_count=len(candidates))
        return a2a_result
    except Exception as e:
        error_msg = str(e)
        write_audit_event(make_audit_event(
            agent="FundamentalAnalyst", status="failed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            extra={"error": error_msg},
        ))
        log.error("agent.failed", agent="FundamentalAnalyst", correlation_id=correlation_id,
                  error=error_msg)
        return A2ATaskResult.fail(task.id, error_msg)


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="FundamentalAnalyst A2A Agent")
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
    return {"status": "ok", "agent": "FundamentalAnalyst", "port": 8003}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="info")
