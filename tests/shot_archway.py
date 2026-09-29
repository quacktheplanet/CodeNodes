"""Screenshot of the castle gate where the live (raymarched) castle meets the real plateau mesh, used to check
the edge where the two meet (the thin dark jagged line the user saw at the bottom of the archway).

    blender --factory-startup --window-geometry 0 0 1600 960 --python tests/shot_archway.py

Needs the demo file (examples/gpu_demo_out/codenodes_demo_v4_1.blend; set CODENODES_DEMO to use another) and a
window. Writes archway_<tag>.png into CODENODES_SHOTS (default: examples/gpu_demo_out), then quits.
"""
import math
import os
import sys

import bpy
from mathutils import Euler, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, live  # noqa: E402

DEMO = os.environ.get("CODENODES_DEMO", os.path.join(ROOT, "examples", "gpu_demo_out", "codenodes_demo_v4_1.blend"))
SHOTS = os.environ.get("CODENODES_SHOTS", os.path.join(ROOT, "examples", "gpu_demo_out"))
TAG = os.environ.get("CODENODES_TAG", "now")
STEP = {"n": 0}


def view3d(win):
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            return area
    return None


def tick():
    STEP["n"] += 1
    n = STEP["n"]
    win = bpy.context.window_manager.windows[0]
    try:
        if n == 1:
            bpy.ops.wm.open_mainfile(filepath=DEMO)
            return 1.0
        if n == 2:
            win.scene = bpy.data.scenes["Fireflies"]
            area = view3d(win)
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screen_full_area()
            return 1.0
        if n == 3:
            area = view3d(win)
            sp = area.spaces.active
            r3 = sp.region_3d
            r3.view_perspective = 'PERSP'
            r3.view_location = Vector((0.0, -0.9, 1.2))
            r3.view_rotation = Euler((math.radians(84), 0.0, math.radians(0))).to_quaternion()
            r3.view_distance = 1.3
            sp.overlay.show_overlays = False
            sp.lens = 40
            for _ in range(3):
                gn_link.sync()
                live._flush()
            return 1.5
        if n == 4:
            area = view3d(win)
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screenshot_area(filepath=os.path.join(SHOTS, f"archway_{TAG}.png"))
            print("ARCHWAY SHOT", os.path.join(SHOTS, f"archway_{TAG}.png"), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception:
        import traceback
        traceback.print_exc()
        bpy.ops.wm.quit_blender()
        return None
    return 0.5


codenodes.register()
bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
