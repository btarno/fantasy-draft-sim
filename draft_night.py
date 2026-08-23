#!/usr/bin/env python3
"""
Draft-night assistant. Polls ESPN for live picks and reports when it is Brad's
turn. Designed to run as a cron job on draft night while he is at the venue with
only a phone.

WHY THIS SHAPE
--------------
Read-only ESPN API calls work fine from this host with saved cookies (verified:
HTTP 200 in 0.2s). The Gaming PC + headless Chrome rig was only ever needed for
ESPN *UI writes* (drag-and-drop rankings). So no hardware needs to be present at
the draft.

State lives in draft_night_state.json so consecutive runs know what they have
already announced and never repeat themselves.

OUTPUT CONTRACT
---------------
Prints a report ONLY when there is something worth saying:
  - Brad is within ALERT_WINDOW picks of his turn
  - Brad's pick just landed (confirmation + what it means)
  - a positional run is forming
Silence otherwise. An empty stdout means the cron job sends nothing.

    python3 draft_night.py                 # normal poll
    python3 draft_night.py --force          # report regardless of state
    python3 draft_night.py --simulate N     # pretend N picks have been made
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import requests

import espn_client as api
import injury
from simulate import val

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "draft_night_state.json")

# Announce when this many picks (or fewer) remain before Brad's turn.
ALERT_WINDOW = 3

# A positional run is this many of one position inside the last N picks.
RUN_LOOKBACK = 10
RUN_THRESHOLD = 5


def fetch_draft(cfg):
    ck = api.load_cookies(cfg)
    url = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/{cfg['season']}"
           f"/segments/0/leagues/{cfg['league_id']}")
    r = requests.get(url, params={"view": ["mDraftDetail", "mTeam"]},
                     cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                     timeout=30)
    r.raise_for_status()
    d = r.json()
    dd = d.get("draftDetail") or {}
    teams = {t["id"]: (t.get("name") or f"Team {t['id']}")
             for t in (d.get("teams") or [])}
    return dd, teams


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except json.JSONDecodeError:
            pass
    # last_announced_pick holds a string key ("up24", "got1", "done", "runRB3")
    # so repeated polls never re-announce the same event.
    return {"last_announced_pick": "", "last_seen_count": -1}


def save_state(s):
    json.dump(s, open(STATE, "w"))


def my_pick_numbers(picks, my_team_id):
    return sorted(p["overallPickNumber"] for p in picks
                  if p.get("teamId") == my_team_id)


def build_board_index(cfg):
    """Board keyed by ESPN playerId so picks can be resolved to names."""
    board = api.load_board(cfg)
    by_id = {}
    for p in board:
        pid = p.get("id") or p.get("playerId")
        if pid:
            by_id[pid] = p
    return board, by_id


def adjusted(p):
    """Injury-adjusted value, honouring overrides."""
    r = injury.risk_profile(p, weeks=17, trials=400, seed=42)
    return r["mean"], r["p10"]


def recommend(available, my_roster, cfg, gap, picks_made, n=6):
    """
    Best available given roster needs and who survives until the next turn.

    Scores on VALUE OVER REPLACEMENT, not raw points. Raw points systematically
    inflate QBs (a QB1 outscores an RB1 in total points in almost every format),
    so a raw-points ranking recommends a quarterback in round 2. VOR compares
    each player against the last startable player at his own position, which is
    the only fair cross-position comparison.
    """
    import math

    need = dict(cfg["lineup"])
    flex_pos = cfg["flex_positions"]
    n_flex = cfg["flex"]
    rounds = cfg["rounds"]
    teams = cfg["teams"]

    have = defaultdict(int)
    for p in my_roster:
        have[p["pos"]] += 1

    rounds_done = len(my_roster)
    noise = cfg.get("noise", 31.5)

    # Replacement level per position, from the CURRENTLY AVAILABLE pool.
    repl = {}
    for pos, count in need.items():
        starters = count * teams
        if pos in flex_pos:
            starters += n_flex * teams // max(1, len(flex_pos))
        pool = [p for p in available if p["pos"] == pos and p.get("proj")]
        if not pool:
            repl[pos] = 0.0
            continue
        idx = min(max(0, starters - 1), len(pool) - 1)
        repl[pos] = val(pool[idx])

    scored = []
    for p in available[:70]:
        if not p.get("proj"):
            continue
        adj, floor = adjusted(p)
        pos = p["pos"]
        vor = max(0.0, adj - repl.get(pos, 0.0))
        if vor <= 0:
            continue

        starters_left = max(0, need.get(pos, 0) - have[pos])
        if starters_left > 0:
            w = 1.0 + 0.30 * starters_left
        elif pos in flex_pos:
            surplus = sum(max(0, have[q] - need.get(q, 0)) for q in flex_pos)
            w = 0.85 if surplus < n_flex else 0.40
        elif pos in ("K", "D/ST"):
            w = 0.0 if have[pos] >= need.get(pos, 1) else 0.05
        else:
            w = 0.10

        if pos in ("K", "D/ST") and rounds_done < rounds - 2:
            w = min(w, 0.02)
        if w <= 0:
            continue

        # Survival: will he still be here at my next turn?
        z = ((p["adp"] or 999) - (picks_made + gap + 1)) / max(noise, 1e-6)
        surv = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        urgency = 1.0 - surv

        scored.append((vor * w * (0.6 + 0.4 * urgency), adj, floor, surv, p))

    scored.sort(key=lambda x: -x[0])
    return scored[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--simulate", type=int, default=None,
                    help="pretend the first N picks have been made (testing)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = api.load_config(args.config)
    cfg.setdefault("noise", 31.5)
    my_id = cfg["my_team_id"]

    board, by_id = build_board_index(cfg)

    if args.simulate is not None:
        # Fabricate a draft state for end-to-end testing.
        # IMPORTANT: reuse the REAL pick slots from ESPN so team ownership and
        # the snake order match production. An earlier version generated its own
        # teamIds and produced a wrong "your next pick" number -- the live path
        # was fine, the harness was lying.
        real_dd, teams = fetch_draft(cfg)
        slots = sorted(real_dd.get("picks") or [],
                       key=lambda p: p["overallPickNumber"])
        dd = {"drafted": False, "inProgress": True, "picks": []}
        for i, slot in enumerate(slots):
            entry = dict(slot)
            if i < args.simulate and i < len(board):
                entry["playerId"] = board[i].get("id") or -1
            else:
                entry["playerId"] = -1
            dd["picks"].append(entry)
    else:
        dd, teams = fetch_draft(cfg)

    picks = dd.get("picks") or []
    if not picks:
        return 0

    made = [p for p in picks if (p.get("playerId") or -1) > 0]
    made.sort(key=lambda p: p["overallPickNumber"])
    n_made = len(made)

    state = load_state()
    if not args.force and n_made == state.get("last_seen_count"):
        return 0  # nothing new since last poll

    # Not started yet
    if n_made == 0 and not dd.get("inProgress"):
        state["last_seen_count"] = 0
        save_state(state)
        return 0

    taken_ids = {p["playerId"] for p in made}
    my_roster = [by_id[p["playerId"]] for p in made
                 if p.get("teamId") == my_id and p["playerId"] in by_id]
    available = [p for p in board
                 if (p.get("id") or p.get("playerId")) not in taken_ids]

    mine = my_pick_numbers(picks, my_id)
    next_pick = next((n for n in mine if n > n_made), None)

    # Draft complete
    if next_pick is None:
        if state.get("last_announced_pick") != "done":
            print("🏁 **Draft complete.** Final roster:\n")
            by_pos = defaultdict(list)
            for p in my_roster:
                by_pos[p["pos"]].append(p["name"])
            for pos in ("QB", "RB", "WR", "TE", "K", "D/ST"):
                if by_pos[pos]:
                    print(f"  **{pos}**  {', '.join(by_pos[pos])}")
            state["last_announced_pick"] = "done"
        state["last_seen_count"] = n_made
        save_state(state)
        return 0

    gap = next_pick - n_made - 1

    # Did my pick just land?
    just_mine = [p for p in made[-3:] if p.get("teamId") == my_id]
    report = []

    if just_mine and state.get("last_announced_pick") != f"got{just_mine[-1]['overallPickNumber']}":
        pl = by_id.get(just_mine[-1]["playerId"])
        if pl:
            adj, floor = adjusted(pl)
            report.append(f"✅ **Got {pl['name']}** ({pl['pos']}, {pl['team']}) "
                          f"— adj {adj:.0f}, floor {floor:.0f}")
            state["last_announced_pick"] = f"got{just_mine[-1]['overallPickNumber']}"

    # Approaching my turn?
    if gap <= ALERT_WINDOW:
        key = f"up{next_pick}"
        if args.force or state.get("last_announced_pick") != key:
            recs = recommend(available, my_roster, cfg, gap, n_made)
            need = dict(cfg["lineup"])
            have = defaultdict(int)
            for p in my_roster:
                have[p["pos"]] += 1
            unmet = [f"{pos}×{need[pos]-have[pos]}" for pos in need
                     if have[pos] < need[pos]]

            report.append(f"\n🚨 **YOU'RE UP in {gap} pick{'s' if gap != 1 else ''}** "
                          f"— pick #{next_pick} (round {(next_pick-1)//cfg['teams']+1})")
            if unmet:
                report.append(f"Still need: {' '.join(unmet)}")
            report.append("")
            report.append("```")
            report.append(f"{'PLAYER':<24}{'POS':<5}{'ADJ':>6}{'FLOOR':>7}  SURVIVES?")
            for score, adj, floor, surv, p in recs:
                inj = "" if p["injury"] in ("ACTIVE", None) else f" [{p['injury'][:4]}]"
                flag = "GONE" if surv < 0.30 else ("risky" if surv < 0.65 else "can wait")
                report.append(f"{p['name']:<24}{p['pos']:<5}{adj:6.0f}{floor:7.0f}  {flag}{inj}")
            report.append("```")
            if recs:
                report.append(f"**Take {recs[0][4]['name']}.**")
            state["last_announced_pick"] = key

    # Positional run forming?
    recent = made[-RUN_LOOKBACK:]
    runc = defaultdict(int)
    for p in recent:
        pl = by_id.get(p["playerId"])
        if pl:
            runc[pl["pos"]] += 1
    for pos, c in runc.items():
        if c >= RUN_THRESHOLD and pos not in ("K", "D/ST"):
            key = f"run{pos}{n_made//5}"
            if state.get("last_announced_pick") != key and gap > ALERT_WINDOW:
                report.append(f"\n📉 **{pos} run** — {c} of the last "
                              f"{len(recent)} picks. Tier is draining.")
                state["last_announced_pick"] = key
            break

    state["last_seen_count"] = n_made
    save_state(state)

    if report:
        print(f"_Pick {n_made}/{len(picks)} · your next: #{next_pick}_")
        print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
