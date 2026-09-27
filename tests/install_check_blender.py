"""Inside Blender, for tests/install_check.ps1: prove the installed extension is enabled,
start its server the way the Start button does, and keep Blender open until the MCP client
is finished (the runner creates a `done` file) or ten minutes pass.

Run with a throwaway profile (BLENDER_USER_RESOURCES) and WITHOUT --factory-startup, so the
installed extension's enabled state is what a real user would get.
"""

import os
import sys
import time

import bpy

DONE = os.environ.get("CODENODES_CHECK_DONE", "")
LIMIT = time.time() + 600


def say(text):
    print("INSTALL_CHECK", text, flush=True)          # "CodeNodes test" in the log


def main():
    print("CodeNodes test: install check (this window closes by itself)", flush=True)
    enabled = sorted(a.module for a in bpy.context.preferences.addons if "codenodes" in a.module)
    say(f"enabled {enabled}")
    if not enabled or not hasattr(bpy.ops.codenodes, "server"):
        say("FAIL the CodeNodes extension is not enabled")
        bpy.ops.wm.quit_blender()
        return
    say(f"module {enabled[0]} version {bpy.app.version_string}")
    result = bpy.ops.codenodes.server(action='START')
    say(f"start {result}")
    server = sys.modules[enabled[0] + ".server"]
    info = server.status()
    say(f"status running={info['running']} port={info['port']} token_file={server.token_path()}")
    if not info["running"]:
        say("FAIL the server did not start")
        bpy.ops.wm.quit_blender()
        return
    say("READY")
    bpy.app.timers.register(_watch, first_interval=1.0, persistent=True)


def _watch():
    if (DONE and os.path.exists(DONE)) or time.time() > LIMIT:
        say("quitting")
        bpy.ops.wm.quit_blender()
        return None
    return 1.0


bpy.app.timers.register(main, first_interval=1.0)
