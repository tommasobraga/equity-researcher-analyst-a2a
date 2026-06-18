"""Orchestrator — LangGraph pipeline v3.

Tre workflow selezionabili via parametro mode:

  analyze   — fan-out parallelo data_collector + news_sentiment
              → fundamental_analyst → risk_assessor → report_writer → END

  portfolio — portfolio_loader → portfolio_manager → END

  full      — analyze sequenziale → portfolio_loader → portfolio_manager → END

Routing deterministico oggi (conditional edges su valori dello stato).
Pronto per routing LLM-based quando il cloud provider sarà disponibile:
le funzioni _route_* ricevono PipelineState — basterà sostituire la logica
con una chiamata a react_loop() senza toccare il grafo.

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
from langgraph.types import Send
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
from shared.agent_memory import (
    format_fundamental_history,
    format_risk_history,
    format_run_summaries,
    init_db as init_memory_db,
    read_recent_runs,
    read_ticker_history,
    write_run_summary,
    write_ticker_analysis,
)
from shared.portfolio_db import init_db, load_portfolio_state, save_portfolio_state
from shared.report import generate_html

log = structlog.get_logger()

AGENTS = {
    "data_collector":      "http://localhost:8001",
    "news_sentiment":      "http://localhost:8002",
    "fundamental_analyst": "http://localhost:8003",
    "risk_assessor":       "http://localhost:8004",
    "report_writer":       "http://localhost:8009",
    "portfolio_manager":   "http://localhost:8010",
}

MAX_NEWS_PAYLOAD       = int(os.getenv("MAX_NEWS_PAYLOAD", "15"))
MAX_CANDIDATES_PAYLOAD = int(os.getenv("MAX_CANDIDATES_PAYLOAD", "3"))

_RATE_LIMIT_PATTERN = re.compile(
    r"\b(rate.?limit|too many requests|concurrent connections|overloaded|529)\b",
    re.IGNORECASE,
)


# ------------------------------------------------------------------ #
# Pipeline state                                                       #
# ------------------------------------------------------------------ #

class PipelineState(TypedDict):
    run_id: str
    mode: str               # "analyze" | "portfolio" | "full"
    tickers: list[str]      # empty list = news-driven opportunistic mode
    interactive: bool       # True = prompt user before executing trades (CLI)
    fundamentals: list
    news: list
    themes: list
    candidates: list
    risk_assessment: list
    report: dict
    executive_summary: str
    qa_verdict: str
    degraded: dict          # {component: error_message}
    portfolio_state: dict   # loaded from SQLite by node_portfolio_loader
    portfolio_result: dict  # output from PortfolioManager agent
    ticker_history: dict    # {ticker: {"fundamental": [...], "risk": [...]}} — loaded before FA
    previous_runs: list     # recent run_summaries — loaded before FA


# ------------------------------------------------------------------ #
# Circuit breaker                                                      #
# ------------------------------------------------------------------ #

class _CircuitBreaker:
    """Lightweight async-compatible circuit breaker.

    Apre dopo fail_max failures consecutive; si richiude dopo reset_timeout secondi.
    Implementazione in-house su time.monotonic(), zero dipendenze aggiuntive.
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


_AGENTS_BY_MODE: dict[str, list[str]] = {
    "analyze":   ["data_collector", "news_sentiment", "fundamental_analyst", "risk_assessor", "report_writer"],
    "portfolio": ["portfolio_manager"],
    "full":      list(AGENTS.keys()),
}


