"""Use lines in real Blender: one shared function, each node deciding what it means to that node.

    blender -b --factory-startup --python tests/test_uses.py       (Blender 5.2+: GPU in background mode)
    blender --factory-startup --python tests/test_uses.py          (any version, with a window)

A constant wind is wired into Push by Field nodes. Each Push node has a use line under its function
input ("field · use"). The test types use lines on the node and in the code, and measures how far the
particles are actually pushed. Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""
import os
import sys
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gn_sockets, gpu_guard, links, live  # noqa: E402

_checks = 0
USE = gn_sockets.use_socket("field")

STILL = """// Still points: particles that don't move by themselves
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


def host(name, location=(0.0, 0.0, 0.0)):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
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
    if not bpy.app.background:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def positions(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    gs = ev.evaluated_geometry()
    pc = gs.pointcloud
    if pc is None or not len(pc.points):
        return np.zeros((0, 3), np.float32)
    out = np.empty(len(pc.points) * 3, np.float32)
    pc.attributes["position"].data.foreach_get("vector", out)
    return out.reshape(-1, 3)


def chain(tree, x0, y, label):
    """Still points -> Push by Field -> To Geometry, wired to the output when it's the first chain."""
    src = add(tree, 'PARTICLES', None, x0, y, source=STILL, label=label)
    push = add(tree, 'STAGE', "Push by Field", x0 + 300, y)
    tree.links.new(src.outputs["Particles"], push.inputs["Particles"])
    src.inputs["Count"].default_value = 2000
    return src, push


def drift(obj, frame=11):
    """How far the particles moved along X between the first frame and `frame` (their mean)."""
    scene = bpy.context.scene
    scene.frame_set(1)
    settle()
    a = positions(obj)
    scene.frame_set(frame)
    settle()
    b = positions(obj)
    if len(a) == 0 or len(a) != len(b):
        raise Fail(f"no particles came out of To Geometry ({len(a)}, {len(b)})")
    return float((b - a)[:, 0].mean()), float(np.abs((b - a)[:, 1:]).max())


def main():
    if bpy.app.background and not gpu_guard.available():
        print(f"SKIP: Blender {bpy.app.version_string} has no GPU in background mode (needs 5.2)", flush=True)
        return
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 50
    obj, tree = host("Uses")
    wind = add(tree, 'STAGE', None, 0, -400, source=BREEZE, label="Breeze")
    src, push = chain(tree, 0, 0, "Still")
    tree.links.new(wind.outputs["wind"], push.inputs["field"])
    tg = gn_link.insert_make_real(tree, push)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    settle(6)
    pobj = gn_link.source_of(push.node_tree)

    sock = push.inputs.get(USE)
    check(sock is not None and sock.bl_idname.startswith("NodeSocketString"),
          f"a function input shows its use line on the node ('{USE}')")
    check(sock.default_value == "field(p)", f"it starts as the plain call ({sock.default_value!r})")
    names = [s.name for s in push.inputs]
    check(names.index(USE) == names.index("field") + 1, "right under the function input it belongs to")
    item = next(i for i in push.node_tree.interface.items_tree if i.item_type == 'SOCKET' and i.name == USE)
    check("field(p) * 0.4" in item.description, "its tooltip explains it with an example")

    base, side = drift(obj)
    check(base > 0.05 and side < 1e-5, f"the plain use line pushes the particles along +X ({base:.4f} m)")

    # typed on the node -> the code
    sock.default_value = "field(p) * 2.0"
    settle(6)
    code = pobj.codenodes.text.as_string()
    check("// @in func vec3 field(vec3 p) use: field(p) * 2.0" in code,
          "a use line typed on the node is written into the node's code")
    double, _ = drift(obj)
    check(abs(double / base - 2.0) < 0.02, f"and the particles are pushed twice as far ({double / base:.3f}×)")
    comp, _v = links.composite(gn_link.source_of(src.node_tree))
    check("_field_in(p) * 2.0" in comp.source, "compiled as a wrapper around the shared wind")

    # edited in the code -> the node
    pobj.codenodes.text.from_string(code.replace("field(p) * 2.0", "-field(p) * amount"))
    push.inputs["amount"].default_value = 0.5
    settle(6)
    check(push.inputs[USE].default_value == "-field(p) * amount",
          f"a use line edited in the code shows on the node ({push.inputs[USE].default_value!r})")
    back, _ = drift(obj)
    # the use line has amount in it and Push multiplies by amount again: -1 * 0.5 * 0.5
    check(abs(back / base + 0.25) < 0.01, f"the node's own sliders work inside it ({back / base:.3f}× = -0.25)")

    # a broken use line is refused on the node, and the code is left alone
    before = pobj.codenodes.text.as_string()
    push.inputs[USE].default_value = "field(p"
    settle(4)
    check(pobj.codenodes.text.as_string() == before and "brackets" in pobj.codenodes.last_error,
          f"a broken use line is refused with the reason ({pobj.codenodes.last_error[:60]})")
    push.inputs[USE].default_value = "field(p)"
    push.inputs["amount"].default_value = 1.0
    settle(6)
    check("use:" not in pobj.codenodes.text.as_string().split("\n")[1],
          "typing the plain call back removes the use line from the code")
    again, _ = drift(obj)
    check(abs(again / base - 1.0) < 0.01, f"and the push is back to the original ({again / base:.3f}×)")

    # a value computed by other nodes reaches the code node (Value 2 -> Math x1.5 -> amount = 3)
    val = tree.nodes.new("ShaderNodeValue")
    val.outputs[0].default_value = 2.0
    mul = tree.nodes.new("ShaderNodeMath")
    mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = 1.5
    tree.links.new(val.outputs[0], mul.inputs[0])
    tree.links.new(mul.outputs[0], push.inputs["amount"])
    settle(6)
    amt = next(p.value for p in pobj.codenodes.params if p.name == "amount")
    check(abs(amt - 3.0) < 1e-5, f"a value computed by Math nodes reaches the code node's input ({amt})")
    tripled, _ = drift(obj)
    check(abs(tripled / base - 3.0) < 0.02, f"and drives it ({tripled / base:.3f}×)")
    # Scene Time: it follows the frame
    st = tree.nodes.new("GeometryNodeInputSceneTime")
    tree.links.new(st.outputs["Seconds"], mul.inputs[0])
    bpy.context.scene.frame_set(25)
    settle(6)
    amt = next(p.value for p in pobj.codenodes.params if p.name == "amount")
    fps = bpy.context.scene.render.fps
    check(abs(amt - 1.5 * 25 / fps) < 1e-3,
          f"Scene Time × 1.5 follows the frame: {amt:.4f} at frame 25 ({25 / fps:.4f} s × 1.5)")
    for l in [l for l in tree.links if l.to_socket == push.inputs["amount"]]:
        tree.links.remove(l)
    push.inputs["amount"].default_value = 1.0
    settle(6)
    check(not [o for o in bpy.data.objects if o.get("cn_value_tap")],
          "unwired, the hidden helper that evaluated it goes")

    # one wind, two nodes, two meanings
    src2, push2 = chain(tree, 0, 400, "Still 2")
    tree.links.new(wind.outputs["wind"], push2.inputs["field"])
    push2.inputs[USE].default_value = "vec3(0.0, field(p).x, 0.0)"
    settle(8)
    label = wind.label
    check("used by 2" in label, f"the shared function's header says how many nodes use it ({label!r})")
    p2 = gn_link.source_of(push2.node_tree)
    check("use: vec3(0.0, field(p).x, 0.0)" in p2.codenodes.text.as_string()
          and "use:" not in pobj.codenodes.text.as_string().split("\n")[1],
          f"each node keeps its own use line ({p2.codenodes.text.as_string().splitlines()[1]!r}, {push2.inputs[USE].default_value!r})")

    # a function with the wrong signature: the node says so
    orbits = add(tree, 'STAGE', "Orbits", 0, -800)
    tree.links.new(orbits.outputs["planetPos"], push2.inputs["field"])
    settle(8)
    check("⚠" in push2.label and "takes vec3 field(vec3 p)" in push2.label,
          f"wiring in a function with a different signature shows a warning ({push2.label!r})")
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


try:
    main()
except Fail as exc:
    print(f"FAIL: {exc}", flush=True)
except Exception:
    traceback.print_exc()
    print("FAIL: exception", flush=True)
sys.stdout.flush()
os._exit(0)                 # (with a window too: these checks run straight away and then quit)
