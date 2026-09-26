"""The server inside a running Blender, driven by a real socket client.

    blender --factory-startup --python tests/test_server.py      (needs a window: GPU)

The client runs on a background thread (only sockets, never bpy) while Blender's timer
serves it on the main thread — the same shape as a real assistant session.
"""
import base64
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mcp"))
import codenodes  # noqa: E402
from codenodes import server  # noqa: E402
from codenodes_mcp.connection import BlenderNotRunning, Connection  # noqa: E402  (the shipped client)

PORT = 9899
WORK = os.path.join(tempfile.gettempdir(), "codenodes_server_test")
results = queue.Queue()


class Client(Connection):
    """The real client, plus the raw send and error-tolerant call a test needs."""

    def __init__(self, port, token):
        super().__init__(port=port, token=token, timeout=180.0)
        self.connect()

    def call(self, tool, **args):
        """Like Connection.call but hands errors back instead of raising."""
        try:
            return {"ok": True, "result": super().call(tool, **args)}
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    def send_raw(self, data):
        self._sock.sendall(data)

    def read(self, timeout=60):
        return self._read()


def session(token):
    """Everything the client side checks. Runs on its own thread."""
    checks = []

    def check(cond, msg):
        checks.append((bool(cond), msg))

    try:
        c = Client(PORT, token)

        r = c.call("status")
        check(r["ok"] and r["result"]["running"], f"status says it is running ({r})")
        check(r["result"]["blender"].startswith("5."), f"and reports Blender {r['result'].get('blender')}")

        r = c.call("tools")
        names = {t["name"] for t in r["result"]["tools"]}
        check({"make", "scene", "render", "look_at", "light", "bake"} <= names,
              f"the tool list covers the workflow ({len(names)} tools)")
        check(not any(n in names for n in ("exec", "eval", "execute_blender_code", "run_code")),
              f"there is no way to run arbitrary code ({sorted(names)})")
        make = next(t for t in r["result"]["tools"] if t["name"] == "make")
        check(make["summary"].startswith("Build or update"), f"tools carry their own documentation ({make['summary']!r})")

        r = c.call("help")
        check(r["ok"] and set(r["result"]["kinds"]) == {"mesh", "particles", "volume"},
              "help comes through the socket")

        r = c.call("make", kind="mesh", code="// @param r 1.0 0.2 3.0\nfloat sdf(vec3 p){ return sdSphere(p, r); }",
                   name="Ball", resolution=64)
        check(r["ok"] and r["result"]["ok"] and r["result"]["faces"] > 100,
              f"a mesh is built over the wire ({r['result'].get('faces')} faces)")

        r = c.call("make", kind="mesh", code="float sdf(vec3 p){ return lenght(p); }", name="Bad")
        check(r["ok"] and not r["result"]["ok"] and "line 1" in r["result"]["error"],
              "a code mistake comes back as a readable result, not a protocol error")

        r = c.call("set_params", name="Ball", r=1.7)
        check(r["ok"] and r["result"]["ok"], f"parameters can be changed ({r['result'].get('error')})")

        r = c.call("scene")
        ball = next((o for o in r["result"]["objects"] if o["name"] == "Ball"), None)
        check(ball is not None and abs(ball["codenodes"]["params"]["r"] - 1.7) < 1e-4,
              "the scene reflects the change")

        check(c.call("look_at", target="Ball")["result"]["ok"], "the camera can be aimed")
        check(c.call("light", style="studio")["result"]["ok"], "the lights can be set")

        out = os.path.join(WORK, "over_the_wire.png")
        r = c.call("render", path=out, samples=8, width=200)
        check(r["ok"] and r["result"]["ok"] and r["result"]["exists"], f"a render is produced ({r['result'].get('error')})")

        r = c.call("read_image", path=out)
        check(r["ok"] and base64.b64decode(r["result"]["image_base64"])[:4] == b"\x89PNG",
              "the image comes back as base64 for the model to look at")

        # bad input must not take anything down
        check(not c.call("make", kind="sculpture", code="x")["result"]["ok"], "an unknown kind is refused")
        check(not c.call("nonsense")["ok"], "an unknown tool is refused")
        check(not c.call("make")["ok"], "a missing argument is refused")
        c.send_raw(b"{not json}\0")
        check(not c.read()["ok"], "malformed input gets an error, not a dropped connection")
        check(c.call("status")["ok"], "and the connection still works afterwards")

        # a second client at the same time
        c2 = Client(PORT, token)
        check(c2.call("status")["ok"], "a second client can connect")
        r1, r2 = c.call("frame", number=5), c2.call("scene")
        check(r1["ok"] and r2["ok"] and r2["result"]["frame"] == 5, "both clients are served")
        c2.close()

        # the token is enforced
        rogue = Client(PORT, "")           # empty means "send no token at all"
        r = rogue.call("scene")
        check(not r["ok"] and "unauthorised" in r["error"], "a client with no token is refused")
        rogue.close()
        bad = Client(PORT, "not-the-token")
        check("unauthorised" in bad.call("scene")["error"], "a client with the wrong token is refused")
        bad.close()

        check(c.call("status")["ok"], "the server survived all of that")
        c.close()
    except Exception:
        checks.append((False, "client raised: " + traceback.format_exc()))
    results.put(checks)


def main():
    os.makedirs(WORK, exist_ok=True)
    codenodes.register()
    token = server.load_token()
    info = server.start(PORT, token)
    if not info.get("running"):
        print("FAIL:", info.get("error"), flush=True)
        bpy.ops.wm.quit_blender()
        return
    thread = threading.Thread(target=session, args=(token,), daemon=True)
    thread.start()
    started = time.time()

    def tick():
        if thread.is_alive() and time.time() - started < 300:
            return 0.1                                  # let the server's own timer work
        passed = failed = 0
        try:
            for ok, msg in results.get_nowait():
                print(("  ok: " if ok else "FAIL: ") + msg, flush=True)
                passed, failed = passed + bool(ok), failed + (not ok)
        except queue.Empty:
            print("FAIL: the client never finished", flush=True)
            failed = 1
        info = server.status()
        print(f"  ok: server handled {info['served']} calls", flush=True)
        server.stop()
        print(("\nFAIL" if failed else f"\nALL {passed + 1} CHECKS PASSED"), flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=0.2)


main()
