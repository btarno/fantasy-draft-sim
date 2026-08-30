"""
Manual draft tracker for draft night.

The ESPN mDraftDetail API lagged badly during the live draft (stuck at 0 picks
while the room was actively drafting), so this works from a hand-maintained
list of who is gone. Slower to feed, but it is the truth.

Edit TAKEN and MY_ROSTER, then run.
"""
import json
import injury

# Players confirmed off the board (any team).
TAKEN = [
    "CeeDee Lamb",
]

# What Brad actually owns.
MY_ROSTER = [
    "CeeDee Lamb",
]

LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "D/ST": 1}
FLEX = 1
FLEX_POS = ("RB", "WR", "TE")


def norm(s):
    return "".join(c for c in s.lower() if c.isalnum())


board = json.load(open("board.json"))
taken = {norm(t) for t in TAKEN}
mine = [p for p in board if norm(p["name"]) in {norm(m) for m in MY_ROSTER}]

have = {}
for p in mine:
    have[p["pos"]] = have.get(p["pos"], 0) + 1

print("MY ROSTER:")
for p in mine:
    print(f"  {p['pos']:<5}{p['name']}")

unmet = []
for pos, n in LINEUP.items():
    short = n - have.get(pos, 0)
    if short > 0:
        unmet.append(f"{pos}x{short}")
print(f"\nSTILL NEED: {' '.join(unmet)}  (+{FLEX} FLEX)")

avail = [p for p in board
         if norm(p["name"]) not in taken and p.get("proj")]

# Score by injury-adjusted value, weighted by positional need.
rows = []
for p in avail[:60]:
    if p["pos"] in ("K", "D/ST"):
        continue
    r = injury.risk_profile(p, weeks=17, trials=300, seed=42)
    pos = p["pos"]
    starters_left = max(0, LINEUP.get(pos, 0) - have.get(pos, 0))
    w = 1.0 + 0.35 * starters_left if starters_left else 0.80
    rows.append((r["mean"] * w, r["mean"], r["p10"],
                 r["p_misses_4plus"], p))
rows.sort(key=lambda x: -x[0])

print("\n--- BEST AVAILABLE (need-weighted) ---")
for score, mean, p10, pm, p in rows[:12]:
    inj = "" if p["injury"] == "ACTIVE" else " [" + p["injury"][:4] + "]"
    print(f"  {p['name']:<23}{p['pos']:<4}ADP{(p['adp'] or 999):6.1f}  "
          f"adj {mean:.0f}  floor {p10:.0f}  miss4 {pm*100:.0f}pct{inj}")
