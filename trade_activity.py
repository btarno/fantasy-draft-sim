"""Show all trade activity, tolerating null status fields."""
import requests

import espn_client as api

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base, params={"view": "mTransactions2"}, cookies=ck,
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=40).json()

me = cfg["my_team_id"]
tx = [t for t in (d.get("transactions") or [])
      if (t.get("type") or "").startswith("TRADE")]

print(f"=== TRADE ACTIVITY ({len(tx)} total) ===")
for t in tx:
    mine = "  <-- YOURS" if t.get("teamId") == me else ""
    ttype = str(t.get("type") or "?")
    status = str(t.get("status") or "-")
    tid = t.get("teamId")
    print(f"  {ttype:<18}{status:<10}team={tid}{mine}")

mine_tx = [t for t in tx if t.get("teamId") == me]
print(f"\nYour trade transactions: {len(mine_tx)}")
pending = [t for t in mine_tx if (t.get("status") or "").upper() == "PENDING"]
print(f"Still pending: {len(pending)}")
