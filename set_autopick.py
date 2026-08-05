#!/usr/bin/env python3
"""
Set ESPN's Autopick Strategy: position MIN/MAX limits + per-round preferences.

WHAT THIS PAGE ACTUALLY OFFERS (from ESPN's own instructions)
------------------------------------------------------------
  Position Limits      : MIN and MAX per position -- a hard guardrail. MAX QB 3
                         makes it structurally impossible for autopick to hoard
                         quarterbacks, which is the single most expensive mistake
                         in a 2QB league (-47 pts across 8,400 simulated drafts).

  Pick-By-Pick Strategy: per-round dropdown, one of
                         Best Available | Quarterback | Running Back |
                         Wide Receiver | Tight End | Flex |
                         Team Defense/Special Teams | Place Kicker
                         "will cause your team to draft the best available
                          player who qualifies at that position"

The per-round strategy is the real prize: it encodes the draft plan directly,
independent of the ranking list, so both mechanisms have to agree before
autopick can go wrong.

PLAN ENCODED HERE
-----------------
  R1-R4   Best Available  (sim finding: best-available beat every scripted
                           construction; zero-RB was worst at -56 pts)
  R5      Quarterback     (QB1 -- flat part of the QB curve begins)
  R6      Flex            (RB/WR value)
  R7      Quarterback     (QB2 -- both starters secured without reaching)
  R8-R16  Flex / TE       (depth; 2 FLEX slots reward RB/WR)
  R17     Place Kicker
  R18     Team D/ST

Position limits keep it honest even if a round preference cannot be met.
"""
import argparse
import json
import subprocess
import sys
import time

CDP = "/tmp/cdp.py"
URL = "https://fantasy.espn.com/football/editdraftstrategy?leagueId=906803824"

# MIN / MAX per position. MAX is the guardrail that matters.
LIMITS = {
    "Quarterback": (2, 3),
    "Running Back": (4, 7),
    "Wide Receiver": (4, 8),
    "Tight End": (1, 2),
    "Place Kicker": (1, 1),
    "Team Defense/Special Teams": (1, 1),
}

# Per-round preference. 18 rounds.
ROUNDS = {
    1: "Best Available", 2: "Best Available", 3: "Best Available",
    4: "Best Available",
    5: "Quarterback",
    6: "Flex",
    7: "Quarterback",
    8: "Flex", 9: "Flex", 10: "Tight End", 11: "Flex", 12: "Flex",
    13: "Flex", 14: "Flex", 15: "Flex", 16: "Flex",
    17: "Place Kicker",
    18: "Team Defense/Special Teams",
}


def ws_url():
    out = subprocess.run([sys.executable, CDP, "targets"],
                         capture_output=True, text=True, timeout=60).stdout
    for tok in out.split():
        if tok.startswith("ws://"):
            return tok
    sys.exit("no CDP target -- is the SSH tunnel up?")


