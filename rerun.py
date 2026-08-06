#!/usr/bin/env python3
"""
Full regression + re-analysis suite.

Run this after ANY change to the model, and before trusting any conclusion.
It re-validates the injury model against source data, re-runs every strategy
comparison on a freshly pulled board, and flags if a headline finding flipped.

    python3 rerun.py            # standard
    python3 rerun.py --quick    # fewer trials, faster
    python3 rerun.py --no-refresh   # use cached board

Exit code 0 = all checks passed and findings are stable.
Exit code 1 = something failed or a conclusion changed. Read the output.
"""
import argparse
import statistics
import subprocess
import sys
import time

import espn_client as api
import simulate as sim
from construction import CONSTRUCTIONS, make_construction_strategy

# Findings this suite is guarding. If one of these flips, the suite says so
# loudly instead of quietly reporting new numbers.
EXPECTED = {
    "double_tap_is_worst": "QB@R4+R5 should be the worst QB-timing strategy",
    "zero_rb_is_worst": "zero-RB should be the worst roster construction",
    "best_available_top": "best-available should be at or near the top",
}

CALIBRATED = {"noise": 31.5, "qb_urgency": 7.6}


def hdr(n, title):
    print(f"\n{'=' * 68}\n{n}. {title}\n{'=' * 68}")


def run_validation():
    hdr(1, "INJURY MODEL VALIDATION (vs ProFootballLogic 2015)")
    r = subprocess.run([sys.executable, "validate_injury.py", "12000"],
                       capture_output=True, text=True, timeout=300)
    print(r.stdout.strip())
    passed = "ALL CHECKS PASSED" in r.stdout
    if not passed:
        print("\n  >>> INJURY MODEL FAILED VALIDATION. Fix before trusting output.")
    return passed


def paired_compare(board, cfg, variants, n, label, ref_key=None):
    """Run paired comparison. Returns {name: (mean, delta_vs_ref, t)}."""
    results = {}
    ranks = {}
    for name, strat in variants.items():
        s_, r_ = [], []
        for seed in range(n):
            sc, rk, _, _, _ = sim.run_draft(board, cfg, strat, seed=seed,
                                            params=CALIBRATED)
            s_.append(sc)
            r_.append(rk)
        results[name] = s_
        ranks[name] = r_

    ref = ref_key or list(variants)[0]
    out = {}
    for name in variants:
        deltas = [a - b for a, b in zip(results[name], results[ref])]
        mean_d = statistics.mean(deltas)
        se = (statistics.stdev(deltas) / (len(deltas) ** 0.5)) if len(deltas) > 1 else 1
        t = mean_d / se if se else 0
        top3 = sum(1 for x in ranks[name] if x <= 3) / n * 100
        out[name] = {"mean": statistics.mean(results[name]), "delta": mean_d,
                     "t": t, "top3": top3}

    print(f"  {label}  (n={n}, ref={ref})\n")
    print(f"  {'strategy':<18}{'mean':>9}{'top3':>8}{'vs ref':>10}{'t':>8}")
    for name, d in sorted(out.items(), key=lambda kv: -kv[1]["mean"]):
        sig = "" if name == ref else ("  SIG" if abs(d["t"]) > 2.5 else "  ns")
        print(f"  {name:<18}{d['mean']:9.1f}{d['top3']:7.1f}%"
              f"{d['delta']:+10.1f}{d['t']:+8.2f}{sig}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    args = ap.parse_args()

    n_qb = 100 if args.quick else 250
    n_con = 60 if args.quick else 150

    t0 = time.time()
    ok = True
    findings = {}

    ok &= run_validation()

    cfg = api.load_config("config.json")

    hdr(2, "BOARD")
    board = api.load_board(cfg, refresh=not args.no_refresh)
    print(f"  {len(board)} players"
          f"{' (freshly pulled)' if not args.no_refresh else ' (cached)'}")
    flagged = [p for p in board if p["injury"] not in ("ACTIVE", None)]
    print(f"  {len(flagged)} carrying an injury designation")
    for p in board[:5]:
        inj = "" if p["injury"] in ("ACTIVE", None) else f"  [{p['injury']}]"
        print(f"    {p['name']:<24}{p['pos']:<5}ADP {p['adp']:<6}proj {p['proj']}{inj}")

    hdr(3, "QB TIMING")
    qb_variants = {f"QB@R{a}+R{b}": sim.make_strategy(a, b, cfg)
                   for a, b in [(4, 6), (4, 5), (6, 7), (8, 9)]}
    qb_res = paired_compare(board, cfg, qb_variants, n_qb, "QB timing")
    worst_qb = min(qb_res, key=lambda k: qb_res[k]["mean"])
    findings["double_tap_is_worst"] = (worst_qb == "QB@R4+R5")

    hdr(4, "ROSTER CONSTRUCTION")
    con_variants = {name: make_construction_strategy(plan, cfg)
                    for name, plan in CONSTRUCTIONS.items()}
    con_res = paired_compare(board, cfg, con_variants, n_con,
                             "roster construction", ref_key="balanced")
    ordered = sorted(con_res, key=lambda k: -con_res[k]["mean"])
    # zero-RB and WR-heavy sit ~7 pts apart, which n=60 (--quick) cannot resolve.
    # Assert zero-RB is in the bottom two rather than strictly last, so a quick
    # run does not report a false flip. The full run checks the stricter version.
    bottom = ordered[-2:] if args.quick else ordered[-1:]
    findings["zero_rb_is_worst"] = ("zero-RB" in bottom)
    findings["best_available_top"] = ("best-available" in ordered[:2])

    hdr(5, "INJURY RISK — TOP OF BOARD (the 1.01 decision)")
    import injury
    rows = []
    for p in board[:8]:
        if p["pos"] in ("QB", "RB", "WR", "TE") and p.get("proj"):
            rows.append(injury.risk_profile(p, weeks=17,
                                            trials=1500 if args.quick else 4000,
                                            seed=42))
    rows.sort(key=lambda r: -r["mean"])
    print(f"  {'player':<24}{'pos':<5}{'raw':>6}{'adj':>7}{'p10':>7}"
          f"{'miss':>6}{'P(4+)':>7}{'P(full)':>9}")
    for r in rows:
        print(f"  {r['name']:<24}{r['pos']:<5}{r['proj_raw'] or 0:6.0f}{r['mean']:7.0f}"
              f"{r['p10']:7.0f}{r['mean_missed']:6.1f}"
              f"{r['p_misses_4plus']*100:6.0f}%{r['p_full_season']*100:8.0f}%")
    if rows:
        print(f"\n  >>> RECOMMENDED 1.01: {rows[0]['name']} "
              f"(adj {rows[0]['mean']:.0f}, floor {rows[0]['p10']:.0f})")
        raw_best = max(rows, key=lambda r: r["proj_raw"] or 0)
        if raw_best["name"] != rows[0]["name"]:
            print(f"      NOTE: {raw_best['name']} has the higher RAW projection "
                  f"({raw_best['proj_raw']:.0f}) but ranks lower on injury risk.")

    hdr(6, "FINDING STABILITY")
    for key, desc in EXPECTED.items():
        held = findings.get(key)
        print(f"  [{'HOLDS' if held else 'CHANGED'}] {desc}")
        if not held:
            ok = False

    print(f"\n{'=' * 68}")
    print(f"  completed in {time.time() - t0:.0f}s")
    if ok:
        print("  ALL CHECKS PASSED — findings stable, model validated.")
        return 0
    print("  ATTENTION: a check failed or a headline finding changed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
