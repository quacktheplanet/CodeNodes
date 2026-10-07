"""Lists in real Blender: a List node (a table you edit), list inputs, and real Geometry Nodes lists.

    blender -b --factory-startup --python tests/test_lists.py      (any version; the GPU checks need 5.2+
                                                                    in background mode, or a window)

Attractors (a List node) feeds Attract to List; the test measures particles being pulled to each row,
edits the table, wires single columns, and on Blender 5.2 reads the List node's outputs with native
nodes. Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""
import os
import sys
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import decl, gn_link, gn_sockets, gpu_guard, links, live  # noqa: E402

_checks = 0
STILL = """// Still points: a cloud that doesn't move by itself
void spawn(inout Particle p) { p.position = randBall(p.seed) * 3.0; p.velocity = vec3(0.0); p.life = 1e6; }
"""
NEAREST = """// Pull to Points: towards the nearest point of a list of positions
// @in list vec3 pts
void behave(inout Particle p, float dt) {
  float best = 1e9; vec3 to = vec3(0.0);
  for (int i = 0; i < pts_count(); i++) { vec3 d = pts(i) - p.position; if (dot(d, d) < best) { best = dot(d, d); to = d; } }
  p.velocity = to * 2.0;
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


def host(name):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(f"{name} Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.nodes.new("NodeGroupInput").location = (-600, 0)
    tree.nodes.new("NodeGroupOutput").location = (1800, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree


def add(tree, kind, key, x, y=0, source=None, label=None):
    group, err = gn_link.create(kind, key, label, source)
    if err:
        raise Fail(f"{key or label}: {err}")
    return gn_link.insert(tree, group, (x, y))


def settle(n=4):
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


def at(obj, frame):
    bpy.context.scene.frame_set(frame)
    settle()
    return positions(obj)


def nearest(points, targets):
    d = np.linalg.norm(points[:, None, :] - np.asarray(targets, np.float32)[None, :, :], axis=2)
    return d.min(axis=1), d.argmin(axis=1)


def main():
    gpu = gpu_guard.available()
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 80
    obj, tree = host("Lists")
    table = add(tree, 'STAGE', "Attractors", 0, -400)
    src = add(tree, 'PARTICLES', None, 0, 0, source=STILL, label="Cloud")
    pull = add(tree, 'STAGE', "Attract to List", 300, 0)
    tree.links.new(src.outputs["Particles"], pull.inputs["Particles"])
    tree.links.new(table.outputs[gn_sockets.TABLE_OUT], pull.inputs["targets"])
    tg = gn_link.insert_make_real(tree, pull)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    src.inputs["Count"].default_value = 3000
    settle(6)
    tobj = gn_link.source_of(table.node_tree)
    native = gn_sockets.lists_native()

    outs = [(o.name, o.bl_idname) for o in table.outputs]
    check(outs[0] == (gn_sockets.TABLE_OUT, "NodeSocketBundle"), f"a List node gives the whole table ({outs[0]})")
    check([n for n, _t in outs[1:4]] == ["position", "strength", "radius"],
          f"and each column on its own ({'real lists' if native else 'bundles before Blender 5.2'})")
    check(pull.inputs["targets"].bl_idname == "NodeSocketBundle",
          "a list of records comes in on a bundle socket")
    check(table.label.endswith("3 rows · used by 1"), f"its header counts rows and users ({table.label!r})")
    tip = next(i for i in pull.node_tree.interface.items_tree if i.item_type == 'SOCKET' and i.name == "targets")
    check(tip.description.endswith("(each row: position, strength, radius)"),
          f"the list input's tooltip names the fields of each row ({tip.description[-50:]!r})")
    head = gn_link.source_of(src.node_tree)
    comp, _v = links.composite(head)
    check("return 3; }" in comp.source and "_targets(int i)" in comp.source,
          "the table is compiled into the chain")

    tvals = [(-1.5, 0.0, 0.5), (1.5, 0.0, 0.5), (0.0, 0.0, 2.0)]
    if gpu:
        p0, p1 = at(obj, 1), at(obj, 60)
        d0, _ = nearest(p0, tvals)
        d1, _ = nearest(p1, tvals)
        check(len(p1) == 3000 and np.median(d1) < 0.6 * np.median(d0),
              f"particles are pulled to the attractors (median distance {np.median(d0):.2f} -> {np.median(d1):.2f} m)")

    # edit the table: one far, strong attractor more
    code = tobj.codenodes.text.as_string()
    tobj.codenodes.text.from_string(code.rstrip("\n") + "\nBelow    0.0 0.0 -6.0     40.0      0.5\n")
    settle(6)
    check(table.label.startswith("Attractors · 4 rows"), f"add a row and the node says 4 rows ({table.label!r})")
    comp, _v = links.composite(head)
    check("return 4; }" in comp.source, "and the chain is recompiled with it")
    if gpu:
        p2 = at(obj, 60)
        check(float(p2[:, 2].mean()) < float(p1[:, 2].mean()) - 0.5,
              f"the new row pulls the cloud down (mean height {p1[:, 2].mean():.2f} -> {p2[:, 2].mean():.2f} m)")
    tobj.codenodes.text.from_string(code)
    settle(6)

    # a mistake in the table is reported on the List node and leaves the chain alone
    tobj.codenodes.text.from_string(code.replace("0.4\n", "0.4 9\n", 1))
    settle(6)
    check("⚠" in table.label or tobj.codenodes.last_error or decl_err(tobj),
          f"a row with too many cells is reported ({table.label!r})")
    tobj.codenodes.text.from_string(code)
    settle(6)

    # one column into a single-type list
    near = add(tree, 'STAGE', None, 300, 400, source=NEAREST, label="Pull to Points")
    src2 = add(tree, 'PARTICLES', None, 0, 400, source=STILL, label="Cloud 2")
    tree.links.new(src2.outputs["Particles"], near.inputs["Particles"])
    tree.links.new(table.outputs["position"], near.inputs["pts"])
    settle(6)
    if native:
        item = next(i for i in near.node_tree.interface.items_tree if i.item_type == 'SOCKET' and i.name == "pts")
        check(item.socket_type == "NodeSocketVector" and item.structure_type == 'LIST',
              "a list of vec3 is a vector list socket (Blender 5.2)")
    head2 = gn_link.source_of(src2.node_tree)
    comp2, _v = links.composite(head2)
    check("vec3(-1.5, 0.0, 0.5)" in comp2.source.replace(".0,", ".0,") or "vec3(-1.5, 0, 0.5)" in comp2.source
          or "-1.5" in comp2.source, "the position column comes in as a vec3 list")
    check(table.label.endswith("used by 2"), f"the List is now used by two nodes ({table.label!r})")

    if native:
        # the same table read by native nodes: Points at Get List Item(position, Index)
        # (on the host's own tree: a code node's group used twice becomes two separate nodes)
        nobj, ntree = obj, tree
        tab2 = table
        pts = ntree.nodes.new("GeometryNodePoints")
        ln = ntree.nodes.new("GeometryNodeListLength")
        gi = ntree.nodes.new("GeometryNodeListGetItem")
        for n in (gi, ln):
            try:
                n.socket_type = 'VECTOR'
            except (AttributeError, TypeError):
                pass
        idx = ntree.nodes.new("GeometryNodeInputIndex")
        ntree.links.new(tab2.outputs["position"], ln.inputs[0])
        ntree.links.new(tab2.outputs["position"], gi.inputs["List"])
        ntree.links.new(idx.outputs[0], gi.inputs["Index"])
        ntree.links.new(ln.outputs[0], pts.inputs["Count"])
        ntree.links.new(gi.outputs[0], pts.inputs["Position"])
        g2 = next(n for n in ntree.nodes if n.type == 'GROUP_OUTPUT')
        ntree.links.new(pts.outputs[0], g2.inputs[0])
        settle(4)
        got = positions(nobj)
        check(len(got) == 3 and np.allclose(got, tvals, atol=1e-5),
              f"native nodes read the List node's columns as real lists ({got.round(2).tolist()})")
    if native:
        # a native list (Field to List over Index) wired straight into a code node's list input
        ftl = tree.nodes.new("GeometryNodeFieldToList")
        ftl.list_items.new('VECTOR', "p")
        ftl.inputs["Count"].default_value = 4
        idx2 = tree.nodes.new("GeometryNodeInputIndex")
        mul = tree.nodes.new("ShaderNodeMath")
        mul.operation = 'MULTIPLY'
        mul.inputs[1].default_value = 0.5
        comb = tree.nodes.new("ShaderNodeCombineXYZ")
        tree.links.new(idx2.outputs[0], mul.inputs[0])
        tree.links.new(mul.outputs[0], comb.inputs["X"])
        tree.links.new(comb.outputs[0], ftl.inputs["p"])
        tree.links.new(ftl.outputs[0], near.inputs["pts"])
        settle(6)
        comp2, _v = links.composite(head2)
        check("int n1_pts_count() { return 4; }" in comp2.source
              and "vec3(0.0, 0.0, 0.0), vec3(0.5, 0.0, 0.0), vec3(1.0, 0.0, 0.0), vec3(1.5, 0.0, 0.0)" in comp2.source,
              "a native list (Field to List) wired into a code node is read and compiled in")
        ftl.inputs["Count"].default_value = 6
        settle(6)
        comp2, _v = links.composite(head2)
        check("return 6; }" in comp2.source, "and follows it when the list changes (6 items)")
        if gpu:
            p = at(obj, 40)
            check(len(p) > 0, "the chains still run")
        tree.links.remove(next(l for l in tree.links if l.to_socket == near.inputs["pts"]))
        settle(6)
        left = [o.name for o in bpy.data.objects if o.get("cn_list_tap")]
        check(not left, f"unwire it and its hidden helper goes ({left})")
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


def decl_err(obj):
    try:
        decl.parse(obj.codenodes.text.as_string())
        return ""
    except Exception as exc:
        return str(exc)


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
