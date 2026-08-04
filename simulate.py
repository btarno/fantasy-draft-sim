"""
Draft simulation engine.

Models a snake draft where opponents pick off ADP with gaussian noise plus
positional-need pressure. Your own picks follow a configurable strategy so
different approaches can be compared under identical conditions.

IMPORTANT: the opponent model is an ASSUMPTION, not measured behavior.
See README "Limitations" before trusting any output.
"""
import random
from collections import defaultdict

# Injury multipliers applied to projections when valuing a player.
# Multipliers applied to a player's projection based on ESPN's injury designation.
#
# IMPORTANT: this is a STATIC HAIRCUT, not injury simulation. It discounts players
# who are ALREADY flagged hurt at the time the board was pulled. It does not model
# new injuries occurring during the season, games missed, or replacement-level
# fill-in production. See README "Limitations".
INJURY_DISCOUNT = {
    "INJURY_RESERVE": 0.15,
    "OUT": 0.35,
    "DOUBTFUL": 0.6,
    "QUESTIONABLE": 0.92,
}


def val(p):
    """Injury-adjusted projected points. Missing projections score 0."""
    mult = INJURY_DISCOUNT.get(p.get("injury") or "ACTIVE", 1.0)
    return (p.get("proj") or 0) * mult


# ---------------------------------------------------------------------------
# Full-season Monte Carlo scoring (opt-in, see injury.py)
#
# val() above is the fast static haircut used during the draft itself. For
# EVALUATING a finished roster, monte_carlo_score() simulates each player's
# season week by week -- who gets hurt, for how long, and what the replacement
# produces. That is the honest way to compare a fragile roster to a durable one.
# ---------------------------------------------------------------------------

