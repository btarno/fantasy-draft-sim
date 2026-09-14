"""Live week-1 matchup: my scores vs opponent, player by player."""
import requests

import espn_client as api

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
        20: "BENCH", 21: "IR", 23: "FLEX"}

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base,
                 params={"view": ["mMatchupScore", "mRoster", "mTeam"],
                         "scoringPeriodId": 1},
                 cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                 timeout=45).json()

teams = {t["id"]: (t.get("name") or f"T{t['id']}") for t in d["teams"]}
me = cfg["my_team_id"]

opp = None
for m in (d.get("schedule") or []):
    if m.get("matchupPeriodId") != 1:
        continue
    h, a = (m.get("home") or {}), (m.get("away") or {})
    if h.get("teamId") == me:
        opp = a.get("teamId")
    elif a.get("teamId") == me:
        opp = h.get("teamId")


def score_team(tid):
    for t in d["teams"]:
        if t["id"] != tid:
            continue
        rows = []
        total = 0.0
        for e in ((t.get("roster") or {}).get("entries") or []):
            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
            slot = SLOT.get(e.get("lineupSlotId"), "?")
            actual = proj = 0.0
            for s in (pl.get("stats") or []):
                if s.get("scoringPeriodId") != 1:
                    continue
                if s.get("statSourceId") == 0:
                    actual = s.get("appliedTotal") or 0.0
                elif s.get("statSourceId") == 1:
                    proj = s.get("appliedTotal") or 0.0
            starter = slot not in ("BENCH", "IR", "?")
            if starter:
                total += actual
            rows.append((starter, slot, pl.get("fullName"), actual, proj,
                         pl.get("injuryStatus")))
        return total, rows
    return 0.0, []


my_total, my_rows = score_team(me)
op_total, op_rows = score_team(opp)

print(f"=== WEEK 1: {teams[me]} {my_total:.1f} — "
      f"{op_total:.1f} {teams.get(opp)} ===")
print()
order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "K": 5, "D/ST": 6}
print("  MY STARTERS                     actual   proj   diff")
for starter, slot, nm, act, proj, inj in sorted(
        [r for r in my_rows if r[0]], key=lambda r: order.get(r[1], 9)):
    diff = act - proj
    flag = "" if inj in ("ACTIVE", None) else f"  [{str(inj)[:4]}]"
    print(f"    {slot:<6}{str(nm):<22}{act:>6.1f} {proj:>6.1f} "
          f"{diff:>+6.1f}{flag}")

print()
print(f"  opponent total: {op_total:.1f}")
print(f"  margin: {my_total - op_total:+.1f}")

bench = [r for r in my_rows if not r[0] and r[3] > 0]
if bench:
    print("\n  bench players who scored:")
    for _, slot, nm, act, proj, inj in sorted(bench, key=lambda r: -r[3]):
        print(f"    {str(nm):<22}{act:>6.1f}")
