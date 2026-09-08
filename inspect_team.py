"""What does C-Tun actually have? The pitch has to be accurate."""
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
board = json.load(open("board.json"))


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


by_name = {norm(p["name"]): p for p in board}

for t in d["teams"]:
    if not (t.get("name") or "").startswith("Predestined"):
        continue
    print(f"=== {t.get('name')} (rank {t.get('currentProjectedRank')}) ===")
    groups = {}
    for e in ((t.get("roster") or {}).get("entries") or []):
        pl = (e.get("playerPoolEntry") or {}).get("player") or {}
        nm = pl.get("fullName")
        pos = POS.get(pl.get("defaultPositionId") or 0, "?")
        bp = by_name.get(norm(nm))
        adj = None
        if bp and bp.get("proj"):
            adj = injury.risk_profile(bp, weeks=17, trials=200,
                                      seed=42)["mean"]
        groups.setdefault(pos, []).append((adj or 0, nm))
    for pos in ("QB", "RB", "WR", "TE", "K", "D/ST"):
        if pos not in groups:
            continue
        print(f"  {pos}:")
        for adj, nm in sorted(groups[pos], reverse=True):
            print(f"     {nm:<24}adj {adj:.0f}")
