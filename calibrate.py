#!/usr/bin/env python3
"""
Calibrate the opponent model against a league's REAL past draft.

Instead of assuming how opponents behave, measure it:
  - noise        = stdev of (actual pick position - player's ADP)
  - qb_urgency   = how much earlier QBs went vs their ADP, by round
  - reach/fall   = per-manager tendencies

Usage:  python3 calibrate.py [--season 2025]
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict

import requests

import espn_client as api


def fetch_draft(cfg, season):
    ck = api.load_cookies(cfg)
    url = f"{api.API_HOST}/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{cfg['league_id']}"
    r = requests.get(url, params={"view": ["mDraftDetail", "mTeam"]}, cookies=ck,
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
    if r.status_code != 200:
        sys.exit(f"No draft data for {season} (HTTP {r.status_code})")
    d = r.json()
    picks = d.get("draftDetail", {}).get("picks", [])
    if not picks:
        sys.exit(f"Season {season} has no recorded picks.")
    teams = {t["id"]: (t.get("name") or f"Team {t['id']}") for t in d.get("teams", [])}
    return picks, teams


def fetch_player_ranks(cfg, season, player_ids):
    """Get each drafted player's ADP/rank for that season."""
    ck = api.load_cookies(cfg)
    url = f"{api.API_HOST}/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{cfg['league_id']}"
    filt = {"players": {"limit": 600,
                        "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "PPR"}}}
    r = requests.get(url, params={"scoringPeriodId": 0, "view": "kona_player_info"},
                     cookies=ck,
                     headers={"User-Agent": "Mozilla/5.0", "x-fantasy-filter": json.dumps(filt)},
                     timeout=60)
    if r.status_code != 200:
        sys.exit(f"Player fetch failed: HTTP {r.status_code}")

    out = {}
    adp_values = set()
    for e in r.json().get("players", []):
        p = e.get("player", {})
        own = p.get("ownership") or {}
        ranks = (p.get("draftRanksByRankType") or {}).get("PPR") or {}
        adp = own.get("averageDraftPosition")
        if adp:
            adp_values.add(round(adp, 1))
        out[p.get("id")] = {
            "name": p.get("fullName"),
            "pos": api.POS.get(p.get("defaultPositionId"), "?"),
            "adp": adp,
            "rank": ranks.get("rank"),
        }

    # ESPN stops serving real historical ADP after a season closes -- it returns a
    # single flat placeholder for every player. Detect that and fall back to
    # PPR draft rank, which IS retained.
    adp_is_placeholder = len(adp_values) <= 2
    if adp_is_placeholder:
        print(f"  NOTE: ESPN returned a flat placeholder ADP ({adp_values or 'none'}) for "
              f"{season}.\n        Falling back to PPR draft rank as the reference.\n")
        for v in out.values():
            v["adp"] = None

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None,
                    help="past season to calibrate from (default: most recent available)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = api.load_config(args.config)
    season = args.season or (cfg["season"] - 1)

    picks, teams = fetch_draft(cfg, season)
    ranks = fetch_player_ranks(cfg, season, [p["playerId"] for p in picks])

    print(f"=== CALIBRATION FROM {season} DRAFT ({len(picks)} picks) ===\n")

    deltas = []          # actual overall pick - ADP (skill positions only)
    all_deltas = []
    qb_deltas = defaultdict(list)
    pos_deltas = defaultdict(list)
    by_team = defaultdict(list)
    matched = 0
    qb_by_round = defaultdict(int)
    max_round = 0

    SKILL = ("QB", "RB", "WR", "TE")

    for pk in picks:
        info = ranks.get(pk["playerId"])
        if not info:
            continue
        # Prefer ADP; fall back to PPR rank when ADP is missing for old seasons
        ref = info["adp"] or info["rank"]
        if not ref:
            continue
        matched += 1
        max_round = max(max_round, pk["roundId"])
        d = pk["overallPickNumber"] - ref
        all_deltas.append(d)
        pos_deltas[info["pos"]].append(d)
        # K and D/ST are always taken hundreds of picks "early" vs their rank --
        # including them destroys the noise estimate. Skill positions only.
        if info["pos"] in SKILL:
            deltas.append(d)
            by_team[pk["teamId"]].append(d)
        if info["pos"] == "QB":
            qb_deltas[pk["roundId"]].append(d)
            qb_by_round[pk["roundId"]] += 1

    if matched < 20:
        sys.exit(f"Only matched {matched} picks to ADP — not enough to calibrate. "
                 f"ESPN may no longer serve {season} ADP.")

    noise = statistics.stdev(deltas)
    print(f"Matched {matched}/{len(picks)} picks | {max_round} rounds\n")
    if max_round != cfg["rounds"]:
        print(f"  ⚠️  {season} used {max_round} rounds but config.json says {cfg['rounds']}.")
        print(f"      Check whether roster size changed.\n")
    print(f"  MEASURED NOISE (skill positions only): {noise:.1f}")
    print(f"    -> use  noise={noise:.1f}  in the sim   (default assumption was 12.0)")
    print(f"  median deviation: {statistics.median(deltas):+.1f} picks "
          f"({'drafts near consensus' if abs(statistics.median(deltas)) < 8 else 'systematic bias'})")

    print("\n  BY POSITION (negative = drafted EARLIER than ADP):")
    for pos in ("QB", "RB", "WR", "TE", "K", "D/ST"):
        v = pos_deltas.get(pos)
        if v and len(v) >= 3:
            print(f"    {pos:<5} n={len(v):<4} mean {statistics.mean(v):+7.1f}   "
                  f"median {statistics.median(v):+7.1f}")

    if qb_deltas:
        print("\n  QB URGENCY BY ROUND (how far ahead of ADP QBs went):")
        slope_pts = []
        for rnd in sorted(qb_deltas):
            v = qb_deltas[rnd]
            m = statistics.mean(v)
            print(f"    R{rnd:<3} n={len(v):<3} mean {m:+7.1f}")
            slope_pts.append((rnd, -m))
        # crude urgency slope: how much earlier per round
        if len(slope_pts) >= 3:
            xs = [x for x, _ in slope_pts]
            ys = [y for _, y in slope_pts]
            xm, ym = statistics.mean(xs), statistics.mean(ys)
            denom = sum((x - xm) ** 2 for x in xs)
            slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denom if denom else 0
            print(f"\n    -> estimated qb_urgency ~= {max(0, slope):.1f}  (default assumption was 2.2)")

        print("\n  QBs DRAFTED PER ROUND:")
        for rnd in sorted(qb_by_round):
            print(f"    R{rnd:<3} {'#' * qb_by_round[rnd]} ({qb_by_round[rnd]})")

    print("\n  PER-MANAGER TENDENCY (negative = reaches / drafts early):")
    rows = []
    for tid, v in by_team.items():
        if len(v) >= 5:
            rows.append((statistics.mean(v), statistics.stdev(v), len(v), teams.get(tid, tid)))
    rows.sort()
    for mean, sd, n, name in rows:
        tag = "reacher" if mean < -6 else ("value hunter" if mean > 6 else "near ADP")
        print(f"    {name:<26} mean {mean:+7.1f}  stdev {sd:6.1f}  n={n:<3} {tag}")

    print(f"\n=== SUGGESTED SIM PARAMS ===")
    print(f"    noise = {noise:.1f}")
    print(f"    (pass to ffsim via --noise once wired, or edit DEFAULT_PARAMS)")


if __name__ == "__main__":
    main()
