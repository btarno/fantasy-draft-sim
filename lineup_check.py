"""
Check the CURRENT lineup: is anyone slotted wrong, and did anything change
since the draft (injuries, waiver adds, trades)?

Reads live rosters rather than the draft-night list, because the roster has
almost certainly moved.
"""
import json
from collections import defaultdict

import requests

import espn_client as api
import injury

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
        20: "BENCH", 21: "IR", 23: "FLEX"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base, params={"view": ["mRoster", "mTeam"]}, cookies=ck,
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=45).json()

board = json.load(open("board.json"))


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


by_name = {norm(p["name"]): p for p in board}
me = next(t for t in d["teams"] if t["id"] == cfg["my_team_id"])
entries = ((me.get("roster") or {}).get("entries") or [])

cache = {}


def prof(p):
    k = norm(p["name"])
    if k not in cache:
        cache[k] = injury.risk_profile(p, weeks=17, trials=400, seed=42)
    return cache[k]


roster = []
for e in entries:
    pl = (e.get("playerPoolEntry") or {}).get("player") or {}
    nm = pl.get("fullName")
    slot = SLOT.get(e.get("lineupSlotId"), "?")
    pos = POS.get(pl.get("defaultPositionId") or 0, "?")
    live_status = (pl.get("injuryStatus") or "ACTIVE").upper()
    bp = by_name.get(norm(nm))
    adj = fl = None
    if bp and bp.get("proj"):
        r = prof(bp)
        adj, fl = r["mean"], r["p10"]
    roster.append({"name": nm, "pos": pos, "slot": slot,
                   "status": live_status, "adj": adj, "floor": fl})

print(f"=== {me.get('name')} — LIVE ROSTER ({len(roster)}) ===")
print()
starters = [r for r in roster if r["slot"] not in ("BENCH", "IR", "?")]
bench = [r for r in roster if r["slot"] in ("BENCH", "IR")]

order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "K": 5, "D/ST": 6}
starters.sort(key=lambda r: order.get(r["slot"], 9))

print("  STARTERS")
tot = 0.0
for r in starters:
    flag = "" if r["status"] == "ACTIVE" else f"  [{r['status'][:4]}]"
    a = f"{r['adj']:.0f}" if r["adj"] else "-"
    f_ = f"{r['floor']:.0f}" if r["floor"] else "-"
    tot += r["adj"] or 0
    print(f"    {r['slot']:<6}{r['name']:<22}{r['pos']:<5}adj {a:>4} "
          f"floor {f_:>4}{flag}")

print(f"\n  starter total (adjusted): {tot:.0f}")

print("\n  BENCH")
for r in sorted(bench, key=lambda x: -(x["adj"] or 0)):
    flag = "" if r["status"] == "ACTIVE" else f"  [{r['status'][:4]}]"
    a = f"{r['adj']:.0f}" if r["adj"] else "-"
    print(f"    {r['slot']:<6}{r['name']:<22}{r['pos']:<5}adj {a:>4}{flag}")

# --- would a swap improve the lineup?
print("\n=== LINEUP CHECK ===")
issues = []
for s in starters:
    if s["slot"] in ("K", "D/ST") or s["adj"] is None:
        continue
    eligible = [b for b in bench
                if b["adj"] and b["status"] == "ACTIVE"
                and (b["pos"] == s["pos"]
                     or (s["slot"] == "FLEX" and b["pos"] in ("RB", "WR", "TE")))]
    for b in eligible:
        if b["adj"] > (s["adj"] or 0) + 5:
            issues.append(f"  START {b['name']} ({b['adj']:.0f}) over "
                          f"{s['name']} ({s['adj']:.0f}) at {s['slot']}")
if issues:
    print("\n".join(sorted(set(issues))))
else:
    print("  Lineup is optimal by adjusted value — no swaps available.")

hurt = [r for r in roster if r["status"] not in ("ACTIVE",)]
if hurt:
    print("\n=== INJURY FLAGS ===")
    for r in hurt:
        where = "STARTING" if r["slot"] not in ("BENCH", "IR") else "bench"
        print(f"  {r['name']:<22}{r['pos']:<5}{r['status']:<16}{where}")
