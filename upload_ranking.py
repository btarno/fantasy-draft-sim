#!/usr/bin/env python3
"""
Upload ranking.json to ESPN's custom Pre-Draft Rankings via CDP drag events.

HOW ESPN'S UI WORKS (discovered by inspection, not assumption)
--------------------------------------------------------------
The "Edit Pre-Draft Strategy" page has two tabs:
  - Autopick Strategy  : positional priority, uses input.position-value boxes
  - Pre-Draft Rankings : the player list -- 50 rows per page, each a
                         <tr data-player-row=N draggable="true">

There is no numeric rank input and no bulk-import for a custom order. Reordering
is HTML5 drag-and-drop only. Synthetic DragEvent dispatch with a real
DataTransfer DOES update React's state (verified: dragging row 2 onto row 0
moved Nacua from #3 to #1).

STRATEGY
--------
Insertion sort against the live DOM. For each target position i, find where the
desired player currently sits and drag them to i. Only the first `--limit`
players need explicit ordering -- ESPN keeps everything below in its default
order, which is fine because our list agrees with ADP more the deeper you go.

    python3 upload_ranking.py --limit 40
    python3 upload_ranking.py --dry-run
    python3 upload_ranking.py --verify-only
"""
import argparse
import json
import subprocess
import sys
import time

CDP = "/tmp/cdp.py"
URL = "https://fantasy.espn.com/football/editdraftstrategy?leagueId=906803824"

# JS helpers injected into the page.
JS_ORDER = r"""
(() => {
  const rows = Array.from(document.querySelectorAll('tr[data-player-row]'));
  return JSON.stringify(rows.map(r => {
    const a = r.querySelector('a');
    if (a) return a.textContent.trim();
    const t = r.textContent.trim().replace(/^[0-9]+/, '');
    const m = t.match(/^([A-Z][a-z'.]+(?:\s[A-Z][a-z'.]+)*(?:\s(?:Jr\.|Sr\.|II|III))?)/);
    return m ? m[1] : t.slice(0, 24);
  }));
})()
"""

JS_DRAG = r"""
(() => {
  const rows = Array.from(document.querySelectorAll('tr[data-player-row]'));
  const from = %d, to = %d;
  if (from < 0 || to < 0 || from >= rows.length || to >= rows.length) return 'oob';
  const src = rows[from], tgt = rows[to];
  const dt = new DataTransfer();
  src.dispatchEvent(new DragEvent('dragstart', {bubbles:true,cancelable:true,dataTransfer:dt}));
  tgt.dispatchEvent(new DragEvent('dragenter', {bubbles:true,cancelable:true,dataTransfer:dt}));
  tgt.dispatchEvent(new DragEvent('dragover',  {bubbles:true,cancelable:true,dataTransfer:dt}));
  tgt.dispatchEvent(new DragEvent('drop',      {bubbles:true,cancelable:true,dataTransfer:dt}));
  src.dispatchEvent(new DragEvent('dragend',   {bubbles:true,cancelable:true,dataTransfer:dt}));
  return 'ok';
})()
"""


def ws_url():
    out = subprocess.run([sys.executable, CDP, "targets"],
                         capture_output=True, text=True, timeout=60).stdout
    for tok in out.split():
        if tok.startswith("ws://"):
            return tok
    sys.exit("no CDP target -- is the SSH tunnel to the Gaming PC up?")


def ev(ws, expr, timeout=90):
    r = subprocess.run([sys.executable, CDP, "eval", ws, expr],
                       capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout).get("result", {}).get("value")
    except (json.JSONDecodeError, AttributeError):
        return None


def get_order(ws):
    raw = ev(ws, JS_ORDER)
    try:
        return json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                    help="how many top slots to explicitly order (default 40)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    desired = [r["name"] for r in json.load(open("ranking.json"))]

    if args.dry_run:
        print(f"Would set the first {args.limit} slots to:")
        for i, n in enumerate(desired[:args.limit], 1):
            print(f"  {i:<4}{n}")
        return 0

    ws = ws_url()
    print(f"CDP {ws[:44]}...")
    subprocess.run([sys.executable, CDP, "nav", ws, URL],
                   capture_output=True, text=True, timeout=120)
    time.sleep(9)

    title = ev(ws, "document.title")
    if "Pre-Draft" not in str(title):
        sys.exit(f"unexpected page '{title}' -- session may have expired")

    # Make sure we're on the Pre-Draft Rankings tab.
    ev(ws, "(()=>{const b=Array.from(document.querySelectorAll('button'))"
           ".find(x=>x.textContent.trim()==='Pre-Draft Rankings');"
           "if(b) b.click(); return 1;})()")
    time.sleep(5)

    live = get_order(ws)
    print(f"live rows: {len(live)}")
    if not live:
        sys.exit("could not read the player list from the page")

    if args.verify_only:
        print("\ncurrent ESPN order (top 15):")
        for i, n in enumerate(live[:15], 1):
            print(f"  {i:<4}{n}")
        return 0

    limit = min(args.limit, len(live))
    print(f"\nreordering the first {limit} slots by insertion sort...\n")

    moves = 0
    for i in range(limit):
        live = get_order(ws)
        want = desired[i] if i < len(desired) else None
        if not want:
            break
        if i < len(live) and norm(live[i]) == norm(want):
            continue
        # locate the desired player below position i
        src = next((j for j in range(i, len(live)) if norm(live[j]) == norm(want)), None)
        if src is None:
            print(f"  slot {i+1:<3} {want:<24} NOT on this page -- skipped")
            continue
        res = ev(ws, JS_DRAG % (src, i))
        if res != "ok":
            print(f"  slot {i+1:<3} {want:<24} drag failed ({res})")
            continue
        moves += 1
        print(f"  slot {i+1:<3} {want:<24} moved from {src+1}")
        time.sleep(0.35)

    time.sleep(2)
    final = get_order(ws)
    print(f"\n{moves} moves applied. Verifying...\n")
    ok = 0
    for i in range(limit):
        want = desired[i] if i < len(desired) else None
        got = final[i] if i < len(final) else None
        match = want and got and norm(want) == norm(got)
        ok += bool(match)
        if i < 15:
            flag = "OK" if match else "MISMATCH"
            print(f"  {i+1:<4}want {str(want):<24}got {str(got):<24}[{flag}]")
    print(f"\n  {ok}/{limit} slots correct")

    # ESPN does NOT autosave drag reorders -- there is an explicit
    # "Save Rankings" button at the bottom of the panel. Matching a loose
    # /^save/i pattern hits a generic nav "Save" instead and silently does
    # nothing, which looks like success until you reload the page.
    saved = ev(ws, "(()=>{const b=Array.from(document.querySelectorAll('button'))"
                   ".find(x=>x.textContent.trim()==='Save Rankings');"
                   "if(!b) return 'BUTTON NOT FOUND';"
                   "if(b.disabled) return 'disabled';"
                   "b.click(); return 'clicked Save Rankings';})()")
    print(f"  save: {saved}")
    time.sleep(5)
    if saved != "clicked Save Rankings":
        print("  >>> NOT SAVED. Re-run; do not assume the order persisted.")
        return 1
    return 0 if ok >= limit * 0.9 else 1


if __name__ == "__main__":
    sys.exit(main())
