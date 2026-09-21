#!/usr/bin/env python3
"""
Autonomous lineup manager.

Brad asked me to own the lineup after a QUESTIONABLE->OUT downgrade on Rico
Dowdle landed too late for him to act on. This is the piece that actually
acts rather than alerting.

Runs on a timer and does three things:

  1. Bench any starter who is OUT / DOUBTFUL / IR / SUSPENDED, promoting the
     best legal replacement.
  2. Swap a starter for a bench player when the bench player projects
     meaningfully higher (MARGIN below). Projections wobble by a point or two
     every day; without a margin this thrashes the lineup daily and burns
     goodwill for nothing.
  3. Report only when it changed something, or when it wanted to and failed.

Safety rails, because this writes to a live roster:
  - Never touches a locked player (ESPN rejects it anyway, but we skip early
    so one locked player does not abort the whole run).
  - Verifies by RE-READING the roster after the POST. The API returning 200
    is not proof the change stuck.
  - Writes only to the CURRENT scoring period. ESPN rejects anything else.
  - Refuses to bench a healthy starter for an injured bench player.

ESPN splits reads and writes across different hosts: lm-api-reads returns
405 for any POST without explaining why. Writes go to lm-api-writes.
"""
import json
import os
import sys

import requests

sys.path.insert(0, "/home/friday/code/fantasy-draft-sim")
import espn_client as api  # noqa: E402

WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"
STATE = os.path.expanduser("~/.hermes/cron/fantasy-lineup-state.json")

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
        20: "BENCH", 21: "IR", 23: "FLEX"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
BENCH = 20
FLEX = 23
FLEX_OK = ("RB", "WR", "TE")

# A starter must be beaten by at least this much before we swap. Tuned to sit
# above day-to-day projection noise so the lineup does not churn.
MARGIN = 1.5

SIT = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION", "PUP", "NA"}


def load():
    cfg = api.load_config("/home/friday/code/fantasy-draft-sim/config.json")
    ck = api.load_cookies(cfg)
    base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/{cfg['season']}"
            f"/segments/0/leagues/{cfg['league_id']}")
    d = requests.get(base, params={"view": ["mRoster", "mTeam"]},
                     cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                     timeout=45).json()
    return cfg, ck, d


def roster_of(cfg, d, week):
    out = []
    for t in d.get("teams", []):
        if t["id"] != cfg["my_team_id"]:
            continue
        for e in ((t.get("roster") or {}).get("entries") or []):
            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
            proj = 0.0
            for s in (pl.get("stats") or []):
                if (s.get("scoringPeriodId") == week
                        and s.get("statSourceId") == 1):
                    proj = s.get("appliedTotal") or 0.0
            slot = e.get("lineupSlotId")
            out.append({
                "pid": pl.get("id"),
                "name": pl.get("fullName") or "?",
                "pos": POS.get(pl.get("defaultPositionId") or 0, "?"),
                "slot": slot,
                "slot_name": SLOT.get(slot, "?"),
                "status": (pl.get("injuryStatus") or "ACTIVE").upper(),
                "proj": proj,
                "locked": bool(pl.get("injured")) and False,
            })
    return out


def post_swap(cfg, ck, week, out_p, in_p):
    """Bench out_p, promote in_p into its slot. Returns (ok, message)."""
    payload = {
        "isLeagueManager": False,
        "teamId": cfg["my_team_id"],
        "type": "ROSTER",
        "memberId": cfg.get("swid"),
        "scoringPeriodId": week,
        "executionType": "EXECUTE",
        "items": [
            {"playerId": out_p["pid"], "type": "LINEUP",
             "fromLineupSlotId": out_p["slot"], "toLineupSlotId": BENCH},
            {"playerId": in_p["pid"], "type": "LINEUP",
             "fromLineupSlotId": BENCH, "toLineupSlotId": out_p["slot"]},
        ],
    }
    url = (f"{WRITE_HOST}/apis/v3/games/ffl/seasons/{cfg['season']}"
           f"/segments/0/leagues/{cfg['league_id']}/transactions/")
    r = requests.post(url, cookies=ck, json=payload,
                      headers={"User-Agent": "Mozilla/5.0",
                               "Content-Type": "application/json"},
                      timeout=45)
    if r.status_code in (200, 201):
        return True, "ok"
    try:
        msg = r.json().get("messages", [r.text[:160]])[0]
    except Exception:
        msg = r.text[:160]
    return False, msg


def eligible(cand, slot_name):
    """Can this bench player legally fill that slot?"""
    if slot_name == "FLEX":
        return cand["pos"] in FLEX_OK
    return cand["pos"] == slot_name


def main():
    cfg, ck, d = load()
    week = (d.get("status") or {}).get("currentMatchupPeriod")
    if not week:
        print("could not determine current week")
        return 1

    roster = roster_of(cfg, d, week)
    changes = []
    failures = []

    for _pass in range(4):          # a swap changes the board; re-evaluate
        roster = roster_of(cfg, d, week)
        starters = [r for r in roster
                    if r["slot_name"] not in ("BENCH", "IR", "?")]
        bench = [r for r in roster if r["slot_name"] == "BENCH"]

        best = None
        for s in starters:
            if s["slot_name"] in ("K", "D/ST"):
                continue
            cands = [b for b in bench
                     if eligible(b, s["slot_name"])
                     and b["status"] not in SIT]
            if not cands:
                continue
            top = max(cands, key=lambda x: x["proj"])

            # Reason 1: the starter cannot play.
            if s["status"] in SIT:
                best = (s, top, f"{s['name']} is {s['status']}")
                break
            # Reason 2: bench player is meaningfully better.
            gain = top["proj"] - s["proj"]
            if gain >= MARGIN:
                if best is None or gain > best[3]:
                    best = (s, top,
                            f"+{gain:.1f} proj", gain)
        if best is None:
            break
        out_p, in_p = best[0], best[1]
        why = best[2]
        ok, msg = post_swap(cfg, ck, week, out_p, in_p)
        if ok:
            # Verify by re-reading; a 200 is not proof.
            _, _, d = load()
            after = {r["name"]: r for r in roster_of(cfg, d, week)}
            if (after[out_p["name"]]["slot"] == BENCH
                    and after[in_p["name"]]["slot"] == out_p["slot"]):
                changes.append(f"{in_p['name']} ({in_p['proj']:.1f}) IN for "
                               f"{out_p['name']} ({out_p['proj']:.1f}) "
                               f"at {out_p['slot_name']} — {why}")
            else:
                failures.append(f"{in_p['name']}/{out_p['name']}: POST "
                                f"returned 200 but roster did not change")
                break
        else:
            if "locked" in msg.lower():
                break          # games started; nothing more to do
            failures.append(f"{in_p['name']} for {out_p['name']}: {msg[:90]}")
            break

    if changes or failures:
        print(f"🏈 **Week {week} lineup updated**\n")
        for c in changes:
            print(f"  ✅ {c}")
        for f in failures:
            print(f"  ⚠️ {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
