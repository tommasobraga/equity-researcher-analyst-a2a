"""Orchestrator — LangGraph pipeline v3.

Three selectable workflows via the mode parameter:

  analyze   — parallel fan-out data_collector + news_sentiment
              → fundamental_analyst → risk_assessor → report_writer → END

  portfolio — portfolio_loader → portfolio_manager → END

  full      — analyze pipeline → portfolio_loader → portfolio_manager → END

Routing is deterministic today (conditional edges on state values).
LLM-based routing ready: _route_* functions receive full PipelineState —
replace body with react_loop() call when cloud provider is available.

Phase 3: structured retry (tenacity), custom circuit breaker,
         LangGraph checkpointing (SQLite), graceful degradation,
         payload windowing.
"""
import asyncio
import datetime
import json
import os
import re
import sys
import time
import uuid
import webbrowser
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, TypedDict

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

# Ensure UTF-8 stdout on Windows (cp1252 default rejects ← → used in print statements)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
from shared.rag_retriever import retrieve_context
from shared.llm_judge import run_judge
from shared.report import generate_html
from shared.validators import validate_tickers
from shared.models import TaskDecomposition
from orchestrator.gates import (
    PASS, RETRY, FAIL,
    node_gate_data_collector,
    node_gate_news_sentiment,
    node_gate_fundamental_analyst, route_gate_fundamental_analyst,
    node_gate_risk_assessor, route_gate_risk_assessor,
    node_gate_report_writer, route_gate_report_writer,
)

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
JUDGE_SCORE_THRESHOLD  = int(os.getenv("JUDGE_SCORE_THRESHOLD", "60"))

_RATE_LIMIT_PATTERN = re.compile(
    r"\b(rate.?limit|too many requests|concurrent connections|overloaded|529)\b",
    re.IGNORECASE,
)

_SKIP_NODES = {"router"}  # nodi interni di routing, non visibili nella UI


# ------------------------------------------------------------------ #
# Pipeline state                                                       #
# ------------------------------------------------------------------ #

def _merge_dicts(a: dict, b: dict) -> dict:
    """Reducer for dict fields written by parallel nodes."""
    return {**a, **b}


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
    degraded: Annotated[dict, _merge_dicts]  # {component: error_message}
    portfolio_state: dict   # loaded from SQLite by node_portfolio_loader
    portfolio_result: dict  # output from PortfolioManager agent
    rag_context: str        # retrieved from data/rag/documents/ — injected into FA prompt
    judgment: dict          # LLM Judge result: verdict, grounding_score, issues, summary
    ticker_history: dict    # {ticker: {"fundamental": [...], "risk": [...]}} — loaded before FA
    previous_runs: list     # recent run_summaries — loaded before FA
    retry_counts: dict      # {"gate_risk_assessor": 0, "gate_report_writer": 0}
    gate_feedback: dict     # {"risk_assessor": "...", "report_writer": "..."}
    user_prompt: str        # natural language task description — empty if not provided
    task_decomposition: dict  # TaskDecomposition.model_dump() — empty if not decomposed


# ------------------------------------------------------------------ #
# Circuit breaker                                                      #
# ------------------------------------------------------------------ #

class _CircuitBreaker:
    """Lightweight async-compatible circuit breaker.

    Opens after fail_max consecutive failures; resets after reset_timeout seconds.
    In-house implementation on time.monotonic(), no extra dependencies.
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
    return [Send("data_collector", state), Send("news_sentiment", state), Send("rag_retriever", state)]


def _route_after_judge(state: PipelineState) -> str:
    """In full mode, continue to portfolio branch; in analyze mode, persist memory and stop.
    If the LLM Judge blocked the report (score below threshold), skip portfolio in any mode.
    """
    if state.get("degraded", {}).get("judge_blocked"):
        return "memory_writer"
    if state["mode"] == "full":
        return "portfolio_loader"
    return "memory_writer"


# ------------------------------------------------------------------ #
# Graph nodes — analysis branch                                        #
# ------------------------------------------------------------------ #

_DECOMPOSER_SYSTEM = """You are a financial research task decomposer with extended reasoning.

Think step by step before producing output:
1. What is the user's core research intent?
2. Which specific stocks or sectors are implied?
3. What time horizon and risk profile does the request suggest?
4. What constraints should downstream agents respect?
5. What is the single most important analytical angle for this request?

