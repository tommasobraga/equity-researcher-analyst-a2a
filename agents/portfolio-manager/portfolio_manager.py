"""Portfolio Manager agent — Anthropic SDK direct + FastAPI, port 8010.

Manages a fictional equity portfolio (paper trading) in two modes:
  - full:      receives the full analysis pipeline output (candidates,
               risk_assessment, report) and makes buy/sell decisions.
  - portfolio: receives only the current portfolio state and produces
               a review (P&L estimate, sector exposure, overweight flags).
"""
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

log = structlog.get_logger()

_QUALITY_LABELS = {
    "alta": "high",
    "media": "medium",
    "bassa": "low",
    "dati_insufficienti": "insufficient data",
}

_MODEL_ID = "claude-sonnet-4-6"

# ------------------------------------------------------------------ #
# Prompts                                                              #
# ------------------------------------------------------------------ #

_PM_FULL_SYSTEM = """You are a quantitative portfolio manager. Today is {today}.

You manage a fictional equity portfolio for demonstration purposes (paper trading).

You receive:
- the current portfolio state (cash, open positions)
- equity candidates from fundamental analysis with their scores (max 50)
- the risk assessment for each candidate

DECISION RULES:
BUY:  scoring.total >= 35 AND quality in ["alta","media"] AND ticker not already in portfolio at weight > 15% of total capital
SELL: existing position whose ticker has quality in ["bassa","dati_insufficienti"] in the risk assessment
HOLD: everything else

POSITION SIZING:
- Size per position: 10% of available cash (rounded to 1 share lot)
- Price: use fundamentals.price if available, else analyst_target, else 100.0 as fallback
- Never exceed 20% of total portfolio value in a single ticker

OUTPUT — respond ONLY with valid JSON (no markdown):
{{
  "pm_mode": "full",
  "trades": [
    {{"ticker": "X", "action": "BUY|SELL|HOLD", "shares": 0, "price": 0.0, "reason": "1 sentence"}}
  ],
  "portfolio_update": {{
    "cash_before": 0.0,
    "cash_after": 0.0,
    "positions_before": [],
    "positions_after": [
      {{"ticker": "X", "shares": 0, "entry_price": 0.0, "entry_date": "{today}", "status": "open"}}
    ],
    "trades": [
      {{"ticker": "X", "action": "BUY|SELL", "shares": 0, "price": 0.0, "reason": "1 sentence"}}
    ]
  }},
  "review": "2-3 sentences: summary of decisions taken and rationale"
}}"""

_PM_PORTFOLIO_SYSTEM = """You are a quantitative portfolio manager. Today is {today}.

You receive the current state of a fictional equity portfolio (paper trading).
Produce a concise review without making new buy/sell recommendations.

CALCULATIONS TO PERFORM:
- Estimated P&L per position: (entry_price * 1.05 - entry_price) * shares  (+5% proxy)
- Weight per ticker: (shares * entry_price) / (cash + total_positions_value) * 100
- Flag "overweight" if weight > 20%
- Sector exposure: aggregate tickers by sector if available, otherwise by ticker

OUTPUT — respond ONLY with valid JSON (no markdown):
{{
  "pm_mode": "portfolio",
  "trades": [],
  "portfolio_update": {{}},
  "review": "Full review: estimated total value, aggregate P&L, per-ticker breakdown, any overweight flags"
}}"""


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

