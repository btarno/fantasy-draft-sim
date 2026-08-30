"""Scan every roster for trade fits: who is RB-poor and WR-rich."""
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

teams = []
for t in d.get("teams", []):
    entries = ((t.get("roster") or {}).get("entries") or [])
    counts = defaultdict(int)
    players = []
    for e in entries:
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        pos = POS.get(pl.get("defaultPositionId") or 0, "?")
        nm = pl.get("fullName") or "?"
        counts[pos] += 1
        players.append((pos, nm))
    name = t.get("name") or f"Team {t['id']}"
    teams.append((t["id"], name, counts, players))

print("=== LEAGUE ROSTER SHAPE ===")
print(f"  {'team':<28}{'RB':>3}{'WR':>4}{'QB':>4}{'TE':>4}   trade fit")
for tid, name, c, players in teams:
    me = " <-- YOU" if tid == MY_ID else ""
    fit = ""
    if tid != MY_ID:
        # RB-rich + WR-poor = wants a WR, has RB surplus. Best partner.
        if c["RB"] >= 6 and c["WR"] <= 5:
            fit = "*** RB-rich / WR-thin"
        elif c["RB"] >= 6:
            fit = "RB surplus"
        elif c["WR"] <= 4:
            fit = "WR need"
    print(f"  {name[:27]:<28}{c['RB']:>3}{c['WR']:>4}{c['QB']:>4}"
          f"{c['TE']:>4}   {fit}{me}")

print()
print("=== RB HELD BY OTHERS (adjusted value, best first) ===")
rows = []
for tid, name, c, players in teams:
    if tid == MY_ID:
        continue
    for pos, nm in players:
        if pos != "RB":
            continue
        p = by_name.get(norm(nm))
        if not p or not p.get("proj"):
            continue
        r = injury.risk_profile(p, weeks=17, trials=300, seed=42)
        rows.append((r["mean"], r["p10"], r["p_misses_4plus"], nm, name,
                     c["RB"], c["WR"], p["injury"]))
rows.sort(key=lambda x: -x[0])
for mean, p10, pm, nm, team, rb, wr, inj in rows[:16]:
    flag = "" if inj == "ACTIVE" else " Q"
    print(f"  {nm:<21}adj {mean:>4.0f} fl {p10:>4.0f} miss4 {pm*100:>3.0f}%{flag:<2} "
          f"| {team[:20]:<21} RB{rb} WR{wr}")
