"""Orchestrator — LangGraph pipeline v2.

Grafo sequenziale con 5 nodi:
  data_collector      (8001) — fondamentali ticker
  news_sentiment      (8002) — news + temi di mercato
  fundamental_analyst (8003) — candidati equity con tesi
  risk_assessor       (8004) — scoring e scenari
  report_writer       (8005) — report finale italiano

Fase 3: retry strutturato (tenacity), circuit breaker custom,
        LangGraph checkpointing (SQLite), graceful degradation,
        payload windowing.
"""
import asyncio
import json
import os
import re
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, TypedDict

import httpx
import structlog
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.a2a_models import A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.demo import is_demo_mode
from shared.exceptions import AgentTimeoutError, AgentUnavailableError, RateLimitError
from shared.hmac_auth import sign_request
from shared.report import generate_html

log = structlog.get_logger()

AGENTS = {
    "data_collector":      "http://localhost:8001",
    "news_sentiment":      "http://localhost:8002",
    "fundamental_analyst": "http://localhost:8003",
    "risk_assessor":       "http://localhost:8004",
    "report_writer":       "http://localhost:8009",
}

MAX_NEWS_PAYLOAD       = int(os.getenv("MAX_NEWS_PAYLOAD", "15"))
MAX_CANDIDATES_PAYLOAD = int(os.getenv("MAX_CANDIDATES_PAYLOAD", "5"))

_RATE_LIMIT_PATTERN = re.compile(
    r"\b(rate.?limit|too many requests|concurrent connections|overloaded|529)\b",
    re.IGNORECASE,
)


# ------------------------------------------------------------------ #
# Pipeline state                                                       #
# ------------------------------------------------------------------ #

class PipelineState(TypedDict):
    run_id: str
    tickers: list[str]
    fundamentals: list
    news: list
    themes: list
    candidates: list
    risk_assessment: list
    report: dict
    executive_summary: str
    qa_verdict: str
    degraded: dict  # {component: error_message} — popolato da graceful degradation


# ------------------------------------------------------------------ #
# Circuit breaker                                                      #
# ------------------------------------------------------------------ #

class _CircuitBreaker:
    """Lightweight async-compatible circuit breaker.

    Apre dopo fail_max failures consecutive; si richiude dopo reset_timeout secondi.
    Non usa pybreaker (⚠️ nel piano autorizzativo) — implementazione in-house
    su time.monotonic(), zero dipendenze aggiuntive.
    """

    def __init__(self, fail_max: int = 3, reset_timeout: float = 300.0):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._fails = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at > self.reset_timeout:
            self._fails = 0
            self._opened_at = None
            return False
        return True

    def record_success(self) -> None:
        self._fails = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._fails += 1
        if self._fails >= self.fail_max:
            self._opened_at = time.monotonic()
            log.warning("circuit.opened", fails=self._fails)


_breakers: dict[str, _CircuitBreaker] = {name: _CircuitBreaker() for name in AGENTS}


# ------------------------------------------------------------------ #
# A2A client                                                           #
# ------------------------------------------------------------------ #

async def send_task(
    agent_url: str,
    message: str,
    data: dict[str, Any] | None = None,
    timeout: float = 300.0,
    correlation_id: str | None = None,
) -> A2ATaskResult:
    """Single attempt — raises typed exceptions, never swallows errors."""
    task_id = str(uuid.uuid4())
    parts: list[dict] = [{"type": "text", "text": message}]
    if data:
        parts.append({"type": "data", "data": data})

    metadata: dict[str, Any] = {}
    if correlation_id:
        metadata["correlation_id"] = correlation_id

    rpc = JsonRpcRequest(
        method="tasks/send",
        params={
            "id": task_id,
            "message": {"role": "user", "parts": parts},
            "metadata": metadata,
        },
        id=1,
    )
    body = rpc.model_dump_json().encode()
    try:
        auth_headers = sign_request(body)
    except (KeyError, EnvironmentError):
        auth_headers = {}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{agent_url}/tasks",
                content=body,
                headers={"Content-Type": "application/json", **auth_headers},
            )
    except httpx.TimeoutException as e:
        raise AgentTimeoutError(f"{agent_url}: timeout after {timeout}s") from e
    except httpx.ConnectError as e:
        raise AgentUnavailableError(f"{agent_url}: connection refused") from e

    if resp.status_code == 429:
        raise RateLimitError(f"{agent_url}: HTTP 429")
    if resp.status_code >= 500:
        raise AgentUnavailableError(f"{agent_url}: HTTP {resp.status_code}")
    resp.raise_for_status()

    rpc_resp = JsonRpcResponse(**resp.json())
    if rpc_resp.error:
        raise RuntimeError(f"Agent error: {rpc_resp.error}")

    result = A2ATaskResult(**rpc_resp.result)
    if result.status == "failed":
        if _RATE_LIMIT_PATTERN.search(result.message.text()):
            raise RateLimitError(f"{agent_url}: {result.message.text()}")
    return result


