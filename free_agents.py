"""
Full free-agent sweep -- not just the preseason board.

waiver_scan.py only checks players on board.json (the top 300 by preseason
ADP). A week-1 breakout who was undrafted never appears there at all, which is
exactly the kind of pickup that matters in-season. Query ESPN's player pool
directly and sort by WEEK 1 ACTUAL points.
"""
import json

import requests

import espn_client as api

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")

# Who is already on a roster?
r = requests.get(base, params={"view": "mRoster"}, cookies=ck,
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=45).json()
rostered = set()
for t in r.get("teams", []):
    for e in ((t.get("roster") or {}).get("entries") or []):
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        if pl.get("id"):
            rostered.add(pl["id"])

print(f"rostered players in league: {len(rostered)}")

# Pull a big slice of the player pool, sorted by percent-owned.
filt = {
    "players": {
        "limit": 400,
        "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
    }
}
p = requests.get(
    f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
    f"/leagues/{cfg['league_id']}",
    params={"view": "kona_player_info"},
    cookies=ck,
    headers={"User-Agent": "Mozilla/5.0",
             "x-fantasy-filter": json.dumps(filt)},
    timeout=60).json()

pool = (p.get("players") or [])
print(f"player pool returned: {len(pool)}")

rows = []
for entry in pool:
    pl = entry.get("player") or {}
    pid = pl.get("id")
    if pid in rostered:
        continue
    pos = POS.get(pl.get("defaultPositionId") or 0, "?")
    if pos not in ("RB", "WR", "TE"):
        continue
    wk1 = 0.0
    season_proj = 0.0
    for s in (pl.get("stats") or []):
        if s.get("scoringPeriodId") == 1 and s.get("statSourceId") == 0:
            wk1 = s.get("appliedTotal") or 0.0
        if s.get("scoringPeriodId") == 0 and s.get("statSourceId") == 1:
            season_proj = s.get("appliedTotal") or 0.0
    own = (pl.get("ownership") or {}).get("percentOwned") or 0.0
    status = (pl.get("injuryStatus") or "ACTIVE").upper()
    rows.append((wk1, season_proj, own, pl.get("fullName"), pos, status))

print()
print("=== TOP FREE AGENTS BY WEEK-1 ACTUAL POINTS ===")
print(f"  {'player':<24}{'pos':<5}{'wk1':>6}{'proj':>8}{'own%':>7}")
for wk1, proj, own, nm, pos, status in sorted(rows, reverse=True)[:18]:
    flag = "" if status == "ACTIVE" else f"  [{status[:4]}]"
    print(f"  {str(nm)[:23]:<24}{pos:<5}{wk1:>6.1f}{proj:>8.1f}"
          f"{own:>7.1f}{flag}")

print()
print("=== BEST FREE-AGENT RBs BY SEASON PROJECTION ===")
rbs = [r for r in rows if r[4] == "RB"]
for wk1, proj, own, nm, pos, status in sorted(rbs, key=lambda x: -x[1])[:10]:
    flag = "" if status == "ACTIVE" else f"  [{status[:4]}]"
    print(f"  {str(nm)[:23]:<24}{pos:<5}{wk1:>6.1f}{proj:>8.1f}"
          f"{own:>7.1f}{flag}")
