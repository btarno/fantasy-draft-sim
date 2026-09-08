"""Show MY pending trade offers with the actual players involved."""
import json

import requests

import espn_client as api

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")

# USE mPendingTransactions, NOT mTransactions2.
#
# mTransactions2 keeps every proposal row at status=PENDING forever. A decline
# is a SEPARATE record linked by relatedTransactionId and never updates the
# original, so filtering that log on status reports week-old dead offers as
# live. mPendingTransactions is the view ESPN's own UI reads and is the only
# trustworthy answer to "do I have offers right now".
d = requests.get(base, params={"view": ["mPendingTransactions", "mTeam"]},
                 cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                 timeout=45).json()

teams = {t["id"]: (t.get("name") or f"Team {t['id']}") for t in d["teams"]}
me = cfg["my_team_id"]

board = json.load(open("board.json"))
by_id = {p.get("id"): p["name"] for p in board if p.get("id")}

pend = [t for t in (d.get("transactions") or [])
        if (t.get("type") or "").startswith("TRADE")]

print(f"=== PENDING TRADES ({len(pend)}) ===")
for t in pend:
    prop = t.get("teamId")
    mine = "  <-- YOU PROPOSED" if prop == me else "  <-- SENT TO YOU"
    print(f"\n  id={str(t.get('id'))[:8]}  from={teams.get(prop)}{mine}")
    items = t.get("items") or []
    for it in items:
        pid = it.get("playerId")
        nm = by_id.get(pid, f"player {pid}")
        frm = teams.get(it.get("fromTeamId"), "?")
        to = teams.get(it.get("toTeamId"), "?")
        print(f"     {nm:<24} {frm}  ->  {to}")
    if not items:
        print("     (no item detail returned)")
