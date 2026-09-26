"""Baking a CodeNodes animation to disk, and playing it back with plain Geometry Nodes.

    blender --factory-startup --python tests/test_bake.py

Needs a window (baking uses the GPU). Saves a .blend at the end that tests/test_farm.py
opens in background Blender, with no GPU and without this add-on, to prove the bake
renders anywhere.
"""
import os
import shutil
import sys
import tempfile
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import api, cache, live  # noqa: E402
from codenodes.sdf_code import TEMPLATE  # noqa: E402

_checks = 0
WORK = os.path.join(tempfile.gettempdir(), "codenodes_bake_test")
BLEND = os.path.join(WORK, "baked.blend")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def shape(obj):
    """(vertex count, summed extent) of the object as Blender actually evaluates it."""
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    n = len(me.vertices)
    ext = 0.0
    if n:
        co = np.empty(n * 3, np.float32)
        me.vertices.foreach_get("co", co)
        ext = round(float(np.ptp(co.reshape(-1, 3), axis=0).sum()), 3)
    ev.to_mesh_clear()
    return n, ext


def run():
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)      # so the cache lands next to the .blend

    scene = bpy.context.scene
    if "Cube" in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects["Cube"])
    r = api.code_to_mesh(TEMPLATE, name="Blob", resolution=72, animate=True)
    check(r["ok"], f"animated blob builds ({r['stats']})")
    obj = bpy.data.objects["Blob"]

    live_shape = {}
    for f in (1, 4, 8):
        scene.frame_set(f)
        live_shape[f] = shape(obj)
    check(len({s for s in live_shape.values()}) == 3, f"the live mesh differs per frame {live_shape}")

    res = api.bake("Blob", 1, 8)
    check(res["ok"] and res["baked"], f"bake reports success ({res['stats']})")
    files = sorted(f for f in os.listdir(res["dir"]) if f.endswith(".ply"))
    check(len(files) == 8, f"one file per frame on disk ({len(files)} files)")
    check(all(os.path.getsize(os.path.join(res["dir"], f)) > 1000 for f in files), "every baked file has real content")
    check(res["dir"].replace("\\", "/").startswith(os.path.dirname(BLEND).replace("\\", "/")),
          "the cache sits next to the .blend")

    mod = obj.modifiers.get(cache.MODIFIER_NAME)
    check(mod is not None and mod.type == 'NODES' and mod.node_group is not None, "a Geometry Nodes cache modifier is attached")
    kinds = {n.bl_idname for n in mod.node_group.nodes}
    check("GeometryNodeImportPLY" in kinds and "FunctionNodeFormatString" in kinds,
          "the group reads the frame's file with stock nodes")
    check(len(obj.data.vertices) == 0, "the object's stored mesh is emptied (the cache supplies it)")
    check(not obj.codenodes.animate and not obj.codenodes.live, "live rebuilding is switched off after baking")

    baked_shape = {}
    for f in (1, 4, 8):
        scene.frame_set(f)
        baked_shape[f] = shape(obj)
    check(baked_shape == live_shape, f"the cache plays back exactly what was live {baked_shape}")

    scene.frame_set(99)
    check(shape(obj) == live_shape[8], "frames past the end hold the last baked frame (no missing-file errors)")
    scene.frame_set(-5)
    check(shape(obj) == live_shape[1], "frames before the start hold the first baked frame")

    # the GPU is not involved any more: other GN nodes can now work on the result
    scene.frame_set(4)
    before = shape(obj)
    extra = obj.modifiers.new("Wire", 'WIREFRAME')
    after = shape(obj)
    check(after[0] > before[0], f"another modifier can work on the cached geometry ({before[0]} -> {after[0]} verts)")
    obj.modifiers.remove(extra)

    bpy.ops.codenodes.unbake('EXEC_DEFAULT', object_name="Blob")
    check(obj.modifiers.get(cache.MODIFIER_NAME) is None and obj.codenodes.live, "unbake removes the cache and restores live")
    scene.frame_set(4)
    check(shape(obj)[0] == live_shape[4][0], "the object rebuilds live again after unbake")

    # bake a node graph too, then leave the file baked for the farm test
    from codenodes import nodes
    tree, out = nodes.new_demo_graph("BakeGraph")
    nodes.build_output(tree, out)
    out.resolution = 64
    bpy.ops.codenodes.bake('EXEC_DEFAULT', tree=tree.name, node=out.name, frame_start=1, frame_end=4)
    check(cache.is_baked(out.target), "a node graph's Mesh Output bakes too")
    graph_shapes = set()
    for f in (1, 2, 3, 4):
        scene.frame_set(f)
        graph_shapes.add(shape(out.target))
    check(len(graph_shapes) > 1, f"the baked graph animates ({len(graph_shapes)} distinct frames)")

    api.bake("Blob", 1, 8)
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    print(f"BLEND {BLEND}", flush=True)
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


def main():
    codenodes.register()

    def tick():
        try:
            run()
        except Fail as exc:
            print(f"FAIL: {exc}", flush=True)
        except Exception:
            traceback.print_exc()
            print("FAIL: exception", flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=0.6)


main()
