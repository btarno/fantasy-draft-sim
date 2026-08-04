#!/usr/bin/env python3
"""
Live draft assistant.

During your real draft, mark players as taken and get a ranked
recommendation for your next pick based on your current roster,
positional scarcity, and what's likely to survive until your next turn.

    python3 assist.py

Commands (inside the tool):
    <name>          mark a player as drafted by someone else
    +<name>         mark a player as drafted BY YOU
    undo            undo the last action
    board [pos]     show best available (optionally filtered)
    me              show your roster + starting lineup
    runs            show positional run pressure
    save / load     persist draft state to draft_state.json
    quit
"""
import json
import os
import sys
from collections import defaultdict

import espn_client as api
from simulate import val

STATE_FILE = "draft_state.json"


def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


class DraftState:
    def __init__(self, cfg, board):
        self.cfg = cfg
        self.board = board
        self.taken = {}        # norm_name -> player
        self.mine = []
        self.history = []      # (norm_name, was_mine)

    # ---------------------------------------------------------- lookup
    def find(self, query):
        q = norm(query)
        if not q:
            return []
        avail = self.available()
        exact = [p for p in avail if norm(p["name"]) == q]
        if exact:
            return exact
        starts = [p for p in avail if norm(p["name"]).startswith(q)]
        if starts:
            return starts
        # last-name / substring match
        return [p for p in avail if q in norm(p["name"])]

    def available(self):
        return [p for p in self.board if norm(p["name"]) not in self.taken]

    # ---------------------------------------------------------- mutate
    def take(self, player, mine=False):
        key = norm(player["name"])
        self.taken[key] = player
        if mine:
            self.mine.append(player)
        self.history.append((key, mine))

    def undo(self):
        if not self.history:
            return None
        key, was_mine = self.history.pop()
        p = self.taken.pop(key, None)
        if was_mine and self.mine:
            self.mine.pop()
        return p

    # ---------------------------------------------------------- analysis
    def my_counts(self):
        c = defaultdict(int)
        for p in self.mine:
            c[p["pos"]] += 1
        return c

    def picks_until_next(self):
        """How many picks pass before my next turn (snake)."""
        teams = self.cfg["teams"]
        slot = self.cfg["my_slot"]
        n = len(self.taken)
        rnd = n // teams          # 0-indexed round
        idx_in_round = n % teams
        my_idx = slot - 1 if rnd % 2 == 0 else teams - slot
        if idx_in_round <= my_idx:
            return my_idx - idx_in_round
        # my pick this round has passed -- next is in the following round
        nxt = rnd + 1
        my_next = slot - 1 if nxt % 2 == 0 else teams - slot
        return (teams - idx_in_round) + my_next

    def survival(self, player, gap):
        """
        Rough probability the player is still there after `gap` picks.
        Uses ADP with the league's measured noise as a normal CDF approximation.
        """
        import math
        noise = self.cfg.get("noise", 31.5)
        current = len(self.taken)
        # expected picks before he goes
        z = (player["adp"] - (current + gap)) / max(noise, 1e-6)
        # P(taken later) ~ Phi(z)
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))

    def need_weight(self, pos, rounds_done=0):
        """
        How badly do I need this position?

        Positional need only becomes urgent as the draft progresses. Early on,
        raw value should dominate -- the sim showed that reaching for a position
        you'll fill later (especially QB) is the single most costly mistake.

        Once a position's starting slots are full, its weight collapses hard.
        Without this, raw VOR happily drafts a 4th TE and a 3rd K while the WR
        room sits empty -- scarce positions have high VOR at the top, and that
        signal has to be switched off once you own the slots.
        """
        need = dict(self.cfg["lineup"])
        c = self.my_counts()
        required = need.get(pos, 0)
        have = c[pos]
        starters_left = max(0, required - have)

        # Ramp: need matters ~0 in round 1, fully by round ~8
        ramp = min(1.0, rounds_done / 8.0)

        if starters_left > 0:
            return 1.0 + 0.35 * starters_left * ramp

        # --- starters filled: everything below is depth, not need ---

        # K and D/ST: you can only ever start one. A second is worthless.
        if pos in ("K", "D/ST"):
            return 0.0

        if pos in self.cfg["flex_positions"]:
            flex_surplus = sum(max(0, c[p] - need.get(p, 0))
                               for p in self.cfg["flex_positions"])
            if flex_surplus < self.cfg["flex"]:
                # TE rarely deserves a flex slot over RB/WR in PPR
                return 0.85 if pos in ("RB", "WR") else 0.45
            # Beyond flex = bench. RB/WR depth wins games; TE depth does not.
            depth = have - required
            if pos == "TE":
                return 0.04 if depth >= 1 else 0.10
            return 0.45 * (0.75 ** max(0, depth - 1))

        # QB past requirement: exactly one backup has value, no more.
        surplus = have - required
        return 0.12 if surplus == 0 else 0.01

    def replacement_level(self, pos):
        """
        Projection of the last startable player at this position, computed from
        the CURRENTLY AVAILABLE pool -- not the full preseason board.

        Using the static board understates replacement level as the draft
        progresses: once 20 TEs are gone the 21st is no longer 'above
        replacement', but a static baseline still says he is. That bug made the
        assistant hoard 6 TEs.
        """
        need = dict(self.cfg["lineup"])
        required = need.get(pos, 0)
        if required <= 0:
            return 0.0

        # How many of this position will still be drafted as starters league-wide
        starters = required * self.cfg["teams"]
        if pos in self.cfg["flex_positions"]:
            starters += self.cfg["flex"] * self.cfg["teams"] // len(self.cfg["flex_positions"])

        # Baseline from the remaining pool, discounted by picks already made
        pool = [p for p in self.available() if p["pos"] == pos]
        if not pool:
            return 0.0
        drafted_at_pos = sum(1 for p in self.taken.values() if p["pos"] == pos)
        idx = max(0, starters - drafted_at_pos)
        if idx >= len(pool):
            return val(pool[-1])
        return val(pool[idx])

    def recommend(self, n=10):
        gap = self.picks_until_next()
        avail = self.available()
        rounds_done = len(self.taken) // self.cfg["teams"]
        repl = {pos: self.replacement_level(pos)
                for pos in ("QB", "RB", "WR", "TE", "K", "D/ST")}
        scored = []
        for p in avail[:80]:
            base = val(p)
            if base <= 0:
                continue
            # Value over replacement -- the fair cross-position comparison
            vor = max(0.0, base - repl.get(p["pos"], 0))
            w = self.need_weight(p["pos"], rounds_done)
            if w <= 0:
                continue
            surv = self.survival(p, gap)
            urgency = 1.0 - surv
            score = vor * w * (0.55 + 0.45 * urgency)

            # K / D-ST: worthless early, mandatory at the end
            if p["pos"] in ("K", "D/ST"):
                rounds_left = self.cfg["rounds"] - rounds_done
                if rounds_left > 2:
                    score *= 0.02
                else:
                    # must-fill: force to the top of the list
                    score = 1e6 + vor
            scored.append((score, base, surv, vor, p))
        scored.sort(key=lambda x: -x[0])
        return scored[:n], gap

    def run_pressure(self):
        """Recent positional runs -- what's flying off the board."""
        recent = [self.taken[k] for k, _ in self.history[-12:]]
        c = defaultdict(int)
        for p in recent:
            c[p["pos"]] += 1
        return c, len(recent)

    # ---------------------------------------------------------- persist
    def save(self, path=STATE_FILE):
        json.dump({
            "taken": [{"name": p["name"], "mine": norm(p["name"]) in
                       {norm(m["name"]) for m in self.mine}}
                      for p in self.taken.values()],
        }, open(path, "w"), indent=1)

    def load(self, path=STATE_FILE):
        if not os.path.exists(path):
            return False
        data = json.load(open(path))
        by_norm = {norm(p["name"]): p for p in self.board}
        for rec in data.get("taken", []):
            p = by_norm.get(norm(rec["name"]))
            if p:
                self.take(p, mine=rec.get("mine", False))
        return True


