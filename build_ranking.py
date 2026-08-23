"""
Build a STRATEGY-AWARE pre-draft ranking list for ESPN.

Why this file exists
--------------------
ESPN's custom pre-draft rankings are a single flat ordered list. Autopick walks
it top-down and takes the best available player that fits an open slot, but it
PRIORITIZES filling empty starting slots. That second part matters enormously.

Ranking purely by injury-adjusted value is actively harmful in a 2QB league: QB
totals are inflated by starting two, which puts ~50 of the top 60 slots on
quarterbacks. Autopick would take Josh Allen 1.01 and hoard QBs -- the mistake
8,400 simulated drafts flagged as the most expensive in this format (-47 pts).

But over-correcting is just as bad. Pushing QBs too deep in the list means that
when autopick goes looking for a QB to fill a mandatory slot, the best remaining
name in YOUR list is a backup. First attempt at this file buried QB2 near rank
240 and simulated rosters came out ~445 points light. Validated by
validate_ranking.py before anything is written to ESPN.

What this does
--------------
Places each player at the ROUND WE ACTUALLY WANT THEM, then flattens:

  1. Slots 1-40   : RB/WR/TE only, ordered by injury-adjusted value.
  2. Slots ~41-90 : BOTH QB starters seeded close together, interleaved with
                    continued RB/WR value. This is the flat part of the QB curve
                    AND it guarantees a real starter is top-of-list when autopick
                    needs to fill either QB slot.
  3. Then         : depth by adjusted value; QB3+ after the starters.
  4. Last         : K and D/ST, since one of each is all you can start.
"""
import json

import espn_client as api
import injury

# Positions that may be taken in the early rounds.
EARLY_POS = ("RB", "WR", "TE")

# Where QBs start entering the list.
#
# TUNED PER FORMAT. In the old 2QB setup this was 41 (late round 4), because two
# mandatory QB slots meant autopick needed a genuine starter available twice.
# The league moved to 1QB + 1 FLEX on 2026-08-24, so only 12 QBs start league-
# wide instead of 24 and the position is far less scarce -- but the top of the
# QB curve is now steeper relative to replacement (QB12 replacement, not QB24).
# Slot 55 puts QB1 around round 5, which validate_ranking.py confirms.
QB_ENTRY_SLOT = 55

# Skill players placed between each seeded QB.
SKILL_PER_QB = 6

# Seed this many QBs. Roster max is 2 QB (ESPN positionLimits), and only one
# starts, so a starter plus one backup is the whole requirement.
QB_TARGET = 2

# Kickers and defenses go at the very bottom regardless of projection.
LAST_POS = ("K", "D/ST")


def adjusted_values(board, trials=1200, seed=42):
    """Injury-adjusted value for every player with a projection."""
    out = []
    for p in board:
        if not p.get("proj"):
            continue
        r = injury.risk_profile(p, weeks=17, trials=trials, seed=seed)
        out.append({**p, "adj": r["mean"], "floor": r["p10"],
                    "modeled_status": r["modeled_status"],
                    "override": r["override_reason"]})
    return out


def build_ranking(board, trials=1200):
    """
    Return an ordered list of players representing our draft strategy.
    """
    players = adjusted_values(board, trials=trials)

    by_pos = {}
    for p in players:
        by_pos.setdefault(p["pos"], []).append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: -x["adj"])

    skill = [p for p in players if p["pos"] in EARLY_POS]
    skill.sort(key=lambda x: -x["adj"])
    qbs = by_pos.get("QB", [])
    kickers = by_pos.get("K", [])
    dsts = by_pos.get("D/ST", [])

    ranking = []
    qb_used = 0
    skill_i = 0

    # Slots 1 .. (QB_ENTRY_SLOT - 1): skill positions only.
    while len(ranking) < QB_ENTRY_SLOT - 1 and skill_i < len(skill):
        ranking.append(skill[skill_i])
        skill_i += 1

    # QB window: seed our QB targets, interleaved 1 QB per 4 skill players so
    # the list keeps offering RB/WR value alongside.
    while qb_used < QB_TARGET and qb_used < len(qbs):
        ranking.append(qbs[qb_used])
        qb_used += 1
        for _ in range(SKILL_PER_QB):
            if skill_i < len(skill):
                ranking.append(skill[skill_i])
                skill_i += 1

    # Remaining skill players by value.
    while skill_i < len(skill):
        ranking.append(skill[skill_i])
        skill_i += 1

    # Remaining QBs after our targets -- real value but low priority.
    ranking.extend(qbs[qb_used:])

    # K and D/ST dead last: one of each is the max you can start.
    ranking.extend(kickers)
    ranking.extend(dsts)

    # Dedupe defensively while preserving order.
    seen, final = set(), []
    for p in ranking:
        if p["name"] in seen:
            continue
        seen.add(p["name"])
        final.append(p)
    return final


def main():
    cfg = api.load_config("config.json")
    board = api.load_board(cfg)
    ranking = build_ranking(board)

    out = [{"rank": i, "name": p["name"], "pos": p["pos"], "team": p["team"],
            "adj": round(p["adj"], 1), "espn_adp": p["adp"],
            "injury": p["injury"], "modeled": p["modeled_status"]}
           for i, p in enumerate(ranking, 1)]
    json.dump(out, open("ranking.json", "w"), indent=1)

    print(f"Built {len(out)} ranked players -> ranking.json\n")
    print(f"  {'#':<5}{'player':<24}{'pos':<5}{'adj':>7}{'ESPN ADP':>10}")
    for r in out[:14]:
        print(f"  {r['rank']:<5}{r['name']:<24}{r['pos']:<5}{r['adj']:7.0f}{r['espn_adp']:10}")
    print("  ...")
    qb_slots = [r["rank"] for r in out if r["pos"] == "QB"][:4]
    print(f"\n  First QBs appear at ranks: {qb_slots}")
    k_slots = [r["rank"] for r in out if r["pos"] in LAST_POS][:2]
    print(f"  K/D-ST start at rank: {k_slots}")


if __name__ == "__main__":
    main()
