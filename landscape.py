"""
Post-week-1 trade landscape.

A manager who just LOST is measurably more willing to deal than one who won,
so rank the partners by record and by whether their RB room actually produced.
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
d = requests.get(base,
                 params={"view": ["mRoster", "mTeam", "mMatchupScore"],
                         "scoringPeriodId": 1},
                 cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                 timeout=45).json()

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
board = json.load(open("board.json"))


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


by_name = {norm(p["name"]): p for p in board}
me = cfg["my_team_id"]
cache = {}


def prof(p):
    k = norm(p["name"])
    if k not in cache:
        cache[k] = injury.risk_profile(p, weeks=17, trials=250, seed=42)
    return cache[k]


# Week-1 result per team.
#
# NOTE: home/away totalPoints reads 0.0 until ESPN finalises the week, which
# made every team look like it lost. Fall back to summing actual player scores
# from the roster when totalPoints is still zero.
def roster_points(tid):
    for t in d.get("teams", []):
        if t["id"] != tid:
            continue
        tot = 0.0
        for e in ((t.get("roster") or {}).get("entries") or []):
            slot = e.get("lineupSlotId")
            if slot in (20, 21):        # bench / IR do not count
                continue
            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
            for s in (pl.get("stats") or []):
                if (s.get("scoringPeriodId") == 1
                        and s.get("statSourceId") == 0):
                    tot += s.get("appliedTotal") or 0.0
        return tot
    return 0.0


result = {}
for m in (d.get("schedule") or []):
    if m.get("matchupPeriodId") != 1:
        continue
    h, a = (m.get("home") or {}), (m.get("away") or {})
    hid, aid = h.get("teamId"), a.get("teamId")
    hp = h.get("totalPoints") or 0.0
    ap = a.get("totalPoints") or 0.0
    if hp == 0.0 and ap == 0.0:
        hp = roster_points(hid) if hid else 0.0
        ap = roster_points(aid) if aid else 0.0
    # Still all-zero means the week genuinely has not been played.
    if hp == 0.0 and ap == 0.0:
        continue
    if hid:
        result[hid] = ("W" if hp > ap else "L", hp)
    if aid:
        result[aid] = ("W" if ap > hp else "L", ap)

print("=== POST-WEEK-1 LANDSCAPE ===")
print(f"  {'team':<28}{'W/L':<5}{'pts':>7}  {'RB':>3}{'WR':>4}  spare RBs")
rowsout = []
for t in d.get("teams", []):
    tid = t["id"]
    name = t.get("name") or f"T{tid}"
    counts = defaultdict(int)
    rbs = []
    for e in ((t.get("roster") or {}).get("entries") or []):
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        nm = pl.get("fullName")
        pos = POS.get(pl.get("defaultPositionId") or 0, "?")
        counts[pos] += 1
        if pos == "RB":
            bp = by_name.get(norm(nm))
            if bp and bp.get("proj"):
                rbs.append((prof(bp)["mean"], nm,
                            (pl.get("injuryStatus") or "ACTIVE").upper()))
    rbs.sort(reverse=True)
    wl, pts = result.get(tid, ("?", 0.0))
    spare = [r for r in rbs[2:] if r[0] > 171 and r[2] == "ACTIVE"]
    marker = "  <-- YOU" if tid == me else ""
    spare_s = ", ".join(f"{n} {v:.0f}" for v, n, _ in spare[:3]) or "-"
    print(f"  {name[:27]:<28}{wl:<5}{pts:>7.1f}  "
          f"{counts['RB']:>3}{counts['WR']:>4}  {spare_s}{marker}")
    if tid != me and spare:
        rowsout.append((wl, pts, name, spare, counts))

print()
print("=== BEST TARGETS (lost week 1 + has a spare RB) ===")
for wl, pts, name, spare, counts in sorted(rowsout,
                                           key=lambda x: (x[0] != "L", x[1])):
    tag = "LOST wk1 — motivated" if wl == "L" else "won wk1"
    for v, n, _ in spare[:2]:
        print(f"  {n:<22}adj {v:>4.0f}  | {name[:24]:<25} "
              f"RB{counts['RB']} WR{counts['WR']}  ({tag})")
