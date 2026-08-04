#!/usr/bin/env python3
"""
ffsim -- ESPN fantasy draft simulator and strategy tester.

Commands:
  league     Show league settings, draft date, and your pick numbers
  board      Show the current ADP board
  mock       Run a single mock draft and show the resulting roster
  compare    Paired statistical test across QB-timing strategies
  sweep      Sensitivity sweep across opponent-model parameters
"""
import argparse
import datetime
import statistics
import sys
from collections import Counter, defaultdict

import espn_client as api
import simulate as sim


def cmd_league(cfg, args):
    info = api.get_league_info(cfg)
    s = info.get("settings", {})
    draft = s.get("draftSettings", {})
    print(f"=== {s.get('name')} ===")
    print(f"Season {cfg['season']} | {len(info.get('teams', []))} teams")

    if draft.get("date"):
        when = datetime.datetime.fromtimestamp(draft["date"] / 1000)
        print(f"Draft: {when:%A %b %d, %Y at %I:%M %p} ({draft.get('type')}, "
              f"{draft.get('timePerSelection')}s/pick)")

    order = draft.get("pickOrder") or []
    names = {t["id"]: t.get("name") or f"Team {t['id']}" for t in info.get("teams", [])}
    if order:
        print("\nRound 1 order:")
        my_slot = None
        for i, tid in enumerate(order, 1):
            mark = ""
            if cfg.get("my_team_id") and tid == cfg["my_team_id"]:
                mark = "   <-- YOU"
                my_slot = i
            print(f" {i:2d}. {names.get(tid, tid)}{mark}")
        if my_slot:
            teams, rounds = cfg["teams"], cfg["rounds"]
            picks = []
            for rnd in range(1, rounds + 1):
                slot = my_slot if rnd % 2 == 1 else teams + 1 - my_slot
                picks.append(f"R{rnd}.{slot:02d}(#{(rnd - 1) * teams + slot})")
            print("\nYour picks:")
            for i in range(0, len(picks), 6):
                print("  " + "  ".join(picks[i:i + 6]))


def cmd_board(cfg, args):
    board = api.load_board(cfg, refresh=args.refresh, limit=args.limit)
    rows = [p for p in board if not args.pos or p["pos"] == args.pos.upper()]
    print(f"{'#':>4} {'PLAYER':<24}{'POS':<5}{'TM':<5}{'ADP':<8}{'PROJ':<8}INJ")
    for i, p in enumerate(rows[:args.top], 1):
        inj = "" if p["injury"] in ("ACTIVE", None) else p["injury"]
        print(f"{i:>4} {p['name']:<24}{p['pos']:<5}{p['team']:<5}"
              f"{p['adp']:<8}{str(p['proj']):<8}{inj}")


def cmd_mock(cfg, args):
    board = api.load_board(cfg, refresh=args.refresh)
    strategy = sim.make_strategy(args.qb1, args.qb2, cfg, rb_floor=args.rb_floor)
    score, rank, roster, log, all_scores = sim.run_draft(board, cfg, strategy, seed=args.seed)

    print(f"=== MOCK DRAFT (seed {args.seed}) — slot {cfg['my_slot']} ===")
    print(f"    QB timing: R{args.qb1} / R{args.qb2}   RB floor: {args.rb_floor}\n")
    for rnd, p in log:
        inj = "" if p["injury"] in ("ACTIVE", None) else f" [{p['injury']}]"
        print(f"  R{rnd:<3}{p['name']:<24}{p['pos']:<5}{p['team']:<5}"
              f"(ADP {p['adp']}, proj {p['proj']}){inj}")

    print("\n=== STARTING LINEUP ===")
    for slot, p in sim.optimal_lineup(roster, cfg):
        print(f"  {slot:<6}{p['name']:<24}{p['proj']}")
    print(f"\n  Projected total: {score:.1f}    Finish: #{rank} of {cfg['teams']}")


