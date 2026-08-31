"""
Score every team's roster with the injury model and compare to ESPN's rank.

The honest test: if my model ALSO ranks Brad near the bottom, the draft went
badly and I should say so. If it disagrees, I need to explain exactly why
rather than hand-waving that ESPN is wrong.
"""
import json
from collections import defaultdict

import requests

import espn_client as api
import injury

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base, params={"view": ["mRoster", "mTeam"]}, cookies=ck,
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=45).json()

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
board = json.load(open("board.json"))


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


by_name = {norm(p["name"]): p for p in board}
LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_POS = ("RB", "WR", "TE")

cache = {}


def prof(p):
    k = norm(p["name"])
    if k not in cache:
        cache[k] = injury.risk_profile(p, weeks=17, trials=300, seed=42)
    return cache[k]


def score(players):
    """Best legal starting lineup: adjusted mean, floor, and raw projection."""
    ranked = defaultdict(list)
    raw_ranked = defaultdict(list)
    for p in players:
        r = prof(p)
        ranked[p["pos"]].append((r["mean"], r["p10"], p["name"]))
        raw_ranked[p["pos"]].append((p.get("proj") or 0, p["name"]))
    for k in ranked:
        ranked[k].sort(reverse=True)
        raw_ranked[k].sort(reverse=True)

    tot = fl = 0.0
    raw = 0.0
    used = set()
    for pos, n in LINEUP.items():
        for mean, p10, nm in ranked.get(pos, [])[:n]:
            tot += mean
            fl += p10
            used.add(nm)
        for rp, nm in raw_ranked.get(pos, [])[:n]:
            raw += rp
    rest = [x for pos in FLEX_POS for x in ranked.get(pos, [])
            if x[2] not in used]
    rest.sort(reverse=True)
    if rest:
        tot += rest[0][0]
        fl += rest[0][1]
    raw_rest = [x for pos in FLEX_POS for x in raw_ranked.get(pos, [])
                if x[1] not in used]
    raw_rest.sort(reverse=True)
    if raw_rest:
        raw += raw_rest[0][0]
    return tot, fl, raw


rows = []
for t in d.get("teams", []):
    entries = ((t.get("roster") or {}).get("entries") or [])
    players = []
    for e in entries:
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        nm = pl.get("fullName")
        bp = by_name.get(norm(nm))
        if bp and bp.get("proj"):
            players.append(bp)
    tot, fl, raw = score(players)
    rows.append((tot, fl, raw, t.get("name") or f"Team {t['id']}",
                 t["id"], t.get("currentProjectedRank")))

rows.sort(key=lambda x: -x[0])
print("=== MY MODEL vs ESPN RANK ===")
print(f"  {'#':<3}{'team':<28}{'adj':>7}{'floor':>7}{'raw':>7}  ESPN")
for i, (tot, fl, raw, name, tid, espn) in enumerate(rows, 1):
    me = "  <-- YOU" if tid == cfg["my_team_id"] else ""
    print(f"  {i:<3}{name[:27]:<28}{tot:>7.0f}{fl:>7.0f}{raw:>7.0f}"
          f"{espn:>6}{me}")

# Also rank by raw projection to see what ESPN is probably doing.
print()
raw_sorted = sorted(rows, key=lambda x: -x[2])
print("=== RANKED BY RAW PROJECTION (ignores injury) ===")
for i, (tot, fl, raw, name, tid, espn) in enumerate(raw_sorted, 1):
    me = "  <-- YOU" if tid == cfg["my_team_id"] else ""
    print(f"  {i:<3}{name[:27]:<28}{raw:>7.0f}   ESPN {espn}{me}")
