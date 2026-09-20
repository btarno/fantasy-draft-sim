#!/usr/bin/env python3
"""
Set the ESPN lineup via the API.

ESPN accepts roster moves through a transaction POST -- no browser driving, no
drag-and-drop against a React UI that changes every season. The browser is only
needed to MINT the cookies; once we have espn_s2 + SWID the write is a plain
HTTP call.

Slot ids:
  0 QB   2 RB   4 WR   6 TE   16 D/ST   17 K   20 BENCH   21 IR   23 FLEX

Usage:
  python3 set_lineup.py --check                 show current lineup + slots
  python3 set_lineup.py --bench "X" --start "Y" swap two players
"""
import argparse
import json
import sys

import requests

import espn_client as api

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
        20: "BENCH", 21: "IR", 23: "FLEX"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
BENCH = 20


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def load():
    cfg = api.load_config("config.json")
    ck = api.load_cookies(cfg)
    base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/{cfg['season']}"
            f"/segments/0/leagues/{cfg['league_id']}")
    d = requests.get(base, params={"view": ["mRoster", "mTeam"]}, cookies=ck,
                     headers={"User-Agent": "Mozilla/5.0"},
                     timeout=45).json()
    return cfg, ck, base, d


def my_roster(cfg, d):
    for t in d["teams"]:
        if t["id"] != cfg["my_team_id"]:
            continue
        out = []
        for e in ((t.get("roster") or {}).get("entries") or []):
            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
            out.append({
                "pid": pl.get("id"),
                "name": pl.get("fullName"),
                "pos": POS.get(pl.get("defaultPositionId") or 0, "?"),
                "slot": e.get("lineupSlotId"),
                "slot_name": SLOT.get(e.get("lineupSlotId"), "?"),
                "status": (pl.get("injuryStatus") or "ACTIVE").upper(),
            })
        return out
    return []


def find(roster, name):
    n = norm(name)
    exact = [r for r in roster if norm(r["name"]) == n]
    if exact:
        return exact[0]
    part = [r for r in roster if n in norm(r["name"])]
    if len(part) == 1:
        return part[0]
    if len(part) > 1:
        raise SystemExit(f"ambiguous '{name}': "
                         f"{', '.join(p['name'] for p in part)}")
    raise SystemExit(f"not on roster: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--bench", help="player to move to bench")
    ap.add_argument("--start", help="player to move into the vacated slot")
    a = ap.parse_args()

    cfg, ck, base, d = load()
    roster = my_roster(cfg, d)

    if a.check or not (a.bench and a.start):
        print("=== CURRENT LINEUP ===")
        order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4,
                 "K": 5, "D/ST": 6, "BENCH": 7, "IR": 8}
        for r in sorted(roster, key=lambda x: order.get(x["slot_name"], 9)):
            flag = "" if r["status"] == "ACTIVE" else f"  [{r['status'][:4]}]"
            print(f"  {r['slot_name']:<6}{str(r['name']):<24}"
                  f"{r['pos']:<5}slot={r['slot']}{flag}")
        return 0

    out_p = find(roster, a.bench)
    in_p = find(roster, a.start)

    if out_p["slot"] == BENCH:
        raise SystemExit(f"{out_p['name']} is already on the bench")
    if in_p["slot"] != BENCH:
        raise SystemExit(f"{in_p['name']} is already starting "
                         f"({in_p['slot_name']})")

    target = out_p["slot"]
    print(f"  {out_p['name']} ({out_p['slot_name']}) -> BENCH")
    print(f"  {in_p['name']} -> {SLOT.get(target)}")

    payload = {
        "isLeagueManager": False,
        "teamId": cfg["my_team_id"],
        "type": "ROSTER",
        "memberId": cfg.get("swid"),
        "scoringPeriodId": (d.get("status") or {}).get("currentMatchupPeriod"),
        "executionType": "EXECUTE",
        "items": [
            {"playerId": out_p["pid"], "type": "LINEUP",
             "fromLineupSlotId": out_p["slot"], "toLineupSlotId": BENCH},
            {"playerId": in_p["pid"], "type": "LINEUP",
             "fromLineupSlotId": BENCH, "toLineupSlotId": target},
        ],
    }

    # WRITES GO TO A DIFFERENT HOST.
    #
    # api.API_HOST is lm-api-reads.fantasy.espn.com, which returns
    # 405 HTTP_METHOD_NOT_SUPPORTED for any POST -- it is read-only and does
    # not say so. Roster transactions must POST to lm-api-writes.
    WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"
    url = (f"{WRITE_HOST}/apis/v3/games/ffl/seasons/{cfg['season']}"
           f"/segments/0/leagues/{cfg['league_id']}/transactions/")
    r = requests.post(url, cookies=ck, json=payload,
                      headers={"User-Agent": "Mozilla/5.0",
                               "Content-Type": "application/json"},
                      timeout=45)
    print(f"  HTTP {r.status_code}")
    if r.status_code not in (200, 201):
        try:
            msg = r.json().get("messages", [r.text[:200]])[0]
        except Exception:
            msg = r.text[:200]
        print(f"  {msg}")
        # Distinguish "ESPN refused for a legitimate reason" from "broken".
        if "locked" in msg.lower():
            print("  -> player's game has started; lineup is locked. "
                  "Not an auth or endpoint problem.")
        elif "current scoring period" in msg.lower():
            print("  -> ESPN only accepts lineup writes for the CURRENT "
                  "week. Cannot pre-set a future week.")
        return 1

    # Verify by re-reading, never trust the POST alone.
    _, _, _, d2 = load()
    r2 = my_roster(cfg, d2)
    now_out = find(r2, out_p["name"])
    now_in = find(r2, in_p["name"])
    ok = now_out["slot"] == BENCH and now_in["slot"] == target
    print(f"  verified: {out_p['name']} -> {now_out['slot_name']}, "
          f"{in_p['name']} -> {now_in['slot_name']}")
    print("  RESULT: " + ("OK" if ok else "MISMATCH — change did not stick"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
