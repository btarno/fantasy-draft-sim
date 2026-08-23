#!/usr/bin/env python3
"""
Minimal Chrome DevTools Protocol driver.

Lives in the repo (not /tmp) because /tmp is cleared on reboot and that silently
broke the ESPN upload path -- upload_ranking.py reported "no CDP target" when the
real cause was a missing driver file.

    python3 cdp.py targets
    python3 cdp.py eval  <ws_url> "<javascript>"
    python3 cdp.py nav   <ws_url> "<url>"
"""
import json
import sys
import urllib.request

import websocket

HOST = "http://127.0.0.1:9222"


class CDP:
    def __init__(self, ws_url, timeout=60):
        self.ws = websocket.create_connection(ws_url, timeout=timeout)
        self.i = 0

    def send(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method,
                                 "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg


def list_targets():
    with urllib.request.urlopen(f"{HOST}/json", timeout=15) as r:
        return json.load(r)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]

    if cmd == "targets":
        for t in list_targets():
            if t.get("type") == "page":
                print(t.get("webSocketDebuggerUrl"), t.get("url", "")[:60])
        return 0

    ws_url = sys.argv[2]
    c = CDP(ws_url)

    if cmd == "eval":
        expr = sys.argv[3]
        out = c.send("Runtime.evaluate", expression=expr, returnByValue=True,
                     awaitPromise=True)
        print(json.dumps(out.get("result", {}), indent=2))
    elif cmd == "nav":
        url = sys.argv[3]
        c.send("Page.enable")
        out = c.send("Page.navigate", url=url)
        print(json.dumps(out.get("result", {}), indent=2))
    else:
        print(f"unknown command: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