def ev(ws, expr, timeout=90):
    r = subprocess.run([sys.executable, CDP, "eval", ws, expr],
                       capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout).get("result", {}).get("value")
    except (json.JSONDecodeError, AttributeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print("Position limits (MIN/MAX):")
        for p, (lo, hi) in LIMITS.items():
            print(f"  {p:<30}{lo}  {hi}")
        print("\nPer-round preference:")
        for r in sorted(ROUNDS):
            print(f"  R{r:<4}{ROUNDS[r]}")
        return 0

    ws = ws_url()
    subprocess.run([sys.executable, CDP, "nav", ws, URL],
                   capture_output=True, text=True, timeout=120)
    time.sleep(9)

    if "Pre-Draft" not in str(ev(ws, "document.title")):
        sys.exit("unexpected page -- session may have expired")

    ev(ws, "(()=>{const b=Array.from(document.querySelectorAll('button'))"
           ".find(x=>x.textContent.trim()==='Autopick Strategy');"
           "if(b) b.click(); return 1;})()")
    time.sleep(5)

    if args.verify_only:
        state = ev(ws, r"""(() => {
          const rows = Array.from(document.querySelectorAll('tr'))
            .filter(r => r.querySelector('input.position-value'));
          const limits = rows.map(r => {
            const ins = r.querySelectorAll('input.position-value');
            return {pos: r.textContent.trim().replace(/\*+/g,'').slice(0,30),
                    min: ins[0] ? ins[0].value : '',
                    max: ins[1] ? ins[1].value : ''};
          });
          const sels = Array.from(document.querySelectorAll('select'));
          return JSON.stringify({limits, rounds: sels.map(s => s.value)});
        })()""")
        print(state)
        return 0

    # --- position limits
    print("setting position limits...")
    set_limits = ev(ws, """(() => {
      const want = %s;
      const rows = Array.from(document.querySelectorAll('tr'))
        .filter(r => r.querySelector('input.position-value'));
      let n = 0;
      const setv = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value');
        d.set.call(el, String(v));
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        el.dispatchEvent(new Event('blur', {bubbles:true}));
      };
      for (const r of rows) {
        const label = r.textContent.trim().replace(/\\*+/g,'');
        for (const key in want) {
          if (label.indexOf(key) === 0) {
            const ins = r.querySelectorAll('input.position-value');
            if (ins[0]) { setv(ins[0], want[key][0]); n++; }
            if (ins[1]) { setv(ins[1], want[key][1]); n++; }
          }
        }
      }
      return 'set ' + n + ' limit fields';
    })()""" % json.dumps(LIMITS))
    print(f"  {set_limits}")
    time.sleep(2)

    # --- per-round dropdowns
    #
    # PITFALL: the page has 3 FILTER selects (Pro Team, Health, Import Another
    # Draft List) before the 18 round selects, so naive index math writes the
    # plan into the wrong rounds. Match on the round number in each select's own
    # <tr> instead of trusting position.
    print("setting per-round preferences...")
    set_rounds = ev(ws, """(() => {
      const want = %s;
      const sels = Array.from(document.querySelectorAll('select'));
      let n = 0, missing = [], applied = {};
      for (const s of sels) {
        const row = s.closest('tr');
        if (!row) continue;
        // Round selects have exactly the 8 preference options.
        const opts = Array.from(s.options).map(o => o.textContent.trim());
        if (opts[0] !== 'Best Available') continue;
        const m = row.textContent.trim().match(/^([0-9]{1,2})/);
        if (!m) continue;
        const rnd = m[1];
        if (!(rnd in want)) continue;
        const target = want[rnd];
        const opt = Array.from(s.options).find(o => o.textContent.trim() === target);
        if (!opt) { missing.push(rnd + ':' + target); continue; }
        const d = Object.getOwnPropertyDescriptor(
          window.HTMLSelectElement.prototype, 'value');
        d.set.call(s, opt.value);
        s.dispatchEvent(new Event('input', {bubbles:true}));
        s.dispatchEvent(new Event('change', {bubbles:true}));
        applied[rnd] = target;
        n++;
      }
      return JSON.stringify({set: n, missing,
        rounds: Object.keys(applied).length});
    })()""" % json.dumps({str(k): v for k, v in ROUNDS.items()}))
    print(f"  {set_rounds}")
    time.sleep(2)

    saved = ev(ws, "(()=>{const b=Array.from(document.querySelectorAll('button'))"
                   ".find(x=>/^Save/.test(x.textContent.trim()) && "
                   "x.textContent.trim()!=='Save Rankings');"
                   "if(!b) return 'BUTTON NOT FOUND';"
                   "if(b.disabled) return 'disabled';"
                   "const t=b.textContent.trim(); b.click(); return 'clicked '+t;})()")
    print(f"  save: {saved}")
    return 0 if str(saved).startswith("clicked") else 1


if __name__ == "__main__":
    sys.exit(main())
