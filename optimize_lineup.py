"""
Week-2 lineup optimiser.

Uses ESPN's WEEK-2 projections (not season-long averages) because that is what
actually decides a single matchup: opponent, pace, and bye weeks are baked in.
Compares my current lineup against the best legal one and reports the delta.
"""
import json

import requests

import espn_client as api

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
        20: "BENCH", 21: "IR", 23: "FLEX"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "D/ST": 1}
FLEX_OK = ("RB", "WR", "TE")

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base,
                 params={"view": ["mRoster", "mTeam", "mMatchupScore"],
                         "scoringPeriodId": 2},
                 cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                 timeout=45).json()

week = (d.get("status") or {}).get("currentMatchupPeriod")
teams = {t["id"]: (t.get("name") or f"T{t['id']}") for t in d["teams"]}
me = cfg["my_team_id"]

roster = []
for t in d["teams"]:
    if t["id"] != me:
        continue
    for e in ((t.get("roster") or {}).get("entries") or []):
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        nm = pl.get("fullName")
        pos = POS.get(pl.get("defaultPositionId") or 0, "?")
        slot = SLOT.get(e.get("lineupSlotId"), "?")
        status = (pl.get("injuryStatus") or "ACTIVE").upper()
        wk2 = None
        for s in (pl.get("stats") or []):
            if s.get("scoringPeriodId") == 2 and s.get("statSourceId") == 1:
                wk2 = s.get("appliedTotal")
        roster.append({"nm": nm, "pos": pos, "slot": slot,
                       "status": status, "proj": wk2 or 0.0,
                       "starting": slot not in ("BENCH", "IR", "?")})

print(f"=== WEEK {week} PROJECTIONS ===")
print()
cur = sum(r["proj"] for r in roster if r["starting"])

order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "K": 5, "D/ST": 6}
print("  CURRENT STARTERS              wk2 proj")
for r in sorted([x for x in roster if x["starting"]],
                key=lambda x: order.get(x["slot"], 9)):
    flag = "" if r["status"] == "ACTIVE" else f"  [{r['status'][:4]}]"
    print(f"    {r['slot']:<6}{str(r['nm']):<22}{r['proj']:>7.1f}{flag}")
print(f"    {'':6}{'TOTAL':<22}{cur:>7.1f}")

print()
print("  BENCH                         wk2 proj")
for r in sorted([x for x in roster if not x["starting"]],
                key=lambda x: -x["proj"]):
    flag = "" if r["status"] == "ACTIVE" else f"  [{r['status'][:4]}]"
    print(f"    {r['pos']:<6}{str(r['nm']):<22}{r['proj']:>7.1f}{flag}")

# ---- build the optimal legal lineup
SIT = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION", "PUP"}
avail = [r for r in roster if r["status"] not in SIT and r["proj"] > 0]
bypos = {}
for r in avail:
    bypos.setdefault(r["pos"], []).append(r)
for p in bypos:
    bypos[p].sort(key=lambda x: -x["proj"])

best = []
used = set()
for pos, n in LINEUP.items():
    for r in bypos.get(pos, [])[:n]:
        best.append((pos, r))
        used.add(r["nm"])
flex_pool = sorted(
    [r for p in FLEX_OK for r in bypos.get(p, []) if r["nm"] not in used],
    key=lambda x: -x["proj"])
if flex_pool:
    best.append(("FLEX", flex_pool[0]))
    used.add(flex_pool[0]["nm"])

opt_total = sum(r["proj"] for _, r in best)

print()
print("=== OPTIMAL LINEUP ===")
for pos, r in sorted(best, key=lambda x: order.get(x[0], 9)):
    mark = "" if r["starting"] else "   <-- SWAP IN"
    print(f"    {pos:<6}{str(r['nm']):<22}{r['proj']:>7.1f}{mark}")
print(f"    {'':6}{'TOTAL':<22}{opt_total:>7.1f}")

print()
gain = opt_total - cur
if gain > 0.5:
    print(f"  >>> {gain:+.1f} points available from swaps")
    for pos, r in best:
        if not r["starting"]:
            outs = [x for x in roster
                    if x["starting"] and x["nm"] not in used
                    and (x["pos"] == r["pos"] or x["slot"] == "FLEX")]
            if outs:
                worst = min(outs, key=lambda x: x["proj"])
                print(f"      START {r['nm']} ({r['proj']:.1f}) "
                      f"over {worst['nm']} ({worst['proj']:.1f})")
else:
    print("  Lineup is already optimal for week 2.")

for m in (d.get("schedule") or []):
    if m.get("matchupPeriodId") != week:
        continue
    h, a = (m.get("home") or {}), (m.get("away") or {})
    if h.get("teamId") == me or a.get("teamId") == me:
        opp = a.get("teamId") if h.get("teamId") == me else h.get("teamId")
        print(f"\n=== WEEK {week} OPPONENT: {teams.get(opp)} ===")
