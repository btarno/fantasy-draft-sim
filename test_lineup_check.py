"""Force-test the lineup checker by faking injury states on real API data."""
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location(
    "lc", "/home/friday/.hermes/scripts/fantasy-lineup-check.py")
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)

import requests  # noqa: E402

real_get = requests.get


def make_fake(mutations):
    """mutations: {player_name: new_status}"""
    def fake_get(*a, **kw):
        r = real_get(*a, **kw)
        try:
            d = r.json()
        except Exception:
            return r
        for t in d.get("teams", []):
            for e in ((t.get("roster") or {}).get("entries") or []):
                pl = (e.get("playerPoolEntry") or {}).get("player") or {}
                nm = pl.get("fullName")
                if nm in mutations:
                    pl["injuryStatus"] = mutations[nm]

        class Fake:
            status_code = 200

            @staticmethod
            def json():
                return d

            @staticmethod
            def raise_for_status():
                return None
        return Fake()
    return fake_get


print("=== TEST 1: starting RB ruled OUT ===")
requests.get = make_fake({"J.K. Dobbins": "OUT"})
lc.main()

print("\n=== TEST 2: two starters OUT + one QUESTIONABLE ===")
requests.get = make_fake({
    "Travis Etienne Jr.": "OUT",
    "CeeDee Lamb": "DOUBTFUL",
    "Rashee Rice": "QUESTIONABLE",
})
lc.main()

requests.get = real_get
