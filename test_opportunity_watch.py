"""Verify the opportunity watcher distinguishes MY team from rivals."""
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location(
    "ow", "/home/friday/.hermes/scripts/fantasy-opportunity-watch.py")
ow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ow)

STATE = os.path.expanduser("~/.hermes/cron/fantasy-opportunity-state.json")


def bump(team_substr, delta):
    """Inflate a team's stored RB count so the next run sees a 'loss'."""
    s = json.load(open(STATE))
    for tid, v in s["team_rb"].items():
        if team_substr.lower() in v["name"].lower():
            print(f"  [test] {v['name']}: stored {v['n']} -> {v['n'] + delta}")
            v["n"] += delta
            break
    json.dump(s, open(STATE, "w"))


print("=== TEST 1: MY team loses an RB (must NOT pitch me to myself) ===")
bump("Fumblebottom", 2)
ow.main()

print("\n=== TEST 2: a RIVAL loses an RB (should pitch a trade) ===")
bump("Ben There", 2)
ow.main()

print("\n=== TEST 3: no change (must be silent) ===")
ow.main()
print("[end]")