# ------------------------------------------------------------------ UI

def show_recs(st):
    recs, gap = st.recommend()
    c = st.my_counts()
    need = dict(st.cfg["lineup"])
    unmet = [f"{p}({need[p] - c[p]})" for p in need if c[p] < need[p]]
    print(f"\n  Pick #{len(st.taken) + 1} | {gap} picks until your next turn")
    if unmet:
        print(f"  Still need: {' '.join(unmet)}")
    print(f"\n  {'RECOMMENDED':<26}{'POS':<5}{'PROJ':>7}{'VOR':>6}{'ADP':>7}{'SURVIVES?':>11}")
    for score, base, surv, vor, p in recs:
        inj = "" if p["injury"] in ("ACTIVE", None) else f" [{p['injury']}]"
        flag = "GONE" if surv < 0.25 else ("risky" if surv < 0.6 else "can wait")
        print(f"  {p['name']:<26}{p['pos']:<5}{base:7.0f}{vor:6.0f}{p['adp']:7.1f}{flag:>11}{inj}")


def show_me(st):
    print(f"\n  YOUR ROSTER ({len(st.mine)} players)")
    by = defaultdict(list)
    for p in st.mine:
        by[p["pos"]].append(p)
    for pos in ("QB", "RB", "WR", "TE", "K", "D/ST"):
        if by[pos]:
            names = ", ".join(f"{p['name']} ({p['proj']:.0f})" for p in by[pos])
            print(f"    {pos:<6}{names}")
    if st.mine:
        from simulate import optimal_lineup, lineup_score
        print(f"\n    Projected starters: {lineup_score(st.mine, st.cfg):.0f}")


