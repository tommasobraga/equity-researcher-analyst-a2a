import sqlite3, json
conn = sqlite3.connect("output/memory.db")
rows = conn.execute(
    "SELECT agent, created_at, data FROM ticker_analyses WHERE ticker=? ORDER BY created_at DESC",
    ("MSFT",)
).fetchall()
for agent, ts, data in rows:
    d = json.loads(data)
    thesis = d.get("thesis", d.get("scenarios", {}).get("base", ""))[:100]
    print(f"{ts[:19]}  {agent:<20}  {thesis}")
