import sqlite3
from pathlib import Path

db_path = Path(__file__).parent.parent / "output" / "portfolio.db"
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row

print("=== portfolio ===")
for r in con.execute("SELECT * FROM portfolio"):
    print(dict(r))

print("\n=== positions (tutte) ===")
rows = con.execute("SELECT * FROM positions ORDER BY id").fetchall()
if rows:
    for r in rows:
        print(dict(r))
else:
    print("(vuota)")

print("\n=== trades (ultimi 20) ===")
rows = con.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 20").fetchall()
if rows:
    for r in rows:
        print({k: (v.encode("ascii", "replace").decode() if isinstance(v, str) else v) for k, v in dict(r).items()})
else:
    print("(vuota)")

print("\n=== CHECK COERENZA ===")
port = dict(con.execute("SELECT * FROM portfolio WHERE id=1").fetchone())
cash = port["cash"]
open_pos = con.execute(
    "SELECT ticker, shares, entry_price FROM positions WHERE status='open'"
).fetchall()
invested = sum(r["shares"] * r["entry_price"] for r in open_pos)
total = cash + invested
print(f"Cash:              {cash:>12,.2f} USD")
print(f"Invested (cost):   {invested:>12,.2f} USD")
print(f"Totale (cost):     {total:>12,.2f} USD")
print(f"Seed iniziale:     {100_000:>12,.2f} USD")
print(f"Delta vs seed:     {total - 100_000:>+12,.2f} USD")

tickers_open = [r["ticker"] for r in open_pos]
dupes = [t for t in set(tickers_open) if tickers_open.count(t) > 1]
if dupes:
    print(f"\nATTENZIONE — ticker duplicati in positions open: {dupes}")
else:
    print("\nNessun ticker duplicato in positions open.")

trade_tickers = set(r["ticker"] for r in con.execute("SELECT DISTINCT ticker FROM trades"))
pos_tickers = set(r["ticker"] for r in con.execute("SELECT DISTINCT ticker FROM positions"))
orphan = trade_tickers - pos_tickers
if orphan:
    print(f"Trade senza posizione corrispondente: {orphan}")
else:
    print("Tutti i trade hanno una posizione corrispondente.")

con.close()