def cmd_compare(cfg, args):
    """Paired test: identical seeds -> identical opponents. Only strategy varies."""
    board = api.load_board(cfg, refresh=args.refresh)
    variants = {}
    for spec in args.strategies.split(","):
        q1, q2 = spec.strip().split("/")
        variants[f"QB@R{q1}+R{q2}"] = sim.make_strategy(int(q1), int(q2), cfg,
                                                        rb_floor=args.rb_floor)

    results = {k: [] for k in variants}
    ranks = {k: [] for k in variants}
    for seed in range(args.n):
        for name, strategy in variants.items():
            sc, rk, _, _, _ = sim.run_draft(board, cfg, strategy, seed=seed)
            results[name].append(sc)
            ranks[name].append(rk)

    print(f"=== PAIRED COMPARISON — {args.n} identical drafts per strategy ===\n")
    print(f"  {'strategy':<16}{'mean pts':>10}{'stdev':>9}{'top3':>8}{'1st':>8}")
    for name in variants:
        v, r = results[name], ranks[name]
        top3 = sum(1 for x in r if x <= 3) / len(r) * 100
        first = sum(1 for x in r if x == 1) / len(r) * 100
        print(f"  {name:<16}{statistics.mean(v):10.1f}{statistics.stdev(v):9.1f}"
              f"{top3:7.1f}%{first:7.1f}%")

    ref = list(variants)[0]
    print(f"\n=== PAIRED DELTAS vs {ref} ===")
    print("  (same seed = same opponents, so this isolates the strategy effect)\n")
    for name in variants:
        if name == ref:
            continue
        deltas = [a - b for a, b in zip(results[name], results[ref])]
        mean = statistics.mean(deltas)
        stderr = statistics.stdev(deltas) / (len(deltas) ** 0.5)
        t = mean / stderr if stderr else 0
        verdict = "SIGNIFICANT" if abs(t) > 2.5 else "not significant (noise)"
        pct = mean / statistics.mean(results[ref]) * 100
        print(f"  {name:<16} {mean:+8.1f} pts ({pct:+.2f}%)  t={t:+6.2f}  {verdict}")

    print("\n  NOTE: statistical significance != practical significance.")
    print("  A +4 pt edge on a ~2500 pt season is 0.16% — real but meaningless.")


def cmd_sweep(cfg, args):
    """Vary opponent-model parameters. If a finding only holds in one cell, distrust it."""
    board = api.load_board(cfg, refresh=args.refresh)
    variants = {}
    for spec in args.strategies.split(","):
        q1, q2 = spec.strip().split("/")
        variants[f"QB@R{q1}+R{q2}"] = sim.make_strategy(int(q1), int(q2), cfg,
                                                        rb_floor=args.rb_floor)

    scenarios = [
        ("baseline",             {"noise": 12.0, "qb_urgency": 2.2}, None),
        ("disciplined league",   {"noise": 5.0, "qb_urgency": 2.2}, None),
        ("chaotic league",       {"noise": 22.0, "qb_urgency": 2.2}, None),
        ("QBs fall (low panic)", {"noise": 12.0, "qb_urgency": 0.8}, None),
        ("QB run league",        {"noise": 12.0, "qb_urgency": 4.5}, None),
        ("extreme QB panic",     {"noise": 12.0, "qb_urgency": 7.0}, None),
        ("mixed archetypes",     {"noise": 12.0, "qb_urgency": 2.2},
         {"sharp": 4, "homer": 4, "qb_panic": 3}),
    ]

    winners = Counter()
    for label, params, mix in scenarios:
        print(f"\n### {label}   noise={params['noise']} qb_urgency={params['qb_urgency']}")
        rows = []
        for name, strategy in variants.items():
            rk, sc = [], []
            for seed in range(args.n):
                s_, r_, _, _, _ = sim.run_draft(board, cfg, strategy, seed=seed,
                                                params=params, archetype_mix=mix)
                rk.append(r_)
                sc.append(s_)
            top3 = sum(1 for x in rk if x <= 3) / len(rk) * 100
            first = sum(1 for x in rk if x == 1) / len(rk) * 100
            rows.append((top3, first, statistics.mean(sc), name))
        rows.sort(reverse=True)
        for top3, first, pts, name in rows:
            print(f"    {name:<16} top3 {top3:5.1f}%   1st {first:5.1f}%   pts {pts:7.1f}")
        winners[rows[0][3]] += 1

    print("\n\n=== WINNER TALLY ===")
    for name, cnt in winners.most_common():
        print(f"  {cnt:2d}x  {name}")
    print("\n  A strategy that only wins in one or two scenarios is overfit to")
    print("  the opponent model. Trust findings that hold across all of them.")


