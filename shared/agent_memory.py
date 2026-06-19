"""Persistent memory layer for cross-run agent context.

Database: output/memory.db (separate from portfolio.db and checkpoints.db).
Uses aiosqlite — same pattern as shared/portfolio_db.py.

Tables:
  ticker_analyses  — per-ticker history from fundamental_analyst and risk_assessor
  run_summaries    — cross-run context: tickers analyzed, top candidates, trades

Format functions (sync) produce human-readable bullets for LLM prompts.
They return "" when history is empty so callers can gate on truthiness.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

_DB_PATH = Path(__file__).parent.parent / "output" / "memory.db"


async def init_db() -> None:
    _DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS ticker_analyses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                ticker      TEXT NOT NULL,
                agent       TEXT NOT NULL,
                data        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ta_ticker_agent
                ON ticker_analyses(ticker, agent, created_at DESC);

            CREATE TABLE IF NOT EXISTS run_summaries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL UNIQUE,
                mode        TEXT NOT NULL,
                tickers     TEXT NOT NULL,
                candidates  TEXT NOT NULL,
                trades      TEXT,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rs_date
                ON run_summaries(created_at DESC);
        """)
        await db.commit()


async def write_ticker_analysis(run_id: str, ticker: str, agent: str, data: dict) -> None:
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT INTO ticker_analyses (run_id, ticker, agent, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, ticker, agent, json.dumps(data, ensure_ascii=False), _now()),
        )
        await db.commit()


async def read_ticker_history(ticker: str, agent: str, limit: int = 5) -> list[dict]:
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT run_id, ticker, agent, data, created_at FROM ticker_analyses "
            "WHERE ticker = ? AND agent = ? ORDER BY created_at DESC LIMIT ?",
            (ticker, agent, limit),
        )).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d["data"])
        result.append(d)
    return result


async def write_run_summary(
    run_id: str,
    mode: str,
    tickers: list,
    candidates: list,
    trades: list | None = None,
) -> None:
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO run_summaries "
            "(run_id, mode, tickers, candidates, trades, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                mode,
                json.dumps(tickers, ensure_ascii=False),
                json.dumps(candidates, ensure_ascii=False),
                json.dumps(trades, ensure_ascii=False) if trades is not None else None,
                _now(),
            ),
        )
        await db.commit()


async def read_recent_runs(limit: int = 3) -> list[dict]:
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT run_id, mode, tickers, candidates, trades, created_at "
            "FROM run_summaries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tickers"] = json.loads(d["tickers"])
        d["candidates"] = json.loads(d["candidates"])
        d["trades"] = json.loads(d["trades"]) if d["trades"] else None
        result.append(d)
    return result


# ------------------------------------------------------------------ #
# Format functions — sync, for LLM prompt injection                   #
# ------------------------------------------------------------------ #

def format_fundamental_history(ticker: str, history: list[dict]) -> str:
    """Compact bullet list of past fundamental analyses for a ticker."""
    if not history:
        return ""
    lines = [f"FUNDAMENTAL ANALYSIS HISTORY - {ticker} (last {len(history)} run(s)):"]
    for rec in history:
        date_str = rec["created_at"][:10]
        d = rec["data"]
        thesis = (d.get("thesis") or "")[:250]
        catalyst = (d.get("catalyst") or "")[:150]
        price = (d.get("fundamentals") or {}).get("price", "")
        pe = (d.get("fundamentals") or {}).get("pe_ttm", "")
        meta = f"  price: {price}, P/E: {pe}" if price or pe else ""
        lines.append(f"• {date_str}{meta}")
        if thesis:
            lines.append(f"  thesis: {thesis}")
        if catalyst:
            lines.append(f"  catalyst: {catalyst}")
    return "\n".join(lines)


def format_risk_history(ticker: str, history: list[dict]) -> str:
    """Compact bullet list of past risk assessments for a ticker."""
    if not history:
        return ""
    lines = [f"RISK ASSESSMENT HISTORY - {ticker} (last {len(history)} run(s)):"]
    for rec in history:
        date_str = rec["created_at"][:10]
        d = rec["data"]
        score = (d.get("scoring") or {}).get("totale", "N/A")
        quality = d.get("quality", "N/A")
        scenarios = d.get("scenarios") or {}
        base = (scenarios.get("base") or "")[:150]
        bear = (scenarios.get("bear") or "")[:150]
        lines.append(f"• {date_str}  score: {score}/50  quality: {quality}")
        if base:
            lines.append(f"  base: {base}")
        if bear:
            lines.append(f"  bear: {bear}")
    return "\n".join(lines)


def format_run_summaries(summaries: list[dict]) -> str:
    """Compact list of recent pipeline runs for cross-run context."""
    if not summaries:
        return ""
    lines = [f"LAST {len(summaries)} PREVIOUS RUN(S):"]
    for s in summaries:
        date_str = s["created_at"][:10]
        mode = s["mode"]
        tickers_str = ", ".join(s.get("tickers") or [])
        candidates_str = ", ".join(s.get("candidates") or [])
        lines.append(f"- {date_str} [{mode}]  analyzed: {tickers_str}  |  candidates: {candidates_str}")
        trades = s.get("trades") or []
        if trades:
            trade_str = ", ".join(f"{t.get('action','?')} {t.get('ticker','?')}" for t in trades)
            lines.append(f"  trades: {trade_str}")
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
