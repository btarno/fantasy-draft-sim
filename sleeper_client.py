"""
Sleeper platform adapter.

Sleeper's API is genuinely better to work with than ESPN's:
  - Free, read-only, NO AUTHENTICATION at all
  - Not IP-blocked (ESPN's CloudFront 403s datacenter IPs, forcing this whole
    project through headless Chrome on a residential connection)
  - Documented: https://docs.sleeper.com/
  - Exposes fields ESPN does not: age, injury_body_part, depth_chart_order,
    years_exp, search_rank

It also has real gaps versus ESPN:
  - NO point projections. ESPN hands you appliedTotal per player; Sleeper does
    not project at all. Any valuation has to come from elsewhere.
  - NO ADP in the main API. `search_rank` is a popularity/search proxy, not
    average draft position.
  - NO write access. You cannot set rankings or autopick strategy via API the
    way we drove ESPN's UI. Draft picks can be READ live, which is arguably more
    useful for assist.py.

So the sensible arrangement if the league moves: keep ESPN projections as the
valuation layer, use Sleeper for roster/draft state, live pick tracking, and the
richer player metadata.
"""
import json
import os
import time

import requests

BASE = "https://api.sleeper.app/v1"
CACHE = os.path.expanduser("~/.cache/sleeper")
PLAYER_CACHE = os.path.join(CACHE, "players_nfl.json")
# Sleeper explicitly asks callers not to hammer the 5MB+ player endpoint;
# once a day is plenty since it only changes with roster moves.
PLAYER_TTL = 24 * 3600

TIMEOUT = 45


def _get(path):
    r = requests.get(f"{BASE}{path}", timeout=TIMEOUT,
                     headers={"User-Agent": "fantasy-draft-sim"})
    r.raise_for_status()
    return r.json()


def state():
    """Current NFL season/week state."""
    return _get("/state/nfl")


def all_players(refresh=False):
    """
    Full player database, ~12k players x 53 fields. Cached on disk for a day.
    """
    os.makedirs(CACHE, exist_ok=True)
    if not refresh and os.path.exists(PLAYER_CACHE):
        if time.time() - os.path.getmtime(PLAYER_CACHE) < PLAYER_TTL:
            return json.load(open(PLAYER_CACHE))
    data = _get("/players/nfl")
    json.dump(data, open(PLAYER_CACHE, "w"))
    return data


def user(username_or_id):
    return _get(f"/user/{username_or_id}")


def user_leagues(user_id, season, sport="nfl"):
    return _get(f"/user/{user_id}/leagues/{sport}/{season}")


def league(league_id):
    return _get(f"/league/{league_id}")


def league_users(league_id):
    return _get(f"/league/{league_id}/users")


def league_rosters(league_id):
    return _get(f"/league/{league_id}/rosters")


def league_drafts(league_id):
    return _get(f"/league/{league_id}/drafts")


def draft(draft_id):
    return _get(f"/draft/{draft_id}")


def draft_picks(draft_id):
    """
    Every pick made so far. This is the live-draft hook: poll it during the
    draft and assist.py knows who is gone without anyone typing names.
    """
    return _get(f"/draft/{draft_id}/picks")


# --------------------------------------------------------------- translation

# Sleeper roster_positions -> our config lineup vocabulary.
SLOT_MAP = {
    "QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE",
    "K": "K", "DEF": "D/ST",
}
FLEX_SLOTS = {"FLEX", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX", "IDP_FLEX"}
BENCH_SLOTS = {"BN", "IR", "TAXI"}


def config_from_league(lg, my_roster_id=None, my_slot=None):
    """
    Translate a Sleeper league object into the config.json shape this project
    already uses, so the simulator does not care which platform it came from.
    """
    rp = lg.get("roster_positions") or []
    lineup, flex, bench = {}, 0, 0
    superflex = False
    for slot in rp:
        if slot in SLOT_MAP:
            lineup[SLOT_MAP[slot]] = lineup.get(SLOT_MAP[slot], 0) + 1
        elif slot in FLEX_SLOTS:
            flex += 1
            if slot == "SUPER_FLEX":
                superflex = True
        elif slot in BENCH_SLOTS:
            bench += 1

    settings = lg.get("settings") or {}
    scoring = lg.get("scoring_settings") or {}

    flex_positions = ["RB", "WR", "TE"]
    if superflex:
        flex_positions = ["QB", "RB", "WR", "TE"]

    return {
        "platform": "sleeper",
        "league_id": lg.get("league_id"),
        "league_name": lg.get("name"),
        "season": int(lg.get("season") or 0),
        "teams": settings.get("num_teams") or len(rp and [] or []) or 12,
        "rounds": len(rp),
        "lineup": lineup,
        "flex": flex,
        "flex_positions": flex_positions,
        "bench": bench,
        "ppr": scoring.get("rec", 0),
        "pass_td": scoring.get("pass_td", 4),
        "my_roster_id": my_roster_id,
        "my_slot": my_slot,
        "superflex": superflex,
        # Sleeper provides no projections; valuation must come from ESPN or
        # another source. Flag it loudly rather than silently scoring zeros.
        "has_projections": False,
    }


def board_from_sleeper(players, espn_board=None):
    """
    Build a simulator-compatible board from Sleeper players.

    Sleeper has no projections, so if espn_board is supplied we join on name to
    carry projections and ADP across, and layer Sleeper's richer metadata
    (age, injury_body_part, depth_chart_order) on top.
    """
    def norm(s):
        return "".join(c for c in (s or "").lower() if c.isalnum())

    espn_by_name = {norm(p["name"]): p for p in (espn_board or [])}

    out = []
    for pid, p in players.items():
        pos = p.get("position")
        if pos not in ("QB", "RB", "WR", "TE", "K", "DEF"):
            continue
        if p.get("status") not in ("Active", "Injured Reserve", "PUP", None):
            continue
        name = p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        base = espn_by_name.get(norm(name))
        out.append({
            "sleeper_id": pid,
            "name": name,
            "pos": SLOT_MAP.get(pos, pos),
            "team": p.get("team") or "FA",
            "adp": base["adp"] if base else None,
            "proj": base["proj"] if base else None,
            "injury": (p.get("injury_status") or "ACTIVE").upper().replace(" ", "_"),
            # fields ESPN does not expose
            "age": p.get("age"),
            "years_exp": p.get("years_exp"),
            "injury_body_part": p.get("injury_body_part"),
            "depth_chart_order": p.get("depth_chart_order"),
            "search_rank": p.get("search_rank"),
        })
    # Sort by ESPN ADP when available, else Sleeper's search_rank as a weak proxy.
    out.sort(key=lambda p: (p["adp"] is None, p["adp"] or p["search_rank"] or 9999))
    return out
