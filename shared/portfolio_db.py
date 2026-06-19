"""SQLite persistence layer for the fictional portfolio.

Database: output/portfolio.db (separate from output/checkpoints.db).
Uses aiosqlite (already a project dependency via langgraph-checkpoint-sqlite).

Tables:
  portfolio     — single row: cash balance
  positions     — open/closed equity positions
  trades        — append-only trade log, linked to pipeline runs via correlation_id

Upgrade path: PostgreSQL in Fase 5/6 — same interface, different driver.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

_DB_PATH = Path(__file__).parent.parent / "output" / "portfolio.db"
_INITIAL_CASH = 100_000.0


async def init_db() -> None:
    """Create tables if they don't exist and seed initial portfolio."""
    _DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id         INTEGER PRIMARY KEY DEFAULT 1,
                cash       REAL    NOT NULL,
                currency   TEXT    NOT NULL DEFAULT 'USD',
                updated_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT    NOT NULL,
                shares      INTEGER NOT NULL,
                entry_price REAL    NOT NULL,
                entry_date  TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS trades (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker         TEXT    NOT NULL,
                action         TEXT    NOT NULL,
                shares         INTEGER NOT NULL,
                price          REAL    NOT NULL,
                executed_at    TEXT    NOT NULL,
                reason         TEXT,
                correlation_id TEXT
            );
        """)
        row = await db.execute("SELECT id FROM portfolio WHERE id = 1")
        if await row.fetchone() is None:
            await db.execute(
                "INSERT INTO portfolio (id, cash, currency, updated_at) VALUES (1, ?, 'USD', ?)",
                (_INITIAL_CASH, _now()),
            )
        await db.commit()


async def load_portfolio_state() -> dict:
    """Return current portfolio as a plain dict (safe to put in PipelineState)."""
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        row = await (await db.execute("SELECT cash, currency, updated_at FROM portfolio WHERE id = 1")).fetchone()
        cash = row["cash"]
        currency = row["currency"]
        updated_at = row["updated_at"]

        positions = [
            dict(r) for r in
            await (await db.execute(
                "SELECT ticker, shares, entry_price, entry_date, status FROM positions WHERE status = 'open'"
            )).fetchall()
        ]

        trades = [
            dict(r) for r in
            await (await db.execute(
                "SELECT ticker, action, shares, price, executed_at, reason, correlation_id "
                "FROM trades ORDER BY id DESC LIMIT 50"
            )).fetchall()
        ]

    return {
        "cash": cash,
        "currency": currency,
        "updated_at": updated_at,
        "positions": positions,
        "trade_history": trades,
    }


async def save_portfolio_state(portfolio_update: dict, correlation_id: str) -> None:
    """Persist the PM's output back to SQLite.

    portfolio_update is the 'portfolio_update' key from the PM's A2A response data:
    {
      "cash_after": float,
      "positions_after": [{"ticker", "shares", "entry_price", "entry_date", "status"}, ...],
    }
    trades is the 'trades' key from the PM's response:
    [{"ticker", "action", "shares", "price", "reason"}, ...]
    """
    await init_db()
    now = _now()

    cash_after = portfolio_update.get("cash_after")
    positions_after = portfolio_update.get("positions_after", [])
    trades = portfolio_update.get("trades", [])

    async with aiosqlite.connect(_DB_PATH) as db:
        if cash_after is not None:
            await db.execute(
                "UPDATE portfolio SET cash = ?, updated_at = ? WHERE id = 1",
                (cash_after, now),
            )

        if positions_after:
            new_tickers = {p["ticker"] for p in positions_after}

            # Close only positions that disappeared from positions_after (actual sells).
            # HOLD positions are updated in-place to avoid phantom closed rows.
            open_rows = await (
                await db.execute("SELECT ticker FROM positions WHERE status = 'open'")
            ).fetchall()
            open_tickers = {r[0] for r in open_rows}

            for ticker in open_tickers - new_tickers:
                await db.execute(
                    "UPDATE positions SET status = 'closed' WHERE ticker = ? AND status = 'open'",
                    (ticker,),
                )

            for pos in positions_after:
                ticker = pos["ticker"]
                if ticker in open_tickers:
                    await db.execute(
                        "UPDATE positions SET shares = ?, entry_price = ?, entry_date = ? "
                        "WHERE ticker = ? AND status = 'open'",
                        (pos["shares"], pos["entry_price"], pos.get("entry_date", now[:10]), ticker),
                    )
                else:
                    await db.execute(
                        "INSERT INTO positions (ticker, shares, entry_price, entry_date, status) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            ticker,
                            pos["shares"],
                            pos["entry_price"],
                            pos.get("entry_date", now[:10]),
                            pos.get("status", "open"),
                        ),
                    )

        for trade in trades:
            await db.execute(
                "INSERT INTO trades (ticker, action, shares, price, executed_at, reason, correlation_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    trade["ticker"],
                    trade["action"],
                    trade["shares"],
                    trade["price"],
                    now,
                    trade.get("reason"),
                    correlation_id,
                ),
            )

        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
