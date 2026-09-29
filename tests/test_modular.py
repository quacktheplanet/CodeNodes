"""Modular code nodes in real Blender: nodes that declare their own inputs and outputs, chains of
GPU stages compiled into one program, per-particle attributes, function links, warps on particles
and meshes, To Geometry (the node that used to be called Make Real). Needs a window:

    blender --factory-startup --python tests/test_modular.py

Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...", then quits. Set CODENODES_SHOTS to a
folder to save screenshots and CODENODES_NUMBERS to a .json path to save frame rates.
"""
import json
import os
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gn_sockets, gpu_guard, gpu_live, links, live, render_ops  # noqa: E402

print("CodeNodes test: modular code nodes (this window closes by itself)", flush=True)
_checks = 0
SHOTS = os.environ.get("CODENODES_SHOTS", "")
NUMBERS = {}
TMP = tempfile.mkdtemp(prefix="cn_mod_")
SAVE = os.path.join(TMP, "modular.blend")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def view3d():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                return win, area
    return None, None


def shot(name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    win, area = view3d()
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(SHOTS, name))
    print(f"  screenshot: {os.path.join(SHOTS, name)}", flush=True)


def host(name, location=(0.0, 0.0, 0.0)):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    tree = bpy.data.node_groups.new(f"{name} Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-600, 0), (1800, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree


def add(tree, kind, key, x, y=0, source=None, label=None):
    group, err = gn_link.create(kind, key, label, source)
    if err:
        raise Fail(f"{key}: {err}")
    return gn_link.insert(tree, group, (x, y))


def wire(tree, a, b, out="Particles", inp="Particles"):
    tree.links.new(a.outputs[out], b.inputs[inp])


def settle(n=3):
    for _ in range(n):
        gn_link.sync()
        live._flush()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


class _Cloud:
    """The evaluated point cloud (particles made real) or mesh, with .vertices / .attributes like a mesh."""

    def __init__(self, geo, keep=()):
        self.keep = keep              # the geometry set that owns it must stay alive
        self.geo = geo
        self.vertices = geo.points if hasattr(geo, "points") else geo.vertices
        self.attributes = geo.attributes


def points(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    try:
        gs = ev.evaluated_geometry()
        if gs.pointcloud is not None and len(gs.pointcloud.points):
            return _Cloud(gs.pointcloud, (gs, ev, dg))
    except AttributeError:
        pass
    return ev.data


def _co(geo, out):
    geo.vertices.foreach_get("co", out) if not hasattr(geo, "geo") or not hasattr(geo.geo, "points")         else geo.attributes["position"].data.foreach_get("vector", out)


def measure_fps(seconds=2.0):
    t0 = time.perf_counter()
    start = gpu_live._fps.get("total", 0)
    while time.perf_counter() - t0 < seconds:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    return (gpu_live._fps.get("total", 0) - start) / (time.perf_counter() - t0)


def look_at(location, distance, rotation=(1.1, 0.0, 0.6)):
    from mathutils import Euler
    win, area = view3d()
    sp = area.spaces.active
    sp.shading.type = 'SOLID'
    sp.overlay.show_floor = False
    r3d = sp.region_3d
    r3d.view_perspective = 'PERSP'
    r3d.view_location = location
    r3d.view_distance = distance
    r3d.view_rotation = Euler(rotation).to_quaternion()


# ---- phases ------------------------------------------------------------------------------------------------

CUSTOM = """\
// A node that declares one of everything
// @in float strength 1.5 0 4
// @in int copies 3 1 8
// @in color tint 1.0 0.5 0.25
// @in func vec3 field(vec3 p)
// @out attr glow 0.5
// @out func pull
vec3 pull(vec3 q) { return -q * strength; }
void behave(inout Particle p, float dt) {
  p.velocity += (field(p.position) + pull(p.position)) * dt * float(copies);
  p.glow = length(tint);
}
"""


def phase_blank(st):
    path = os.path.join(TMP, "start.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    return True


def phase_declarations(st):
    obj, tree = host("Decl")
    node = add(tree, 'STAGE', None, 0, source=CUSTOM, label="Everything")
    settle()
    ins = {s.name: s.bl_idname for s in node.inputs}
    outs = [(s.name, s.bl_idname) for s in node.outputs]
    check(ins.get("strength") == "NodeSocketFloat" and ins.get("copies") == "NodeSocketInt"
          and ins.get("tint") == "NodeSocketColor" and ins.get("field") == "NodeSocketClosure"
          and ins.get("Particles") == "NodeSocketBundle",
          f"the code declares its inputs: float, whole number, colour, function, and a Particles stream "
          f"({sorted(ins)})")
    check(("Particles", "NodeSocketBundle") in outs and ("pull", "NodeSocketClosure") in outs
          and ("glow", "NodeSocketFloat") in outs,
          f"and its outputs: the stream, a function and a per-particle attribute ({outs})")
    check(abs(node.inputs["copies"].default_value - 3) < 1e-6
          and abs(tuple(node.inputs["tint"].default_value)[1] - 0.5) < 1e-6,
          "declared defaults show on the node")
    src = gn_link.source_of(node.node_tree)
    text = src.codenodes.text
    text.from_string(CUSTOM + "// @in float extra 2.0 0 5\n// @out attr heat 0.0\n")
    settle()
    ins = {s.name for s in node.inputs}
    outs = {s.name for s in node.outputs}
    check("extra" in ins and "heat" in outs, "sockets follow the code as it changes (new input and output)")
    node.inputs["tint"].default_value = (0.2, 0.3, 0.4, 1.0)
    settle()
    vals = {p.name: p.value for p in src.codenodes.params}
    check(abs(vals["tint_g"] - 0.3) < 1e-5 and abs(vals["copies"] - 3) < 1e-6,
          "a colour socket drives the code's three colour values")
    st["decl_tree"] = tree.name
    return True


def phase_fireflies(st):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=16, y_subdivisions=16, size=4)
    ground = bpy.context.object
    ground.name = "Ground"
    obj, tree = host("Fireflies")
    names = [('PARTICLES', "Firefly Swarm"), ('STAGE', "Wander"), ('STAGE', "Rise"), ('STAGE', "Blink"),
             ('STAGE', "Firefly Look")]
    nodes = [add(tree, k, key, i * 280) for i, (k, key) in enumerate(names)]
    for a, b in zip(nodes, nodes[1:]):
        wire(tree, a, b)
    nodes[0].inputs["Emit From"].default_value = ground
    nodes[4].inputs["size"].default_value = 0.03
    settle()
    head = gn_link.source_of(nodes[0].node_tree)
    info = links.CHAINS.get(head.name, {})
    check([s.split("· ")[-1] for s in info.get("stages", [])] == ["Wander", "Rise", "Blink", "Firefly Look"],
          f"the wired stages form one chain behind the source ({info.get('stages')})")
    comp, vals = links.composite(head)
    check([a for a, _ in comp.attrs] == ["brightness", "phase"] and comp.shape == "firefly",
          f"the chain carries the attributes its nodes declare {comp.attrs} and draws fireflies")
    bpy.context.scene.frame_set(60)
    settle(4)
    fps = measure_fps(2.0)
    NUMBERS["fireflies_live_fps"] = round(fps, 1)
    check(head.name in gpu_live.hosts and fps > 5 and not head.codenodes.last_error,
          f"the whole chain runs live as one GPU program ({fps:.0f} fps, no errors)")
    look_at((0.0, 0.0, 0.35), 0.9)
    settle(2)
    shot("fireflies_live_close.png")
    look_at((0.0, 0.0, 0.3), 5.5)
    settle(2)
    shot("fireflies_live.png")
    st["ff"] = (obj.name, tree.name, [n.name for n in nodes], head.name)
    return True


def phase_to_geometry(st):
    obj_name, tree_name, node_names, head_name = st["ff"]
    tree = bpy.data.node_groups[tree_name]
    look = tree.nodes[node_names[-1]]
    tg = gn_link.insert_make_real(tree, look)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    settle(4)
    head = bpy.data.objects[head_name]
    check(tg.inputs[0].name == "Particles" and tg.inputs[0].bl_idname == "NodeSocketBundle",
          "To Geometry takes the chain's Particles stream")
    check(tg.node_tree.name.startswith("To Geometry") and tg.label.startswith("To Points"),
          f"the To Geometry node's header says what it outputs ({tg.label})")
    me = points(bpy.data.objects[obj_name])
    names = {a.name for a in me.attributes}
    check(len(me.vertices) == head.codenodes.count and {"brightness", "phase", "velocity"} <= names,
          f"the points carry every per-particle attribute ({len(me.vertices)} points; "
          f"{sorted(n for n in names if not n.startswith('.'))})")
    br = np.empty(len(me.vertices), np.float32)
    me.attributes["brightness"].data.foreach_get("value", br)
    ph = np.empty(len(me.vertices), np.float32)
    me.attributes["phase"].data.foreach_get("value", ph)
    check(0.0 <= br.min() and br.max() <= 1.0001 and br.std() > 0.05 and ph.std() > 0.1,
          f"Blink's values vary per particle (brightness {br.min():.2f}..{br.max():.2f}, phase spread "
          f"{ph.std():.2f})")
    # the attribute output of a stage node is a field that reads it after To Geometry
    blink = tree.nodes[node_names[3]]
    check("brightness" in blink.outputs and blink.outputs["brightness"].bl_idname == "NodeSocketFloat",
          "Blink offers brightness as a field output")
    col = bpy.data.collections.get(gn_link.SOURCES)
    lc = gn_link._find_layer_collection(bpy.context.view_layer.layer_collection, col.name)
    check(lc is not None and lc.exclude, "the helper objects stay out of the view layer (Outliner stays clean)")
    return True


def phase_function_link(st):
    obj_name, tree_name, node_names, head_name = st["ff"]
    tree = bpy.data.node_groups[tree_name]
    wind = add(tree, 'STAGE', "Wind Field", 280, -400)
    push = add(tree, 'STAGE', "Push by Field", 700, -250)
    rise, blink = tree.nodes[node_names[2]], tree.nodes[node_names[3]]
    for l in [l for l in tree.links if l.from_node == rise and l.to_node == blink]:
        tree.links.remove(l)
    wire(tree, rise, push)
    wire(tree, push, blink)
    tree.links.new(wind.outputs["wind"], push.inputs["field"])
    settle(4)
    head = bpy.data.objects[head_name]
    comp, _v = links.composite(head)
    wsrc = gn_link.source_of(wind.node_tree)
    check(any(k[1] == "field" for k in links.CHAINS[head.name]["funcs"]) and "_wind" in comp.source
          and "field f0_" in comp.source.replace("#define n", "\n").replace("_field f0", "field f0"),
          "a function output wires into another node's function input (Wind Field -> Push by Field)")
    check(wsrc.name in links.STAGE_HEADS and not head.codenodes.last_error,
          f"the chain compiles with the wind function included ({head.codenodes.last_error[:80]})")
    return True


def phase_bend_particles(st):
    obj, tree = host("Swirl", (4.0, 0.0, 0.0))
    swirl = add(tree, 'PARTICLES', "Swirl", 0)
    bend = add(tree, 'STAGE', "Bend", 300)
    wire(tree, swirl, bend)
    tg = gn_link.insert_make_real(tree, bend)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    swirl.inputs["Count"].default_value = 20000
    bend.inputs["angle"].default_value = 0.0
    settle(4)
    bpy.context.scene.frame_set(30)
    settle(3)
    flat = np.empty(len(points(obj).vertices) * 3, np.float32)
    _co(points(obj), flat)
    bend.inputs["angle"].default_value = 2.0
    bend.inputs["span"].default_value = 3.0
    settle(4)
    me = points(obj)
    bent = np.empty(len(me.vertices) * 3, np.float32)
    _co(me, bent)
    flat, bent = flat.reshape(-1, 3), bent.reshape(-1, 3)
    r = 3.0 / 2.0
    th = flat[:, 2] / r
    expect = np.stack([r - (r - flat[:, 0]) * np.cos(th), flat[:, 1], (r - flat[:, 0]) * np.sin(th)], 1)
    err = float(np.abs(bent - expect).max()) if len(bent) == len(flat) else 1e9
    check(len(bent) == 20000 and err < 1e-3,
          f"Bend bends the swirl exactly as its maths says (max error {err:.1e} m)")
    bend.inputs["angle"].default_value = 0.0
    settle(4)
    back = np.empty(len(points(obj).vertices) * 3, np.float32)
    _co(points(obj), back)
    check(float(np.abs(back.reshape(-1, 3) - flat).max()) < 1e-5,
          "non-destructive: straighten it again and the swirl is exactly as before")
    # a live, bent galaxy for the picture
    for l in [l for l in tree.links if l.to_node == gout]:
        tree.links.remove(l)
    tree.nodes.remove(tg)
    swirl.inputs["Template"].default_value = "Galaxy"
    settle(3)
    bend.inputs["angle"].default_value = 1.6
    bend.inputs["span"].default_value = 2.5
    bend.inputs["stretch"].default_value = 1.8
    bend.inputs["twist"].default_value = 0.6
    bend.inputs["axis"].default_value = 0
    settle(4)
    fps = measure_fps(1.5)
    NUMBERS["bent_galaxy_fps"] = round(fps, 1)
    look_at((4.0, 0.0, 1.0), 7.5, (1.2, 0.0, 0.2))
    settle(2)
    shot("bend_swirl_live.png")
    check(fps > 5, f"a bent, twisted galaxy draws live ({fps:.0f} fps)")
    return True


def phase_bend_mesh(st):
    obj, tree = host("Column", (-4.0, 0.0, 0.0))
    gin = next(n for n in tree.nodes if n.type == 'GROUP_INPUT')
    cyl = tree.nodes.new("GeometryNodeMeshCylinder")
    cyl.inputs["Vertices"].default_value = 24
    cyl.inputs["Side Segments"].default_value = 40
    cyl.inputs["Radius"].default_value = 0.25
    cyl.inputs["Depth"].default_value = 3.0
    xf = tree.nodes.new("GeometryNodeTransform")
    xf.inputs["Translation"].default_value = (0.0, 0.0, 1.5)
    tree.links.new(cyl.outputs["Mesh"], xf.inputs["Geometry"])
    bend = add(tree, 'STAGE', "Bend", 300)
    tree.links.new(xf.outputs["Geometry"], bend.inputs["Mesh"])
    tg = gn_link.insert_make_real(tree, bend)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    bend.inputs["angle"].default_value = 1.5
    bend.inputs["span"].default_value = 3.0
    settle(5)
    src = gn_link.source_of(bend.node_tree)
    me = points(obj)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    top = co[np.argmax(co[:, 2])] if len(co) else None
    check(src.name in links.MESH_HEADS and len(co) == 24 * 41,
          f"the same Bend node works on a mesh wired straight into it ({len(co)} vertices)")
    r = 3.0 / 1.5
    th = 3.0 / r
    expect_top = np.array([r - r * np.cos(th), 0.0, r * np.sin(th)])
    ang = np.arctan2(co[:, 2], r - co[:, 0]) if len(co) else np.zeros(1)   # how far along the arc
    centre_top = co[ang > ang.max() - 0.01].mean(0) if len(co) else np.zeros(3)
    check(np.linalg.norm(centre_top - expect_top) < 0.02,
          f"the column's top ends where the bend puts it ({np.round(centre_top, 2)} vs {np.round(expect_top, 2)})")
    look_at((-3.2, 0.0, 1.2), 6.0, (1.25, 0.0, 0.0))
    settle(2)
    shot("bend_mesh.png")
    return True


def phase_assistant(st):
    """What Claude calls: code_node for the source, then code_stage for each step."""
    from codenodes import agent
    r = agent.code_node("particles", template="Spark Ball", name="Sparks", make_real=False)
    check(r["ok"], f"code_node makes a particle source ({r.get('error', '')})")
    a = agent.code_stage("Sparks", template="Gravity", values={"strength": 3.0})
    b = agent.code_stage("Sparks", template="Glow Look")
    head = bpy.data.objects[r["source"]]
    stages = [s.split("· ")[-1] for s in links.CHAINS.get(head.name, {}).get("stages", [])]
    check(a["ok"] and b["ok"] and a["wired"] and stages == ["Gravity", "Glow Look"],
          f"code_stage adds and wires stages at the end of the chain ({stages})")
    bad = agent.code_stage("Sparks", code="void behave(inout Particle p, float dt) {\n  p.velocity += gravty;\n}\n",
                           name="Typo")
    check(not bad["ok"] and "Typo" in bad.get("error", "") and "line 2" in bad.get("error", ""),
          f"a mistake in a stage is reported by node and line ({bad.get('error', '')[:90]})")
    return True


def phase_migration(st):
    g = bpy.data.node_groups.new("Make Real", "GeometryNodeTree")
    g[gn_link.MAKE_REAL] = True
    gn_link.sync_make_real()
    check(g.name.startswith("To Geometry"), f"an old Make Real node is renamed on load ({g.name})")
    return True


def phase_render(st):
    scene = bpy.context.scene
    if scene.camera is None:
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
        scene.collection.objects.link(cam)
        cam.location = (0.0, -9.0, 4.0)
        cam.rotation_euler = (1.15, 0.0, 0.0)
        scene.camera = cam
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 320, 180
    before = gpu_guard.stats()["during_render"]
    bpy.ops.codenodes.render('EXEC_DEFAULT', animation=False)
    after = gpu_guard.stats()["during_render"]
    check(after == before, "rendering the chains with CodeNodes runs no GPU code during the render")
    return True


def phase_reopen(st):
    bpy.ops.wm.save_as_mainfile(filepath=SAVE)
    bpy.ops.wm.open_mainfile(filepath=SAVE)
    st["t"] = time.perf_counter()
    return True


def phase_after_reopen(st):
    gn_link.sync()
    head = bpy.data.objects.get(st["ff"][3])
    if (head is None or head.name not in links.CHAINS) and time.perf_counter() - st["t"] < 5:
        return False
    check(head is not None and len(links.CHAINS.get(head.name, {}).get("stages", [])) == 5,
          "after reopening, the firefly chain is found again (five stages)")
    return True


PLAN = [phase_blank, phase_declarations, phase_fireflies, phase_to_geometry, phase_function_link,
        phase_bend_particles, phase_bend_mesh, phase_assistant, phase_migration, phase_render, phase_reopen,
        phase_after_reopen]
STATE = {}


def finish(exc):
    if NUMBERS and os.environ.get("CODENODES_NUMBERS"):
        with open(os.environ["CODENODES_NUMBERS"], "w") as fh:
            json.dump(NUMBERS, fh, indent=1)
    print("NUMBERS", json.dumps(NUMBERS), flush=True)
    if exc is None:
        print(f"\nALL {_checks} CHECKS PASSED", flush=True)
    elif isinstance(exc, Fail):
        print(f"FAIL: {exc}", flush=True)
    else:
        traceback.print_exception(exc)
        print("FAIL: exception", flush=True)
    bpy.ops.wm.quit_blender()


def driver():
    try:
        if not PLAN:
            finish(None)
            return None
        if PLAN[0](STATE):
            PLAN.pop(0)
        return 0.3
    except Exception as exc:
        finish(exc)
        return None


def main():
    only = os.environ.get("CODENODES_ONLY")
    if only:
        PLAN[:] = [phase_blank] + [f for f in PLAN if f.__name__ in only.split(",")]
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.app.timers.register(driver, first_interval=1.0, persistent=True)


main()
