import json
import injury
from collections import defaultdict

ROSTER = [
    ("Jalen Hurts", "QB", 10),
    ("Travis Etienne Jr.", "RB", 8),
    ("TreVeyon Henderson", "RB", 11),
    ("CeeDee Lamb", "WR", 14),
    ("Rashee Rice", "WR", 5),
    ("Colston Loveland", "TE", 10),
    ("Rico Dowdle", "RB", 9),
    ("J.K. Dobbins", "RB", 10),
    ("Xavier Worthy", "WR", 5),
    ("Jordan Addison", "WR", 6),
    ("Mark Andrews", "TE", 13),
    ("Romeo Doubs", "WR", 11),
    ("Denzel Boston", "WR", 11),
    ("Tre Tucker", "WR", 13),
]

board = json.load(open("board.json"))


def norm(s):
    return "".join(c for c in s.lower() if c.isalnum())


by_name = {norm(p["name"]): p for p in board}

print("=== ROSTER, injury-adjusted ===")
print()
total_adj = 0.0
total_floor = 0.0
found = []
for name, pos, bye in ROSTER:
    p = by_name.get(norm(name))
    if not p:
        print(f"  {name:<22}{pos:<4}bye {bye:<3} (not on preseason board)")
        continue
    r = injury.risk_profile(p, weeks=17, trials=600, seed=42)
    flag = "" if p["injury"] == "ACTIVE" else " Q"
    found.append((r["mean"], r["p10"], name, pos, bye, flag,
                  r["p_misses_4plus"]))
    print(f"  {name:<22}{pos:<4}bye {bye:<3} adj {r['mean']:>4.0f} "
          f"floor {r['p10']:>4.0f}  miss4 {r['p_misses_4plus']*100:>3.0f}%{flag}")

print()
byes = defaultdict(list)
for name, pos, bye in ROSTER:
    byes[bye].append(f"{name.split()[-1]}({pos})")
print("=== BYE WEEK EXPOSURE ===")
for wk in sorted(byes):
    n = len(byes[wk])
    warn = "  <-- HEAVY" if n >= 3 else ""
    print(f"  week {wk:<3} {n} players: {', '.join(byes[wk])}{warn}")

print()
risky = [f for f in found if f[6] >= 0.45]
print("=== HIGHEST INJURY RISK ===")
for mean, p10, name, pos, bye, flag, pm in sorted(risky, key=lambda x: -x[6]):
    print(f"  {name:<22}{pos:<4}miss4 {pm*100:.0f}%  floor {p10:.0f}")
