"""
Roster construction strategies.

QB timing is one lever. This module tests the bigger one: what you do with
your first 3-5 picks, and how you balance RB vs WR given FLEX slots.

Each builder returns a pick function compatible with simulate.run_draft.
"""
from collections import defaultdict

from simulate import val, counts


def _by_pos(avail, pos):
    return [p for p in avail if p["pos"] == pos]


def make_construction_strategy(
    plan,
    cfg,
    qb1_round=5,
    qb2_round=7,
    rb_floor=3,
    wr_floor=3,
    elite_te_threshold=235,
):
    """
    plan: list of position groups for the early rounds, e.g.
        ["RB","RB","WR"]      -> Robust RB
        ["WR","WR","RB"]      -> Zero-ish RB
        ["BEST","BEST","BEST"]-> pure best-available
        ["RB","WR","BEST"]    -> balanced

    Tokens:
        "RB" / "WR" / "TE"  force that position
        "BEST"              best available among flex-eligible
        "ELITE_TE"          take an elite TE if one is there, else BEST
    """
    need = dict(cfg["lineup"])
    flex_pos = cfg["flex_positions"]
    last_rounds = cfg["rounds"] - 1

    def pick(avail, roster, rnd):
        c = counts(roster)
        of = lambda pos: _by_pos(avail, pos)

        # End of draft: fill K / D-ST
        if rnd >= last_rounds:
            for pos in ("D/ST", "K"):
                if c[pos] < need.get(pos, 0) and of(pos):
                    return of(pos)[0]

        # QB timing (same lever as before, held constant while we vary construction)
        if c["QB"] < 1 and rnd >= qb1_round and of("QB"):
            return of("QB")[0]
        if c["QB"] < need.get("QB", 1) and rnd >= qb2_round and of("QB"):
            return of("QB")[0]

        # ---- the construction plan governs the early rounds
        if rnd <= len(plan):
            token = plan[rnd - 1]
            if token == "ELITE_TE":
                te = [p for p in of("TE") if val(p) >= elite_te_threshold]
                if te and c["TE"] == 0:
                    return te[0]
                token = "BEST"
            if token in ("RB", "WR", "TE"):
                cands = of(token)
                if cands:
                    return max(cands[:5], key=val)
            # BEST (or forced position exhausted)
            pool = [p for p in avail if p["pos"] in flex_pos][:6]
            if pool:
                return max(pool, key=val)

        # ---- after the plan: enforce floors so no room goes empty
        if rb_floor and c["RB"] < rb_floor:
            rbs = of("RB")
            if rbs:
                best_rb = max(rbs[:8], key=val)
                pool = [p for p in avail if p["pos"] in flex_pos][:8]
                best_any = max(pool, key=val) if pool else None
                if best_any is None or val(best_rb) >= val(best_any) * 0.82:
                    return best_rb

        if wr_floor and c["WR"] < wr_floor:
            wrs = of("WR")
            if wrs:
                best_wr = max(wrs[:8], key=val)
                pool = [p for p in avail if p["pos"] in flex_pos][:8]
                best_any = max(pool, key=val) if pool else None
                if best_any is None or val(best_wr) >= val(best_any) * 0.82:
                    return best_wr

        if rnd >= 8 and c["TE"] < need.get("TE", 1) and of("TE"):
            return of("TE")[0]

        cands = [p for p in avail if p["pos"] in flex_pos]
        return max(cands[:8], key=val) if cands else avail[0]

    return pick


# Named constructions worth testing.
CONSTRUCTIONS = {
    "robust-RB":      ["RB", "RB", "RB"],
    "RB-RB-WR":       ["RB", "RB", "WR"],
    "RB-WR-RB":       ["RB", "WR", "RB"],
    "balanced":       ["RB", "WR", "BEST"],
    "WR-heavy":       ["WR", "WR", "WR"],
    "WR-WR-RB":       ["WR", "WR", "RB"],
    "zero-RB":        ["WR", "WR", "WR", "WR"],
    "best-available": ["BEST", "BEST", "BEST"],
    "elite-TE":       ["BEST", "ELITE_TE", "BEST"],
    "hero-RB":        ["RB", "WR", "WR"],
}
