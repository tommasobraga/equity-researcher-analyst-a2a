"""Bonifica portfolio.db — rimuove le posizioni 'closed' fantasma.

Causa del problema: save_portfolio_state chiudeva TUTTE le posizioni aperte
e le re-inseriva ad ogni run, anche quelle in HOLD. Questo generava righe
'closed' duplicate che non corrispondevano a vendite reali.

Stato attuale (rilevato il 2026-06-18):
  Posizioni chiuse reali:  MSFT 20 @ 449.87 (venduta nel run 14:10, id=5)
  Posizioni chiuse false:  tutti gli altri id closed (1,2,3,4,6,7,8)
  Posizioni aperte (ok):   NVDA 12 (id=9), UCG.MI 344 (id=10),
                           ASML 8 (id=11), ISP.MI 1627 (id=12)

La tabella trades e il saldo cash non vengono modificati.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "output" / "portfolio.db"

# id dell'unica posizione closed legittima: MSFT (la riga attiva al momento della vendita)
LEGIT_CLOSED_ID = 5


def _snapshot(con: sqlite3.Connection, label: str) -> None:
    print(f"\n{'='*55}")
    print(f"  {label}")
    print(f"{'='*55}")
    rows = con.execute("SELECT id, ticker, shares, entry_price, status FROM positions ORDER BY id").fetchall()
    for r in rows:
        print(f"  id={r[0]:>2}  {r[1]:<10}  {r[2]:>5} shares @ {r[3]:>8.2f}  [{r[4]}]")
    cash = con.execute("SELECT cash FROM portfolio WHERE id=1").fetchone()[0]
    print(f"\n  Cash: {cash:>12,.2f} USD")


def main() -> None:
    con = sqlite3.connect(DB_PATH)

    _snapshot(con, "STATO PRIMA DELLA BONIFICA")

    phantom_ids = con.execute(
        "SELECT id FROM positions WHERE status = 'closed' AND id != ?",
        (LEGIT_CLOSED_ID,),
    ).fetchall()
    phantom_ids = [r[0] for r in phantom_ids]

    if not phantom_ids:
        print("\nNessuna riga fantasma trovata — DB gia' coerente.")
        con.close()
        return

    print(f"\nRighe fantasma da eliminare: id = {phantom_ids}")
    confirm = input("Procedere con la bonifica? [y/N] ").strip().lower()
    if confirm != "y":
        print("Operazione annullata.")
        con.close()
        return

    placeholders = ",".join("?" * len(phantom_ids))
    con.execute(f"DELETE FROM positions WHERE id IN ({placeholders})", phantom_ids)
    con.commit()

    _snapshot(con, "STATO DOPO LA BONIFICA")

    # Verifica coerenza finale
    cash = con.execute("SELECT cash FROM portfolio WHERE id=1").fetchone()[0]
    open_pos = con.execute(
        "SELECT shares, entry_price FROM positions WHERE status='open'"
    ).fetchall()
    invested = sum(r[0] * r[1] for r in open_pos)
    total = cash + invested
    print(f"\n  Cash + Invested = {total:,.2f} USD  (seed: 100,000.00 USD)")
    print(f"  Delta vs seed   = {total - 100_000:+,.2f} USD")
    print("\nBonifica completata.")
    con.close()


if __name__ == "__main__":
    main()
