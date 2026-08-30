import json
import requests
import espn_client as api
import injury

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base, params={"view": ["mDraftDetail", "mTeam"]}, cookies=ck,
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()
dd = d.get("draftDetail") or {}
picks = dd.get("picks") or []
made = [p for p in picks if (p.get("playerId") or -1) > 0]
made.sort(key=lambda p: p["overallPickNumber"])

board = api.load_board(cfg)
by_id = {p.get("id"): p for p in board if p.get("id")}
taken = {p["playerId"] for p in made}

print(f"PICKS MADE: {len(made)}")
if made:
    print("\n--- gone so far ---")
    for p in made[-12:]:
        pl = by_id.get(p["playerId"])
        nm = pl["name"] if pl else f"id:{p['playerId']}"
        pos = pl["pos"] if pl else "?"
        print(f"  #{p['overallPickNumber']:<4}{nm:<24}{pos}")

mine = sorted(p["overallPickNumber"] for p in picks
              if p.get("teamId") == cfg["my_team_id"])
nxt = next((n for n in mine if n > len(made)), None)
print(f"\nYOUR NEXT PICK: #{nxt}   ({(nxt or 0) - len(made) - 1} picks away)")

avail = [p for p in board if p.get("id") not in taken
         and p.get("proj") and p["pos"] in ("RB", "WR", "TE")]
rows = []
for p in avail[:20]:
    r = injury.risk_profile(p, weeks=17, trials=300, seed=42)
    rows.append((r["mean"], r["p10"], r["p_misses_4plus"], p))
rows.sort(key=lambda x: -x[0])

print("\n--- BEST AVAILABLE ---")
for mean, p10, pm, p in rows[:10]:
    inj = "" if p["injury"] == "ACTIVE" else " [" + p["injury"][:4] + "]"
    print(f"  {p['name']:<23}{p['pos']:<4}adj {mean:.0f}  floor {p10:.0f}  "
          f"miss4 {pm*100:.0f}pct{inj}")
