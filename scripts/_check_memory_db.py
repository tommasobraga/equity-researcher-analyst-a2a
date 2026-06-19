"""Verifica coerenza di output/memory.db."""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "output" / "memory.db"

if not DB_PATH.exists():
    print("memory.db non trovato in output/ — DB non ancora creato.")
    raise SystemExit(0)

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

print(f"memory.db  size: {DB_PATH.stat().st_size:,} bytes\n")

# ── run_summaries ─────────────────────────────────────────────────────────────
print("=" * 60)
print("TABLE: run_summaries")
print("=" * 60)
rows = con.execute(
    "SELECT id, run_id, mode, tickers, candidates, trades, created_at "
    "FROM run_summaries ORDER BY created_at"
).fetchall()
print(f"Righe totali: {len(rows)}\n")
for r in rows:
    tickers   = json.loads(r["tickers"])
    candidates = json.loads(r["candidates"])
    trades    = json.loads(r["trades"]) if r["trades"] else []
    print(f"  id={r['id']}  {r['created_at'][:19]}  mode={r['mode']}")
    print(f"    tickers    : {tickers}")
    print(f"    candidates : {candidates}")
    if trades:
        trade_list = [f"{t.get('action')} {t.get('ticker')}" for t in trades]
        print(f"    trades     : {trade_list}")

# ── ticker_analyses ───────────────────────────────────────────────────────────
print(f"\n{'=' * 60}")
print("TABLE: ticker_analyses")
print("=" * 60)
total = con.execute("SELECT COUNT(*) FROM ticker_analyses").fetchone()[0]
print(f"Righe totali: {total}\n")

# distribuzione per (ticker, agent)
dist = con.execute(
    "SELECT ticker, agent, COUNT(*) as cnt FROM ticker_analyses GROUP BY ticker, agent ORDER BY ticker, agent"
).fetchall()
print("Distribuzione per (ticker, agent):")
for r in dist:
    print(f"  {r['ticker']:<12}  {r['agent']:<25}  {r['cnt']} record")

# verifica JSON valido e campi attesi per ogni agente
print("\nVerifica integrità JSON e campi chiave:")
errors = []
rows_ta = con.execute(
    "SELECT id, run_id, ticker, agent, data FROM ticker_analyses"
).fetchall()
for r in rows_ta:
    try:
        d = json.loads(r["data"])
    except json.JSONDecodeError as e:
        errors.append(f"  id={r['id']} {r['ticker']}/{r['agent']}: JSON invalido — {e}")
        continue
    if r["agent"] == "fundamental_analyst":
        for field in ("thesis", "catalyst"):
            if field not in d:
                errors.append(f"  id={r['id']} {r['ticker']}/fundamental_analyst: campo '{field}' mancante")
    if r["agent"] == "risk_assessor":
        for field in ("scoring", "quality", "scenarios"):
            if field not in d:
                errors.append(f"  id={r['id']} {r['ticker']}/risk_assessor: campo '{field}' mancante")

if errors:
    print("ATTENZIONE — errori trovati:")
    for e in errors:
        print(e)
else:
    print("  Tutti i record hanno JSON valido e campi attesi. OK")

# ── verifica cross-tabella: ogni run_id in run_summaries ha ticker_analyses? ──
print(f"\n{'=' * 60}")
print("CHECK CROSS-TABELLA")
print("=" * 60)
rs_run_ids = {r["run_id"] for r in con.execute("SELECT run_id FROM run_summaries").fetchall()}
ta_run_ids = {r["run_id"] for r in con.execute("SELECT DISTINCT run_id FROM ticker_analyses").fetchall()}

orphan_summaries = rs_run_ids - ta_run_ids
orphan_analyses  = ta_run_ids - rs_run_ids

if orphan_summaries:
    print(f"run_summaries senza ticker_analyses: {orphan_summaries}")
else:
    print("Ogni run_summary ha almeno un ticker_analysis corrispondente. OK")

if orphan_analyses:
    print(f"ticker_analyses senza run_summary: {orphan_analyses}")
else:
    print("Ogni ticker_analysis ha un run_summary corrispondente. OK")

con.close()
