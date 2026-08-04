"""
Shared ESPN Fantasy API client + config loading.

Handles cookie auth and the two endpoints that actually matter:
  - league settings (draft date, pick order, scoring, roster slots)
  - the player board (ADP, projections, injury status)
"""
import json
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

# ---------------------------------------------------------------- config

DEFAULT_CONFIG_PATHS = [
    os.environ.get("FFSIM_CONFIG", ""),
    "./config.json",
    os.path.expanduser("~/.config/fantasy-draft-sim/config.json"),
]

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

TEAM = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI",
    22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH",
    29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# ESPN's read-only host. NOTE: fantasy.espn.com 302s to a marketing page —
# this subdomain is the one that returns JSON.
API_HOST = "https://lm-api-reads.fantasy.espn.com"


def load_config(path=None):
    """Load config.json. Env vars override file values."""
    cfg = {}
    candidates = [path] if path else DEFAULT_CONFIG_PATHS
    for p in candidates:
        if p and os.path.exists(p):
            with open(p) as f:
                cfg = json.load(f)
            break

    # Env overrides
    cfg.setdefault("league_id", os.environ.get("FFSIM_LEAGUE_ID", ""))
    cfg.setdefault("season", int(os.environ.get("FFSIM_SEASON", 2026)))
    cfg.setdefault("cookie_file", os.environ.get(
        "FFSIM_COOKIE_FILE", os.path.expanduser("~/.config/fantasy-draft-sim/cookies.json")))
    cfg.setdefault("teams", 12)
    cfg.setdefault("rounds", 16)
    cfg.setdefault("my_slot", 1)
    cfg.setdefault("board_cache", "./board.json")
    # Starting lineup: how many of each position start each week
    cfg.setdefault("lineup", {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "K": 1, "D/ST": 1})
    cfg.setdefault("flex", 2)
    cfg.setdefault("flex_positions", ["RB", "WR", "TE"])

    if not cfg["league_id"]:
        sys.exit("No league_id. Copy config.example.json -> config.json and fill it in, "
                 "or set FFSIM_LEAGUE_ID.")
    return cfg


def load_cookies(cfg):
    """
    Read SWID + espn_s2 from a cookie file.

    Accepts either:
      {"SWID": "{...}", "espn_s2": "..."}
    or a CDP/browser-export list:
      [{"name": "SWID", "value": "..."}, ...]
    """
    path = os.path.expanduser(cfg["cookie_file"])
    if not os.path.exists(path):
        sys.exit(f"Cookie file not found: {path}\nSee README section 'Getting your cookies'.")

    with open(path) as f:
        raw = json.load(f)

    if isinstance(raw, list):
        by_name = {c.get("name"): c.get("value") for c in raw}
    else:
        by_name = raw

    swid = by_name.get("SWID") or by_name.get("swid")
    s2 = by_name.get("espn_s2") or by_name.get("ESPN_S2")
    if not (swid and s2):
        sys.exit(f"Cookie file {path} is missing SWID and/or espn_s2.")
    return {"SWID": swid, "espn_s2": s2}


# ---------------------------------------------------------------- API

def _get(cfg, params, extra_headers=None):
    url = f"{API_HOST}/apis/v3/games/ffl/seasons/{cfg['season']}/segments/0/leagues/{cfg['league_id']}"
    headers = {"User-Agent": "Mozilla/5.0"}
    if extra_headers:
        headers.update(extra_headers)
    r = requests.get(url, params=params, cookies=load_cookies(cfg),
                     headers=headers, timeout=60)
    if r.status_code == 401:
        sys.exit("HTTP 401 — your cookies expired. Re-export them (see README).")
    if r.status_code == 403:
        sys.exit("HTTP 403 — ESPN blocked this IP. Datacenter/VPS IPs are blocked; "
                 "run from a residential connection.")
    if r.status_code != 200:
        sys.exit(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def get_league_info(cfg):
    """Draft date, pick order, scoring settings, roster slots."""
    return _get(cfg, {"view": ["mSettings", "mTeam"]})


def get_board(cfg, limit=300):
    """
    Player board with live ADP, auction values, projections, injury status.

    Sorted by ADP ascending. Only players with an ADP are returned.
    """
    filt = {"players": {
        "limit": limit,
        "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "PPR"},
    }}
    data = _get(cfg, {"scoringPeriodId": 0, "view": "kona_player_info"},
                {"x-fantasy-filter": json.dumps(filt)})

    out = []
    for entry in data.get("players", []):
        p = entry.get("player", {})
        own = p.get("ownership") or {}
        adp = own.get("averageDraftPosition")
        if not adp:
            continue

        proj = None
        for st in p.get("stats") or []:
            if (st.get("seasonId") == cfg["season"]
                    and st.get("statSourceId") == 1
                    and st.get("statSplitTypeId") == 0):
                proj = st.get("appliedTotal")

        out.append({
            "name": p.get("fullName"),
            "pos": POS.get(p.get("defaultPositionId"), "?"),
            "team": TEAM.get(p.get("proTeamId"), "?"),
            "adp": round(adp, 1),
            "auction": own.get("auctionValueAverage"),
            "proj": round(proj, 1) if proj else None,
            "own_pct": round(own.get("percentOwned") or 0, 1),
            "injury": p.get("injuryStatus") or "ACTIVE",
        })

    out.sort(key=lambda x: x["adp"])
    return out


def load_board(cfg, refresh=False, limit=300):
    """Load the board from cache, or fetch and cache it."""
    cache = cfg["board_cache"]
    if not refresh and os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    board = get_board(cfg, limit=limit)
    with open(cache, "w") as f:
        json.dump(board, f, indent=1)
    return board
