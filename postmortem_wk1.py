"""
Honest post-mortem: why did Ben's roster outscore mine in week 1?

Separate three explanations that get conflated:
  1. Better players (draft-quality gap)
  2. Better week (variance -- his guys beat projection, mine missed)
  3. Better decisions (he started the right players, I did not)

Only #1 and #3 are my fault. #2 is noise and says nothing about strategy.
"""
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
ben = next(tid for tid, nm in teams.items() if nm.startswith("Ben There"))


def rows(tid):
    for t in d["teams"]:
        if t["id"] != tid:
            continue
        out = []
        for e in ((t.get("roster") or {}).get("entries") or []):
            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
            slot = SLOT.get(e.get("lineupSlotId"), "?")
            act = proj = 0.0
            for s in (pl.get("stats") or []):
                if s.get("scoringPeriodId") != 1:
                    continue
                if s.get("statSourceId") == 0:
                    act = s.get("appliedTotal") or 0.0
                elif s.get("statSourceId") == 1:
                    proj = s.get("appliedTotal") or 0.0
            out.append({
                "starter": slot not in ("BENCH", "IR", "?"),
                "slot": slot, "name": pl.get("fullName"),
                "act": act, "proj": proj,
                "played": act > 0 or proj == 0,
            })
        return out
    return []


mine, his = rows(me), rows(ben)


def summarise(label, rs):
    st = [r for r in rs if r["starter"]]
    act = sum(r["act"] for r in st)
    proj = sum(r["proj"] for r in st)
    # Best legal lineup if I had perfect hindsight.
    print(f"  {label}")
    print(f"    projected starters : {proj:6.1f}")
    print(f"    actual starters    : {act:6.1f}")
    print(f"    beat projection by : {act - proj:+6.1f}")
    return act, proj


print("=== WEEK 1 ===")
my_act, my_proj = summarise("Lord Fumblebottom", mine)
print()
his_act, his_proj = summarise("Ben There, Wrecked That", his)

print()
print("=== WHERE THE GAP CAME FROM ===")
roster_gap = his_proj - my_proj
luck_gap = (his_act - his_proj) - (my_act - my_proj)
print(f"  roster quality (projection gap) : {roster_gap:+6.1f}")
print(f"  week-1 variance (luck gap)      : {luck_gap:+6.1f}")
print(f"  total margin                    : {his_act - my_act:+6.1f}")

print()
print("=== DID I MISPLAY MY OWN LINEUP? ===")
bench_scored = [r for r in mine if not r["starter"] and r["act"] > 0]
starters_zero = [r for r in mine if r["starter"] and r["act"] == 0]
for b in sorted(bench_scored, key=lambda r: -r["act"]):
    print(f"    bench  {b['name']:<22}{b['act']:>6.1f}")
for s in starters_zero:
    print(f"    START  {s['name']:<22}{s['act']:>6.1f}  (proj {s['proj']:.1f})")
