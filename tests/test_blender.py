"""Code -> Mesh inside real Blender. Needs a window (the GPU isn't available with -b):

    blender --factory-startup --python tests/test_blender.py

Runs from a timer once the window is up, prints each check, ends with
"ALL n CHECKS PASSED" or "FAIL: ...", then quits.
"""
import math
import os
import sys
import time
import traceback

import bmesh
import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import api, live, sampler  # noqa: E402
from codenodes.sdf_code import SdfCodeError  # noqa: E402

_checks = 0


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def verts(obj):
    co = np.empty(len(obj.data.vertices) * 3, np.float32)
    obj.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def manifold(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    ok = all(e.is_manifold for e in bm.edges) and len(bm.edges) > 0
    euler = len(bm.verts) - len(bm.edges) + len(bm.faces)
    bm.free()
    return ok, euler


SPHERE = """
// @param radius 1.0 0.2 2.0
float sdf(vec3 p) { return sdSphere(p, radius); }
"""


def run_checks():
    # --- basic build -----------------------------------------------------------------
    r = api.code_to_mesh(SPHERE, name="T_Sphere", resolution=96)
    check(r["ok"] and r["faces"] > 1000, f"sphere builds ({r['faces']:,} faces; {r['stats']})")
    obj = bpy.data.objects["T_Sphere"]
    rad = np.linalg.norm(verts(obj), axis=1)
    check(abs(rad.mean() - 1.0) < 0.01 and rad.std() < 0.01, f"vertices sit on radius 1 (mean {rad.mean():.4f})")
    check(not obj.data.validate(verbose=False), "mesh passes Blender's own validation")
    ok, euler = manifold(obj)
    check(ok and euler == 2, f"mesh is manifold and closed (Euler {euler})")
    check([p.name for p in obj.codenodes.params] == ["radius"], "@param shows up as a slider")

    # --- params ----------------------------------------------------------------------
    r = api.set_params("T_Sphere", radius=1.5)
    rad = np.linalg.norm(verts(obj), axis=1)
    check(r["ok"] and abs(rad.mean() - 1.5) < 0.015, f"changing the slider rebuilds (radius now {rad.mean():.3f})")
    r = api.code_to_mesh(SPHERE.replace("float sdf", "// @param squash 1.0 0.1 2.0\nfloat sdf")
                         .replace("sdSphere(p, radius)", "sdSphere(p * vec3(1, 1, squash), radius)"), name="T_Sphere")
    vals = r["params"]
    check(r["ok"] and vals.get("radius") == 1.5 and vals.get("squash") == 1.0,
          f"editing the code keeps slider values and adds new ones ({vals})")

    # --- materials and modifiers survive a rebuild -----------------------------------------
    mat = bpy.data.materials.new("T_Mat")
    obj.data.materials.append(mat)
    obj.modifiers.new("Bevel", 'BEVEL')
    meshes_before = len(bpy.data.meshes)
    api.set_params("T_Sphere", radius=1.2)
    check(obj.data.materials[:] == [mat] and "Bevel" in obj.modifiers, "materials and modifiers survive a rebuild")
    check(len(bpy.data.meshes) == meshes_before, "old mesh data is freed (no orphan meshes)")

    # --- errors never break the object -----------------------------------------------
    faces_before = len(obj.data.polygons)
    bad = "// @param radius 1.0\nfloat sdf(vec3 p) {\n  return lenght(p) - radius;\n}\n"
    r = api.code_to_mesh(bad, name="T_Sphere")
    check(not r["ok"] and "line 3" in r["error"] and "lenght" in r["error"],
          f"compile error names the user's line: {r['error'].splitlines()[1].strip()!r}")
    check(len(obj.data.polygons) == faces_before, "a failed build leaves the mesh as it was")
    r = api.code_to_mesh("float dist(vec3 p) { return 1.0; }", name="T_Sphere")
    check(not r["ok"] and "float sdf(vec3 p)" in r["error"], "missing sdf() is explained")
    r = api.code_to_mesh(SPHERE, name="T_Sphere", resolution=4000)
    check(not r["ok"] and "resolution" in r["error"], "out-of-range resolution is refused")
    r = api.code_to_mesh(SPHERE, name="T_Sphere", bounds_min=(1, 1, 1), bounds_max=(-1, -1, -1))
    check(not r["ok"] and "bounds" in r["error"], "inverted bounds are refused")
    r = api.code_to_mesh("// @param x 1\n// @param x 2\nfloat sdf(vec3 p){return 1.0;}", name="T_Sphere")
    check(not r["ok"] and "twice" in r["error"], "duplicate @param is refused")
    r = api.code_to_mesh("float sdf(vec3 p) { return 10.0; }", name="T_Empty")
    check(r["ok"] and r["faces"] == 0 and "no surface" in r["stats"], "no surface in bounds is an empty mesh, not an error")
    r = api.code_to_mesh("float sdf(vec3 p) { return 0.0 / 0.0; }", name="T_NaN")
    check(r["ok"] and r["faces"] == 0, "NaN everywhere is handled")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    r = api.code_to_mesh(SPHERE, name="T_Sphere")
    bpy.ops.object.mode_set(mode='OBJECT')
    check(not r["ok"] and "Edit Mode" in r["error"], "rebuilding in Edit Mode is refused cleanly")

    # --- the time guard refuses runaway code before running it all --------------------
    slow = "float sdf(vec3 p){ float d = length(p) - 1.0; for (int i = 0; i < 20000; i++) d += 1e-9 * sin(d + float(i)); return d; }"
    try:
        sampler.sample(slow, (-2, -2, -2), (2, 2, 2), 256, budget_s=0.05)
        check(False, "slow code is refused")
    except SdfCodeError as exc:
        check("too slow" in str(exc), f"slow code is refused before it stalls the GPU ({str(exc)[:70]}...)")

    # --- topology and shapes ------------------------------------------------------------
    r = api.code_to_mesh("float sdf(vec3 p){ return sdTorus(p, 1.0, 0.35); }", name="T_Torus", resolution=128)
    ok, euler = manifold(bpy.data.objects["T_Torus"])
    check(r["ok"] and ok and euler == 0, f"torus has one hole (Euler {euler})")
    r = api.code_to_mesh("float sdf(vec3 p){ return sdBox(p, vec3(0.8, 0.5, 0.3)); }", name="T_Box",
                         resolution=64, bounds_min=(-1, -1, -1), bounds_max=(1, 1, 1))
    ext = np.ptp(verts(bpy.data.objects["T_Box"]), axis=0)
    check(r["ok"] and np.allclose(ext, [1.6, 1.0, 0.6], atol=0.05), f"box has the right size ({np.round(ext, 3)})")

    # --- animation through uTime ----------------------------------------------------------
    from codenodes.sdf_code import TEMPLATE
    r = api.code_to_mesh(TEMPLATE, name="T_Blobs", resolution=96, animate=True)
    check(r["ok"] and r["faces"] > 1000, f"the liquid-metal template builds ({r['faces']:,} faces)")
    blobs = bpy.data.objects["T_Blobs"]
    scene = bpy.context.scene
    scene.frame_set(1)
    a = verts(blobs).mean(axis=0)
    scene.frame_set(40)
    b = verts(blobs).mean(axis=0)
    check(np.linalg.norm(a - b) > 0.01, f"Animate rebuilds on frame change (centre moved {np.linalg.norm(a - b):.3f})")
    blobs.codenodes.animate = False

    # --- stress: many rebuilds at mixed resolutions -------------------------------------
    t0 = time.perf_counter()
    for k in range(40):
        api.set_params("T_Sphere", radius=0.5 + (k % 7) * 0.2)
        obj.codenodes["resolution"] = (48, 160, 96, 224)[k % 4]
    check(True, f"40 rebuilds at mixed resolutions without trouble ({time.perf_counter() - t0:.1f} s)")
    # five objects were added since the count (empty, NaN, torus, box, blobs)
    check(len(bpy.data.meshes) == meshes_before + 5, "no mesh data leaks across rebuilds")

    # --- high resolution -----------------------------------------------------------------
    r = api.code_to_mesh(TEMPLATE, name="T_Hi", resolution=384)
    check(r["ok"] and r["faces"] > 100_000, f"resolution 384 works: {r['stats']}")

    # --- Claude-style live edit: change the text, the object follows -----------------------
    text = bpy.data.objects["T_Torus"].codenodes.text
    text.from_string("float sdf(vec3 p){ return sdTorus(p, 1.2, 0.2); }")
    return text


def after_live_edit(text, started):
    obj = bpy.data.objects["T_Torus"]
    r = np.linalg.norm(verts(obj)[:, :2], axis=1)
    if r.max() < 1.35 and time.perf_counter() - started < 5:
        return False                       # not rebuilt yet; poll again
    check(1.38 < r.max() < 1.45, f"editing the code text rebuilds live (outer radius {r.max():.3f})")

    # --- Make Plain Mesh (it once shared an id with Bake to Disk and was shadowed) ----------
    bpy.context.view_layer.objects.active = obj
    check(bpy.ops.codenodes.make_plain() == {'FINISHED'} and not obj.codenodes.enabled
          and len(obj.data.vertices) > 0, "Make Plain Mesh keeps the mesh and stops the code driving it")
    codenodes.unregister()
    codenodes.register()
    check(hasattr(bpy.ops.codenodes, "make_plain"), "the add-on unregisters and registers again cleanly")
    return True


def main():
    codenodes.register()
    state = {"phase": 0}

    def tick():
        try:
            if state["phase"] == 0:
                state["text"] = run_checks()
                state["t"] = time.perf_counter()
                state["phase"] = 1
                return 0.3
            if not after_live_edit(state["text"], state["t"]):
                return 0.3
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