def _call_claude(system: str, user: str, max_tokens: int = 2048) -> tuple[str, dict]:
    response = get_llm_client().messages.create(
        model=_MODEL_ID,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    usage = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
    return response.content[0].text, usage


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


def _build_full_mode_response(
    portfolio_state: dict,
    candidates: list,
    demo_trades: list,
    risk_assessment: list | None = None,
) -> dict:
    """Compute portfolio decisions from actual DB state + demo trade templates.

    BUY logic: execute if ticker weight < 15% of total capital (adds to existing positions too).
    SELL logic: trigger for existing positions whose quality degraded to 'bassa'/'dati_insufficienti'.
    """
    cash = portfolio_state.get("cash", 100_000.0)
    existing = {p["ticker"]: p for p in portfolio_state.get("positions", [])}
    candidate_tickers = {c["ticker"] for c in candidates} if candidates else set()
    today = date.today().isoformat()

    positions_value = sum(p["shares"] * p["entry_price"] for p in existing.values())
    total_capital = cash + positions_value

    # Quality map from risk assessment — drives SELL decisions
    quality_map = {r["ticker"]: r.get("quality", "alta") for r in (risk_assessment or [])}

    cash_after = cash
    new_positions = list(existing.values())
    buy_trades: list = []
    sell_trades: list = []
    all_trades: list = []

    # --- SELL pass: existing positions whose quality degraded ---
    for ticker, pos in existing.items():
        quality = quality_map.get(ticker, "alta")
        if quality in ("bassa", "dati_insufficienti"):
            proceeds = pos["shares"] * pos["entry_price"]
            cash_after += proceeds
            new_positions = [p for p in new_positions if p["ticker"] != ticker]
            sell_trade = {
                "ticker": ticker,
                "action": "SELL",
                "shares": pos["shares"],
                "price": pos["entry_price"],
                "reason": f"Quality degraded to '{_QUALITY_LABELS.get(quality, quality)}' in risk assessment — position liquidated",
            }
            sell_trades.append(sell_trade)
            all_trades.append(sell_trade)

    # Tickers sold in the SELL pass — excluded from BUY/HOLD pass to prevent same-cycle re-entry
    sold_tickers = {t["ticker"] for t in sell_trades}

    # --- BUY/HOLD pass: candidates from current analysis ---
    for trade in demo_trades:
        ticker = trade["ticker"]
        action = trade["action"]
        if candidate_tickers and ticker not in candidate_tickers:
            continue
        if ticker in sold_tickers:
            continue  # just liquidated — no re-entry in the same cycle
        if action == "BUY":
            price = trade["price"]
            if price <= 0:
                all_trades.append({**trade, "action": "HOLD", "shares": 0})
                continue

            current_pos_value = (
                existing[ticker]["shares"] * existing[ticker]["entry_price"]
                if ticker in existing else 0.0
            )
            current_weight = current_pos_value / total_capital * 100 if total_capital > 0 else 0.0

            if current_weight >= 15.0:
                all_trades.append({**trade, "action": "HOLD", "shares": 0,
                                   "reason": trade.get("reason", "") + f" (weight {current_weight:.1f}% ≥ 15%, limit reached)"})
            else:
                # Cap at weight limit: can invest at most up to 15% total capital
                room = (0.15 * total_capital) - current_pos_value
                budget = min(cash_after * 0.10, room)
                shares = int(budget / price)
                cost = shares * price
                if shares > 0 and cash_after >= cost:
                    cash_after -= cost
                    if ticker in existing:
                        new_positions = [
                            {**p, "shares": p["shares"] + shares} if p["ticker"] == ticker else p
                            for p in new_positions
                        ]
                        note = f" (added, weight {current_weight:.1f}%→{(current_pos_value+cost)/total_capital*100:.1f}%)"
                    else:
                        new_positions.append({
                            "ticker": ticker, "shares": shares,
                            "entry_price": price, "entry_date": today, "status": "open",
                        })
                        note = ""
                    executed = {**trade, "shares": shares, "reason": trade.get("reason", "") + note}
                    buy_trades.append(executed)
                    all_trades.append(executed)
                else:
                    all_trades.append({**trade, "action": "HOLD", "shares": 0,
                                       "reason": trade.get("reason", "") + " (insufficient cash)"})
        elif action == "SELL":
            if ticker in existing and ticker not in {t["ticker"] for t in sell_trades}:
                pos = existing[ticker]
                cash_after += pos["shares"] * trade["price"]
                new_positions = [p for p in new_positions if p["ticker"] != ticker]
                t = {**trade, "shares": pos["shares"]}
                sell_trades.append(t)
                all_trades.append(t)
            else:
                all_trades.append({**trade, "action": "HOLD", "shares": 0})
        else:
            all_trades.append(trade)

    total_value = cash_after + sum(p["shares"] * p["entry_price"] for p in new_positions)
    cash_pct = cash_after / total_value * 100 if total_value > 0 else 100.0
    ow = [
        p["ticker"] for p in new_positions
        if total_value > 0 and (p["shares"] * p["entry_price"]) / total_value * 100 > 20
    ]
    ow_note = f" Overweight: {', '.join(ow)}." if ow else " No overweight positions."

    if buy_trades or sell_trades:
        sell_note = (
            f"Liquidated {len(sell_trades)} position(s) "
            f"({', '.join(t['ticker'] for t in sell_trades)}) due to degraded quality. "
            if sell_trades else ""
        )
        pos_str = ", ".join(
            f"{p['ticker']} ({p['shares']} shares, ~{p['shares']*p['entry_price']/total_value*100:.1f}%)"
            for p in new_positions if total_value > 0
        )
        buy_note = (
            f"Opened {len(buy_trades)} long(s): {pos_str}. "
            if buy_trades else "No new purchases. "
        )
        review = (
            sell_note + buy_note
            + f"Remaining cash: {cash_after:,.2f} USD ({cash_pct:.1f}% of portfolio).{ow_note}"
        )
    else:
        holds = [t["ticker"] for t in all_trades if t["action"] == "HOLD"]
        hold_note = f" Positions held: {', '.join(holds)}." if holds else ""
        pos_str = (
            ", ".join(f"{p['ticker']} ({p['shares']} shares)" for p in new_positions)
            or "none"
        )
        review = (
            f"No new trades.{hold_note} "
            f"Current positions: {pos_str}. "
            f"Cash: {cash_after:,.2f} USD ({cash_pct:.1f}% of portfolio).{ow_note}"
        )

    n = len(buy_trades) + len(sell_trades)
    return {
        "pm_mode": "full",
        "trades": all_trades,
        "portfolio_update": {
            "cash_before": cash,
            "cash_after": cash_after,
            "positions_before": list(existing.values()),
            "positions_after": new_positions,
            "trades": buy_trades + sell_trades,
        },
        "review": review,
        "_n_executed": n,
    }


def _build_portfolio_review(portfolio_state: dict) -> dict:
    """Build a portfolio review dict from actual DB state (used in demo mode)."""
    cash = portfolio_state.get("cash", 100_000.0)
    currency = portfolio_state.get("currency", "USD")
    positions = portfolio_state.get("positions", [])
    positions_value = sum(p["shares"] * p["entry_price"] for p in positions)
    total_value = cash + positions_value
    cash_pct = cash / total_value * 100 if total_value > 0 else 100.0

    if positions:
        breakdown = ", ".join(
            f"{p['ticker']} {p['shares']}sh @ {p['entry_price']}" for p in positions
        )
        overweight = [
            p["ticker"]
            for p in positions
            if (p["shares"] * p["entry_price"]) / total_value * 100 > 20
        ]
        ow_note = f" Overweight: {', '.join(overweight)}." if overweight else " No overweight flags."
        review = (
            f"Portfolio review: {len(positions)} open position(s), "
            f"{cash:,.2f} {currency} cash ({cash_pct:.1f}% of portfolio). "
            f"Estimated total value: {total_value:,.2f} {currency}. "
            f"Positions: {breakdown}.{ow_note}"
        )
    else:
        review = (
            f"Portfolio review: 0 open positions, "
            f"{cash:,.2f} {currency} cash (100% undeployed). "
            "No active exposure. No overweight flags."
        )

    return {"pm_mode": "portfolio", "trades": [], "portfolio_update": {}, "review": review}


async def run_agent(task: A2ATask) -> A2ATaskResult:
    correlation_id = task.metadata.get("correlation_id")
    t0 = time.monotonic()

    if is_demo_mode():
        input_data_demo: dict[str, Any] = {}
        for part in task.message.parts:
            if hasattr(part, "data"):
                input_data_demo.update(part.data)
        pm_mode_demo = input_data_demo.get("pm_mode", "portfolio")
        if pm_mode_demo == "portfolio":
            portfolio_state_demo = input_data_demo.get("portfolio_state", {})
            result = A2ATaskResult.ok(
                task.id,
                "Portfolio review completed.",
                data=_build_portfolio_review(portfolio_state_demo),
            )
        else:
            demo = load_demo_response("portfolio-manager")
            portfolio_state_demo = input_data_demo.get("portfolio_state", {})
            candidates_demo = input_data_demo.get("candidates", [])
            risk_assessment_demo = input_data_demo.get("risk_assessment", [])
            data = _build_full_mode_response(
                portfolio_state_demo, candidates_demo, demo["data"]["trades"], risk_assessment_demo
            )
            n = data.pop("_n_executed", 0)
            message = (
                f"Portfolio full: {n} trade(s) executed."
                if n > 0 else "Portfolio full: no new trades."
            )
            result = A2ATaskResult.ok(task.id, message, data=data)
        write_audit_event(make_audit_event(
            agent="PortfolioManager", status="demo",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000), demo_mode=True,
        ))
        log.info("agent.demo", agent="PortfolioManager", correlation_id=correlation_id)
        return result

    input_data: dict[str, Any] = {}
    for part in task.message.parts:
        if hasattr(part, "data"):
            input_data.update(part.data)

    pm_mode = input_data.get("pm_mode", "portfolio")
    portfolio_state = input_data.get("portfolio_state", {"cash": 100000.0, "positions": []})
    today = date.today().isoformat()

    try:
        if pm_mode == "full":
            candidates = input_data.get("candidates", [])
            risk_assessment = input_data.get("risk_assessment", [])
            judgment = input_data.get("judgment", {})
            conservative = judgment.get("verdict") == "FAIL"
            conservative_note = (
                "\n\nNOTE — LLM JUDGE FAIL: serious grounding issues detected in the report. "
                "Operate in CONSERVATIVE MODE: do NOT execute any new BUY orders. "
                "Only review existing positions for HOLD or SELL. "
                f"Judge summary: {judgment.get('summary', '')}"
                if conservative else ""
            )
            user_prompt = (
                f"CURRENT PORTFOLIO STATE:\n{json.dumps(portfolio_state, ensure_ascii=False)}\n\n"
                f"EQUITY CANDIDATES:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
                f"RISK ASSESSMENT:\n{json.dumps(risk_assessment, ensure_ascii=False)}\n\n"
                "Produce portfolio decisions."
            )
            system = _PM_FULL_SYSTEM.format(today=today) + conservative_note
        else:
            user_prompt = (
                f"CURRENT PORTFOLIO STATE:\n{json.dumps(portfolio_state, ensure_ascii=False)}\n\n"
                "Produce the portfolio review."
            )
            system = _PM_PORTFOLIO_SYSTEM.format(today=today)

        raw_text, usage = _call_claude(system=system, user=user_prompt)
        json_clean = _extract_json(raw_text)

        try:
            pm_data = json.loads(json_clean)
        except json.JSONDecodeError:
            pm_data = {"pm_mode": pm_mode, "trades": [], "portfolio_update": {}, "review": raw_text}

        n_trades = len([t for t in pm_data.get("trades", []) if t.get("action") in ("BUY", "SELL")])
        summary = (
            f"Portfolio {pm_mode}: {n_trades} trade(s) executed."
            if pm_mode == "full"
            else "Portfolio review completed."
        )

        write_audit_event(make_audit_event(
            agent="PortfolioManager", status="completed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            prompt=system, input_text=user_prompt, output_text=raw_text,
            token_usage=usage,
            extra={"pm_mode": pm_mode, "trades_count": n_trades},
        ))
        log.info("agent.completed", agent="PortfolioManager", correlation_id=correlation_id,
                 pm_mode=pm_mode, trades=n_trades)

        return A2ATaskResult.ok(task.id, summary, data=pm_data)

    except Exception as e:
        error_msg = str(e)
        write_audit_event(make_audit_event(
            agent="PortfolioManager", status="failed",
            correlation_id=correlation_id, model_id=_MODEL_ID,
            duration_ms=int((time.monotonic() - t0) * 1000),
            extra={"error": error_msg},
        ))
        log.error("agent.failed", agent="PortfolioManager", correlation_id=correlation_id, error=error_msg)
        return A2ATaskResult.fail(task.id, error_msg)


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="PortfolioManager A2A Agent")
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
    return {"status": "ok", "agent": "PortfolioManager", "port": 8010}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8010, log_level="info")
