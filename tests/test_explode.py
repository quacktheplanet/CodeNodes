"""Explode and Collapse in real Blender.

    blender -b --factory-startup --python tests/test_explode.py   (round trips on any version; the GPU part
                                                                   needs 5.2+ in background mode)

1. Every starter: Explode then Collapse gives back its code exactly.
2. A node with a constant, a helper function and a constant array, in a working chain: Explode makes a
   node group of its pieces, the particles are exactly the same, the pieces drive the code (change the
   Value node and the result follows), and Collapse gives back one node with the original code.
Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""
import os
import sys
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import decl, explode, explode_ops, gn_link, gn_sockets, gpu_guard, links, live  # noqa: E402

_checks = 0
STILL = """// Still points
void spawn(inout Particle p) { p.position = randBall(p.seed) * 0.5; p.velocity = vec3(0.0); p.life = 1e6; }
"""
SWIRL = """// Swirl Push: pushes along a palette direction, curled
// @in float speed 1.0 0 4  "How fast"
const float CALM = 0.5;
const vec3 DIRS[3] = vec3[3](vec3(1.0, 0.0, 0.0), vec3(0.0, 1.0, 0.0),
                             vec3(0.0, 0.0, 1.0));
vec3 curl(vec3 p) {
  return vec3(-p.y, p.x, 0.0) * speed;
}
vec3 pick(int i) { return DIRS[i % DIRS.length()]; }
void behave(inout Particle p, float dt) {
  p.velocity = curl(p.position) * CALM + pick(0) * 0.3;
}
"""


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def settle(n=5):
    for _ in range(n):
        gn_link.sync()
        live._flush()


def positions(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    gs = obj.evaluated_get(dg).evaluated_geometry()
    pc = gs.pointcloud
    if pc is None or not len(pc.points):
        return np.zeros((0, 3), np.float32)
    out = np.empty(len(pc.points) * 3, np.float32)
    pc.attributes["position"].data.foreach_get("vector", out)
    return out.reshape(-1, 3)


def at(obj, frame=11):
    bpy.context.scene.frame_set(1)
    settle()
    bpy.context.scene.frame_set(frame)
    settle()
    return positions(obj)


def unchanged(e):
    """The pieces as Explode made them (nothing edited)."""
    out = {}
    for p in e.pieces:
        if p.kind == 'value':
            out[p.name] = ('value', float(p.item.value))
        elif p.kind == 'list':
            out[p.name] = ('list', decl.parse(p.code).table)
        else:
            out[p.name] = ('func', p.code)
    return out


def round_trips():
    total, with_pieces, n_pieces = 0, 0, 0
    for kind in ('PARTICLES', 'STAGE', 'MESH', 'DEFORM'):
        for key in gn_sockets.templates_for(kind):
            code = gn_link.template(kind, key)
            try:
                decl.parse(code)
            except Exception:
                continue
            if "@list" in code:
                continue
            total += 1
            e = explode.explode(code, key)
            if not e.pieces:
                continue
            with_pieces += 1
            n_pieces += len(e.pieces)
            back = explode.collapse(e.core, unchanged(e), e.originals,
                                    arrays=[p.name for p in e.pieces if p.kind == 'list'])
            if back != code:
                import difflib
                print("\n".join(difflib.unified_diff(code.splitlines(), back.splitlines(), lineterm="")))
                raise Fail(f"{kind} {key}: Explode then Collapse changed the code")
            decl.parse(e.core)
            for p in e.pieces:
                if p.code:
                    decl.parse(p.code)
    check(total > 30 and with_pieces > 0,
          f"every starter round-trips exactly: Explode then Collapse gives back its code ({total} starters, "
          f"{with_pieces} with pieces, {n_pieces} pieces)")


def main():
    codenodes.register()
    round_trips()
    e = explode.explode(SWIRL, "Swirl Push")
    check(sorted((p.kind, p.name) for p in e.pieces) == [('func', 'curl'), ('func', 'pick'), ('list', 'DIRS'),
                                                          ('value', 'CALM')],
          "the test node's pieces: a value, two functions and a list")
    if not gpu_guard.available():
        print(f"\n(the GPU part needs Blender 5.2+ in background mode)\nALL {_checks} CHECKS PASSED", flush=True)
        return
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    obj = bpy.data.objects.new("Explode", bpy.data.meshes.new("Explode"))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new("Explode Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.nodes.new("NodeGroupInput").location = (-600, 0)
    gout = tree.nodes.new("NodeGroupOutput")
    gout.location = (1400, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    g, err = gn_link.create('PARTICLES', None, "Still", STILL)
    src = gn_link.insert(tree, g, (0, 0))
    g, err = gn_link.create('STAGE', None, "Swirl Push", SWIRL)
    swirl = gn_link.insert(tree, g, (300, 0))
    src.inputs["Count"].default_value = 2000
    tree.links.new(src.outputs["Particles"], swirl.inputs["Particles"])
    tg = gn_link.insert_make_real(tree, swirl)
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    swirl.inputs["speed"].default_value = 1.5
    settle(6)
    sobj = gn_link.source_of(swirl.node_tree)
    before = at(obj)
    check(len(before) == 2000 and float(np.abs(before).max()) > 0.6, "the node works before exploding")

    w = explode_ops.explode_node(tree, swirl)
    settle(6)
    group = w.node_tree
    inside = {n.label or n.name: n for n in group.nodes}
    check(gn_link.holds_code(group) and explode_ops.is_exploded(group),
          f"Explode makes a node group (Tab or double-click into it): '{group.name}'")
    check({"CALM", "curl", "pick", "DIRS"} <= {(n.label.split(" · ")[0] if n.label else n.name)
                                               for n in group.nodes},
          f"inside: one node per piece ({sorted(inside)})")
    check([s.name for s in w.inputs if s.name in ("Particles", "speed")] == ["Particles", "speed"]
          and abs(w.inputs["speed"].default_value - 1.5) < 1e-6,
          "the node's inputs stay on the outside, with their values")
    core = explode_ops._core(group)
    code = sobj.codenodes.text.as_string()
    check("// @in float CALM 0.5" in code and "// @in func vec3 curl(vec3 p)" in code
          and "// @in list vec3 DIRS" in code and "void behave" in code,
          "the node keeps its entry point; each piece is now an input in its place")
    after = at(obj)
    err = float(np.abs(after - before).max()) if len(after) == len(before) else 1e9
    check(err < 1e-5, f"the particles are exactly the same exploded (max difference {err:.1e} m)")
    calm = next(n for n in group.nodes if n.bl_idname == "ShaderNodeValue" and n.label == "CALM")
    calm.outputs[0].default_value = 0.0
    settle(6)
    still = at(obj)
    moved_x = float((still - before)[:, 0].mean())
    check(abs(float(np.abs(still - before).max())) > 1e-3, f"a piece drives the code: CALM = 0 changes the motion")
    calm.outputs[0].default_value = 0.5
    w.inputs["speed"].default_value = 3.0
    settle(6)
    fast = at(obj)
    check(float(np.abs(fast - before).max()) > 1e-3, "and a value typed on the group reaches the pieces that use it")
    w.inputs["speed"].default_value = 1.5
    settle(6)
    del moved_x, core

    new = explode_ops.collapse_node(tree, w)
    settle(6)
    check(new is not None and gn_link.is_code_group(new.node_tree) and not any(
        n.type == 'GROUP' and explode_ops.is_exploded(n.node_tree) for n in tree.nodes),
          "Collapse gives back one code node")
    check(sobj.codenodes.text.as_string() == SWIRL, "with exactly the original code")
    check(abs(new.inputs["speed"].default_value - 1.5) < 1e-6 and new.inputs["Particles"].is_linked
          and new.outputs["Particles"].is_linked, "its values and wiring")
    back = at(obj)
    err = float(np.abs(back - before).max()) if len(back) == len(before) else 1e9
    check(err < 1e-5, f"and the same particles (max difference {err:.1e} m)")
    left = [o.name for o in bpy.data.objects if o.name.startswith("CN · ") and o.name.split(" · ")[1] in
            ("curl", "pick", "DIRS")]
    check(not left, f"the pieces' helper objects are gone ({left})")

    # explode, edit a piece, collapse: the edit goes into the code
    w = explode_ops.explode_node(tree, new)
    settle(6)
    calm = next(n for n in w.node_tree.nodes if n.bl_idname == "ShaderNodeValue" and n.label == "CALM")
    calm.outputs[0].default_value = 0.25
    curl = next(n for n in w.node_tree.nodes if n.type == 'GROUP' and gn_link.is_code_group(n.node_tree)
                and gn_link.source_of(n.node_tree).name.endswith("curl"))
    ct = gn_link.source_of(curl.node_tree).codenodes.text
    ct.from_string(ct.as_string().replace("* speed", "* speed * 2.0"))
    settle(6)
    explode_ops.collapse_node(tree, w)
    settle(6)
    code = sobj.codenodes.text.as_string()
    check("const float CALM = 0.25;" in code and "vec3(-p.y, p.x, 0.0) * speed * 2.0;" in code
          and "DIRS[i % DIRS.length()]" in code,
          "pieces edited while exploded go back into the code (and arrays are indexed again)")
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


try:
    main()
except Fail as exc:
    print(f"FAIL: {exc}", flush=True)
except Exception:
    traceback.print_exc()
    print("FAIL: exception", flush=True)
if bpy.app.background:
    sys.stdout.flush()
    os._exit(0)