async def send_task_with_retry(
    agent_name: str,
    message: str,
    data: dict[str, Any] | None = None,
    timeout: float = 300.0,
    correlation_id: str | None = None,
) -> A2ATaskResult:
    """Retry su RateLimitError (tenacity backoff esponenziale).
    Circuit breaker su AgentUnavailableError / AgentTimeoutError — fail fast.
    """
    agent_url = AGENTS[agent_name]
    breaker = _breakers[agent_name]

    if breaker.is_open:
        raise AgentUnavailableError(f"{agent_name}: circuit breaker open")

    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type(RateLimitError),
        wait=wait_exponential(multiplier=2, min=10, max=120),
        stop=stop_after_attempt(5),
        reraise=True,
    ):
        with attempt:
            try:
                result = await send_task(agent_url, message, data, timeout, correlation_id)
                breaker.record_success()
                return result
            except RateLimitError:
                log.warning(
                    "agent.rate_limit",
                    agent=agent_name,
                    attempt=attempt.retry_state.attempt_number,
                )
                raise
            except (AgentUnavailableError, AgentTimeoutError) as e:
                breaker.record_failure()
                log.error("agent.unavailable", agent=agent_name, error=str(e))
                raise

    raise AgentUnavailableError(f"{agent_name}: all retry attempts exhausted")