def cmd_risk(cfg, args):
    """Injury risk profiles: compare players whose projections are close."""
    import injury

    board = api.load_board(cfg, refresh=args.refresh)
    byname = {p["name"].lower(): p for p in board}

    if args.players:
        targets = []
        for q in args.players.split(","):
            q = q.strip().lower()
            hit = byname.get(q) or next(
                (p for p in board if q in p["name"].lower()), None)
            if hit:
                targets.append(hit)
            else:
                print(f"  (no match for '{q}')")
    else:
        targets = [p for p in board if p["pos"] in ("QB", "RB", "WR", "TE")][:args.top]

    print(f"=== INJURY RISK PROFILES ({args.weeks}-week season, "
          f"{args.trials} simulated seasons each) ===\n")
    print(f"  {'player':<24}{'pos':<5}{'raw':>6}{'adj':>7}{'p10':>7}{'p90':>7}"
          f"{'miss':>6}{'P(4+)':>7}{'P(full)':>9}")
    rows = []
    for p in targets:
        r = injury.risk_profile(p, weeks=args.weeks, trials=args.trials, seed=42)
        rows.append(r)
    rows.sort(key=lambda r: -r["mean"])
    for r in rows:
        print(f"  {r['name']:<24}{r['pos']:<5}{r['proj_raw'] or 0:6.0f}{r['mean']:7.0f}"
              f"{r['p10']:7.0f}{r['p90']:7.0f}{r['mean_missed']:6.1f}"
              f"{r['p_misses_4plus']*100:6.0f}%{r['p_full_season']*100:8.0f}%")
    print("\n  raw     = ESPN projection (assumes a fully healthy season)")
    print("  adj     = injury-adjusted, incl. production from a replacement while out")
    print("  p10/p90 = 10th / 90th percentile season outcomes")
    print("  P(4+)   = probability of missing 4 or more games")
    print("\n  Model validated against ProFootballLogic 2015 data "
          "(python3 validate_injury.py)")


def main():
    ap = argparse.ArgumentParser(description="ESPN fantasy draft simulator")
    ap.add_argument("--config", help="path to config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("league", help="show league settings and your picks")
    p.set_defaults(func=cmd_league)

    p = sub.add_parser("board", help="show the ADP board")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--pos", help="filter by position (QB/RB/WR/TE)")
    p.add_argument("--limit", type=int, default=300, help="players to fetch")
    p.add_argument("--refresh", action="store_true", help="re-fetch instead of using cache")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("mock", help="run one mock draft")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--qb1", type=int, default=5, help="earliest round for QB1")
    p.add_argument("--qb2", type=int, default=7, help="earliest round for QB2")
    p.add_argument("--rb-floor", type=int, default=3, dest="rb_floor")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_mock)

    p = sub.add_parser("compare", help="paired statistical test of strategies")
    p.add_argument("-n", type=int, default=400, help="drafts per strategy")
    p.add_argument("--strategies", default="4/6,4/5,6/7,8/9",
                   help="comma-separated qb1/qb2 rounds")
    p.add_argument("--rb-floor", type=int, default=3, dest="rb_floor")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("sweep", help="sensitivity sweep across opponent models")
    p.add_argument("-n", type=int, default=200, help="drafts per cell")
    p.add_argument("--strategies", default="4/6,4/5,6/7,8/9")
    p.add_argument("--rb-floor", type=int, default=3, dest="rb_floor")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("risk", help="injury risk profiles for players")
    p.add_argument("--players", help="comma-separated names to compare")
    p.add_argument("--top", type=int, default=15, help="top N if no names given")
    p.add_argument("--weeks", type=int, default=17)
    p.add_argument("--trials", type=int, default=4000)
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_risk)

    args = ap.parse_args()
    cfg = api.load_config(args.config)
    args.func(cfg, args)


if __name__ == "__main__":
    main()
