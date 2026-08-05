"""
Injury risk modeling for full-season Monte Carlo.

Replaces the old static "haircut" approach (multiply projection by a fudge
factor if the player is flagged hurt today) with an actual week-by-week
simulation of who is available.

DATA SOURCE
-----------
Per-position hazard rates and injury durations come from ProFootballLogic's
2015 league-wide injury tracking study, which followed snaps and injuries for
1,794 players across the season:

    https://www.profootballlogic.com/articles/nfl-injury-rate-analysis/

    Position   Inj/Game   Avg Length (games)   Games Available of 16
    All          4.1%           3.1                  14.2
    QB           2.5%           3.1                  14.9
    RB           5.2%           3.9                  13.3
    TE           4.9%           2.6                  14.2
    WR           4.5%           3.2                  14.0
    OL           3.4%           3.3                  14.4

Key distribution fact from the same study: injury length is heavily right
skewed -- 64% of injuries that cost at least one game cost two or fewer, with
a long tail of season-enders. A geometric draw with an IR tail reproduces that
shape far better than using the 3.1-game mean directly.

CAVEATS
-------
- Single season of data (2015), 16-game schedule. Modern seasons are 17 games;
  hazard is applied per game so this scales, but the source rates are not
  re-measured.
- Rates are league-wide by position, NOT player-specific. Age, injury history,
  and workload are not modeled -- a 30-year-old RB coming off surgery gets the
  same hazard as a 24-year-old who has never missed a snap. Current designation
  is the only player-specific input.
- Kickers/punters were excluded from the source study. They get the league
  average here.
"""
import random

# Per-game probability of suffering an injury that costs at least the next game.
#
# The source article's raw per-game rates (QB 2.5%, RB 5.2%, WR 4.5%, TE 4.9%)
# do NOT reproduce its own games-available column when simulated forward -- they
# come out ~0.5 games too healthy. The source cohort includes players suffering
# multiple separate injuries (882 injuries across 688 players), which a simple
# per-game hazard understates. These values are the raw rates scaled ~1.3x, fitted
# so simulate_availability() matches the observed games-available column.
# Run validate_injury.py after changing anything here.
HAZARD = {
    "QB": 0.0313,   # raw 0.025
    "RB": 0.0697,   # raw 0.052
    "WR": 0.0582,   # raw 0.045
    "TE": 0.0645,   # raw 0.049
    "K": 0.0250,    # not measured in source; kickers are low-contact
    "D/ST": 0.0,    # team unit, never "injured"
}
DEFAULT_HAZARD = 0.053   # raw 0.041 scaled

# Mean games missed per injury, by position (source table).
MEAN_LENGTH = {
    "QB": 3.1,
    "RB": 3.9,
    "WR": 3.2,
    "TE": 2.6,
    "K": 2.5,
    "D/ST": 0.0,
}
DEFAULT_LENGTH = 3.1

# Probability an injury becomes season-ending (IR) rather than short-term.
# Source shows a pronounced spike at ~7 games and a red "never returned" band;
# this is the tail that the geometric body alone cannot produce.
#
# Calibrated: validate_injury.py compares simulated mean length and the
# "64% cost <= 2 games" figure against the source. IR_PROB and the tail cap
# below were tuned until both matched (see that script's PASS output).
IR_PROB = 0.09
IR_GAMES = 13.0   # effective games lost when an injury ends the season

# How a player's CURRENT designation modifies the season.
# starting_out = games already expected to be missed at week 1
# hazard_mult  = elevated reinjury risk (soft tissue recurrence, playing hurt)
CURRENT_STATUS = {
    "ACTIVE":         {"starting_out": 0,  "hazard_mult": 1.00},
    "QUESTIONABLE":   {"starting_out": 1,  "hazard_mult": 1.35},
    "DOUBTFUL":       {"starting_out": 2,  "hazard_mult": 1.50},
    "OUT":            {"starting_out": 4,  "hazard_mult": 1.60},
    "INJURY_RESERVE": {"starting_out": 12, "hazard_mult": 1.60},
}

# Fraction of a starter's per-game production you get from whoever replaces him
# (bench player or streamed pickup). Higher at QB/K where the drop is smaller in
# relative terms; lower at RB/WR where elite production is not replaceable.
REPLACEMENT_FRACTION = {
    "QB": 0.62,
    "RB": 0.50,
    "WR": 0.52,
    "TE": 0.55,
    "K": 0.90,
    "D/ST": 0.90,
}
DEFAULT_REPLACEMENT = 0.55