def monte_carlo_score(roster, cfg, trials=300, seed=None, weeks=17):
    """
    Distribution of season outcomes for one roster.

    Each trial simulates availability for every player, then scores the best
    lineup available that week. Returns dict with mean/median/p10/p90.
    """
    import random as _random

    import injury as _inj

    rng = _random.Random(seed)
    lineup = dict(cfg["lineup"])
    flex_pos = cfg["flex_positions"]
    n_flex = cfg["flex"]

    totals = []
    for _ in range(trials):
        # Per-player availability mask for this simulated season
        avail_weeks = {}
        for p in roster:
            avail_weeks[p["name"]] = _inj.simulate_availability(
                p.get("pos", "?"), p.get("injury"), weeks, rng)

        # Approximate weekly play: a player available A of W weeks contributes
        # his per-game rate in those weeks. Build per-week availability by
        # spreading missed games randomly (order does not affect the total for
        # a fixed lineup, but it does affect WHO fills in).
        season = 0.0
        for wk in range(weeks):
            playing = []
            for p in roster:
                a = avail_weeks[p["name"]]
                if rng.random() < a / weeks:
                    playing.append(p)
            season += _best_week(playing, lineup, flex_pos, n_flex, weeks)
        totals.append(season)

    totals.sort()
    n = len(totals)
    return {
        "mean": sum(totals) / n,
        "median": totals[n // 2],
        "p10": totals[n // 10],
        "p90": totals[(n * 9) // 10],
    }


def _best_week(playing, lineup, flex_pos, n_flex, weeks):
    """Best single-week score from the players available that week."""
    by_pos = defaultdict(list)
    for p in playing:
        by_pos[p["pos"]].append((p.get("proj") or 0) / weeks)
    for k in by_pos:
        by_pos[k].sort(reverse=True)

    total, used = 0.0, defaultdict(int)
    for pos, count in lineup.items():
        pool = by_pos.get(pos, [])
        for i in range(min(count, len(pool))):
            total += pool[i]
            used[pos] += 1

    # FLEX from whatever is left over
    leftovers = []
    for pos in flex_pos:
        leftovers.extend(by_pos.get(pos, [])[used[pos]:])
    leftovers.sort(reverse=True)
    total += sum(leftovers[:n_flex])
    return total


def counts(roster):
    c = defaultdict(int)
    for p in roster:
        c[p["pos"]] += 1
    return c


def snake_order(teams, rounds):
    """Yield (round, slot) in snake order."""
    order = []
    for rnd in range(1, rounds + 1):
        slots = list(range(1, teams + 1))
        if rnd % 2 == 0:
            slots.reverse()
        order.extend((rnd, s) for s in slots)
    return order


# ------------------------------------------------------------- opponents

def opponent_pick(avail, roster, rnd, cfg, params, archetype="normal", qb_run=0):
    """
    Score every available player and take the lowest.

      score = ADP + gaussian(0, noise)

    Adjustments:
      - unmet starting requirement lowers the score (drafted sooner)
      - QB urgency ramps by round -- models multi-QB league pressure
      - qb_run adds cascade pressure when QBs are flying off the board
      - K/D-ST heavily penalized before the last rounds
    """
    c = counts(roster)
    need = dict(cfg["lineup"])
    noise = params["noise"]
    qb_urgency = params["qb_urgency"]

    if archetype == "homer":       # erratic, reaches for favorites
        noise *= 2.0
    elif archetype == "sharp":     # disciplined, sticks near ADP
        noise *= 0.5
    elif archetype == "qb_panic":  # badly overvalues QBs
        qb_urgency *= 2.0

    scored = []
    for p in avail[:40]:
        pos = p["pos"]
        score = p["adp"] + random.gauss(0, noise)

        if pos in need and c[pos] < need[pos]:
            if pos == "QB":
                score -= 6 + rnd * qb_urgency
                score -= qb_run * 8
            elif pos in ("RB", "WR"):
                score -= 8
            elif pos == "TE":
                score -= 5

        if pos in ("K", "D/ST") and rnd < cfg["rounds"] - 2:
            score += 250
        if pos == "QB" and c["QB"] >= need.get("QB", 1):
            score += 60

        scored.append((score, p))

    scored.sort(key=lambda x: x[0])
    return scored[0][1]


# ------------------------------------------------------------- strategy

def make_strategy(qb1_round, qb2_round, cfg, rb_floor=3, elite_te_threshold=235):
    """
    Build a pick function.

      qb1_round / qb2_round -- earliest round to take QB1 / QB2
      rb_floor              -- minimum RBs to secure by mid-draft
      elite_te_threshold    -- projection above which an early TE is worth it
    """
    need = dict(cfg["lineup"])
    flex_pos = cfg["flex_positions"]
    last_rounds = cfg["rounds"] - 1

    def pick(avail, roster, rnd):
        c = counts(roster)
        of = lambda pos: [p for p in avail if p["pos"] == pos]

        # Fill K / D-ST only at the very end
        if rnd >= last_rounds:
            for pos in ("D/ST", "K"):
                if c[pos] < need.get(pos, 0) and of(pos):
                    return of(pos)[0]

        # QB timing
        if c["QB"] < 1 and rnd >= qb1_round and of("QB"):
            return of("QB")[0]
        if c["QB"] < need.get("QB", 1) and rnd >= qb2_round and of("QB"):
            return of("QB")[0]

        # Early rounds: best RB/WR by VALUE, with an elite-TE exception.
        # NOTE: must rank by val(), not ADP order -- otherwise the injury
        # discount is computed and then ignored at the most important picks.
        if rnd <= 3:
            cands = [p for p in avail if p["pos"] in ("RB", "WR", "TE")][:8]
            elite_te = [p for p in cands if p["pos"] == "TE" and val(p) >= elite_te_threshold]
            if elite_te and c["TE"] == 0 and rnd >= 2:
                return max(elite_te, key=val)
            rbwr = [p for p in cands if p["pos"] in ("RB", "WR")]
            return max(rbwr, key=val) if rbwr else max(cands, key=val)

        # RB floor -- don't let the RB room go empty chasing WRs
        if rb_floor and rnd >= 6 and c["RB"] < rb_floor:
            rbs = of("RB")
            if rbs:
                best_rb = max(rbs[:8], key=val)
                pool = [p for p in avail if p["pos"] in flex_pos][:8]
                best_any = max(pool, key=val) if pool else None
                if best_any is None or val(best_rb) >= val(best_any) * 0.82:
                    return best_rb

        if rnd >= 8 and c["TE"] < need.get("TE", 1) and of("TE"):
            return of("TE")[0]

        cands = [p for p in avail if p["pos"] in flex_pos]
        return max(cands[:8], key=val) if cands else avail[0]

    return pick


# ------------------------------------------------------------- scoring

def lineup_score(roster, cfg):
    """Sum of the optimal starting lineup, injury-adjusted."""
    by_pos = defaultdict(list)
    for p in roster:
        by_pos[p["pos"]].append(p)
    for k in by_pos:
        by_pos[k].sort(key=lambda x: -val(x))

    total, used = 0.0, set()
    for pos, n in cfg["lineup"].items():
        if pos in ("K", "D/ST"):
            continue
        for p in by_pos[pos][:n]:
            total += val(p)
            used.add(p["name"])

    flex = [p for p in roster if p["pos"] in cfg["flex_positions"] and p["name"] not in used]
    flex.sort(key=lambda x: -val(x))
    for p in flex[:cfg["flex"]]:
        total += val(p)

    for pos in ("D/ST", "K"):
        if by_pos[pos]:
            total += val(by_pos[pos][0])

    return total


def optimal_lineup(roster, cfg):
    """Return [(slot_label, player)] for display."""
    by_pos = defaultdict(list)
    for p in roster:
        by_pos[p["pos"]].append(p)
    for k in by_pos:
        by_pos[k].sort(key=lambda x: -val(x))

    out, used = [], set()
    for pos, n in cfg["lineup"].items():
        if pos in ("K", "D/ST"):
            continue
        for p in by_pos[pos][:n]:
            out.append((pos, p))
            used.add(p["name"])

    flex = [p for p in roster if p["pos"] in cfg["flex_positions"] and p["name"] not in used]
    flex.sort(key=lambda x: -val(x))
    for p in flex[:cfg["flex"]]:
        out.append(("FLEX", p))

    for pos in ("D/ST", "K"):
        if by_pos[pos]:
            out.append((pos, by_pos[pos][0]))

    return out


# ------------------------------------------------------------- run

DEFAULT_PARAMS = {"noise": 12.0, "qb_urgency": 2.2}


def run_draft(board, cfg, strategy, seed=0, params=None, archetype_mix=None):
    """
    Run one full draft.

    Returns (my_score, my_rank, my_roster, pick_log, all_scores).
    """
    params = params or DEFAULT_PARAMS
    random.seed(seed)

    avail = [dict(p) for p in board]
    teams, my_slot = cfg["teams"], cfg["my_slot"]
    rosters = {i: [] for i in range(1, teams + 1)}

    # Assign archetypes to opponents
    arch = {}
    slots = [s for s in range(1, teams + 1) if s != my_slot]
    random.shuffle(slots)
    i = 0
    for name, n in (archetype_mix or {}).items():
        for _ in range(n):
            if i < len(slots):
                arch[slots[i]] = name
                i += 1
    for s in slots:
        arch.setdefault(s, "normal")

    log, qb_run = [], 0
    for rnd, slot in snake_order(teams, cfg["rounds"]):
        if not avail:
            break
        if slot == my_slot:
            choice = strategy(avail, rosters[slot], rnd)
        else:
            choice = opponent_pick(avail, rosters[slot], rnd, cfg, params, arch[slot], qb_run)

        qb_run = min(3, qb_run + 1) if choice["pos"] == "QB" else max(0, qb_run - 1)
        avail.remove(choice)
        rosters[slot].append(choice)
        if slot == my_slot:
            log.append((rnd, choice))

    scores = {t: lineup_score(r, cfg) for t, r in rosters.items()}
    rank = sorted(scores, key=lambda t: -scores[t]).index(my_slot) + 1
    return scores[my_slot], rank, rosters[my_slot], log, scores
