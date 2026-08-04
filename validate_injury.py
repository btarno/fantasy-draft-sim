#!/usr/bin/env python3
"""
Validate the injury model against the source study it is built from.

If simulated games-available and injury-length distributions do not match
ProFootballLogic's 2015 observations, the model is wrong and any risk numbers
it produces are decoration.
"""
import random
import sys

import injury

# Observed values from https://www.profootballlogic.com/articles/nfl-injury-rate-analysis/
# (16-game season, players on rosters at season end)
OBSERVED_AVAIL_16 = {"QB": 14.9, "RB": 13.3, "WR": 14.0, "TE": 14.2}
OBSERVED_PCT_2_OR_FEWER = 0.64      # of injuries costing >=1 game, 64% cost <=2
OBSERVED_MEAN_LENGTH = {"QB": 3.1, "RB": 3.9, "WR": 3.2, "TE": 2.6}
OBSERVED_PCT_FULL_SEASON = 0.45     # 45% of players available all 16


def check(label, sim, obs, tol):
    delta = sim - obs
    ok = abs(delta) <= tol
    flag = "PASS" if ok else "FAIL"
    print(f"  {label:<34} sim {sim:6.2f}   obs {obs:6.2f}   diff {delta:+6.2f}   [{flag}]")
    return ok


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    rng = random.Random(7)
    all_pass = True

    print("=== VALIDATION vs ProFootballLogic 2015 data ===")
    print(f"    {trials} simulated seasons per position, 16-game schedule\n")

    print("GAMES AVAILABLE OF 16 (healthy players at week 1):")
    for pos, obs in OBSERVED_AVAIL_16.items():
        avail = [injury.simulate_availability(pos, "ACTIVE", 16, rng)
                 for _ in range(trials)]
        all_pass &= check(f"{pos} games available", sum(avail) / len(avail), obs, 0.6)

    print("\nINJURY LENGTH DISTRIBUTION:")
    lengths = []
    for _ in range(trials):
        L = injury.draw_injury_length("WR", rng)
        lengths.append(min(L, 16))   # cap season-enders at a full season
    pct2 = sum(1 for L in lengths if L <= 2) / len(lengths)
    all_pass &= check("pct of injuries <= 2 games", pct2, OBSERVED_PCT_2_OR_FEWER, 0.08)

    for pos, obs in OBSERVED_MEAN_LENGTH.items():
        Ls = [min(injury.draw_injury_length(pos, rng), 16) for _ in range(trials)]
        all_pass &= check(f"{pos} mean injury length", sum(Ls) / len(Ls), obs, 0.9)

    print("\nSEASON-LEVEL:")
    full = 0
    total = 0
    for pos in ("QB", "RB", "WR", "TE"):
        for _ in range(trials // 4):
            total += 1
            if injury.simulate_availability(pos, "ACTIVE", 16, rng) == 16:
                full += 1
    all_pass &= check("pct available all 16 games", full / total,
                      OBSERVED_PCT_FULL_SEASON, 0.10)

    print("\nCURRENT-DESIGNATION EFFECT (sanity, not validated against data):")
    for status in ("ACTIVE", "QUESTIONABLE", "OUT", "INJURY_RESERVE"):
        avail = [injury.simulate_availability("RB", status, 17, rng)
                 for _ in range(trials // 4)]
        print(f"  RB with {status:<16} avg {sum(avail)/len(avail):5.1f} of 17 weeks available")

    print()
    if all_pass:
        print("  ALL CHECKS PASSED — model reproduces the source distributions.")
        return 0
    print("  SOME CHECKS FAILED — parameters need adjustment before trusting output.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