# Players whose ESPN designation is NOT injury-related.
#
# ESPN tags contract hold-ins and holdouts with the same injury designations it
# uses for actual injuries, which makes the risk model penalize a business
# dispute as if it were a torn ligament. Worse, it is applied inconsistently --
# in Aug 2026 Gibbs and Bijan Robinson were in identical hold-in situations and
# only Gibbs carried a QUESTIONABLE tag.
#
# Map name -> the status to use instead. Review before every draft; a hold-in
# that drags into the season IS a real availability risk, just not a medical one.
NON_INJURY_OVERRIDE = {
    # Aug 2026: contract hold-ins, not medical. Both attending camp, skipping
    # practice while negotiating extensions. Revisit if either drags past cutdowns.
    "Jahmyr Gibbs": ("ACTIVE", "contract hold-in, seeking extension (Aug 2026)"),
}


def effective_status(player):
    """
    The injury status to actually model for this player.

    Applies NON_INJURY_OVERRIDE so contract disputes are not scored as injuries.
    """
    name = player.get("name")
    if name in NON_INJURY_OVERRIDE:
        return NON_INJURY_OVERRIDE[name][0]
    return player.get("injury") or "ACTIVE"


def _geometric(p, rng, cap=20):
    """Number of trials until success, >= 1."""
    n = 1
    while rng.random() > p and n < cap:
        n += 1
    return n


def draw_injury_length(pos, rng):
    """
    Games missed for one injury.

    Right-skewed: a geometric body reproduces the observed "64% of injuries cost
    2 or fewer games", plus an IR tail for season-enders.
    """
    if rng.random() < IR_PROB:
        return 99  # season-ending
    mean = MEAN_LENGTH.get(pos, DEFAULT_LENGTH)
    # Strip the IR tail out of the observed mean to get the short-term body mean.
    body_mean = max(1.1, (mean - IR_PROB * IR_GAMES) / (1.0 - IR_PROB))
    return _geometric(1.0 / body_mean, rng)


def simulate_availability(pos, injury_status, weeks, rng):
    """
    Simulate one player's season week by week.

    Returns the number of weeks the player is AVAILABLE (out of `weeks`).
    """
    if pos == "D/ST":
        return weeks

    status = CURRENT_STATUS.get(injury_status or "ACTIVE", CURRENT_STATUS["ACTIVE"])
    hazard = HAZARD.get(pos, DEFAULT_HAZARD) * status["hazard_mult"]

    out_for = status["starting_out"]
    available = 0

    for _ in range(weeks):
        if out_for > 0:
            out_for -= 1
            continue
        # Healthy this week
        available += 1
        if rng.random() < hazard:
            out_for = draw_injury_length(pos, rng)

    return available


def expected_points(player, weeks, rng, replacement_fraction=None):
    """
    Injury-adjusted season points for one player, INCLUDING the production you
    get from a replacement while he is out.

    This is the key difference from the old static haircut: an injured starter
    does not zero out your lineup slot, you play someone worse.
    """
    proj = player.get("proj") or 0
    if proj <= 0:
        return 0.0

    pos = player.get("pos", "?")
    avail = simulate_availability(pos, effective_status(player), weeks, rng)
    per_game = proj / weeks

    frac = replacement_fraction
    if frac is None:
        frac = REPLACEMENT_FRACTION.get(pos, DEFAULT_REPLACEMENT)

    missed = weeks - avail
    return per_game * avail + per_game * frac * missed


def risk_profile(player, weeks=17, trials=2000, seed=None):
    """
    Distribution summary for one player. Useful for comparing two players whose
    projections are close but whose risk differs.

    Returns dict with mean/median/p10/p90 points and games-missed stats.
    """
    rng = random.Random(seed)
    pts, missed = [], []
    for _ in range(trials):
        avail = simulate_availability(player.get("pos", "?"),
                                     effective_status(player), weeks, rng)
        missed.append(weeks - avail)
        proj = player.get("proj") or 0
        per_game = proj / weeks
        frac = REPLACEMENT_FRACTION.get(player.get("pos", "?"), DEFAULT_REPLACEMENT)
        pts.append(per_game * avail + per_game * frac * (weeks - avail))

    pts.sort()
    missed.sort()
    n = len(pts)
    name = player.get("name")
    override = NON_INJURY_OVERRIDE.get(name)
    return {
        "name": name,
        "pos": player.get("pos"),
        "proj_raw": player.get("proj"),
        "espn_status": player.get("injury") or "ACTIVE",
        "modeled_status": effective_status(player),
        "override_reason": override[1] if override else None,
        "mean": sum(pts) / n,
        "median": pts[n // 2],
        "p10": pts[n // 10],
        "p90": pts[(n * 9) // 10],
        "mean_missed": sum(missed) / n,
        "p_misses_4plus": sum(1 for m in missed if m >= 4) / n,
        "p_full_season": sum(1 for m in missed if m == 0) / n,
    }
