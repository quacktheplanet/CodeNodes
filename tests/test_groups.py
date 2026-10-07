"""Code nodes inside ordinary node groups (Ctrl+G): chains, functions and values run through the group.

    blender -b --factory-startup --python tests/test_groups.py      (Blender 5.2+: GPU in background mode)

Builds Still points -> Push by Field (a breeze wired in) -> To Geometry, measures how far the points
are pushed, then groups different parts of it (and groups a group) and checks the result is the same.
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
from codenodes import gn_link, gpu_guard, gpu_live, groups, links, live  # noqa: E402

_checks = 0
STILL = """// Still points
void spawn(inout Particle p) { p.position = randBall(p.seed) * 0.5; p.velocity = vec3(0.0); p.life = 1e6; }
"""
BREEZE = """// A steady breeze along X
// @out func wind
vec3 wind(vec3 q) { return vec3(1.0, 0.0, 0.0); }
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


def drift(obj):
    scene = bpy.context.scene
    scene.frame_set(1)
    settle()
    a = positions(obj)
    scene.frame_set(11)
    settle()
    b = positions(obj)
    if len(a) == 0 or len(a) != len(b):
        return float("nan")
    return float((b - a)[:, 0].mean())


def node_of(tree, obj_name):
    for t in bpy.data.node_groups:
        for n in t.nodes:
            if n.type == 'GROUP' and gn_link.is_code_group(n.node_tree) and gn_link.source_of(n.node_tree) \
                    and gn_link.source_of(n.node_tree).name == obj_name:
                return t, n
    return None, None


def main():
    if not gpu_guard.available():
        print(f"SKIP: Blender {bpy.app.version_string} has no GPU in background mode (needs 5.2)", flush=True)
        return
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    obj = bpy.data.objects.new("Groups", bpy.data.meshes.new("Groups"))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new("Groups Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.nodes.new("NodeGroupInput").location = (-600, 0)
    gout = tree.nodes.new("NodeGroupOutput")
    gout.location = (1400, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree

    def add(kind, key, x, y=0, source=None, label=None):
        group, err = gn_link.create(kind, key, label, source)
        if err:
            raise Fail(err)
        return gn_link.insert(tree, group, (x, y))

    src = add('PARTICLES', None, 0, source=STILL, label="Still")
    push = add('STAGE', "Push by Field", 300)
    wind = add('STAGE', None, 0, -400, source=BREEZE, label="Breeze")
    amount = tree.nodes.new("ShaderNodeValue")
    amount.location = (0, -250)
    amount.outputs[0].default_value = 1.0
    src.inputs["Count"].default_value = 2000
    tree.links.new(src.outputs["Particles"], push.inputs["Particles"])
    tree.links.new(wind.outputs["wind"], push.inputs["field"])
    tree.links.new(amount.outputs[0], push.inputs["amount"])
    tg = gn_link.insert_make_real(tree, push)
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    settle(6)
    head = gn_link.source_of(src.node_tree).name
    pname = gn_link.source_of(push.node_tree).name
    wname = gn_link.source_of(wind.node_tree).name
    base = drift(obj)
    check(base > 0.05, f"ungrouped: the breeze pushes the points ({base:.4f} m)")

    # 1. the stage alone in a group: stream in and out, the function and a value coming in
    g1 = groups.make_group(tree, [push], "Pusher")
    settle(6)
    info = links.CHAINS.get(head, {})
    check(info.get("stages") == [pname] and (pname, "field") in info.get("funcs", {}),
          f"a stage inside a group is still in the chain, with its function ({info})")
    d1 = drift(obj)
    check(abs(d1 / base - 1.0) < 1e-3, f"and pushes exactly as before ({d1 / base:.4f}×)")
    amount.outputs[0].default_value = 3.0
    settle(6)
    d3 = drift(obj)
    check(abs(d3 / base - 3.0) < 0.01, f"a value wired into the group reaches the node inside ({d3 / base:.3f}×)")
    amount.outputs[0].default_value = 1.0
    settle(6)

    # 2. the group of a group, with To Geometry inside too
    tg_node = next(n for n in tree.nodes if n.type == 'GROUP' and gn_link.is_make_real(n.node_tree))
    g2 = groups.make_group(tree, [g1, tg_node], "Pusher and Output")
    settle(6)
    d2 = drift(obj)
    check(abs(d2 / base - 1.0) < 1e-3,
          f"a group inside a group, To Geometry inside it, out through two Group Outputs ({d2 / base:.4f}×)")

    # 3. the source and the function provider in their own group
    groups.make_group(tree, [src, wind], "Sources")
    settle(6)
    info = links.CHAINS.get(head, {})
    check(info.get("stages") == [pname] and info.get("funcs", {}).get((pname, "field"), (None,))[0] == wname,
          "a source and a function in another group still feed the stage")
    d4 = drift(obj)
    check(abs(d4 / base - 1.0) < 1e-3, f"and the result is the same ({d4 / base:.4f}×)")
    check(obj.name in gpu_live.hosts.get(head, []),
          f"live drawing finds the object the grouped source is shown on ({gpu_live.hosts.get(head)})")
    t, n = node_of(tree, pname)
    check(t is not None and t.name.startswith("Pusher") and t != tree,
          f"(the stage really is inside a group: {t.name if t else None})")
    del g2
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
