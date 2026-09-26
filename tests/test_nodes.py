"""The CodeNodes node editor inside real Blender (needs a window for the GPU):

    blender --factory-startup --python tests/test_nodes.py
"""
import os
import sys
import tempfile
import time
import traceback

import bmesh
import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import nodes  # noqa: E402

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


def euler(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    e = len(bm.verts) - len(bm.edges) + len(bm.faces)
    ok = all(ed.is_manifold for ed in bm.edges)
    bm.free()
    return e, ok


def code_node(tree, name, code, loc=(0, 0)):
    text = bpy.data.texts.new(f"{name}.sdf")
    text.from_string(code)
    n = tree.nodes.new("CN_NodeCode")
    n.name = n.label = name
    n.location = loc
    n.text = text
    return n


def run_checks(state):
    # --- starter graph -------------------------------------------------------------
    tree, out = nodes.new_demo_graph("T_Demo")
    err = nodes.build_output(tree, out)
    check(not err and out.target is not None and len(out.target.data.polygons) > 1000,
          f"starter graph builds a mesh ({out.stats})")
    blob = tree.nodes["Blob"]
    check([s.name for s in blob.inputs] == ["radius", "wobble"], "@param lines became input sockets")
    obj = out.target

    # --- socket values and code edits ------------------------------------------------
    blob.inputs["wobble"].default_value = 0.0
    blob.inputs["radius"].default_value = 1.6
    nodes.build_output(tree, out)
    ext_big = np.ptp(verts(obj), axis=0)
    blob.inputs["radius"].default_value = 0.9
    nodes.build_output(tree, out)
    ext_small = np.ptp(verts(obj), axis=0)
    check(ext_big[2] > ext_small[2] + 0.5, f"a socket value changes the mesh (height {ext_big[2]:.2f} -> {ext_small[2]:.2f})")
    blob.text.from_string(blob.text.as_string().replace("float sdf", "// @param stretch 1.0 0.5 2.0\nfloat sdf"))
    nodes.build_output(tree, out)
    check([s.name for s in blob.inputs] == ["radius", "wobble", "stretch"] and
          abs(blob.inputs["radius"].default_value - 0.9) < 1e-6,
          "editing the code adds a socket and keeps the other values")

    # --- an error in one node --------------------------------------------------------
    faces = len(obj.data.polygons)
    good = blob.text.as_string()
    blob.text.from_string(good.replace("sdSphere(p, radius)", "sdSphere(p, radis)"))
    err = nodes.build_output(tree, out)
    check("node 'Blob'" in err and "radis" in err, f"a compile error names the node: {err.splitlines()[0]!r}")
    check("radis" in blob.error and blob.error.startswith("line "), f"the node itself shows the error ({blob.error!r})")
    check(len(obj.data.polygons) == faces, "the mesh is untouched by the failed build")
    blob.text.from_string(good)
    check(not nodes.build_output(tree, out) and not blob.error, "fixing the code clears the errors")

    # --- subtract gives the right topology ---------------------------------------------
    t2 = bpy.data.node_groups.new("T_Sub", nodes.TREE)
    ball = code_node(t2, "Ball", "float sdf(vec3 p){ return sdSphere(p, 1.0); }")
    rod = code_node(t2, "Rod", "float sdf(vec3 p){ return sdCylinder(p, 0.4, 2.0); }")
    sub = t2.nodes.new("CN_NodeCombine")
    sub.operation = 'subtract'
    o2 = t2.nodes.new("CN_NodeMeshOutput")
    t2.links.new(ball.outputs[0], sub.inputs["A"])
    t2.links.new(rod.outputs[0], sub.inputs["B"])
    t2.links.new(sub.outputs[0], o2.inputs[0])
    o2.resolution = 128
    err = nodes.build_output(t2, o2)
    e, ok = euler(o2.target)
    check(not err and ok and e == 0, f"sphere minus rod is a ring (Euler {e})")

    # --- transform, reroute, mute ------------------------------------------------------
    tr = t2.nodes.new("CN_NodeTransform")
    t2.links.new(sub.outputs[0], tr.inputs["SDF"])
    t2.links.new(tr.outputs[0], o2.inputs[0])
    tr.inputs["Location"].default_value = (1.0, 0.0, 0.0)
    tr.inputs["Scale"].default_value = 0.5
    o2.bounds_min, o2.bounds_max = (-1, -1.5, -1.5), (2.5, 1.5, 1.5)
    nodes.build_output(t2, o2)
    v = verts(o2.target)
    centre, size = (v.max(0) + v.min(0)) / 2, np.ptp(v, axis=0)
    check(abs(centre[0] - 1.0) < 0.05 and abs(size[0] - 1.0) < 0.06, f"transform moves and scales (centre x {centre[0]:.3f}, width {size[0]:.3f})")
    rr = t2.nodes.new("NodeReroute")
    t2.links.new(tr.outputs[0], rr.inputs[0])
    t2.links.new(rr.outputs[0], o2.inputs[0])
    n_before = len(o2.target.data.polygons)
    nodes.build_output(t2, o2)
    check(not o2.error and len(o2.target.data.polygons) == n_before, "a reroute passes the shape through")
    sub.mute = True
    nodes.build_output(t2, o2)
    e, _ = euler(o2.target)
    check(not o2.error and e == 2, "a muted Subtract passes A through (back to a closed ball)")
    sub.mute = False

    # --- unconnected output and loops ---------------------------------------------------
    t3 = bpy.data.node_groups.new("T_Empty", nodes.TREE)
    o3 = t3.nodes.new("CN_NodeMeshOutput")
    check("connect a shape" in nodes.build_output(t3, o3), "an unconnected output explains itself")
    a = t3.nodes.new("CN_NodeCombine")
    b = t3.nodes.new("CN_NodeCombine")
    t3.links.new(a.outputs[0], b.inputs["A"])
    t3.links.new(b.outputs[0], a.inputs["A"])
    t3.links.new(b.outputs[0], o3.inputs[0])
    t0 = time.perf_counter()
    err = nodes.build_output(t3, o3)
    check(time.perf_counter() - t0 < 2.0, f"a node loop doesn't hang ({err.splitlines()[0] if err else 'built'})")

    # --- many sliders ----------------------------------------------------------------
    t4 = bpy.data.node_groups.new("T_Many", nodes.TREE)
    prev = None
    for i in range(40):
        code = "".join(f"// @param k{j} {0.1 * j:.1f}\n" for j in range(5)) + \
            f"float sdf(vec3 p){{ return sdSphere(p - vec3({i * 0.05:.2f}, 0, 0), 0.5 + k0 * 0.0); }}"
        n = code_node(t4, f"S{i}", code)
        if prev is None:
            prev = n
        else:
            u = t4.nodes.new("CN_NodeCombine")
            u.operation = 'union'
            t4.links.new(prev.outputs[0], u.inputs["A"])
            t4.links.new(n.outputs[0], u.inputs["B"])
            prev = u
    o4 = t4.nodes.new("CN_NodeMeshOutput")
    t4.links.new(prev.outputs[0], o4.inputs[0])
    err = nodes.build_output(t4, o4)
    check(not err, f"a 40-node graph with 200 sliders builds ({o4.stats.split(' · ')[0] if not err else err})")
    extra = code_node(t4, "Extra", "".join(f"// @param e{j} 0\n" for j in range(60)) + "float sdf(vec3 p){ return 1.0; }")
    u = t4.nodes.new("CN_NodeCombine")
    t4.links.new(prev.outputs[0], u.inputs["A"])
    t4.links.new(extra.outputs[0], u.inputs["B"])
    t4.links.new(u.outputs[0], o4.inputs[0])
    err = nodes.build_output(t4, o4)
    check("at most 256" in err, "going over 256 sliders is refused with a clear message")

    # --- animation -------------------------------------------------------------------
    blob.inputs["wobble"].default_value = 0.25
    out.animate = True
    scene = bpy.context.scene
    scene.frame_set(1)
    a = verts(obj).copy()
    scene.frame_set(20)
    b = verts(obj)
    check(len(a) != len(b) or np.abs(a - b).max() > 1e-3, "Animate rebuilds the graph when the frame changes")
    out.animate = False

    # --- live: changing a socket rebuilds on its own -------------------------------------
    blob.inputs["wobble"].default_value = 0.0
    blob.inputs["radius"].default_value = 1.4
    state["live_from"] = np.ptp(verts(obj), axis=0)[2]
    state["t"] = time.perf_counter()


def check_live(state):
    obj = bpy.data.node_groups["T_Demo"].nodes["Mesh Output"].target
    h = np.ptp(verts(obj), axis=0)[2]
    if abs(h - state["live_from"]) < 0.2 and time.perf_counter() - state["t"] < 5:
        return False
    check(abs(h - state["live_from"]) >= 0.2, f"changing a socket rebuilds live (height {state['live_from']:.2f} -> {h:.2f})")
    return True


def save_and_reload(state):
    path = os.path.join(tempfile.mkdtemp(prefix="codenodes_"), "graph.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    state["faces"] = len(bpy.data.node_groups["T_Demo"].nodes["Mesh Output"].target.data.polygons)
    bpy.ops.wm.open_mainfile(filepath=path)


def after_reload(state):
    tree = bpy.data.node_groups.get("T_Demo")
    check(tree is not None and tree.bl_idname == nodes.TREE, "the graph is saved in the .blend and loads back")
    out = tree.nodes["Mesh Output"]
    err = nodes.build_output(tree, out)
    check(not err and len(out.target.data.polygons) == state["faces"], "the reloaded graph rebuilds the same mesh")


def main():
    codenodes.register()
    state = {"phase": "checks"}

    def tick():
        try:
            if state["phase"] == "checks":
                run_checks(state)
                state["phase"] = "live"
                return 0.3
            if state["phase"] == "live":
                if not check_live(state):
                    return 0.3
                state["phase"] = "reload"
                save_and_reload(state)
                return 0.5
            if state["phase"] == "reload":
                after_reload(state)
                print(f"\nALL {_checks} CHECKS PASSED", flush=True)
        except Fail as exc:
            print(f"FAIL: {exc}", flush=True)
        except Exception:
            traceback.print_exc()
            print("FAIL: exception", flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=0.5, persistent=True)


main()
