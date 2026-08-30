#!/usr/bin/env python3
"""
Trade evaluator.

Scores a proposed trade against the injury model AND roster context, because
raw projections lie twice over:

  1. They ignore injury risk. TreVeyon Henderson projects 197 but carries a 52%
     chance of missing 4+ games -- he is worth ~171 in expectation and his floor
     is 139. The market prices the 197.

  2. They ignore what you already have. Brad's 7th wide receiver cannot enter
     the lineup, so his marginal value is near zero no matter how he projects.
     A startable RB is worth far more than his raw number suggests.

The gap between those two views is the entire edge in a trade.

    python3 trade_eval.py --give "TreVeyon Henderson,Denzel Boston" \
                          --get  "Some Runningback"
"""
import argparse
import json
from collections import defaultdict

import injury

# Brad's roster after the 2026 draft.
MY_ROSTER = [
    "Jalen Hurts", "Travis Etienne Jr.", "TreVeyon Henderson", "CeeDee Lamb",
    "Rashee Rice", "Colston Loveland", "Rico Dowdle", "J.K. Dobbins",
    "Xavier Worthy", "Jordan Addison", "Mark Andrews", "Romeo Doubs",
    "Denzel Boston", "Tre Tucker",
]

# Starting requirements: 1QB 2RB 2WR 1TE 1FLEX (+K, D/ST).
LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX = 1
FLEX_POS = ("RB", "WR", "TE")


def norm(s):
    return "".join(c for c in s.lower() if c.isalnum())


def load_board():
    board = json.load(open("board.json"))
    return {norm(p["name"]): p for p in board}


def evaluate(p, cache={}):
    """Injury-adjusted mean and floor for one player."""
    key = norm(p["name"])
    if key not in cache:
        cache[key] = injury.risk_profile(p, weeks=17, trials=500, seed=42)
    return cache[key]


def lineup_value(names, by_name):
    """
    Value of the best legal starting lineup from a set of players.

    This is what actually scores points. A 7th WR contributes nothing here,
    which is precisely the point -- it is why depth-for-starter trades win.
    """
    players = [by_name[norm(n)] for n in names if norm(n) in by_name]
    ranked = defaultdict(list)
    for p in players:
        r = evaluate(p)
        ranked[p["pos"]].append((r["mean"], r["p10"], p["name"]))
    for pos in ranked:
        ranked[pos].sort(reverse=True)

    total = floor = 0.0
    used = set()
    for pos, count in LINEUP.items():
        for mean, p10, nm in ranked.get(pos, [])[:count]:
            total += mean
            floor += p10
            used.add(nm)

    # FLEX: best remaining eligible player.
    rest = []
    for pos in FLEX_POS:
        for mean, p10, nm in ranked.get(pos, []):
            if nm not in used:
                rest.append((mean, p10, nm))
    rest.sort(reverse=True)
    for mean, p10, nm in rest[:FLEX]:
        total += mean
        floor += p10
        used.add(nm)

    return total, floor, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--give", required=True,
                    help="comma-separated players you send away")
    ap.add_argument("--get", required=True,
                    help="comma-separated players you receive")
    a = ap.parse_args()

    by_name = load_board()
    give = [g.strip() for g in a.give.split(",") if g.strip()]
    get = [g.strip() for g in a.get.split(",") if g.strip()]

    missing = [n for n in give + get if norm(n) not in by_name]
    if missing:
        print(f"  NOT ON BOARD (check spelling): {', '.join(missing)}")
        print("  Cannot score a trade with unknown players.")
        return 1

    print("=== PLAYERS ===")
    raw_out = raw_in = 0.0
    for label, names in (("GIVE", give), ("GET", get)):
        for n in names:
            p = by_name[norm(n)]
            r = evaluate(p)
            raw = p.get("proj") or 0
            if label == "GIVE":
                raw_out += raw
            else:
                raw_in += raw
            flag = "" if p["injury"] == "ACTIVE" else f"  [{p['injury'][:4]}]"
            print(f"  {label:<5}{p['name']:<22}{p['pos']:<4}"
                  f"raw {raw:>4.0f}  adj {r['mean']:>4.0f}  "
                  f"floor {r['p10']:>4.0f}  miss4 {r['p_misses_4plus']*100:>3.0f}%{flag}")

    before_total, before_floor, before_used = lineup_value(MY_ROSTER, by_name)
    after_roster = [p for p in MY_ROSTER
                    if norm(p) not in {norm(g) for g in give}] + get
    after_total, after_floor, after_used = lineup_value(after_roster, by_name)

    print()
    print("=== RAW PROJECTION (what the other manager likely sees) ===")
    print(f"  you send {raw_out:.0f}, you receive {raw_in:.0f}  "
          f"-> {raw_in - raw_out:+.0f} on paper")

    print()
    print("=== STARTING-LINEUP VALUE (what actually scores) ===")
    print(f"  before  {before_total:>7.0f}   floor {before_floor:>6.0f}")
    print(f"  after   {after_total:>7.0f}   floor {after_floor:>6.0f}")
    print(f"  change  {after_total - before_total:>+7.0f}   "
          f"floor {after_floor - before_floor:>+6.0f}")

    gained = after_used - before_used
    lost = before_used - after_used
    if gained:
        print(f"\n  enters your lineup: {', '.join(sorted(gained))}")
    if lost:
        print(f"  drops out of lineup: {', '.join(sorted(lost))}")

    delta = after_total - before_total
    fdelta = after_floor - before_floor
    print()
    if delta > 15 and fdelta > 0:
        verdict = "STRONG ACCEPT -- clear gain in both value and floor."
    elif delta > 0 and fdelta >= -5:
        verdict = "ACCEPT -- modest gain, floor holds."
    elif delta > 0 > fdelta:
        verdict = ("MARGINAL -- gains points but lowers your floor. "
                   "Only take it if you need ceiling.")
    elif delta > -8:
        verdict = "DECLINE -- roughly neutral, not worth the roster churn."
    else:
        verdict = "REJECT -- you lose real starting-lineup value."
    print(f"  VERDICT: {verdict}")

    if raw_in - raw_out < 0 < delta:
        print("\n  NOTE: this looks like a LOSS on raw projections but is a win "
              "on adjusted value.\n  That asymmetry is the trade to pursue -- "
              "the other manager sees the raw number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