async def check_agents_health() -> dict:
    """Query all 5 agent /health endpoints in parallel. Returns aggregated status."""
    async def _ping(name: str, url: str) -> tuple[str, dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/health")
                resp.raise_for_status()
                return name, {"status": "ok", **resp.json()}
        except Exception as e:
            return name, {"status": "unreachable", "error": str(e)}

    results = await asyncio.gather(*[_ping(name, url) for name, url in AGENTS.items()])
    agents_status = dict(results)
    overall = "ok" if all(v["status"] == "ok" for v in agents_status.values()) else "degraded"
    return {"status": overall, "agents": agents_status}


def _extract_data(result: A2ATaskResult, key: str) -> Any:
    for part in result.message.parts:
        if hasattr(part, "data") and key in part.data:
            return part.data[key]
    return None


# ------------------------------------------------------------------ #
# Graph nodes                                                          #
# ------------------------------------------------------------------ #

async def node_data_collector(state: PipelineState) -> dict:
    ticker_list = ", ".join(state["tickers"])
    print(f"\n[1/5] DataCollector ← {ticker_list}")
    result = await send_task_with_retry(
        "data_collector",
        f"Fetch fundamental data for: {ticker_list}. Return a JSON array.",
        correlation_id=state["run_id"],
    )
    if result.status == "failed":
        raise RuntimeError(f"DataCollector failed: {result.message.text()}")

    fundamentals = _extract_data(result, "fundamentals")
    if fundamentals is None:
        fundamentals = json.loads(result.message.text())

    unavailable = [
        t for t in state["tickers"]
        if not any(f.get("ticker") == t and "error" not in f for f in fundamentals)
    ]
    degraded = dict(state.get("degraded", {}))
    if unavailable:
        degraded["data_collector_partial"] = f"No data for: {', '.join(unavailable)}"
        log.warning("node.partial", node="data_collector", missing=unavailable)

    print(f"      → {len(fundamentals)} ticker(s) fetched")
    return {"fundamentals": fundamentals, "degraded": degraded}


async def node_news_sentiment(state: PipelineState) -> dict:
    print("\n[2/5] NewsSentiment ← fetching RSS feeds")
    try:
        result = await send_task_with_retry(
            "news_sentiment",
            "Technology, AI, Software, Semiconductors, Banking, Financial Services",
            timeout=180.0,
            correlation_id=state["run_id"],
        )
    except (AgentUnavailableError, AgentTimeoutError, RateLimitError) as e:
        log.warning("node.degraded", node="news_sentiment", error=str(e))
        print(f"      ⚠ NewsSentiment non disponibile — pipeline continua senza news ({e})")
        return {
            "news": [],
            "themes": [],
            "degraded": {**state.get("degraded", {}), "news_sentiment": str(e)},
        }

    if result.status == "failed":
        print("      ⚠ NewsSentiment failed — pipeline continua senza news")
        return {
            "news": [],
            "themes": [],
            "degraded": {**state.get("degraded", {}), "news_sentiment": result.message.text()},
        }

    news = (_extract_data(result, "news") or [])[:MAX_NEWS_PAYLOAD]
    themes = _extract_data(result, "themes") or []
    print(f"      → {len(news)} news, {len(themes)} themes")
    return {"news": news, "themes": themes}


async def node_fundamental_analyst(state: PipelineState) -> dict:
    print(f"\n[3/5] FundamentalAnalyst ← {len(state['news'])} news, {len(state['themes'])} themes")
    result = await send_task_with_retry(
        "fundamental_analyst",
        "Analyse the provided news, themes and fundamentals. Return equity candidates.",
        data={
            "news": state["news"],
            "themes": state["themes"],
            "fundamentals": state["fundamentals"],
        },
        timeout=300.0,
        correlation_id=state["run_id"],
    )
    if result.status == "failed":
        raise RuntimeError(f"FundamentalAnalyst failed: {result.message.text()}")

    candidates = _extract_data(result, "candidates")
    if candidates is None:
        candidates = json.loads(result.message.text())

    candidates = candidates[:MAX_CANDIDATES_PAYLOAD]
    print(f"      → {len(candidates)} candidate(s) identified")
    return {"candidates": candidates}


async def node_risk_assessor(state: PipelineState) -> dict:
    print(f"\n[4/5] RiskAssessor ← {len(state['candidates'])} candidate(s)")
    result = await send_task_with_retry(
        "risk_assessor",
        "Perform risk assessment and scoring for each candidate.",
        data={"candidates": state["candidates"]},
        timeout=300.0,
        correlation_id=state["run_id"],
    )
    if result.status == "failed":
        raise RuntimeError(f"RiskAssessor failed: {result.message.text()}")

    risk_data = _extract_data(result, "risk_assessment") or []
    enriched_candidates = _extract_data(result, "candidates") or state["candidates"]
    print(f"      → {len(risk_data)} risk assessment(s) complete")
    return {"risk_assessment": risk_data, "candidates": enriched_candidates}


async def node_report_writer(state: PipelineState) -> dict:
    print("\n[5/5] ReportWriter ← generating final report")
    result = await send_task_with_retry(
        "report_writer",
        "Produce the final equity research report in Italian.",
        data={
            "candidates": state["candidates"],
            "risk_assessment": state["risk_assessment"],
            "news": state["news"],
            "themes": state["themes"],
        },
        timeout=300.0,
        correlation_id=state["run_id"],
    )
    if result.status == "failed":
        raise RuntimeError(f"ReportWriter failed: {result.message.text()}")

    report = _extract_data(result, "report") or {}
    summary = _extract_data(result, "executive_summary") or result.message.text()
    qa = _extract_data(result, "qa_verdict") or ""
    print("      → Report generated")
    return {"report": report, "executive_summary": summary, "qa_verdict": qa}


# ------------------------------------------------------------------ #
# Graph definition                                                     #
# ------------------------------------------------------------------ #

def _build_graph_builder() -> StateGraph:
    builder = StateGraph(PipelineState)

    builder.add_node("data_collector", node_data_collector)
    builder.add_node("news_sentiment", node_news_sentiment)
    builder.add_node("fundamental_analyst", node_fundamental_analyst)
    builder.add_node("risk_assessor", node_risk_assessor)
    builder.add_node("report_writer", node_report_writer)

    builder.set_entry_point("data_collector")
    builder.add_edge("data_collector", "news_sentiment")
    builder.add_edge("news_sentiment", "fundamental_analyst")
    builder.add_edge("fundamental_analyst", "risk_assessor")
    builder.add_edge("risk_assessor", "report_writer")
    builder.add_edge("report_writer", END)

    return builder


# ------------------------------------------------------------------ #
# Pipeline entry point                                                 #
# ------------------------------------------------------------------ #

async def run_pipeline(tickers: list[str]) -> dict:
    run_id = str(uuid.uuid4())
    print("\n" + "=" * 60)
    print("  EQUITY RESEARCHER A2A — LangGraph Pipeline v2")
    print(f"  run_id: {run_id}")
    if is_demo_mode():
        print("  mode:   DEMO (nessuna chiamata LLM)")
    print("=" * 60)

    health = await check_agents_health()
    for agent_name, agent_status in health["agents"].items():
        icon = "✓" if agent_status["status"] == "ok" else "✗"
        print(f"  {icon} {agent_name}: {agent_status['status']}")
    if health["status"] == "degraded":
        print("\n  ⚠ Uno o più agenti non raggiungibili. Verificare che siano avviati.")
    print()

    initial_state: PipelineState = {
        "run_id": run_id,
        "tickers": tickers,
        "fundamentals": [],
        "news": [],
        "themes": [],
        "candidates": [],
        "risk_assessment": [],
        "report": {},
        "executive_summary": "",
        "qa_verdict": "",
        "degraded": {},
    }

    t0 = time.time()
    config = {"configurable": {"thread_id": run_id}}
    Path("output").mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string("output/checkpoints.db") as checkpointer:
        graph = _build_graph_builder().compile(checkpointer=checkpointer)
        final_state = await graph.ainvoke(initial_state, config=config)
    execution_seconds = int(time.time() - t0)

    degraded = final_state.get("degraded", {})
    if degraded:
        print("\n  ⚠ Componenti degradati durante la run:")
        for component, reason in degraded.items():
            print(f"    - {component}: {reason}")

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)

    report_path, violations = generate_html(
        executive_summary=final_state["executive_summary"],
        report_dict=final_state["report"],
        qa_verdict=final_state["qa_verdict"],
        tickers=tickers,
        execution_seconds=execution_seconds,
    )
    print(f"\nReport HTML salvato in: {report_path}")
    if violations:
        errors = [v for v in violations if v.severity == "error"]
        warnings = [v for v in violations if v.severity == "warning"]
        if errors:
            print(f"  ⚠  {len(errors)} errore/i critico/i nel report")
        if warnings:
            print(f"  ℹ  {len(warnings)} avvertimento/i di qualità")
    webbrowser.open(report_path.as_uri())

    return {
        "status": "completed",
        "executive_summary": final_state["executive_summary"],
        "qa_verdict": final_state["qa_verdict"],
        "report": final_state["report"],
        "report_path": str(report_path),
        "degraded": degraded,
    }


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Equity Researcher A2A Orchestrator")
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=["AAPL", "MSFT", "UCG.MI"],
        help="Initial ticker universe (enriched by FundamentalAnalyst)",
    )
    parser.add_argument("--output", default=None, help="Save JSON report to file")
    args = parser.parse_args()

    result = asyncio.run(run_pipeline(args.tickers))

    print("\n=== SINTESI ESECUTIVA ===")
    print(result.get("executive_summary", ""))
    print("\n=== QA VERDICT ===")
    print(result.get("qa_verdict", ""))

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nReport salvato in: {args.output}")