def main():
    cfg = api.load_config()
    cfg.setdefault("noise", 31.5)
    board = api.load_board(cfg)
    st = DraftState(cfg, board)

    print(__doc__)
    if os.path.exists(STATE_FILE):
        ans = input(f"Found {STATE_FILE}. Load it? [y/N] ").strip().lower()
        if ans == "y":
            st.load()
            print(f"  Loaded {len(st.taken)} picks ({len(st.mine)} yours)")

    show_recs(st)
    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            show_recs(st)
            continue

        cmd = raw.lower()
        if cmd in ("quit", "exit", "q"):
            break
        if cmd == "undo":
            p = st.undo()
            print(f"  undid: {p['name'] if p else 'nothing'}")
            show_recs(st)
            continue
        if cmd == "me":
            show_me(st)
            continue
        if cmd == "save":
            st.save()
            print(f"  saved -> {STATE_FILE}")
            continue
        if cmd == "load":
            st.load()
            print(f"  loaded {len(st.taken)} picks")
            continue
        if cmd == "runs":
            c, n = st.run_pressure()
            print(f"\n  Last {n} picks by position:")
            for pos, cnt in sorted(c.items(), key=lambda x: -x[1]):
                print(f"    {pos:<6}{'#' * cnt} ({cnt})")
            continue
        if cmd.startswith("board"):
            parts = raw.split()
            pos = parts[1].upper() if len(parts) > 1 else None
            rows = [p for p in st.available() if not pos or p["pos"] == pos]
            print()
            for p in rows[:15]:
                inj = "" if p["injury"] in ("ACTIVE", None) else f" [{p['injury']}]"
                print(f"  {p['name']:<26}{p['pos']:<5}ADP {p['adp']:<7}proj {p['proj']}{inj}")
            continue

        mine = raw.startswith("+")
        query = raw[1:] if mine else raw
        matches = st.find(query)
        if not matches:
            print(f"  no available player matching '{query}'")
            continue
        if len(matches) > 1 and norm(matches[0]["name"]) != norm(query):
            print("  multiple matches:")
            for p in matches[:8]:
                print(f"    {p['name']} ({p['pos']}, {p['team']})")
            continue
        p = matches[0]
        st.take(p, mine=mine)
        tag = "YOU DRAFTED" if mine else "taken"
        print(f"  {tag}: {p['name']} ({p['pos']}, {p['team']})")
        st.save()
        if mine or True:
            show_recs(st)


if __name__ == "__main__":
    main()