Universe: US and EU equities only (no LSE .L tickers, no crypto/DeFi/Web3).
Allowed sectors: Technology, AI, Software, Semiconductors, Banking, Financial Services.
Excluded sectors: energy, utilities, real estate, REITs, consumer staples, industrials, airlines.

After reasoning, respond with a valid JSON object — no markdown, no extra text:
{
  "intent": "ticker_analysis|sector_screen|comparative_analysis|theme_exploration|portfolio_review",
  "tickers": [],
  "mode": "analyze|portfolio|full",
  "research_focus": "concise research focus sentence for downstream agents",
  "sectors": [],
  "horizon_weeks": null,
  "constraints": []
}"""

# Model for extended thinking — must be Sonnet or above (Haiku does not support thinking)
_MODEL_DECOMPOSER = "claude-sonnet-4-6"
_THINKING_BUDGET_TOKENS = 8_000
_DECOMPOSER_MAX_TOKENS = 10_000  # must be > budget_tokens


def _synthetic_rationale(user_prompt: str, decomp: "TaskDecomposition") -> str:
    """Build a deterministic rationale for DEMO_MODE — exercises the injection path."""
    horizon = f"{decomp.horizon_weeks} weeks" if decomp.horizon_weeks else "not specified"
    sectors = ", ".join(decomp.sectors) if decomp.sectors else "to be determined via news"
    constraints = "; ".join(decomp.constraints) if decomp.constraints else "none additional"
    tickers = ", ".join(decomp.tickers) if decomp.tickers else "none explicit — news-driven search"
    return (
        f"[DEMO rationale — extended thinking not active]\n"
        f"Request analysed: '{user_prompt}'\n"
        f"Intent classified as '{decomp.intent}'.\n"
        f"Tickers identified: {tickers}.\n"
        f"Relevant sectors: {sectors}.\n"
        f"Time horizon: {horizon}.\n"
        f"Additional constraints: {constraints}.\n"
        f"Downstream agents should focus on: {decomp.research_focus}."
    )


async def node_task_decomposer(state: PipelineState) -> dict:
    user_prompt = state.get("user_prompt", "")
    if not user_prompt:
        return {}  # no-op — tickers and mode already set by caller

    print(f"\n[DECOMP] TaskDecomposer ← '{user_prompt[:70]}'")

    if is_demo_mode():
        decomp = TaskDecomposition(
            intent="ticker_analysis" if state["tickers"] else "sector_screen",
            tickers=list(state["tickers"]),
            mode=state["mode"],
            research_focus=user_prompt,
            sectors=[],
            horizon_weeks=None,
            constraints=[],
        )
        decomp.rationale = _synthetic_rationale(user_prompt, decomp)
        print(f"      → intent: {decomp.intent}  focus: '{decomp.research_focus[:50]}' (demo)")
        return {"task_decomposition": decomp.model_dump()}

    from shared.llm_client import get_llm_client
    client = get_llm_client()
    rationale = ""
    try:
        # Extended thinking: Sonnet reasons step-by-step before producing JSON.
        # The thinking block is extracted as rationale and passed to downstream agents.
        response = client.messages.create(
            model=_MODEL_DECOMPOSER,
            max_tokens=_DECOMPOSER_MAX_TOKENS,
            thinking={"type": "enabled", "budget_tokens": _THINKING_BUDGET_TOKENS},
            system=_DECOMPOSER_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = ""
        for block in response.content:
            if block.type == "thinking":
                rationale = block.thinking
            elif block.type == "text":
                raw = block.text
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group()) if m else {}
        decomp = TaskDecomposition.model_validate(data)
        decomp.rationale = rationale
    except Exception as e:
        log.warning("task_decomposer.fallback", error=str(e))
        decomp = TaskDecomposition(
            intent="sector_screen" if not state["tickers"] else "ticker_analysis",
            tickers=list(state["tickers"]),
            mode=state["mode"],
            research_focus=user_prompt,
        )

    thinking_active = bool(rationale)
    print(f"      → intent: {decomp.intent}  tickers: {decomp.tickers or '(from pipeline)'}  thinking: {thinking_active}")

    # Merge tickers: explicit (caller) take precedence; decomposed fill gaps
    merged = list(dict.fromkeys(state["tickers"] + decomp.tickers))
    updates: dict = {"task_decomposition": decomp.model_dump()}
    if merged != state["tickers"]:
        updates["tickers"] = merged
    # Only override mode if caller didn't supply tickers (i.e. fully prompt-driven)
    if not state["tickers"] and decomp.mode != state["mode"]:
        updates["mode"] = decomp.mode

    return updates


async def node_router(state: PipelineState) -> dict:
    mode = state["mode"]
    print(f"\n{'='*60}")
    print(f"  EQUITY RESEARCHER A2A — LangGraph Pipeline v3")
    print(f"  run_id: {state['run_id']}  mode: {mode.upper()}")
    if is_demo_mode():
        print("  mode:   DEMO (no LLM calls)")
    print(f"{'='*60}")
    return {}


async def node_data_collector(state: PipelineState) -> dict:
    if not state["tickers"]:
        print("\n[1/5] DataCollector ← skipped (news-driven mode, no tickers specified)")
        return {"fundamentals": []}

    ticker_list = ", ".join(state["tickers"])
    print(f"\n[1/5] DataCollector ← {ticker_list}")
    try:
        result = await send_task_with_retry(
            "data_collector",
            f"Fetch fundamental data for: {ticker_list}. Return a JSON array.",
            data={"tickers": state["tickers"]},
            correlation_id=state["run_id"],
        )
    except (AgentUnavailableError, AgentTimeoutError, RateLimitError) as e:
        log.warning("node.degraded", node="data_collector", error=str(e))
        print(f"      ⚠ DataCollector unavailable — analysis will proceed on news/themes only ({e})")
        return {
            "fundamentals": [],
            "degraded": {**state.get("degraded", {}), "data_collector": str(e)},
        }

    if result.status == "failed":
        log.warning("node.degraded", node="data_collector", error=result.message.text())
        print("      ⚠ DataCollector failed — analysis will proceed on news/themes only")
        return {
            "fundamentals": [],
            "degraded": {**state.get("degraded", {}), "data_collector": result.message.text()},
        }

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

    gate = node_gate_data_collector({"fundamentals": fundamentals, "degraded": degraded})
    print(f"      → {len(gate['fundamentals'])} ticker(s) fetched")
    return {"fundamentals": gate["fundamentals"], "degraded": gate["degraded"]}


async def node_news_sentiment(state: PipelineState) -> dict:
    print("\n[2/5] NewsSentiment ← fetching RSS feeds")
    decomp = state.get("task_decomposition", {})
    topic = (
        decomp.get("research_focus")
        or ", ".join(decomp.get("sectors", []))
        or "Technology, AI, Software, Semiconductors, Banking, Financial Services"
    )
    try:
        result = await send_task_with_retry(
            "news_sentiment",
            topic,
            timeout=180.0,
            correlation_id=state["run_id"],
        )
    except (AgentUnavailableError, AgentTimeoutError, RateLimitError) as e:
        log.warning("node.degraded", node="news_sentiment", error=str(e))
        print(f"      ⚠ NewsSentiment unavailable — pipeline continues without news ({e})")
        return {
            "news": [],
            "themes": [],
            "degraded": {**state.get("degraded", {}), "news_sentiment": str(e)},
        }

    if result.status == "failed":
        print("      ⚠ NewsSentiment failed — pipeline continues without news")
        return {
            "news": [],
            "themes": [],
            "degraded": {**state.get("degraded", {}), "news_sentiment": result.message.text()},
        }

    news = (_extract_data(result, "news") or [])[:MAX_NEWS_PAYLOAD]
    themes = _extract_data(result, "themes") or []
    gate = node_gate_news_sentiment({"news": news, "themes": themes, "degraded": state.get("degraded", {})})
    print(f"      → {len(gate['news'])} news, {len(gate['themes'])} themes")
    return {"news": gate["news"], "themes": gate["themes"], "degraded": gate["degraded"]}


async def node_rag_retriever(state: PipelineState) -> dict:
    print("\n[RAG] RAGRetriever ← querying internal knowledge base")
    query_terms = list(state.get("tickers", []))
    if not query_terms:
        query_terms = ["technology", "AI", "banking", "semiconductors", "software"]
    try:
        context = await asyncio.to_thread(retrieve_context, query_terms)
        n_chunks = context.count("[Source:") if context else 0
        print(f"      → {n_chunks} chunk(s) retrieved")
        return {"rag_context": context}
    except Exception as e:
        log.warning("node.degraded", node="rag_retriever", error=str(e))
        print(f"      ⚠ RAGRetriever unavailable — pipeline continues without context ({e})")
        return {"rag_context": "", "degraded": {**state.get("degraded", {}), "rag_retriever": str(e)}}


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

    decomp = state.get("task_decomposition", {})
    research_focus = decomp.get("research_focus", "")
    constraints = "; ".join(decomp.get("constraints", []))
    rationale = decomp.get("rationale", "")
    fa_instruction = "Analyse the provided news, themes and fundamentals. Return equity candidates."
    if rationale:
        fa_instruction = f"RAGIONAMENTO DEL PIANIFICATORE:\n{rationale}\n\n---\n\n{fa_instruction}"
    elif research_focus:
        fa_instruction = f"Research focus: {research_focus}\n\n{fa_instruction}"
    if constraints:
        fa_instruction += f"\n\nAdditional constraints: {constraints}"

    result = await send_task_with_retry(
        "fundamental_analyst",
        fa_instruction,
        data={
            "news": state["news"],
            "themes": state["themes"],
            "fundamentals": state["fundamentals"],
            "rag_context": state.get("rag_context", ""),
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
    gate_feedback_text = state.get("gate_feedback", {}).get("risk_assessor", "")
    result = await send_task_with_retry(
        "risk_assessor",
        "Perform risk assessment and scoring for each candidate.",
        data={
            "candidates": state["candidates"],
            "risk_history": ra_snippets,
            "gate_feedback": gate_feedback_text,
        },
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
    gate_feedback_text = state.get("gate_feedback", {}).get("report_writer", "")
    decomp = state.get("task_decomposition", {})
    result = await send_task_with_retry(
        "report_writer",
        "Produce the final equity research report in Italian.",
        data={
            "candidates": state["candidates"],
            "risk_assessment": state["risk_assessment"],
            "news": state["news"],
            "themes": state["themes"],
            "previous_runs_context": format_run_summaries(state.get("previous_runs", [])),
            "gate_feedback": gate_feedback_text,
            "research_focus": decomp.get("research_focus", ""),
            "rationale": decomp.get("rationale", ""),
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


async def node_llm_judge(state: PipelineState) -> dict:
    verdict_icons = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}
    print("\n[JUDGE] LLMJudge ← grounding check")
    from shared.llm_client import get_llm_client
    client = None if is_demo_mode() else get_llm_client()
    judgment = await run_judge(
        client=client,
        executive_summary=state.get("executive_summary", ""),
        report_dict=state.get("report", {}),
        news=state.get("news", []),
        fundamentals=state.get("fundamentals", []),
        rag_context=state.get("rag_context", ""),
        correlation_id=state["run_id"],
    )
    icon = verdict_icons.get(judgment.verdict, "?")
    print(f"      {icon} verdict: {judgment.verdict}  grounding_score: {judgment.grounding_score}/100")
    if judgment.issues:
        print(f"      issues: {len(judgment.issues)}")

    degraded = dict(state.get("degraded", {}))
    if judgment.verdict == "FAIL":
        degraded["llm_judge_fail"] = judgment.summary
    elif judgment.verdict == "WARN":
        degraded["llm_judge_warn"] = judgment.summary

    if judgment.grounding_score < JUDGE_SCORE_THRESHOLD:
        degraded["judge_blocked"] = (
            f"grounding_score {judgment.grounding_score} < threshold {JUDGE_SCORE_THRESHOLD} "
            f"(verdict: {judgment.verdict}) — report not publishable"
        )
        print(f"      ✗ BLOCKED: score {judgment.grounding_score} below threshold {JUDGE_SCORE_THRESHOLD}")

    return {"judgment": judgment.to_dict(), "degraded": degraded}


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
        payload["judgment"] = state.get("judgment", {})

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

    # Nodes — analysis agents
    builder.add_node("task_decomposer",      node_task_decomposer)
    builder.add_node("router",               node_router)
    builder.add_node("data_collector",       node_data_collector)
    builder.add_node("news_sentiment",       node_news_sentiment)
    builder.add_node("rag_retriever",        node_rag_retriever)
    builder.add_node("fundamental_analyst",  node_fundamental_analyst)
    builder.add_node("risk_assessor",        node_risk_assessor)
    builder.add_node("report_writer",        node_report_writer)
    builder.add_node("llm_judge",            node_llm_judge)
    builder.add_node("portfolio_loader",     node_portfolio_loader)
    builder.add_node("portfolio_manager",    node_portfolio_manager)
    builder.add_node("memory_writer",        node_memory_writer)

    # Nodes — validation gates (hard gates only; soft gates run inside agent nodes)
    builder.add_node("gate_fundamental_analyst", node_gate_fundamental_analyst)
    builder.add_node("gate_risk_assessor",       node_gate_risk_assessor)
    builder.add_node("gate_report_writer",       node_gate_report_writer)

    builder.set_entry_point("task_decomposer")
    builder.add_edge("task_decomposer", "router")

    # Router: fan-out to analysis branch OR go straight to portfolio branch
    builder.add_conditional_edges("router", _route_from_router)

    # Fan-out branches → fan-in at fundamental_analyst (3 symmetric edges = AND-join)
    # Soft gate validation runs inside node_data_collector / node_news_sentiment.
    builder.add_edge("data_collector",  "fundamental_analyst")
    builder.add_edge("news_sentiment",  "fundamental_analyst")
    builder.add_edge("rag_retriever",   "fundamental_analyst")

    # Hard gate after fundamental_analyst — fail-fast if no valid candidates
    builder.add_edge("fundamental_analyst", "gate_fundamental_analyst")
    builder.add_conditional_edges(
        "gate_fundamental_analyst",
        route_gate_fundamental_analyst,
        {PASS: "risk_assessor", FAIL: END},
    )

    # Hard gate after risk_assessor — reflection retry (max 1)
    builder.add_edge("risk_assessor", "gate_risk_assessor")
    builder.add_conditional_edges(
        "gate_risk_assessor",
        route_gate_risk_assessor,
        {PASS: "report_writer", RETRY: "risk_assessor", FAIL: END},
    )

    # Hard gate after report_writer — reflection retry (max 1)
    builder.add_edge("report_writer", "gate_report_writer")
    builder.add_conditional_edges(
        "gate_report_writer",
        route_gate_report_writer,
        {PASS: "llm_judge", RETRY: "report_writer", FAIL: END},
    )

    builder.add_conditional_edges(
        "llm_judge",
        _route_after_judge,
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
    prompt: str | None = None,
) -> dict:
    if tickers:
        errors = validate_tickers(tickers)
        if errors:
            raise ValueError("Ticker validation failed:\n" + "\n".join(f"  • {e}" for e in errors))

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
        "rag_context": "",
        "judgment": {},
        "ticker_history": {},
        "previous_runs": [],
        "retry_counts": {},
        "gate_feedback": {},
        "user_prompt": prompt or "",
        "task_decomposition": {},
    }

    health = await check_agents_health(mode=mode)
    for agent_name, agent_status in health["agents"].items():
        icon = "✓" if agent_status["status"] == "ok" else "✗"
        print(f"  {icon} {agent_name}: {agent_status['status']}")
    if health["status"] == "degraded":
        print("\n  ⚠ One or more agents unreachable. Make sure they are running.")
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
        print("\n  ⚠ Degraded components during this run:")
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
            judgment=final_state.get("judgment"),
        )
        print(f"\nHTML report saved to: {report_path}")
        if violations:
            errors = [v for v in violations if v.severity == "error"]
            warnings = [v for v in violations if v.severity == "warning"]
            if errors:
                print(f"  ⚠  {len(errors)} critical error(s) in the report")
            if warnings:
                print(f"  ℹ  {len(warnings)} quality warning(s)")
        webbrowser.open(report_path.as_uri())

        result.update({
            "executive_summary": final_state["executive_summary"],
            "qa_verdict": final_state["qa_verdict"],
            "report": final_state["report"],
            "judgment": final_state.get("judgment", {}),
            "report_path": str(report_path),
        })

    # Portfolio results (present in portfolio and full modes)
    if mode in ("portfolio", "full"):
        result["portfolio_result"] = final_state.get("portfolio_result", {})

    return result


# ------------------------------------------------------------------ #
# Streaming pipeline — SSE / Web UI                                    #
# ------------------------------------------------------------------ #

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


_NODE_LABELS: dict[str, str] = {
    "data_collector":           "DataCollector",
    "news_sentiment":           "NewsSentiment",
    "rag_retriever":            "RAGRetriever",
    "fundamental_analyst":      "FundamentalAnalyst",
    "risk_assessor":            "RiskAssessor",
    "report_writer":            "ReportWriter",
    "gate_fundamental_analyst": "FundamentalAnalyst gate",
    "gate_risk_assessor":       "RiskAssessor gate",
    "gate_report_writer":       "ReportWriter gate",
    "llm_judge":                "LLM Judge",
    "portfolio_manager":        "PortfolioManager",
    "memory_writer":            "MemoryWriter",
    "sanitize_rss_item":        "RSS Sanitizer",
}


def _guardrail_event(source: str, key: str, reason: str) -> dict:
    """Produce a guardrail event with category and human-readable message."""
    readable_source = _NODE_LABELS.get(source, source)

    if "redact" in key or "inject" in reason.lower():
        category = "security"
        readable_reason = reason
    elif "blocked" in key:
        category = "blocked"
        readable_reason = reason
    elif "warning" in key or "warning" in reason.lower():
        n = reason.split()[0] if reason[0].isdigit() else ""
        category = "quality"
        readable_reason = f"{n} quality warning(s) in report — review candidates" if n else reason
    elif "llm_judge" in key:
        category = "quality"
        readable_reason = reason
    else:
        category = "degraded"
        if "HTTP 500" in reason or "HTTP 503" in reason or "HTTP 502" in reason:
            readable_reason = "agent unreachable — pipeline continues in degraded mode"
        elif "connection refused" in reason:
            readable_reason = "connection refused — agent not running"
        elif "timeout" in reason.lower():
            readable_reason = "timeout — pipeline continues in degraded mode"
        elif "circuit breaker" in reason.lower():
            readable_reason = "circuit breaker open — agent temporarily excluded"
        elif "No data for" in reason:
            readable_reason = f"partial data — {reason}"
        else:
            readable_reason = reason

    return {
        "type": "guardrail",
        "source": readable_source,
        "key": key,
        "reason": readable_reason,
        "category": category,
        "ts": _now(),
    }


def _node_summary(node: str, update: dict) -> dict:
    """Estrae metriche human-readable dall'update di stato di un nodo."""
    match node:
        case "task_decomposer":
            td = update.get("task_decomposition", {})
            return {"intent": td.get("intent", ""), "focus": td.get("research_focus", "")[:60]}
        case "data_collector":
            return {"count": len(update.get("fundamentals", []))}
        case "news_sentiment":
            news = update.get("news", [])
            redacted = sum(1 for n in news if "[REDACTED]" in str(n))
            return {"news": len(news), "themes": len(update.get("themes", [])), "redacted": redacted}
        case "rag_retriever":
            ctx = update.get("rag_context", "")
            return {"chunks": ctx.count("[Source:") if ctx else 0}
        case "fundamental_analyst":
            return {"candidates": len(update.get("candidates", []))}
        case "risk_assessor":
            return {"assessments": len(update.get("risk_assessment", []))}
        case "report_writer":
            return {"qa": update.get("qa_verdict", "")}
        case "llm_judge":
            j = update.get("judgment", {})
            return {"score": j.get("grounding_score"), "verdict": j.get("verdict")}
        case "portfolio_manager":
            pr = update.get("portfolio_result", {})
            trades = [t for t in pr.get("trades", []) if t.get("action") in ("BUY", "SELL")]
            return {"trades": len(trades)}
        case _:
            return {}


