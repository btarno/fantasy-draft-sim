"""
Pending trades, distinguishing OUTGOING (awaiting their answer) from
INCOMING (awaiting mine).

Two separate traps here:

  1. mTransactions2 leaves proposal rows at status=PENDING forever; a decline
     is a separate record linked by relatedTransactionId. Filtering on status
     alone resurrects dead offers.

  2. mPendingTransactions only returns trades awaiting MY response. An offer I
     sent that the other manager has not answered does NOT appear there, so
     "mPendingTransactions is empty" does NOT mean "I have no live offers".

Correct answer needs both: mTransactions2 filtered against resolutions for
outgoing, mPendingTransactions for incoming.
"""
import datetime
import json

import requests

import espn_client as api

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")

d = requests.get(base, params={"view": ["mTransactions2", "mTeam"]},
                 cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                 timeout=45).json()
inc = requests.get(base, params={"view": ["mPendingTransactions"]},
                   cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                   timeout=45).json()

teams = {t["id"]: (t.get("name") or f"Team {t['id']}") for t in d["teams"]}
me = cfg["my_team_id"]
board = json.load(open("board.json"))
by_id = {p.get("id"): p["name"] for p in board if p.get("id")}

tx = [t for t in (d.get("transactions") or [])
      if (t.get("type") or "").startswith("TRADE")]

resolved = set()
for t in tx:
    ttype = t.get("type") or ""
    if any(k in ttype for k in ("DECLINE", "ACCEPT", "CANCEL")):
        rel = t.get("relatedTransactionId")
        if rel:
            resolved.add(str(rel))
        resolved.add(str(t.get("id")))


def show(t):
    ts = t.get("proposedDate") or t.get("processDate")
    when = (datetime.datetime.fromtimestamp(ts / 1000).strftime("%b %d %H:%M")
            if ts else "?")
    others = set()
    lines = []
    for it in (t.get("items") or []):
        nm = by_id.get(it.get("playerId"), f"player {it.get('playerId')}")
        frm, to = it.get("fromTeamId"), it.get("toTeamId")
        if frm != me:
            others.add(frm)
        if to != me:
            others.add(to)
        arrow = "GET " if to == me else "GIVE"
        lines.append(f"       {arrow} {nm}")
    partner = ", ".join(teams.get(o, "?") for o in others) or "?"
    print(f"     with {partner}   (sent {when})")
    for ln in lines:
        print(ln)


out = [t for t in tx
       if t.get("teamId") == me
       and (t.get("status") or "").upper() == "PENDING"
       and str(t.get("id")) not in resolved]

print(f"=== OUTGOING — waiting on their answer ({len(out)}) ===")
for t in out:
    show(t)
    print()

incoming = [t for t in (inc.get("transactions") or [])
            if (t.get("type") or "").startswith("TRADE")]
print(f"=== INCOMING — waiting on YOUR answer ({len(incoming)}) ===")
for t in incoming:
    show(t)
    print()
if not incoming:
    print("  (none)")
