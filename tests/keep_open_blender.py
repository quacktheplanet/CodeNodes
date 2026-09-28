"""For tests/two_blenders.ps1: load CodeNodes from this repo (its link starts by itself, as
CODENODES_LINK=1 forces for an imported add-on) and stay open until the runner creates the
file named by CODENODES_CHECK_DONE, or two minutes pass."""

import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import codenodes  # noqa: E402

DONE = os.environ.get("CODENODES_CHECK_DONE", "")
LIMIT = time.time() + 120
print("CodeNodes test: two Blenders (this window closes by itself)", flush=True)
codenodes.register()


def watch():
    from codenodes import server
    info = server.status()
    if info["running"] and not getattr(watch, "said", False):
        print(f"KEEP_OPEN listening {info['port']}", flush=True)
        watch.said = True
    if (DONE and os.path.exists(DONE)) or time.time() > LIMIT:
        bpy.ops.wm.quit_blender()
        return None
    return 0.5


bpy.app.timers.register(watch, first_interval=1.0)
