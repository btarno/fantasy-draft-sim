# fantasy-draft-sim

A Monte Carlo draft simulator for ESPN fantasy football leagues. Pulls **live ADP,
projections, and injury status** from ESPN's API, then simulates thousands of snake
drafts to test whether a draft strategy actually beats the alternatives — or just
looks good in one lucky run.

Built because "draft QBs early in a 2QB league" is repeated everywhere as gospel,
and I wanted to know whether it survived contact with my league's actual scoring
settings. (It did not. See [Findings](#findings).)

```
$ ffsim compare -n 400

=== PAIRED DELTAS vs QB@R4+R6 ===
  QB@R4+R5     -21.6 pts (-0.86%)  t=-13.83  SIGNIFICANT
  QB@R6+R7      +0.8 pts (+0.03%)  t= +0.46  not significant (noise)
  QB@R8+R9      +6.0 pts (+0.24%)  t= +3.14  SIGNIFICANT
```

---

## What it does

- **Pulls live data** — ADP, auction values, season projections, and injury
  designations straight from ESPN's private API.
- **Simulates full snake drafts** — 11 opponents pick off ADP with gaussian noise
  plus positional-need pressure, including QB-run cascade behavior.
- **Compares strategies with a paired test** — the same random seed produces the
  same opponents, so the only variable is *your* strategy. This is far more
  sensitive than comparing separate averages.
- **Sweeps the opponent model** — varies noise, QB panic, and opponent archetypes
  (sharps / homers / QB-panickers) so you can tell a real finding from one that's
  overfit to a single set of assumptions.

## Install

```bash
git clone https://github.com/btarno/fantasy-draft-sim.git
cd fantasy-draft-sim
pip install -r requirements.txt
```

Python 3.9+. The only dependency is `requests`.

## Setup

### 1. Find your league ID

It's in the URL when you're on your league page:

```
https://fantasy.espn.com/football/league?leagueId=123456789
                                                   ^^^^^^^^^
```

### 2. Get your cookies

**All ESPN leagues now require authentication** — public ones included. You need
two cookies: `SWID` and `espn_s2`.

1. Log in at [fantasy.espn.com](https://fantasy.espn.com) in your browser
2. Open DevTools (`F12`) → **Application** tab (Chrome) or **Storage** (Firefox)
3. Expand **Cookies** → `https://fantasy.espn.com`
4. Copy the values of `SWID` (keep the curly braces) and `espn_s2` (long string)

Save them to `~/.config/fantasy-draft-sim/cookies.json`:

```json
{
  "SWID": "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}",
  "espn_s2": "AEBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

```bash
chmod 600 ~/.config/fantasy-draft-sim/cookies.json
```

> A browser cookie export (a JSON array of `{"name": ..., "value": ...}` objects)
> also works — the loader handles both shapes.

Cookies last days to weeks. When calls start returning `401`, re-export them.

### 3. Configure your league

```bash
cp config.example.json config.json
```

```json
{
  "league_id": "123456789",
  "season": 2026,
  "my_slot": 1,
  "my_team_id": 9,
  "teams": 12,
  "rounds": 16,
  "lineup": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "K": 1, "D/ST": 1},
  "flex": 2,
  "flex_positions": ["RB", "WR", "TE"]
}
```

| Field | Meaning |
|---|---|
| `my_slot` | Your draft position (1 = first overall) |
| `my_team_id` | ESPN's internal team ID — only used to mark you in `ffsim league` |
| `lineup` | How many of each position **start** each week |
| `flex` | Number of FLEX slots |
| `flex_positions` | Which positions are FLEX-eligible |

`lineup` is the important one — it drives both the opponent need model and lineup
scoring. A 1QB league, a superflex league, and a 2QB league produce very different
results.

## Usage

### See your league and pick numbers

```bash
python3 ffsim.py league
```

```
=== Stone NW League Part 2 ===
Season 2026 | 12 teams
Draft: Wednesday Sep 02, 2026 at 07:00 PM (SNAKE, 90s/pick)

Round 1 order:
  1. Lord Fumblebottom   <-- YOU
  2. Fred
  ...

Your picks:
  R1.01(#1)  R2.12(#24)  R3.01(#25)  R4.12(#48)  R5.01(#49)  R6.12(#72)
```

### Look at the board

```bash
python3 ffsim.py board --top 40
python3 ffsim.py board --pos QB --top 20     # positional view
python3 ffsim.py board --refresh             # bypass the cache
```

The board is cached to `board.json` after the first fetch. Use `--refresh` to pull
fresh ADP.

### Run a mock draft

```bash
python3 ffsim.py mock --seed 7 --qb1 5 --qb2 7
```

Prints every pick, your optimal starting lineup, and where you finished among all
12 projected rosters.

- `--qb1 N` — earliest round to take your first QB
- `--qb2 N` — earliest round to take your second
- `--rb-floor N` — minimum RBs to secure by mid-draft (default 3)
- `--seed N` — change for a different draft

### Compare strategies (the useful one)

```bash
python3 ffsim.py compare -n 400
python3 ffsim.py compare -n 400 --strategies 3/5,5/7,7/9
```

Runs each strategy through **identical drafts** — same seed means same opponents
making the same picks — so the measured difference is caused by your strategy and
nothing else. Reports a paired t-statistic.

Read `|t| > 2.5` as "this difference is real." Then check the **percentage**: a
real difference of 0.2% is still worth nothing.

### Sweep the opponent model

```bash
python3 ffsim.py sweep -n 200
```

Reruns the comparison across seven opponent models — disciplined, chaotic, QB-panic,
mixed archetypes, and so on — then tallies which strategy won each.

**This is the sanity check.** A strategy that wins one scenario is overfit to that
scenario's assumptions. A strategy that loses all seven is genuinely bad.

### Calibrate against your league's real past draft

```bash
python3 calibrate.py --season 2025
```

The opponent model ships with assumed defaults (`noise=12`, `qb_urgency=2.2`).
If your league has a recorded draft from a previous season, **measure those
parameters instead of guessing**:

```
  MEASURED NOISE (skill positions only): 31.5
    -> use  noise=31.5  in the sim   (default assumption was 12.0)
  median deviation: -3.0 picks (drafts near consensus)

  BY POSITION (negative = drafted EARLIER than consensus):
    QB    n=22   mean   -49.2    <- QBs go ~49 picks early in this league
    RB    n=40   mean    -2.6
    WR    n=51   mean    +0.3

  PER-MANAGER TENDENCY:
    Fred                     mean  -28.5  stdev 65.3  reacher
    Ben There, Wrecked That  mean  -13.5  stdev 35.4  reacher
    Blips and Chitz          mean   +1.3  stdev 17.8  near ADP
```

In my league the real noise was **31.5** — nearly 3× my assumption. Rerunning the
comparison with measured parameters didn't reverse the finding, it *amplified* it
(the double-tap penalty grew from −21.6 to −40.5 points).

Two ESPN quirks this handles:

- **Historical ADP is not retained.** Past seasons return a flat placeholder
  (`170.0` for every player). The script detects this and falls back to PPR draft
  rank, which *is* retained.
- **K and D/ST always appear hundreds of picks "early"** relative to their rank,
  which wrecks the noise estimate. They're excluded from the calculation.

## Findings

From my league (12-team, 2QB, 1 PPR, 2 FLEX, **4-point passing TDs**):

**1. Double-tapping QBs in back-to-back early picks is genuinely bad.**
It lost every scenario in the sweep, by ~22 points (−0.9%). Taking two QBs at
picks #48 and #49 means punting a starting FLEX slot worth ~220 points to gain
about 6 QB points.

**2. Everything from "QB in round 4" to "QB in round 8" is a coin flip.**
The measured spread was under 6 points on a ~2,510 point season — 0.24%.
Statistically detectable, practically irrelevant.

**3. The reason is scoring settings, not draft theory.**
With 4-point passing TDs, the QB projection curve is compressed:

```
QB1  Josh Allen      369      QB2 -> QB5 spread:  4 points
QB5  Jalen Hurts     321      QB6 -> QB15 spread: 22 points
QB15 Justin Herbert  283
QB20 Jordan Love     260      QB1 -> QB20 total:  110 points
```

RB1→RB20 spans 140 points. WR1→WR20 spans 136. **QB is the flattest position on
the board** even in a league that starts two of them.

If your league uses 6-point passing TDs, that curve steepens and this conclusion
may reverse. **Run it on your own settings.** That's the entire point of the tool.

## Limitations

Read this before acting on any output.

**The opponent model is an assumption, not measured behavior.** Opponents are
modeled as rational ADP-followers with gaussian noise and need-based urgency. Real
drafters do things this doesn't capture:

- Homer picks (taking every player from one team)
- Genuine positional runs and cascade panic
- Reaching 30 picks early for a personal favorite
- Trading picks mid-draft

The `sweep` command exists to partially address this — if a finding holds across
wildly different opponent models, it probably isn't an artifact of one.

**Projections are treated as truth.** The sim has no concept of bust risk, weekly
variance, or in-season injury. It assumes ESPN's projections are accurate. They
are not. This systematically favors strategies that accumulate cheap projected
points over strategies that buy a safe floor.

Concretely: the "draft QBs very late" strategy wins on paper by landing QB2s who
project well but carry real bust risk. The sim can't see that risk. Weigh it yourself.

**Injuries are a static haircut, not a simulation.** This is worth being precise
about. The tool reads ESPN's *current* injury designation and multiplies the
projection:

| ESPN status | multiplier |
|---|---|
| `ACTIVE` | 1.00 |
| `QUESTIONABLE` | 0.92 |
| `DOUBTFUL` | 0.60 |
| `OUT` | 0.35 |
| `INJURY_RESERVE` | 0.15 |

Those numbers are judgment calls, not fitted to data. And on a typical preseason
board only ~8% of players carry any designation at all — so for 92% of the pool
the haircut does nothing.

What it does **not** model:

- **New injuries during the season.** Every player is assumed to stay as healthy
  as they are today. In reality a meaningful share of any roster misses games.
- **Games missed vs. reduced effectiveness.** A `QUESTIONABLE` tag is treated as
  a flat 8% shave, when the real distribution is closer to "plays fine most weeks,
  misses two entirely."
- **Replacement production.** When your starter is out you play someone else, and
  the sim never credits that.
- **Position-specific durability.** RBs get hurt more than WRs. Not modeled.
- **Age or injury history.** A 30-year-old RB coming off surgery is treated
  identically to a 24-year-old who has never missed a snap.

**Why this matters for the conclusions:** the haircut is sensitive enough to flip
real decisions. On the 2026 board, `QUESTIONABLE` on the consensus 1.01 was enough
to move the recommended first pick to the healthy WR behind him. Change the
multiplier and the pick changes. Treat the injury adjustment as a nudge to think
about risk, not as a calculated answer.

**Only projected points are simulated.** No head-to-head schedule, no playoff
bracket, no waiver wire, no trades, no bye-week management. A higher projected
total is a good proxy for a better team, not a guarantee of a better season.

**ESPN blocks datacenter IPs.** API calls from a VPS or cloud host return
CloudFront `403`. Run this from a residential connection.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `HTTP 401` | Cookies expired — re-export `SWID` and `espn_s2` |
| `HTTP 403` | ESPN blocked your IP. Datacenter/VPS IPs are blocked; use a home connection |
| `302` / HTML instead of JSON | Wrong host. This tool uses `lm-api-reads.fantasy.espn.com` — `fantasy.espn.com` redirects |
| Empty board | Your `season` may be wrong, or ADP isn't published yet for that season |
| `No league_id` | Create `config.json` or set `FFSIM_LEAGUE_ID` |

## Notes

ESPN's fantasy API is undocumented and unofficial. Endpoints can change without
warning. This project is not affiliated with or endorsed by ESPN.

Two implementation details that cost me time, in case you're building something similar:

- The player board must be requested from the **league** URL with
  `view=kona_player_info` and an `x-fantasy-filter` header. The `players_wl` view
  returns bare player records with no ADP or ownership data.
- `espn_s2` should be used exactly as it appears in the cookie. Do not URL-decode it.

## License

MIT
