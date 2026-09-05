"""Live injury + bye check on my starters, plus the week-1 matchup."""
import json

import requests

import espn_client as api

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
        20: "BENCH", 21: "IR", 23: "FLEX"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base, params={"view": ["mRoster", "mTeam", "mMatchupScore"]},
                 cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                 timeout=45).json()

me_id = cfg["my_team_id"]
teams = {t["id"]: (t.get("name") or f"Team {t['id']}") for t in d["teams"]}
me = next(t for t in d["teams"] if t["id"] == me_id)

print("=== LIVE STATUS OF MY ROSTER ===")
print()
starters, bench = [], []
for e in ((me.get("roster") or {}).get("entries") or []):
    pl = (e.get("playerPoolEntry") or {}).get("player") or {}
    nm = pl.get("fullName")
    slot = SLOT.get(e.get("lineupSlotId"), "?")
    pos = POS.get(pl.get("defaultPositionId") or 0, "?")
    status = (pl.get("injuryStatus") or "ACTIVE").upper()
    injured = bool(pl.get("injured"))
    row = (slot, nm, pos, status, injured)
    (bench if slot in ("BENCH", "IR") else starters).append(row)

order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "K": 5, "D/ST": 6}
starters.sort(key=lambda r: order.get(r[0], 9))

problems = []
print("  STARTERS")
for slot, nm, pos, status, injured in starters:
    mark = "" if status == "ACTIVE" else f"   <-- {status}"
    if status != "ACTIVE":
        problems.append((nm, pos, status))
    print(f"    {slot:<6}{nm:<24}{pos:<6}{status}{mark}")

print("\n  BENCH")
for slot, nm, pos, status, injured in bench:
    mark = "" if status == "ACTIVE" else f"   [{status}]"
    print(f"    {slot:<6}{nm:<24}{pos:<6}{status}{mark}")

print()
if problems:
    print("=== ACTION NEEDED ===")
    for nm, pos, status in problems:
        alts = [b for b in bench if b[2] == pos and b[3] == "ACTIVE"]
        alt = alts[0][1] if alts else "no clean replacement on bench"
        print(f"  {nm} ({pos}) is {status} and STARTING. Bench option: {alt}")
else:
    print("=== NO INJURY PROBLEMS IN THE STARTING LINEUP ===")

# week 1 opponent
for m in (d.get("schedule") or []):
    if m.get("matchupPeriodId") != 1:
        continue
    h, a = (m.get("home") or {}), (m.get("away") or {})
    if h.get("teamId") == me_id or a.get("teamId") == me_id:
        opp = a.get("teamId") if h.get("teamId") == me_id else h.get("teamId")
        print(f"\n=== WEEK 1 MATCHUP ===")
        print(f"  {teams[me_id]}  vs  {teams.get(opp)}")
