import sqlite3
from pathlib import Path

db = Path(__file__).parent.parent / "output" / "checkpoints.db"
if not db.exists():
    print("checkpoints.db: non trovato in output/")
else:
    print(f"checkpoints.db  size: {db.stat().st_size:,} bytes")
    con = sqlite3.connect(db)
    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"Tabelle: {[t[0] for t in tables]}\n")
    for (name,) in tables:
        count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({name})").fetchall()]
        print(f"  {name}")
        print(f"    colonne : {cols}")
        print(f"    righe   : {count}")
        if count and count < 5:
            rows = con.execute(f"SELECT * FROM {name} LIMIT 3").fetchall()
            for r in rows:
                # truncate long blobs for readability
                display = tuple(
                    (v[:120] + "...") if isinstance(v, (str, bytes)) and len(str(v)) > 120 else v
                    for v in r
                )
                print(f"    sample  : {display}")
        print()
    con.close()
