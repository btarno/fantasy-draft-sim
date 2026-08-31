"""
Find realistic trade partners: teams whose RB surplus can cover my RB hole,
weighted by how badly they need what I have (WR depth).

Predestined to Win declined and then went RB7 -> RB8, so they are hoarding,
not shopping. Rank partners by MOTIVATION, not just roster shape.
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
MY_ID = cfg["my_team_id"]
cache = {}


def prof(p):
    k = norm(p["name"])
    if k not in cache:
        cache[k] = injury.risk_profile(p, weeks=17, trials=250, seed=42)
    return cache[k]


# My RB2 baseline -- anything better than this is an upgrade.
MY_RB2 = 171.0

teams = []
for t in d.get("teams", []):
    entries = ((t.get("roster") or {}).get("entries") or [])
    counts = defaultdict(int)
    rbs, wrs = [], []
    for e in entries:
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        nm = pl.get("fullName")
        pos = POS.get(pl.get("defaultPositionId") or 0, "?")
        counts[pos] += 1
        bp = by_name.get(norm(nm))
        if not bp or not bp.get("proj"):
            continue
        if pos == "RB":
            rbs.append((prof(bp)["mean"], nm, bp["injury"]))
        elif pos == "WR":
            wrs.append((prof(bp)["mean"], nm))
    rbs.sort(reverse=True)
    wrs.sort(reverse=True)
    teams.append({
        "id": t["id"],
        "name": t.get("name") or f"Team {t['id']}",
        "rank": t.get("currentProjectedRank") or 99,
        "counts": counts,
        "rbs": rbs,
        "wrs": wrs,
    })

me = next(t for t in teams if t["id"] == MY_ID)

print("=== TRADE TARGETS: their spare RB that upgrades my RB2 (171) ===")
print()
rows = []
for t in teams:
    if t["id"] == MY_ID:
        continue
    # Their RBs beyond the 2 they must start + 1 flex = tradeable surplus.
    surplus = t["rbs"][2:]
    for mean, nm, inj in surplus:
        if mean <= MY_RB2 + 10:
            continue
        # Motivation: worse projected rank + thin WR room = more likely to deal.
        wr_need = max(0, 6 - t["counts"]["WR"])
        motivation = (t["rank"] / 12.0) + wr_need * 0.5 + \
                     (len(t["rbs"]) - 5) * 0.4
        rows.append((motivation, mean, nm, t["name"], t["rank"],
                     t["counts"]["RB"], t["counts"]["WR"], inj))

rows.sort(key=lambda x: (-x[0], -x[1]))
for mot, mean, nm, team, rank, rb, wr, inj in rows[:12]:
    flag = "" if inj == "ACTIVE" else " Q"
    print(f"  {nm:<21}adj {mean:>4.0f}{flag:<2} | {team[:24]:<25} "
          f"rank{rank:>3} RB{rb} WR{wr}  motivation {mot:.2f}")
