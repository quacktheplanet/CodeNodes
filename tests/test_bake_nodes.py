"""Bake to Nodes: the code becomes a Geometry Nodes network that matches the GPU mesh.

    blender --factory-startup --python tests/test_bake_nodes.py

Needs a window (the reference mesh comes from the GPU) and ExpressNode: a sibling
checkout at ../ExpressNode, or its path in CODENODES_EXPRESSION_NODES.
"""
import os
import subprocess
import sys
import tempfile
import traceback

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.environ.get("CODENODES_EXPRESSION_NODES",
                                  os.path.join(os.path.dirname(ROOT), "ExpressNode")))
import codenodes  # noqa: E402
from codenodes import api, mod_inputs, sdf_code  # noqa: E402

RING = """// @param radius 1.0 0.2 1.6
// @param thickness 0.3 0.05 0.6
float ring(vec3 q, float r) { return sdTorus(q, r, thickness); }
float sdf(vec3 p) {
  float d = smin(ring(p, radius), sdSphere(p, 0.6), 0.4);
  return max(d, -sdBox(p - vec3(0.0, 0.0, 0.9), vec3(0.45)));
}
"""
SPIN = """float sdf(vec3 p) {
  vec3 q = rotateZ(p, uTime * 1.5);
  return sdRoundBox(q, vec3(1.0, 0.4, 0.3), 0.1);
}
"""
_checks = 0


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def surface(obj):
    """(vertices, BVH) of what an object evaluates to."""
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    geometry = obj.evaluated_get(dg).evaluated_geometry()
    mesh = geometry.mesh
    verts = np.array([v.co[:] for v in mesh.vertices]) if mesh else np.zeros((0, 3))
    polys = [tuple(p.vertices) for p in mesh.polygons] if mesh else []
    return verts, BVHTree.FromPolygons([tuple(v) for v in verts], polys)


def apart(a, b):
    """(max, mean) distance from a's vertices to b's surface, and back."""
    va, ta = surface(a)
    vb, tb = surface(b)
    ab = np.array([tb.find_nearest(tuple(v))[3] for v in va[::7]])
    ba = np.array([ta.find_nearest(tuple(v))[3] for v in vb[::7]])
    return max(ab.max(), ba.max()), (ab.mean() + ba.mean()) / 2


def run():
    voxel = 4.0 / 96
    ref = api.code_to_mesh(RING, name="RingGPU", resolution=96)
    made = api.code_to_mesh(RING, name="Ring", resolution=96)
    check(ref["ok"] and made["ok"], "two copies of the same code on the GPU")
    got = api.bake_to_nodes("Ring")
    check(got["ok"] and got["sliders"] == ["Resolution", "radius", "thickness"],
          f"the code becomes a node network, with its sliders ({got.get('error') or got['sliders']})")
    ring = bpy.data.objects["Ring"]
    check(not ring.codenodes.enabled and ring.codenodes.text is not None,
          "the object is driven by the nodes now, and keeps its code")
    worst, mean = apart(ring, bpy.data.objects["RingGPU"])
    nodes_n, gpu_n = len(surface(ring)[0]), len(surface(bpy.data.objects["RingGPU"])[0])
    check(nodes_n > 1000 and worst < voxel and mean < voxel / 4,
          f"the node mesh lies on the GPU mesh (worst {worst:.4f}, mean {mean:.4f}; a voxel is {voxel:.4f}; "
          f"{nodes_n} vs {gpu_n} verts: OpenVDB's mesher and ours both average the edge crossings)")

    mod = ring.modifiers["Code as Nodes"]
    ident = next(i.identifier for i in mod.node_group.interface.items_tree
                 if i.item_type == 'SOCKET' and i.name == "radius")
    mod_inputs.of(mod)[ident] = 1.4
    ring.update_tag()
    control, _ = apart(ring, bpy.data.objects["RingGPU"])       # the GPU copy is still at 1.0
    check(control > 0.3, f"(and the comparison can tell: at different radii they are {control:.3f} apart)")
    api.set_params("RingGPU", radius=1.4)
    worst, mean = apart(ring, bpy.data.objects["RingGPU"])
    check(worst < voxel and mean < voxel / 4,
          f"a slider on the modifier changes it the way the code would (worst {worst:.4f})")

    scene = bpy.context.scene
    scene.frame_set(1)
    api.code_to_mesh(SPIN, name="SpinGPU", resolution=96)
    bpy.data.objects["SpinGPU"].codenodes.animate = True
    api.code_to_mesh(SPIN, name="Spin", resolution=96)
    check(api.bake_to_nodes("Spin")["ok"], "animated code converts too")
    first = surface(bpy.data.objects["Spin"])[0]
    scene.frame_set(13)
    later = surface(bpy.data.objects["Spin"])[0]
    worst, mean = apart(bpy.data.objects["Spin"], bpy.data.objects["SpinGPU"])
    check(np.abs(first.mean(axis=0) - later.mean(axis=0)).max() > 0 or len(first) != len(later),
          "it moves as the frame changes, with no GPU")
    check(worst < voxel and mean < voxel / 4,
          f"and at frame 13 it matches the GPU's uTime exactly (worst {worst:.4f})")

    loopy = api.code_to_mesh(sdf_code.TEMPLATE, name="Blobs", resolution=48)
    before = len(bpy.data.objects["Blobs"].data.vertices)
    refused = api.bake_to_nodes("Blobs")
    check(loopy["ok"] and not refused["ok"] and "loop" in refused["error"]
          and refused.get("fallback") == "bake" and bpy.data.objects["Blobs"].codenodes.enabled
          and len(bpy.data.objects["Blobs"].data.vertices) == before,
          f"code with a loop is refused, says why, and is left as it was ({refused['error']})")

    # the file opens and renders in a Blender with no add-ons at all: stock nodes only
    for name in ("RingGPU", "SpinGPU", "Blobs"):
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    scene.frame_set(1)
    count = len(surface(ring)[0])
    path = os.path.join(tempfile.mkdtemp(), "nodes_only.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    probe = ("import bpy; dg = bpy.context.evaluated_depsgraph_get(); "
             "g = bpy.data.objects['Ring'].evaluated_get(dg).evaluated_geometry(); "
             "print('VERTS', len(g.mesh.vertices) if g.mesh else 0)")
    out = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", path, "--python-expr", probe],
                         capture_output=True, text=True, timeout=300).stdout
    verts = int(next((ln.split()[1] for ln in out.splitlines() if ln.startswith("VERTS")), "0"))
    check(verts == count, f"saved, it opens with no add-ons and builds the same mesh ({verts} vs {count} verts)")


def main():
    codenodes.register()

    def tick():
        try:
            run()
            print(f"\nALL {_checks} CHECKS PASSED", flush=True)
        except Fail as exc:
            print(f"FAIL: {exc}", flush=True)
        except Exception:
            traceback.print_exc()
            print("FAIL: exception", flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=0.5)


main()
