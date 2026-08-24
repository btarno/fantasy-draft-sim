#!/usr/bin/env python3
"""
ESPN re-authentication via CDP.

The ESPN login form lives in an out-of-process iframe (OOPIF) served from
cdn.registerdisney.go.com, so the fields are NOT in the top-level page's DOM.
Must enumerate all targets and drive the iframe target directly.

Usage:
    python3 espn_reauth.py --step login   --pwfile /path/to/pw
    python3 espn_reauth.py --step otp     --code 123456
    python3 espn_reauth.py --step cookies
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HOST = "http://127.0.0.1:9222"
CDP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cdp.py")
COOKIE_OUT = os.path.expanduser("~/.hermes/credentials/espn_cookies.json")


def targets():
    with urllib.request.urlopen(f"{HOST}/json", timeout=15) as r:
        return json.load(r)


def ev(ws, expr, timeout=90):
    r = subprocess.run([sys.executable, CDP, "eval", ws, expr],
                       capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout).get("result", {}).get("value")
    except (json.JSONDecodeError, AttributeError):
        return None


def nav(ws, url):
    subprocess.run([sys.executable, CDP, "nav", ws, url],
                   capture_output=True, text=True, timeout=120)


def page_ws():
    for t in targets():
        if t.get("type") == "page" and "about:blank" not in t.get("url", ""):
            return t["webSocketDebuggerUrl"], t.get("url")
    for t in targets():
        if t.get("type") == "page":
            return t["webSocketDebuggerUrl"], t.get("url")
    sys.exit("no page target")


def find_login_frame():
    """Locate the Disney OOPIF that actually holds the email/password inputs."""
    for t in targets():
        url = t.get("url", "")
        if t.get("type") not in ("page", "iframe"):
            continue
        if "registerdisney" not in url and "espn.com" not in url:
            continue
        ws = t.get("webSocketDebuggerUrl")
        if not ws:
            continue
        probe = ev(ws, """(() => {
          const e = document.querySelector('input[type=email],input[name=email],#InputIdentityFlowValue');
          const p = document.querySelector('input[type=password]');
          return JSON.stringify({email: !!e, pw: !!p,
            url: location.href.slice(0,70)});
        })()""", timeout=40)
        if probe:
            try:
                d = json.loads(probe)
                if d.get("email"):
                    return ws, d
            except json.JSONDecodeError:
                pass
    return None, None


def step_login(pwfile):
    ws, url = page_ws()
    print(f"top page: {url[:70]}")
    nav(ws, "https://www.espn.com/login")
    time.sleep(12)

    fws, info = find_login_frame()
    if not fws:
        print("could not find the login iframe. Targets seen:")
        for t in targets():
            print(f"  {t.get('type'):<8}{t.get('url','')[:70]}")
        return 1
    print(f"login frame: {info}")

    email = "btarno@gmail.com"
    with open(pwfile) as f:
        pw = f.read().strip()

    setter = """(() => {
      const set = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value');
        d.set.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
      };
      const e = document.querySelector('input[type=email],input[name=email],#InputIdentityFlowValue');
      if (!e) return 'no email field';
      set(e, %s);
      return 'email set';
    })()""" % json.dumps(email)
    print("  ", ev(fws, setter))
    time.sleep(2)

    # Some flows need a Continue click before the password field renders.
    cont = ev(fws, """(() => {
      const b = Array.from(document.querySelectorAll('button'))
        .find(x => /continue|next/i.test(x.textContent.trim()));
      if (b && !b.disabled) { b.click(); return 'clicked ' + b.textContent.trim(); }
      return 'no continue button';
    })()""")
    print("  ", cont)
    time.sleep(6)

    fws2, info2 = find_login_frame()
    fws2 = fws2 or fws
    pwset = ev(fws2, """(() => {
      const set = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value');
        d.set.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
      };
      const p = document.querySelector('input[type=password]');
      if (!p) return 'no password field yet';
      set(p, %s);
      return 'password set';
    })()""" % json.dumps(pw))
    print("  ", pwset)
    time.sleep(2)

    sub = ev(fws2, """(() => {
      const b = Array.from(document.querySelectorAll('button'))
        .find(x => /log ?in|sign ?in|continue/i.test(x.textContent.trim()));
      if (b && !b.disabled) { b.click(); return 'submitted: ' + b.textContent.trim(); }
      return 'no submit button';
    })()""")
    print("  ", sub)
    time.sleep(12)

    ws, url = page_ws()
    body = ev(ws, "document.body.innerText.slice(0,300).replace(/\\n+/g,' | ')")
    print(f"\nafter submit, top page: {url[:70]}")
    print(f"  {body}")

    fws3, _ = find_login_frame()
    if fws3:
        otp = ev(fws3, """(() => {
          const t = document.body.innerText;
          const needs = /code|verif|one.time|otp/i.test(t);
          return JSON.stringify({otpPrompt: needs, text: t.slice(0,220)});
        })()""")
        print(f"  otp check: {otp}")
    return 0


def step_otp(code):
    fws, info = find_login_frame()
    if not fws:
        # OTP form may be its own target
        for t in targets():
            ws = t.get("webSocketDebuggerUrl")
            if not ws:
                continue
            hit = ev(ws, "!!document.querySelector('input[type=text],input[autocomplete=one-time-code]')")
            if hit:
                fws = ws
                break
    if not fws:
        return print("no OTP field found") or 1

    out = ev(fws, """(() => {
      const set = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value');
        d.set.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
      };
      const inputs = Array.from(document.querySelectorAll(
        'input[autocomplete=one-time-code],input[type=text],input[type=tel]'));
      if (!inputs.length) return 'no otp input';
      if (inputs.length >= 6) {
        const c = %s;
        inputs.slice(0,6).forEach((el,i) => set(el, c[i]));
      } else {
        set(inputs[0], %s);
      }
      return 'code entered into ' + inputs.length + ' field(s)';
    })()""" % (json.dumps(code), json.dumps(code)))
    print("  ", out)
    time.sleep(2)
    sub = ev(fws, """(() => {
      const b = Array.from(document.querySelectorAll('button'))
        .find(x => /continue|verify|submit|log ?in/i.test(x.textContent.trim()));
      if (b && !b.disabled) { b.click(); return 'submitted'; }
      return 'no submit';
    })()""")
    print("  ", sub)
    time.sleep(12)
    ws, url = page_ws()
    print(f"  now at: {url[:70]}")
    return 0


def step_cookies():
    ws, url = page_ws()
    nav(ws, "https://fantasy.espn.com/football/team?leagueId=906803824&teamId=9&seasonId=2026")
    time.sleep(10)
    title = ev(ws, "document.title")
    signin = ev(ws, "document.body.innerText.includes('Log In') || "
                    "document.body.innerText.includes('Log in required')")
    print(f"  title: {title}")
    print(f"  login required: {signin}")
    if signin:
        print("  NOT AUTHENTICATED -- do not overwrite the cookie file")
        return 1

    r = subprocess.run([sys.executable, CDP, "eval", ws,
                        "1"], capture_output=True, text=True, timeout=60)
    # Pull cookies via CDP Network domain
    import websocket
    wsc = websocket.create_connection(ws, timeout=60)
    wsc.send(json.dumps({"id": 1, "method": "Network.enable"}))
    wsc.recv()
    wsc.send(json.dumps({"id": 2, "method": "Network.getAllCookies"}))
    while True:
        m = json.loads(wsc.recv())
        if m.get("id") == 2:
            cookies = m["result"]["cookies"]
            break
    wsc.close()

    keep = [c for c in cookies if "espn" in (c.get("domain") or "")
            or "go.com" in (c.get("domain") or "")]
    names = {c["name"] for c in keep}
    if "espn_s2" not in names or "SWID" not in names:
        print(f"  MISSING required cookies. have: {sorted(names)[:10]}")
        return 1

    os.makedirs(os.path.dirname(COOKIE_OUT), exist_ok=True)
    with open(COOKIE_OUT, "w") as f:
        json.dump(keep, f)
    os.chmod(COOKIE_OUT, 0o600)
    print(f"  saved {len(keep)} cookies -> {COOKIE_OUT} (mode 600)")
    print(f"  espn_s2 + SWID present: YES")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True,
                    choices=["login", "otp", "cookies", "probe"])
    ap.add_argument("--pwfile")
    ap.add_argument("--code")
    a = ap.parse_args()

    if a.step == "probe":
        for t in targets():
            print(f"  {t.get('type'):<8}{t.get('url','')[:75]}")
        return 0
    if a.step == "login":
        return step_login(a.pwfile)
    if a.step == "otp":
        return step_otp(a.code)
    return step_cookies()


if __name__ == "__main__":
    sys.exit(main())
