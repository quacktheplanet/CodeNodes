"""Close-up of a few live fireflies (Firefly Look) to check the wings sit on their glowing bodies.

    blender --factory-startup --window-geometry 0 0 1200 800 --python tests/shot_one_firefly.py

Writes one_firefly.png into CODENODES_SHOTS (default: examples/gpu_demo_out) and prints each firefly's
position and where its glow and wings draw, then quits.
"""
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Euler, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gpu_live, live, particles  # noqa: E402

SHOTS = os.environ.get("CODENODES_SHOTS", os.path.join(ROOT, "examples", "gpu_demo_out"))
STEP = {"n": 0}


def view3d(win):
    return next(a for a in win.screen.areas if a.type == 'VIEW_3D')


def tick():
    STEP["n"] += 1
    n = STEP["n"]
    win = bpy.context.window_manager.windows[0]
    try:
        if n == 1:
            for o in list(bpy.data.objects):
                bpy.data.objects.remove(o)
            obj = bpy.data.objects.new("Flies", bpy.data.meshes.new("Flies"))
            bpy.context.scene.collection.objects.link(obj)
            tree = bpy.data.node_groups.new("Flies Tree", "GeometryNodeTree")
            tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
            tree.nodes.new("NodeGroupOutput")
            obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
            nodes = []
            for i, (k, key) in enumerate((('PARTICLES', "Firefly Swarm"), ('STAGE', "Firefly Look"))):
                g, err = gn_link.create(k, key)
                nodes.append(gn_link.insert(tree, g, (i * 300, 0)))
            tree.links.new(nodes[0].outputs["Particles"], nodes[1].inputs["Particles"])
            STEP["nodes"] = [nd.name for nd in nodes]
            STEP["head"] = gn_link.source_of(nodes[0].node_tree).name
            for _ in range(3):
                gn_link.sync()
                live._flush()
            tree.nodes[STEP["nodes"][0]].inputs["Count"].default_value = 3
            tree.nodes[STEP["nodes"][0]].inputs["radius"].default_value = 0.05
            tree.nodes[STEP["nodes"][1]].inputs["size"].default_value = 0.05
            area = view3d(win)
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screen_full_area()
            return 1.0
        if n == 2:
            for _ in range(3):
                gn_link.sync()
                live._flush()
            area = view3d(win)
            sp = area.spaces.active
            sp.overlay.show_overlays = False
            sp.shading.background_type = 'VIEWPORT'
            sp.shading.background_color = (0.02, 0.03, 0.06)
            bpy.context.scene.frame_set(20)
            return 1.0
        if n == 3:
            name = STEP["head"] + gpu_live.PREVIEW_SUFFIX
            sim = particles._sims.get(name) or particles._sims.get(STEP["head"])
            pos = np.asarray(sim.read(0, None, (), 0.0, 0)["position"]) if sim else np.zeros((1, 3))
            centre = Vector(pos.mean(axis=0)) if len(pos) else Vector()
            print("FIREFLIES", np.round(pos, 3).tolist(), flush=True)
            area = view3d(win)
            r3 = area.spaces.active.region_3d
            r3.view_perspective = 'PERSP'
            r3.view_location = centre
            r3.view_rotation = Euler((math.radians(70), 0.0, math.radians(20))).to_quaternion()
            r3.view_distance = 0.6
            STEP["prefs"] = gpu_live.preview_allowed()
            return 0.6
        if n == 4:
            area = view3d(win)
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screenshot_area(filepath=os.path.join(SHOTS, "one_firefly.png"))
            print("ONE FIREFLY SHOT", os.path.join(SHOTS, "one_firefly.png"), flush=True)
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