async def check_agents_health(mode: str = "full") -> dict:
    """Ping only the agents required for the given mode."""
    relevant = _AGENTS_BY_MODE.get(mode, list(AGENTS.keys()))

    async def _ping(name: str, url: str) -> tuple[str, dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/health")
                resp.raise_for_status()
                return name, {"status": "ok", **resp.json()}
        except Exception as e:
            return name, {"status": "unreachable", "error": str(e)}

    results = await asyncio.gather(*[_ping(name, AGENTS[name]) for name in relevant])
    agents_status = dict(results)
    overall = "ok" if all(v["status"] == "ok" for v in agents_status.values()) else "degraded"
    return {"status": overall, "agents": agents_status}


def _extract_data(result: A2ATaskResult, key: str) -> Any:
    for part in result.message.parts:
        if hasattr(part, "data") and key in part.data:
            return part.data[key]
    return None


# ------------------------------------------------------------------ #
# Routing functions (deterministic — LLM-ready interface)             #
# ------------------------------------------------------------------ #

def _route_from_router(state: PipelineState) -> str | list:
    """Route based on mode and ticker presence.

    With tickers:    fan-out data_collector + news_sentiment in parallel.
    Without tickers: news-driven opportunistic mode — skip data_collector,
                     FundamentalAnalyst works from news/themes alone.
    """
    mode = state["mode"]
    if mode == "portfolio":
        return "portfolio_loader"
    if state["tickers"]:
        return [Send("data_collector", state), Send("news_sentiment", state)]
    # No tickers: news-driven mode — data_collector returns immediately with empty fundamentals
    return [Send("data_collector", state), Send("news_sentiment", state)]


def _route_after_fundamental(state: PipelineState) -> str:
    """Fail-fast: skip risk_assessor and report_writer if no candidates identified."""
    if not state.get("candidates"):
        log.warning("pipeline.no_candidates", mode=state["mode"])
        return END
    return "risk_assessor"


def _route_after_report(state: PipelineState) -> str:
    """In full mode, continue to portfolio branch; in analyze mode, persist memory and stop."""
    if state["mode"] == "full":
        return "portfolio_loader"
    return "memory_writer"


# ------------------------------------------------------------------ #
# Graph nodes — analysis branch                                        #
# ------------------------------------------------------------------ #

async def node_router(state: PipelineState) -> dict:
    mode = state["mode"]
    print(f"\n{'='*60}")
    print(f"  EQUITY RESEARCHER A2A — LangGraph Pipeline v3")
    print(f"  run_id: {state['run_id']}  mode: {mode.upper()}")
    if is_demo_mode():
        print("  mode:   DEMO (nessuna chiamata LLM)")
    print(f"{'='*60}")
    return {}


async def node_data_collector(state: PipelineState) -> dict:
    if not state["tickers"]:
        print("\n[1/5] DataCollector ← skipped (news-driven mode, no tickers specified)")
        return {"fundamentals": []}

    ticker_list = ", ".join(state["tickers"])
    print(f"\n[1/5] DataCollector ← {ticker_list}")
    result = await send_task_with_retry(
        "data_collector",
        f"Fetch fundamental data for: {ticker_list}. Return a JSON array.",
        data={"tickers": state["tickers"]},
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

    # Load memory for each ticker so agents can use cross-run context
    await init_memory_db()
    ticker_history: dict = {}
    for ticker in state.get("tickers", []):
        fa_hist = await read_ticker_history(ticker, "fundamental_analyst")
        ra_hist = await read_ticker_history(ticker, "risk_assessor")
        if fa_hist or ra_hist:
            ticker_history[ticker] = {"fundamental": fa_hist, "risk": ra_hist}
    previous_runs = await read_recent_runs()
    loaded = len(ticker_history)
    if loaded:
        print(f"      memory.loaded: {loaded} ticker(s) with history, {len(previous_runs)} past run(s)")

    fa_snippets = {t: format_fundamental_history(t, v["fundamental"])
                   for t, v in ticker_history.items() if v.get("fundamental")}
    ra_snippets = {t: format_risk_history(t, v["risk"])
                   for t, v in ticker_history.items() if v.get("risk")}
    runs_ctx = format_run_summaries(previous_runs)

    result = await send_task_with_retry(
        "fundamental_analyst",
        "Analyse the provided news, themes and fundamentals. Return equity candidates.",
        data={
            "news": state["news"],
            "themes": state["themes"],
            "fundamentals": state["fundamentals"],
            "ticker_history_fundamental": fa_snippets,
            "ticker_history_risk": ra_snippets,
            "previous_runs_context": runs_ctx,
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
    return {"candidates": candidates, "ticker_history": ticker_history, "previous_runs": previous_runs}


async def node_risk_assessor(state: PipelineState) -> dict:
    print(f"\n[4/5] RiskAssessor ← {len(state['candidates'])} candidate(s)")
    ra_snippets = {
        t: format_risk_history(t, v["risk"])
        for t, v in state.get("ticker_history", {}).items()
        if v.get("risk")
    }
    result = await send_task_with_retry(
        "risk_assessor",
        "Perform risk assessment and scoring for each candidate.",
        data={"candidates": state["candidates"], "risk_history": ra_snippets},
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
            "previous_runs_context": format_run_summaries(state.get("previous_runs", [])),
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
# Graph nodes — portfolio branch                                       #
# ------------------------------------------------------------------ #

async def _execute_trades(pm_data: dict, run_id: str, interactive: bool) -> bool:
    """Persist approved trades to SQLite. Prompts user when interactive=True.

    Returns True if trades were actually written to DB, False otherwise.
    """
    portfolio_update = pm_data.get("portfolio_update", {})
    if not portfolio_update:
        print("      → No trades to execute")
        return False

    buy_sell = [t for t in pm_data.get("trades", []) if t.get("action") in ("BUY", "SELL")]
    if not buy_sell:
        print("      → No BUY/SELL trades proposed")
        return False

    if interactive:
        print("\n      Proposed trades:")
        for t in buy_sell:
            print(f"        {t['action']:<4}  {t.get('shares', 0)}x {t['ticker']:<8} "
                  f"@ {t.get('price', 0):.2f}  —  {t.get('reason', '')}")
        cash_before = portfolio_update.get("cash_before", 0.0)
        cash_after = portfolio_update.get("cash_after", 0.0)
        print(f"\n      Cash impact: {cash_before:,.2f} → {cash_after:,.2f} USD\n")

        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(None, input, "      Approve execution? [y/N]: ")
        if answer.strip().lower() != "y":
            print("      → Trades rejected — portfolio unchanged")
            return False

    portfolio_update["trades"] = pm_data.get("trades", [])
    await save_portfolio_state(portfolio_update, correlation_id=run_id)
    print(f"      → {len(buy_sell)} trade(s) executed, portfolio updated in DB")
    return True


async def node_portfolio_loader(state: PipelineState) -> dict:
    """Local node: reads portfolio from SQLite, no A2A call."""
    print("\n[PM] Portfolio Loader ← reading from DB")
    portfolio = await load_portfolio_state()
    n_pos = len(portfolio.get("positions", []))
    print(f"      → {portfolio['cash']:.2f} {portfolio['currency']} cash, {n_pos} open position(s)")
    return {"portfolio_state": portfolio}


async def node_portfolio_manager(state: PipelineState) -> dict:
    pm_mode = state["mode"] if state["mode"] != "full" else "full"
    label = "[PM] PortfolioManager"
    if pm_mode == "full":
        print(f"\n{label} ← analysis output + portfolio state")
    else:
        print(f"\n{label} ← portfolio review only")

    payload: dict[str, Any] = {
        "pm_mode": pm_mode,
        "portfolio_state": state.get("portfolio_state", {}),
    }
    if pm_mode == "full":
        payload["candidates"] = state.get("candidates", [])
        payload["risk_assessment"] = state.get("risk_assessment", [])
        payload["report"] = state.get("report", {})

    result = await send_task_with_retry(
        "portfolio_manager",
        f"Portfolio management — mode: {pm_mode}",
        data=payload,
        timeout=300.0,
        correlation_id=state["run_id"],
    )
    if result.status == "failed":
        raise RuntimeError(f"PortfolioManager failed: {result.message.text()}")

    pm_data = {}
    for part in result.message.parts:
        if hasattr(part, "data"):
            pm_data.update(part.data)

    if state["mode"] == "full":
        executed = await _execute_trades(pm_data, state["run_id"], interactive=state.get("interactive", False))
        pm_data["trades_executed"] = executed
    else:
        print("      → Review completed (read-only, no DB write)")
    return {"portfolio_result": pm_data}


# ------------------------------------------------------------------ #
# Graph nodes — memory writer (terminal for all branches)             #
# ------------------------------------------------------------------ #

async def node_memory_writer(state: PipelineState) -> dict:
    print("\n[MEM] MemoryWriter ← persisting analysis history")
    await init_memory_db()

    risk_map = {r["ticker"]: r for r in state.get("risk_assessment", [])}
    for cand in state.get("candidates", []):
        await write_ticker_analysis(state["run_id"], cand["ticker"], "fundamental_analyst", cand)
        risk = risk_map.get(cand["ticker"])
        if risk:
            await write_ticker_analysis(state["run_id"], cand["ticker"], "risk_assessor", risk)

    pm = state.get("portfolio_result", {})
    trades = (
        [t for t in pm.get("trades", []) if t.get("action") in ("BUY", "SELL")]
        if pm.get("trades_executed") else None
    )
    await write_run_summary(
        run_id=state["run_id"],
        mode=state["mode"],
        tickers=state.get("tickers", []),
        candidates=[c["ticker"] for c in state.get("candidates", [])],
        trades=trades,
    )
    n_c = len(state.get("candidates", []))
    n_ra = len(state.get("risk_assessment", []))
    print(f"      → {n_c} fundamental + {n_ra} risk records saved")
    return {}


# ------------------------------------------------------------------ #
# Graph definition                                                     #
# ------------------------------------------------------------------ #

def _build_graph_builder() -> StateGraph:
    builder = StateGraph(PipelineState)

    # Nodes
    builder.add_node("router",               node_router)
    builder.add_node("data_collector",       node_data_collector)
    builder.add_node("news_sentiment",       node_news_sentiment)
    builder.add_node("fundamental_analyst",  node_fundamental_analyst)
    builder.add_node("risk_assessor",        node_risk_assessor)
    builder.add_node("report_writer",        node_report_writer)
    builder.add_node("portfolio_loader",     node_portfolio_loader)
    builder.add_node("portfolio_manager",    node_portfolio_manager)
    builder.add_node("memory_writer",        node_memory_writer)

    builder.set_entry_point("router")

    # Router: fan-out to analysis branch OR go straight to portfolio branch
    builder.add_conditional_edges("router", _route_from_router)

    # Analysis branch: fan-in at fundamental_analyst (waits for both predecessors)
    builder.add_edge("data_collector",      "fundamental_analyst")
    builder.add_edge("news_sentiment",      "fundamental_analyst")

    # Conditional fail-fast after fundamental_analyst
    builder.add_conditional_edges(
        "fundamental_analyst",
        _route_after_fundamental,
        {"risk_assessor": "risk_assessor", END: END},
    )
    builder.add_edge("risk_assessor", "report_writer")

    # After report_writer: memory_writer (analyze) or continue to portfolio branch (full)
    builder.add_conditional_edges(
        "report_writer",
        _route_after_report,
        {"portfolio_loader": "portfolio_loader", "memory_writer": "memory_writer"},
    )

    # Portfolio branch — memory_writer is the single exit point for all branches
    builder.add_edge("portfolio_loader",  "portfolio_manager")
    builder.add_edge("portfolio_manager", "memory_writer")
    builder.add_edge("memory_writer",     END)

    return builder


# ------------------------------------------------------------------ #
# Pipeline entry point                                                 #
# ------------------------------------------------------------------ #

async def run_pipeline(
    tickers: list[str] | None = None,
    mode: str = "full",
    interactive: bool = False,
) -> dict:
    run_id = str(uuid.uuid4())

    initial_state: PipelineState = {
        "run_id": run_id,
        "mode": mode,
        "tickers": tickers or [],
        "interactive": interactive,
        "fundamentals": [],
        "news": [],
        "themes": [],
        "candidates": [],
        "risk_assessment": [],
        "report": {},
        "executive_summary": "",
        "qa_verdict": "",
        "degraded": {},
        "portfolio_state": {},
        "portfolio_result": {},
        "ticker_history": {},
        "previous_runs": [],
    }

    health = await check_agents_health(mode=mode)
    for agent_name, agent_status in health["agents"].items():
        icon = "✓" if agent_status["status"] == "ok" else "✗"
        print(f"  {icon} {agent_name}: {agent_status['status']}")
    if health["status"] == "degraded":
        print("\n  ⚠ Uno o più agenti non raggiungibili. Verificare che siano avviati.")
    print()

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

    print(f"\n{'='*60}")
    print("  PIPELINE COMPLETE")
    print(f"{'='*60}")

    result: dict[str, Any] = {
        "status": "completed",
        "run_id": run_id,
        "mode": mode,
        "degraded": degraded,
    }

    # Analysis results (present in analyze and full modes)
    if mode in ("analyze", "full"):
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

        result.update({
            "executive_summary": final_state["executive_summary"],
            "qa_verdict": final_state["qa_verdict"],
            "report": final_state["report"],
            "report_path": str(report_path),
        })

    # Portfolio results (present in portfolio and full modes)
    if mode in ("portfolio", "full"):
        result["portfolio_result"] = final_state.get("portfolio_result", {})

    return result


# ------------------------------------------------------------------ #
# Entry point (CLI)                                                    #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Equity Researcher A2A Orchestrator")
    parser.add_argument(
        "--tickers",
        nargs="*",
        default=[],
        help="Ticker universe (optional — omit for news-driven opportunistic mode)",
    )
    parser.add_argument(
        "--mode",
        choices=["analyze", "portfolio", "full"],
        default="full",
        help="Workflow mode: analyze | portfolio | full",
    )
    parser.add_argument("--output", default=None, help="Save JSON result to file")
    args = parser.parse_args()

    result = asyncio.run(run_pipeline(args.tickers, mode=args.mode, interactive=True))

    if args.mode in ("analyze", "full"):
        print("\n=== SINTESI ESECUTIVA ===")
        print(result.get("executive_summary", ""))
        print("\n=== QA VERDICT ===")
        print(result.get("qa_verdict", ""))

    if args.mode in ("portfolio", "full"):
        pm = result.get("portfolio_result", {})
        print("\n=== PORTFOLIO REVIEW ===")
        print(pm.get("review", ""))
        if args.mode == "full":
            trades = [t for t in pm.get("trades", []) if t.get("action") in ("BUY", "SELL")]
            if trades:
                if pm.get("trades_executed"):
                    print(f"\n=== TRADES EXECUTED ({len(trades)}) ===")
                else:
                    print(f"\n=== TRADES PROPOSED — non eseguiti ({len(trades)}) ===")
                for t in trades:
                    print(f"  {t['action']} {t['shares']}x {t['ticker']} @ {t['price']} — {t.get('reason', '')}")

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nRisultato salvato in: {args.output}")
