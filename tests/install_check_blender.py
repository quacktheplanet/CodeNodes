"""Inside Blender, for tests/install_check.ps1: prove the installed extension is enabled and
that its assistant link started by itself (no button, no token to copy), keep Blender open
while the MCP client works (the runner creates a `done` file), then check the preference
turns the link off and on again, and quit. Ten minutes at most.

Run with a throwaway profile (BLENDER_USER_RESOURCES) and WITHOUT --factory-startup, so the
installed extension's enabled state is what a real user would get.
"""

import os
import sys
import time

import bpy

DONE = os.environ.get("CODENODES_CHECK_DONE", "")
LIMIT = time.time() + 600
_state = {"phase": "wait_link", "t": time.time()}


def say(text):
    print("INSTALL_CHECK", text, flush=True)          # "CodeNodes test" in the log


def _server():
    enabled = sorted(a.module for a in bpy.context.preferences.addons if "codenodes" in a.module)
    return enabled, (sys.modules.get(enabled[0] + ".server") if enabled else None)


def _prefs(module):
    return bpy.context.preferences.addons[module].preferences


def tick():
    enabled, server = _server()
    phase = _state["phase"]
    if phase == "wait_link":
        if not enabled or server is None:
            say("FAIL the CodeNodes extension is not enabled")
            return _quit()
        info = server.status()
        if info["running"]:
            path = server.instance_path()
            say(f"module {enabled[0]} version {bpy.app.version_string}")
            say(f"link started by itself: port={info['port']} registered={os.path.exists(path)}")
            say(f"assistant panel gone: {not hasattr(bpy.types, 'CODENODES_PT_server')}")
            say("READY")
            _state["phase"] = "serving"
        elif time.time() - _state["t"] > 20:
            say(f"FAIL the link did not start by itself ({info.get('error', 'not running')})")
            return _quit()
        return 0.5
    if phase == "serving":
        if (DONE and os.path.exists(DONE)) or time.time() > LIMIT:
            prefs = _prefs(enabled[0])
            prefs.link_enabled = False
            off = server.status()["running"]
            gone = not os.path.exists(server.instance_path())
            say(f"{'ok' if not off and gone else 'FAIL'} preference off stops the link "
                f"(running={off}, registration removed={gone})")
            prefs.link_enabled = True
            _state["phase"], _state["t"] = "back_on", time.time()
        return 0.5
    if phase == "back_on":
        info = server.status()
        if info["running"]:
            say(f"ok preference on starts it again (port {info['port']})")
            return _quit()
        if time.time() - _state["t"] > 10:
            say("FAIL preference on did not start the link again")
            return _quit()
        return 0.5
    return None


def _quit():
    say("quitting")
    bpy.ops.wm.quit_blender()
    return None


print("CodeNodes test: install check (this window closes by itself)", flush=True)
bpy.app.timers.register(tick, first_interval=1.0)
