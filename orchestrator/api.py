"""Orchestrator API — FastAPI, porta 8000.

Interfaccia HTTP per l'orchestratore LangGraph.
Riceve richieste con intent esplicito (mode) e le instrada ai workflow giusti.

Endpoints:
  POST /research  — avvia un workflow (analyze | portfolio | full)
  GET  /portfolio — stato corrente del portafoglio SQLite
  GET  /health    — health aggregato di tutti e 6 gli agenti
"""
import sys
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator  # field_validator used in ResearchRequest

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.main import check_agents_health, run_pipeline
from shared.portfolio_db import load_portfolio_state

app = FastAPI(
    title="Equity Researcher A2A — Orchestrator API",
    description="Director API: 3 workflow selezionabili (analyze | portfolio | full)",
    version="3.0.0",
)


# ------------------------------------------------------------------ #
# Request / Response models                                            #
# ------------------------------------------------------------------ #

class ResearchRequest(BaseModel):
    tickers: list[str] = []
    mode: Literal["analyze", "portfolio", "full"] = "full"

    @field_validator("tickers")
    @classmethod
    def normalise_tickers(cls, v):
        return [t.upper().strip() for t in v]


# ------------------------------------------------------------------ #
# Endpoints                                                            #
# ------------------------------------------------------------------ #

@app.post("/research")
async def research(req: ResearchRequest):
    """Avvia un workflow e restituisce il risultato completo.

    mode=analyze   → market analysis only (report in Italian)
    mode=portfolio → portfolio review only (no LLM analysis needed)
    mode=full      → analysis → portfolio manager (report + trade decisions)
    """
    try:
        result = await run_pipeline(req.tickers, mode=req.mode, interactive=False)
        return JSONResponse(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/portfolio")
async def portfolio():
    """Restituisce lo stato corrente del portafoglio da SQLite."""
    try:
        state = await load_portfolio_state()
        return JSONResponse(state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/health")
async def health():
    """Health check aggregato di tutti e 6 gli agenti."""
    status = await check_agents_health()
    http_status = 200 if status["status"] == "ok" else 207
    return JSONResponse(status, status_code=http_status)


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
