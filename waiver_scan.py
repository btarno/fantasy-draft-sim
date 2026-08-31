"""What RB/WR talent is sitting on waivers? Often better than a bad trade."""
import json

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


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


rostered = set()
for t in d.get("teams", []):
    for e in ((t.get("roster") or {}).get("entries") or []):
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        rostered.add(norm(pl.get("fullName")))

board = json.load(open("board.json"))
free = [p for p in board
        if norm(p["name"]) not in rostered and p.get("proj")
        and p["pos"] in ("RB", "WR", "TE")]

rows = []
for p in free[:60]:
    r = injury.risk_profile(p, weeks=17, trials=250, seed=42)
    rows.append((r["mean"], r["p10"], r["p_misses_4plus"], p))
rows.sort(key=lambda x: -x[0])

print(f"=== FREE AGENTS (unrostered, {len(free)} total) ===")
print()
for mean, p10, pm, p in rows[:14]:
    flag = "" if p["injury"] == "ACTIVE" else " Q"
    print(f"  {p['name']:<22}{p['pos']:<4}adj {mean:>4.0f} floor {p10:>4.0f} "
          f"miss4 {pm*100:>3.0f}%{flag}")

print()
print("  My current RB2/FLEX baseline: Henderson 171 / Dowdle 171")
better = [r for r in rows if r[0] > 171 and r[3]["pos"] == "RB"]
if better:
    print(f"  RBs on waivers better than that: "
          f"{', '.join(r[3]['name'] for r in better[:5])}")
else:
    print("  No RB on waivers beats my current RB2. Trade is the only path.")
