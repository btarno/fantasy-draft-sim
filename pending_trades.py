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

def sig(t):
    """
    Identity of a trade by its CONTENT: who sends which player to whom.

    Needed because a cancellation is recorded as its own
    TRADE_PROPOSAL/CANCELED row carrying the same items, with
    relatedTransactionId set to None. Matching only on relatedTransactionId
    misses it and leaves the original showing PENDING forever.
    """
    items = sorted(
        (it.get("playerId"), it.get("fromTeamId"), it.get("toTeamId"))
        for it in (t.get("items") or []))
    return tuple(items)


resolved_ids = set()
resolved_sigs = []          # (signature, timestamp) of each resolution
for t in tx:
    ttype = t.get("type") or ""
    status = (t.get("status") or "").upper()
    is_resolution = (any(k in ttype for k in ("DECLINE", "ACCEPT"))
                     or status in ("CANCELED", "CANCELLED"))
    if not is_resolution:
        continue
    rel = t.get("relatedTransactionId")
    if rel:
        resolved_ids.add(str(rel))
    resolved_ids.add(str(t.get("id")))
    s = sig(t)
    if s:
        resolved_sigs.append((s, t.get("proposedDate") or 0))


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


out = []
seen_sigs = set()
for t in sorted(tx, key=lambda x: -(x.get("proposedDate") or 0)):
    if t.get("teamId") != me:
        continue
    if (t.get("status") or "").upper() != "PENDING":
        continue
    if str(t.get("id")) in resolved_ids:
        continue
    s = sig(t)
    ts = t.get("proposedDate") or 0
    # A resolution with identical items kills this proposal only if it happened
    # AT OR AFTER it. Matching on content alone also killed the live re-send,
    # because a re-sent offer has the same items as the one that was canceled.
    if any(rs == s and rts >= ts for rs, rts in resolved_sigs):
        continue
    # Re-sending the same offer creates a second PENDING row; keep the newest.
    if s in seen_sigs:
        continue
    seen_sigs.add(s)
    out.append(t)

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
