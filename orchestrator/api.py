"""Orchestrator API — FastAPI, port 8000.

HTTP interface for the LangGraph orchestrator.
Accepts requests with explicit intent (mode) and routes them to the correct workflow.

Endpoints:
  GET  /            — streaming demo UI (static/index.html)
  POST /research    — start a workflow (analyze | portfolio | full)
  POST /research/stream — SSE streaming workflow
  GET  /portfolio   — current portfolio state from SQLite
  GET  /health      — aggregated health check for all 6 agents
"""
import json
import sys
from pathlib import Path
from typing import Literal

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator  # field_validator used in ResearchRequest

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.main import check_agents_health, run_pipeline, stream_pipeline
from shared.portfolio_db import load_portfolio_state

log = structlog.get_logger()

_STATIC_DIR = Path(__file__).parent.parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Equity Researcher A2A — Orchestrator API",
    description="Director API: 3 selectable workflows (analyze | portfolio | full)",
    version="3.0.0",
)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ------------------------------------------------------------------ #
# Request / Response models                                            #
# ------------------------------------------------------------------ #

class ResearchRequest(BaseModel):
    tickers: list[str] = []
    mode: Literal["analyze", "portfolio", "full"] = "full"
    prompt: str | None = None

    @field_validator("tickers")
    @classmethod
    def normalise_tickers(cls, v):
        return [t.upper().strip() for t in v]


# ------------------------------------------------------------------ #
# Endpoints                                                            #
# ------------------------------------------------------------------ #

@app.get("/")
async def index():
    """Streaming demo UI."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.post("/research/stream")
async def research_stream(req: ResearchRequest):
    """SSE endpoint — emette eventi JSON per ogni nodo della pipeline."""
    async def event_gen():
        try:
            async for event in stream_pipeline(req.tickers, mode=req.mode, prompt=req.prompt):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            log.error("stream.error", error=str(e))
            yield f"data: {json.dumps({'type': 'error', 'message': 'Internal pipeline error'})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/research")
async def research(req: ResearchRequest):
    """Start a workflow and return the full result.

    mode=analyze   → market analysis only (report in Italian)
    mode=portfolio → portfolio review only (no LLM analysis needed)
    mode=full      → analysis → portfolio manager (report + trade decisions)
    """
    try:
        result = await run_pipeline(req.tickers, mode=req.mode, interactive=False, prompt=req.prompt)
        return JSONResponse(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/portfolio")
async def portfolio():
    """Return the current portfolio state from SQLite."""
    try:
        state = await load_portfolio_state()
        return JSONResponse(state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/report/{filename}")
async def get_report(filename: str):
    """Serve a generated HTML report from the output/ directory."""
    if not filename.endswith(".html") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    report_file = Path(__file__).parent.parent / "output" / filename
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(report_file, media_type="text/html")


@app.get("/health")
async def health():
    """Aggregated health check for all 6 agents."""
    status = await check_agents_health()
    http_status = 200 if status["status"] == "ok" else 207
    return JSONResponse(status, status_code=http_status)


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
