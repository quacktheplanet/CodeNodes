"""Render form: GPU Mesh code and warps compiled to native Geometry Nodes. Background Blender, with
ExpressNode as a sibling checkout (../ExpressNode) or at CODENODES_EXPRESSION_NODES:

    blender -b --factory-startup --python tests/test_render_form.py

Every shipped deform and warp starter is translated: the ones that convert must build, evaluate with no
GPU and no CodeNodes code running, animate with the timeline, and take their sliders from the modifier;
the ones that don't must say why. On Blender 5.2 or later, where gpu.init() gives background Blender the
GPU, each converted starter is also run by the GPU deform path and the positions, `value` and `color`
compared. Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""
import math
import os
import sys
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.environ.get("CODENODES_EXPRESSION_NODES",
                                  os.path.join(os.path.dirname(ROOT), "ExpressNode")))
import codenodes  # noqa: E402,F401
from codenodes import deform, render_form, stage_templates  # noqa: E402
from codenodes.bake_nodes import CannotConvert  # noqa: E402

_checks = 0
CONVERTS = {"Wave", "Twist", "Ripple", "Colour by Height", "Bend", "Taper"}
REFUSED = {"Noise Displace": "fbm3", "Mesa": "fbm3", "Planet Terrain": "fbm3", "Sway by Field": "function input"}
TOLERANCE = 2e-4                   # metres: float32 GPU maths against the nodes' maths


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def starter(name):
    return deform.TEMPLATES.get(name) or stage_templates.STAGES[name]


def mesh_object(name):
    """A subdivided ico sphere with a stored `value` (so Colour by Height has something to paint)."""
    me = bpy.data.meshes.new(name)
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=4, radius=1.5)
    bm.to_mesh(me)
    bm.free()
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    attr = me.attributes.new("value", 'FLOAT', 'POINT')
    attr.data.foreach_set("value", np.sin(co.reshape(-1, 3)[:, 2] * 2.0).astype(np.float32))
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def evaluated(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.data
    n = len(me.vertices)
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    out = {"co": co.reshape(-1, 3)}
    for name, width in (("value", 1), ("color", 4)):
        a = me.attributes.get(name)
        if a is not None:
            buf = np.empty(n * width, np.float32)
            a.data.foreach_get("value" if width == 1 else "color", buf)
            out[name] = buf.reshape(-1, width) if width > 1 else buf
    return out


def gpu_source(source):
    """The starter as GPU Mesh code with its slider defaults written in: stages declare `@in` sliders and
    colours (which the GPU Mesh runner doesn't read), and a warp becomes a deform that moves each vertex."""
    from codenodes import decl
    from codenodes.sdf_code import parse_params
    d = decl.parse(source)
    head = []
    runner_reads = {p.name for p in parse_params(source)}      # `@param` sliders: the runner defines them
    for p in d.params:
        if d.kinds.get(p.name) == 'COLOR' or p.name in runner_reads:
            continue
        value = int(p.default) if d.kinds.get(p.name) == 'INT' else float(p.default)
        head.append(f"#define {p.name} {value!r}")
    head += [f"#define {name} vec3({', '.join(repr(next(q.default for q in d.params if q.name == part)) for part in parts)})"
             for name, parts in d.colors.items()]
    body = "\n".join(line for line in source.splitlines() if not decl.DECL_LINE.match(line))
    if "warp" in d.roles:
        body += "\nvoid deform(inout Vertex v) { v.position = warp(v.position); }\n"
    return "\n".join(head) + "\n" + body


def gpu_reference(source, obj, values, time_s):
    source = gpu_source(source)
    me = obj.data
    n = len(me.vertices)
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    nor = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("normal", nor)
    st = deform.MeshState(co.reshape(-1, 3), nor.reshape(-1, 3), np.zeros((0, 3), np.int32), "test")
    check(st.run(source, values, time_s, 0.0), "the GPU deform path runs in background Blender")
    return st.read()


def main():
    scene = bpy.context.scene
    scene.render.fps, scene.frame_start = 24, 1
    has_gpu = False
    try:
        import gpu
        if bpy.app.version >= (5, 2, 0):
            gpu.init()
            has_gpu = True
    except Exception as exc:
        print(f"  (no GPU comparison: {exc})")
    print(f"  Blender {bpy.app.version_string}; GPU comparison {'on' if has_gpu else 'off (needs 5.2+)'}")

    for name, why in REFUSED.items():
        try:
            render_form.translate(starter(name))
            check(False, f"{name} is refused")
        except CannotConvert as exc:
            check(why in str(exc), f"{name} is refused, saying why ({exc})")

    worst = 0.0
    for name in sorted(CONVERTS):
        source = starter(name)
        scene.frame_set(1)
        tree = render_form.build(source, name)
        check(tree is not None and tree.name == render_form.PREFIX + name,
              f"{name}: its render form builds ({len(tree.nodes)} nodes)")
        obj = mesh_object(name)
        before = evaluated(obj)["co"].copy()
        mod = obj.modifiers.new("Render Form", 'NODES')
        mod.node_group = tree
        frame = 25
        scene.frame_set(frame)
        got = evaluated(obj)
        moved = float(np.abs(got["co"] - before).max())
        if name != "Colour by Height":
            check(moved > 1e-3, f"{name}: the nodes move the mesh (up to {moved:.3f} m)")
        else:
            check(moved < 1e-6 and "color" in got, f"{name}: the nodes paint it and leave it in place")
        if "Time" in source or "uTime" in source:
            scene.frame_set(frame + 6)
            later = evaluated(obj)["co"]
            check(float(np.abs(later - got["co"]).max()) > 1e-4, f"{name}: it animates with the timeline")
            scene.frame_set(frame)
        if name == "Colour by Height":
            spread = float(np.ptp(got["color"][:, :3], axis=0).max())
            check(spread > 0.2, f"{name}: it paints by the 'value' an earlier stage stored (colours vary {spread:.2f})")
        if has_gpu:
            if "value" in obj.data.attributes:
                # the GPU Mesh runner starts every vertex's value at 0; compare on a mesh without one
                obj.data.attributes.remove(obj.data.attributes["value"])
                obj.update_tag()
                got = evaluated(obj)
            pos, value, color = gpu_reference(source, obj, {}, (frame - scene.frame_start) / 24.0)
            err = float(np.abs(pos - got["co"]).max())
            worst = max(worst, err)
            check(err < TOLERANCE, f"{name}: positions match the GPU (max error {err:.2e} m)")
            if "v.value" in source and "value =" in source.replace("v.value =", "value ="):
                if "value" in got:
                    verr = float(np.abs(value - got["value"]).max())
                    check(verr < 1e-3, f"{name}: 'value' matches the GPU (max error {verr:.2e})")
            if "v.color" in source and "color" in got:
                cerr = float(np.abs(color - got["color"]).max())
                check(cerr < 1e-3, f"{name}: 'color' matches the GPU (max error {cerr:.2e})")

    # sliders: the modifier's inputs drive the nodes
    obj = bpy.data.objects["Wave"]
    mod = obj.modifiers["Render Form"]
    base = evaluated(obj)["co"].copy()
    ident = next(i.identifier for i in mod.node_group.interface.items_tree
                 if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == "amplitude")
    if hasattr(mod, "properties"):
        getattr(mod.properties.inputs, ident).value = 0.3
    else:
        mod[ident] = 0.3
    obj.update_tag()
    bigger = evaluated(obj)["co"]
    check(float(np.abs(bigger - base).max()) > 1e-3, "a slider on the modifier changes the result")

    # no CodeNodes code runs at evaluation: the trees hold only stock nodes
    stock = all(not n.bl_idname.startswith("CodeNodes") for t in bpy.data.node_groups for n in t.nodes)
    check(stock, "the render forms are stock nodes only")
    if has_gpu:
        print(f"  worst position error against the GPU: {worst:.2e} m")


try:
    main()
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)
except Fail as exc:
    print(f"FAIL: {exc}", flush=True)
except Exception:
    traceback.print_exc()
    print("FAIL: exception", flush=True)
