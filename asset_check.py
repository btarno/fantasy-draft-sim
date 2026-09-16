"""
Do I actually have tradeable assets, or am I offering surplus nobody wants?

The uncomfortable test: for each of my spare WRs, would he crack the STARTING
lineup of each other team? A player who only upgrades my bench is worthless as
trade currency no matter how he projects in isolation.
"""
import json
from collections import defaultdict

import requests

import espn_client as api
import injury

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
me = cfg["my_team_id"]
cache = {}


def val(nm):
    bp = by_name.get(norm(nm))
    if not bp or not bp.get("proj"):
        return None
    k = norm(nm)
    if k not in cache:
        cache[k] = injury.risk_profile(bp, weeks=17, trials=250, seed=42)
    return cache[k]["mean"]


teams = {}
for t in d.get("teams", []):
    grp = defaultdict(list)
    for e in ((t.get("roster") or {}).get("entries") or []):
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        nm = pl.get("fullName")
        pos = POS.get(pl.get("defaultPositionId") or 0, "?")
        v = val(nm)
        if v is not None:
            grp[pos].append((v, nm))
    for p in grp:
        grp[p].sort(reverse=True)
    teams[t["id"]] = {"name": t.get("name") or f"T{t['id']}", "grp": grp}

# My spare pieces: anyone not in my best starting lineup.
mine = teams[me]["grp"]
MY_SPARES = ["Jordan Addison", "Romeo Doubs", "Xavier Worthy",
             "Denzel Boston", "Tre Tucker", "Mark Andrews",
             "TreVeyon Henderson"]

print("=== WOULD MY SPARE PIECES START ELSEWHERE? ===")
print("  (a WR must beat their WR2 to matter; TE must beat their TE1)")
print()
for nm in MY_SPARES:
    v = val(nm)
    if v is None:
        continue
    bp = by_name.get(norm(nm))
    pos = bp["pos"]
    starts_for = []
    for tid, info in teams.items():
        if tid == me:
            continue
        theirs = info["grp"].get(pos, [])
        # WR2 is index 1; TE1 is index 0; RB2 is index 1.
        idx = 0 if pos == "TE" else 1
        bar = theirs[idx][0] if len(theirs) > idx else 0
        if v > bar:
            starts_for.append((info["name"], bar))
    tag = f"{len(starts_for)}/11 teams"
    print(f"  {nm:<22}{pos:<4}adj {v:>4.0f}  would start for {tag}")
    for tn, bar in starts_for[:3]:
        print(f"       beats {tn[:26]}'s {pos}{idx+1} ({bar:.0f})")
