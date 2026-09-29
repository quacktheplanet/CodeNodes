"""Screenshot of a socket's hover tooltip in the node editor (Wander's "calm"), using simulated mouse events.

    blender --factory-startup --enable-event-simulate --window-geometry 0 0 1400 900 --python tests/shot_tooltip.py

Writes tooltip_calm.png into CODENODES_SHOTS (default: examples/gpu_demo_out), then quits.
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, live  # noqa: E402

SHOTS = os.environ.get("CODENODES_SHOTS", os.path.join(ROOT, "examples", "gpu_demo_out"))
STEP = {"n": 0, "try": 0}


def area_of(win, kind):
    return next((a for a in win.screen.areas if a.type == kind), None)


def tick():
    STEP["n"] += 1
    n = STEP["n"]
    win = bpy.context.window_manager.windows[0]
    try:
        if n == 1:
            import tempfile
            path = os.path.join(tempfile.gettempdir(), "cn_tooltip_start.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)        # closes the splash screen
            return 0.8
        if n == 2:
            for o in list(bpy.data.objects):
                bpy.data.objects.remove(o)
            obj = bpy.data.objects.new("Flies", bpy.data.meshes.new("Flies"))
            bpy.context.scene.collection.objects.link(obj)
            tree = bpy.data.node_groups.new("Flies Tree", "GeometryNodeTree")
            tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
            tree.nodes.new("NodeGroupOutput").location = (700, 0)
            obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
            g0, _e = gn_link.create('PARTICLES', "Firefly Swarm")
            g1, _e = gn_link.create('STAGE', "Wander")
            a = gn_link.insert(tree, g0, (0, 0))
            b = gn_link.insert(tree, g1, (300, 0))
            tree.links.new(a.outputs["Particles"], b.inputs["Particles"])
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            for _ in range(3):
                gn_link.sync()
                live._flush()
            STEP["node"] = b.name
            v3 = area_of(win, 'VIEW_3D')
            v3.type = 'NODE_EDITOR'
            v3.ui_type = 'GeometryNodeTree'
            return 0.8
        if n == 3:
            ne = area_of(win, 'NODE_EDITOR')
            sp = ne.spaces.active
            sp.node_tree = bpy.data.node_groups["Flies Tree"]
            sp.show_region_ui = False                    # the sidebar would cover the node
            tree = sp.node_tree
            for nd in tree.nodes:
                nd.select = nd.name == STEP["node"]
            tree.nodes.active = tree.nodes[STEP["node"]]
            region = next(r for r in ne.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=win, area=ne, region=region):
                bpy.ops.node.view_selected()
            return 0.8
        if n == 4:
            ne = area_of(win, 'NODE_EDITOR')
            region = next(r for r in ne.regions if r.type == 'WINDOW')
            nd = ne.spaces.active.node_tree.nodes[STEP["node"]]
            # the "calm" row: find it by scanning downwards from the node's top-left
            x0, y0 = region.view2d.view_to_region(nd.location.x, nd.location.y, clip=False)
            x1, y1 = region.view2d.view_to_region(nd.location.x + nd.dimensions.x, nd.location.y - nd.dimensions.y,
                                                  clip=False)
            rows = [s.name for s in nd.inputs if not s.hide]
            outs = len([s for s in nd.outputs if not s.hide])
            k = rows.index("calm")
            total = outs + len(rows) + 1                   # header + outputs + group selector row + inputs
            frac = (1.6 + outs + 1 + k) / (total + 1.2)
            STEP["pt"] = (region.x + int(x0 + (x1 - x0) * 0.35), region.y + int(y0 - (y0 - y1) * frac))
            STEP["box"] = (x0, y0, x1, y1)
            win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=STEP["pt"][0], y=STEP["pt"][1])
            return 1.6                                     # tooltips open after a short delay
        if n == 5:
            win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=STEP["pt"][0] + 1, y=STEP["pt"][1])
            return 1.4
        if n == 6:
            with bpy.context.temp_override(window=win):
                bpy.ops.screen.screenshot(filepath=os.path.join(SHOTS, "tooltip_calm.png"))
            print("TOOLTIP SHOT", os.path.join(SHOTS, "tooltip_calm.png"), STEP["pt"], STEP["box"], flush=True)
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
