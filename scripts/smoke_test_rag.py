"""Smoke test: RAG retriever + LLM Judge + orchestrator graph build."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from orchestrator.main import _build_graph_builder, PipelineState
from shared.rag_retriever import retrieve_context
from shared.llm_judge import run_judge, JudgmentResult

errors = []

# 1 — grafo si compila
try:
    graph = _build_graph_builder().compile()
    nodes = list(graph.nodes.keys())
    print(f"OK  grafo compilato — nodi: {nodes}")
except Exception as e:
    errors.append(f"FAIL grafo: {e}")

# 2 — rag_retriever e llm_judge presenti nel grafo
for expected in ("rag_retriever", "llm_judge"):
    if expected not in nodes:
        errors.append(f"FAIL {expected} mancante nel grafo")
    else:
        print(f"OK  {expected} presente nel grafo")

# 3 — rag_context e judgment in PipelineState
for field in ("rag_context", "judgment"):
    if field not in PipelineState.__annotations__:
        errors.append(f"FAIL {field} mancante in PipelineState")
    else:
        print(f"OK  PipelineState.{field} presente")
print(f"    totale campi PipelineState: {len(PipelineState.__annotations__)}")

# 4 — RAG retriever query
try:
    ctx = retrieve_context(["MSFT", "NVDA", "UCG.MI"])
    chunks = ctx.count("[Source:")
    print(f"OK  RAG retriever — {chunks} chunk(s) per query MSFT/NVDA/UCG.MI")
    if chunks > 0:
        first_source = ctx.split("\n")[0]
        print(f"    primo chunk: {first_source}")
except Exception as e:
    errors.append(f"FAIL RAG retriever: {e}")

# 5 — query senza ticker (fallback keyword)
try:
    ctx2 = retrieve_context([])
    chunks2 = ctx2.count("[Source:")
    print(f"OK  RAG retriever fallback (no tickers) — {chunks2} chunk(s)")
except Exception as e:
    errors.append(f"FAIL RAG fallback: {e}")

# 6 — LLM Judge in DEMO_MODE
import os
os.environ["DEMO_MODE"] = "true"
try:
    result: JudgmentResult = asyncio.run(run_judge(
        client=None,
        executive_summary="Test summary",
        report_dict={"candidati": []},
        news=[],
        fundamentals=[],
        rag_context="",
        correlation_id="smoke-test",
    ))
    assert result.verdict == "PASS", f"Atteso PASS, ricevuto {result.verdict}"
    assert result.demo is True
    print(f"OK  LLM Judge demo — verdict: {result.verdict}, grounding_score: {result.grounding_score}")
except Exception as e:
    errors.append(f"FAIL LLM Judge demo: {e}")

print()
if errors:
    print("=== ERRORI ===")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("Tutti i test superati.")
