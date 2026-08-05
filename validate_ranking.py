#!/usr/bin/env python3
"""
Validate a pre-draft ranking list BEFORE writing it to ESPN.

ESPN autopick does not simply take the top name on your list. It prioritizes
filling empty STARTING slots, and only then takes best-available for the bench.
Emulating that faithfully is the whole point of this file -- an autopick model
that ignores slot pressure will happily "validate" a list that drafts one QB in
a two-QB league.

    python3 validate_ranking.py [n_drafts]

Compares, over identical simulated drafts:
    - your ranking list, walked by a realistic autopick
    - ESPN's default ADP order, same autopick
    - the hand-coded plan strategy (the target to get close to)

Exit 0 if your list beats ESPN's default. Exit 1 if it does not -- in which case
do NOT upload it.
"""
import json
import statistics
import sys
from collections import defaultdict

import espn_client as api
import simulate as sim


def make_autopick(rank_map, cfg):
    """
    Realistic ESPN autopick.

    Priority order:
      1. If a mandatory STARTING slot is empty, fill it with the best player at
         that position according to the ranking list.
      2. Otherwise take the best-ranked player who can occupy FLEX.
      3. Late in the draft, pure best-ranked for the bench.
    K and D/ST are deferred until the final rounds, which is what ESPN does.
    """
    need = dict(cfg["lineup"])
    flex_pos = cfg["flex_positions"]
    n_flex = cfg["flex"]
    total_rounds = cfg["rounds"]

    def rank_of(p):
        return rank_map.get(p["name"], 9999)

    def pick(avail, roster, rnd):
        c = defaultdict(int)
        for p in roster:
            c[p["pos"]] += 1

        rounds_left = total_rounds - rnd

        # 1. Mandatory starting slots, K/D-ST deferred to the last 2 rounds.
        unfilled = []
        for pos, count in need.items():
            if c[pos] < count:
                if pos in ("K", "D/ST") and rounds_left > 1:
                    continue
                unfilled.append(pos)

        if unfilled:
            # Most urgent = the position whose best remaining option is ranked
            # highest in our list (i.e. the one we care most about right now).
            cands = [p for p in avail if p["pos"] in unfilled]
            if cands:
                return min(cands, key=rank_of)

        # 2. FLEX
        flex_surplus = sum(max(0, c[q] - need.get(q, 0)) for q in flex_pos)
        if flex_surplus < n_flex:
            cands = [p for p in avail if p["pos"] in flex_pos]
            if cands:
                return min(cands, key=rank_of)

        # 3. Bench: best ranked, but never a 2nd K or D/ST (unstartable).
        cands = [p for p in avail
                 if not (p["pos"] in ("K", "D/ST") and c[p["pos"]] >= need.get(p["pos"], 1))]
        pool = cands or avail
        return min(pool, key=rank_of)

    return pick


def roster_shape(roster):
    c = defaultdict(int)
    for p in roster:
        c[p["pos"]] += 1
    return " ".join(f"{pos}:{c[pos]}" for pos in ("QB", "RB", "WR", "TE", "K", "D/ST") if c[pos])


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    cfg = api.load_config("config.json")
    board = api.load_board(cfg)
    cal = {"noise": 31.5, "qb_urgency": 7.6}

    try:
        ranking = json.load(open("ranking.json"))
    except FileNotFoundError:
        sys.exit("ranking.json not found -- run build_ranking.py first")

    mine = {r["name"]: r["rank"] for r in ranking}
    espn = {p["name"]: i for i, p in enumerate(board, 1)}

    variants = {
        "MY strategy list": make_autopick(mine, cfg),
        "ESPN default (ADP)": make_autopick(espn, cfg),
        "hand-coded plan": sim.make_strategy(5, 7, cfg),
    }

    res, shapes = {}, {}
    for name, f in variants.items():
        scores, ranks = [], []
        for seed in range(n):
            s, r, roster, _, _ = sim.run_draft(board, cfg, f, seed=seed, params=cal)
            scores.append(s)
            ranks.append(r)
            if seed == 0:
                shapes[name] = roster_shape(roster)
        res[name] = (scores, ranks)

    ref = "ESPN default (ADP)"
    print(f"=== RANKING VALIDATION ({n} calibrated drafts) ===\n")
    print(f"  {'list':<22}{'mean':>9}{'top3':>8}{'1st':>7}{'vs ESPN':>10}{'t':>8}")
    ordered = sorted(res.items(), key=lambda kv: -statistics.mean(kv[1][0]))
    for name, (scores, ranks) in ordered:
        deltas = [a - b for a, b in zip(scores, res[ref][0])]
        m = statistics.mean(deltas)
        se = (statistics.stdev(deltas) / (len(deltas) ** 0.5)) if len(deltas) > 1 else 1
        t = m / se if se else 0
        top3 = sum(1 for x in ranks if x <= 3) / n * 100
        first = sum(1 for x in ranks if x == 1) / n * 100
        sig = "" if name == ref else ("  SIG" if abs(t) > 2.5 else "  ns")
        print(f"  {name:<22}{statistics.mean(scores):9.1f}{top3:7.1f}%"
              f"{first:6.1f}%{m:+10.1f}{t:+8.2f}{sig}")

    print("\n  Example roster shape (seed 0):")
    for name in res:
        print(f"    {name:<22}{shapes[name]}")

    my_mean = statistics.mean(res["MY strategy list"][0])
    espn_mean = statistics.mean(res[ref][0])
    plan_mean = statistics.mean(res["hand-coded plan"][0])

    print()
    # Sanity: does the list fill both QB slots?
    qb_ok = "QB:2" in shapes["MY strategy list"] or "QB:3" in shapes["MY strategy list"]
    if not qb_ok:
        print(f"  FAIL: list produced {shapes['MY strategy list']} -- "
              f"a 2QB league needs 2+ QBs. Do NOT upload.")
        return 1

    if my_mean <= espn_mean:
        print(f"  FAIL: your list ({my_mean:.0f}) does not beat ESPN's default "
              f"({espn_mean:.0f}). Do NOT upload.")
        return 1

    gap = plan_mean - my_mean
    print(f"  PASS: your list beats ESPN's default by {my_mean - espn_mean:+.0f} pts.")
    print(f"        Hand-coded plan is {gap:+.0f} pts better -- that gap is the cost")
    print(f"        of autopick vs drafting live with assist.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
