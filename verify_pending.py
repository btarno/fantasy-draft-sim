"""
Are those 'PENDING' trades real, or stale API records?

ESPN keeps proposal rows around after they are resolved. Check the timestamps
and whether a matching DECLINE/CANCEL exists for the same trade id.
"""
import datetime
import json

import requests

import espn_client as api

cfg = api.load_config("config.json")
ck = api.load_cookies(cfg)
base = (f"{api.API_HOST}/apis/v3/games/ffl/seasons/2026/segments/0"
        f"/leagues/{cfg['league_id']}")
d = requests.get(base, params={"view": ["mTransactions2", "mTeam"]},
                 cookies=ck, headers={"User-Agent": "Mozilla/5.0"},
                 timeout=45).json()

teams = {t["id"]: (t.get("name") or f"Team {t['id']}") for t in d["teams"]}
tx = [t for t in (d.get("transactions") or [])
      if (t.get("type") or "").startswith("TRADE")]

print(f"=== ALL {len(tx)} TRADE RECORDS, with timestamps ===")
for t in sorted(tx, key=lambda x: x.get("proposedDate") or 0):
    ts = t.get("proposedDate") or t.get("processDate")
    when = (datetime.datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
            if ts else "?")
    tid = str(t.get("id"))[:8]
    print(f"  {when}  {str(t.get('type')):<16}{str(t.get('status')):<10}"
          f"id={tid}  from={teams.get(t.get('teamId'), '?')[:22]}")

print()
print("=== ids that appear MORE THAN ONCE (proposal + resolution) ===")
from collections import defaultdict
byid = defaultdict(list)
for t in tx:
    byid[str(t.get("id"))].append(
        f"{t.get('type')}/{t.get('status')}")
for tid, kinds in byid.items():
    if len(kinds) > 1:
        print(f"  {tid[:8]}: {kinds}")

print()
print("=== related-id linkage (does a DECLINE point at a proposal?) ===")
for t in tx:
    if "DECLINE" in (t.get("type") or "") or "CANCEL" in (t.get("type") or ""):
        print(f"  {t.get('type')}  id={str(t.get('id'))[:8]}  "
              f"relatedId={str(t.get('relatedTransactionId'))[:8]}")
