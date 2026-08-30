import json
import injury

b = json.load(open("board.json"))
pool = [p for p in b if p.get("proj") and p["pos"] in ("RB", "WR", "TE")][:26]
rows = []
for p in pool:
    r = injury.risk_profile(p, weeks=17, trials=400, seed=42)
    rows.append((r["mean"], r["p10"], r["p_misses_4plus"], p))
rows.sort(key=lambda x: -x[0])

print("=== QUEUE ORDER for pick #8 ===")
print()
for i, (mean, p10, pm, p) in enumerate(rows[:15], 1):
    adp = p["adp"] or 999
    inj = "" if p["injury"] == "ACTIVE" else " [" + p["injury"][:4] + "]"
    note = "gone by 8" if adp < 6.5 else ""
    print(f"  {i:<3}{p['name']:<23}{p['pos']:<4}ADP{adp:6.1f}  "
          f"adj {mean:.0f}  floor {p10:.0f}  miss4 {pm*100:.0f}pct  {note}{inj}")