async def stream_pipeline(
    tickers: list[str] | None = None,
    mode: str = "full",
    prompt: str | None = None,
) -> AsyncIterator[dict]:
    """Async generator — emette eventi SSE per ogni nodo del grafo LangGraph.

    Tipi di evento:
      pipeline_started   — run_id, mode, tickers
      health_check       — status per ogni agente richiesto dalla mode
      node_started       — nodo inizia (da LangGraph debug task event)
      node_completed     — nodo completato + summary data
      guardrail          — nuova entry in degraded dict o item RSS redatto
      pipeline_completed — metriche finali + executive_summary
      error              — eccezione fatale
    """
    if tickers:
        errors = validate_tickers(tickers)
        if errors:
            raise ValueError("Ticker validation failed:\n" + "\n".join(f"  • {e}" for e in errors))

    run_id = str(uuid.uuid4())
    t0 = time.time()

    initial_state: PipelineState = {
        "run_id": run_id,
        "mode": mode,
        "tickers": tickers or [],
        "interactive": False,
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
        "rag_context": "",
        "judgment": {},
        "ticker_history": {},
        "previous_runs": [],
        "retry_counts": {},
        "gate_feedback": {},
        "user_prompt": prompt or "",
        "task_decomposition": {},
    }

    yield {"type": "pipeline_started", "run_id": run_id, "mode": mode,
           "tickers": tickers or [], "ts": _now()}

    health = await check_agents_health(mode=mode)
    for agent_name, status in health["agents"].items():
        yield {"type": "health_check", "agent": agent_name,
               "status": status["status"], "ts": _now()}

    prev_degraded: dict = {}
    final_state: dict = {}

    config = {"configurable": {"thread_id": run_id}}
    Path("output").mkdir(exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string("output/checkpoints.db") as checkpointer:
        graph = _build_graph_builder().compile(checkpointer=checkpointer)

        async for chunk in graph.astream(initial_state, config=config, stream_mode="debug"):
            ctype = chunk.get("type")

            if ctype == "task":
                node = chunk["payload"]["name"]
                if node not in _SKIP_NODES:
                    yield {"type": "node_started", "node": node, "ts": _now()}

            elif ctype == "task_result":
                node = chunk["payload"]["name"]
                # result è lista di (channel, value) pairs — raw output del nodo, pre-reducer
                update = dict(chunk["payload"].get("result") or [])

                if node not in _SKIP_NODES:
                    yield {"type": "node_completed", "node": node,
                           "ts": _now(), "data": _node_summary(node, update)}

                # Guardrail: nuove entry nel degraded dict
                curr_degraded = update.get("degraded", {})
                for key, reason in curr_degraded.items():
                    if key not in prev_degraded:
                        yield _guardrail_event(node, key, str(reason))
                prev_degraded.update(curr_degraded)

                # Guardrail: RSS items redatti da sanitize_rss_item
                if node == "news_sentiment":
                    news = update.get("news", [])
                    n_redacted = sum(1 for n in news if "[REDACTED]" in str(n))
                    if n_redacted:
                        yield _guardrail_event(
                            "sanitize_rss_item", "redacted_items",
                            f"{n_redacted} RSS item(s) redatti — injection detected",
                        )

                final_state.update(update)

    execution_seconds = int(time.time() - t0)
    report_path = None
    if mode in ("analyze", "full"):
        try:
            report_path, _ = generate_html(
                executive_summary=final_state.get("executive_summary", ""),
                report_dict=final_state.get("report", {}),
                qa_verdict=final_state.get("qa_verdict", ""),
                tickers=tickers or [],
                execution_seconds=execution_seconds,
                run_id=run_id,
                judgment=final_state.get("judgment"),
            )
        except Exception as exc:
            log.warning("stream.report_gen_failed", error=str(exc))

    j = final_state.get("judgment", {})
    yield {
        "type": "pipeline_completed",
        "run_id": run_id,
        "ts": _now(),
        "metrics": {
            "latency_s": execution_seconds,
            "candidates": len(final_state.get("candidates", [])),
            "grounding_score": j.get("grounding_score"),
            "verdict": j.get("verdict"),
            "degraded": list(prev_degraded.keys()),
        },
        "executive_summary": final_state.get("executive_summary", ""),
        "qa_verdict": final_state.get("qa_verdict", ""),
        "report_path": str(report_path) if report_path else None,
    }


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
    parser.add_argument(
        "--prompt", default=None,
        help="Natural language task description (activates task decomposition)",
    )
    args = parser.parse_args()

    result = asyncio.run(run_pipeline(args.tickers, mode=args.mode, interactive=True, prompt=args.prompt))

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
                    print(f"\n=== TRADES PROPOSED — not executed ({len(trades)}) ===")
                for t in trades:
                    print(f"  {t['action']} {t['shares']}x {t['ticker']} @ {t['price']} — {t.get('reason', '')}")

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nResult saved to: {args.output}")
