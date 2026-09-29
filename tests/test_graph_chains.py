"""Code-node graphs in real Blender: streams that split into branches (heads and trails from the same
particles), Join Particles merging two streams, one function feeding nodes in different chains (a
Wind Field pushing particles and swaying a mesh), To Geometry partway along a chain, the GPU Cache
node, and Scene Lights / Material Look. Needs a window (run it on a hidden desktop in CI):

    blender --factory-startup --python tests/test_graph_chains.py

Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...", then quits. Set CODENODES_SHOTS to a
folder to save screenshots and CODENODES_NUMBERS to a .json path to save numbers.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gpu_cache, gpu_live, lights, links, live, particles  # noqa: E402

print("CodeNodes test: code-node graphs (this window closes by itself)", flush=True)
_checks = 0
SHOTS = os.environ.get("CODENODES_SHOTS", "")
NUMBERS = {}
TMP = tempfile.mkdtemp(prefix="cn_graph_")


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


def host(name, location=(0.0, 0.0, 0.0), mesh=None):
    obj = bpy.data.objects.new(name, mesh or bpy.data.meshes.new(name))
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
        gpu_cache._run_pending() if gpu_cache._pending else None
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def point_count(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    gs = ev.evaluated_geometry()
    n = 0
    if gs.pointcloud is not None:
        n += len(gs.pointcloud.points)
    if gs.mesh is not None:
        n += len(gs.mesh.vertices)
    return n


def errors_of(*objs):
    return [f"{o.name}: {o.codenodes.last_error}" for o in objs if o.codenodes.last_error]


def src(node):
    return gn_link.source_of(node.node_tree)


def set_count(node, n):
    """The particle count is an input on the node (the node's value wins over the source's)."""
    node.inputs["Count"].default_value = n


def look_at(location, distance, rotation=(1.15, 0.0, 0.6)):
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


def measure_fps(seconds=2.0):
    t0 = time.perf_counter()
    start = gpu_live._fps.get("total", 0)
    while time.perf_counter() - t0 < seconds:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    return (gpu_live._fps.get("total", 0) - start) / (time.perf_counter() - t0)


def test_branches():
    """One stream split into two looks: heads (fireflies) and trails (streaks), one simulation."""
    obj, tree = host("Branches")
    swarm = add(tree, 'PARTICLES', "Firefly Swarm", 0)
    swarm_src = src(swarm)
    set_count(swarm, 3000)
    wander = add(tree, 'STAGE', "Wander", 250)
    blink = add(tree, 'STAGE', "Blink", 500)
    heads = add(tree, 'STAGE', "Firefly Look", 800, 150)
    trails = add(tree, 'STAGE', "Streak Look", 800, -150)
    wire(tree, swarm, wander)
    wire(tree, wander, blink)
    wire(tree, blink, heads)
    wire(tree, blink, trails)
    settle()
    head_name = swarm_src.name
    pipes = links.pipelines_of(head_name)
    check(len(pipes) == 2, f"a stream wired into two looks makes two pipelines ({pipes})")
    branch = bpy.data.objects.get(pipes[1])
    check(branch is not None and gn_link.is_branch_object(branch), "the second one is a hidden branch object")
    check(links.BASE.get(branch.name) == head_name, "which knows its head")
    main_stages = [bpy.data.objects[n].codenodes.template_key for n in links.CHAINS[head_name]["stages"]]
    br_stages = [bpy.data.objects[n].codenodes.template_key for n in links.CHAINS[branch.name]["stages"]]
    check(set([main_stages[-1], br_stages[-1]]) == {"Firefly Look", "Streak Look"},
          f"each ends in its own look ({main_stages[-1]}, {br_stages[-1]})")
    check(main_stages[:-1] == br_stages[:-1] == ["Wander", "Blink"], "and shares the stages before the split")
    owner, _c, _v = gpu_live.sim_owner(branch)
    check(owner == swarm_src, "the branch shares the head's simulation (same behaviours)")
    check(branch.name in gpu_live.hosts and gpu_live.hosts[branch.name] == gpu_live.hosts[head_name],
          "and is drawn wherever its head is")
    bpy.context.scene.frame_set(30)
    look_at((0.0, 0.0, 0.5), 7.0)
    settle(4)
    check(not errors_of(swarm_src, branch), f"both pipelines draw without errors {errors_of(swarm_src, branch)}")
    sims = [n for n in particles._sims if n in (head_name, branch.name)]
    check(sims == [head_name], f"only one GPU simulation runs for both ({sims})")
    fps = measure_fps(1.5)
    NUMBERS["branches_fps"] = round(fps, 1)
    check(fps > 5, f"heads and trails draw live ({fps:.0f} fps)")
    shot("graph_branches.png")
    # a behaviour after the split makes that branch its own simulation
    grav = add(tree, 'STAGE', "Gravity", 650, -150)
    tree.links.remove(next(l for l in tree.links if l.to_node == trails))
    wire(tree, blink, grav)
    wire(tree, grav, trails)
    settle(4)
    pipes = links.pipelines_of(head_name)
    branch = bpy.data.objects.get(pipes[1])
    owner, _c, _v = gpu_live.sim_owner(branch)
    check(owner == branch, "a behaviour after the split gives that branch its own simulation")
    check(not errors_of(swarm_src, branch), "which draws too")
    bpy.data.objects.remove(obj)
    return tree


def test_merge():
    """Two sources into Join Particles, one look after it, and a To Geometry that outputs both."""
    obj, tree = host("Merge", (6.0, 0.0, 0.0))
    a = add(tree, 'PARTICLES', "Swirl", 0, 200)
    b = add(tree, 'PARTICLES', "Fountain", 0, -200)
    set_count(a, 1000)
    set_count(b, 500)
    join = gn_link.insert(tree, gn_link.build_join(), (300, 0))
    glow = add(tree, 'STAGE', "Glow Look", 550)
    real = gn_link.insert(tree, gn_link.build_make_real(), (850, 0))
    tree.links.new(a.outputs["Particles"], join.inputs["Particles A"])
    tree.links.new(b.outputs["Particles"], join.inputs["Particles B"])
    wire(tree, join, glow)
    tree.links.new(glow.outputs["Particles"], real.inputs[0])
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(real.outputs[0], gout.inputs[0])
    settle(4)
    check(len(links.CHAINS.get(src(a).name, {}).get("stages", [])) == 1 and
          len(links.CHAINS.get(src(b).name, {}).get("stages", [])) == 1,
          "the stage after Join Particles is part of both sources' pipelines")
    infos = [n for n in real.node_tree.nodes if n.type == 'OBJECT_INFO']
    check(len(infos) == 2, f"To Geometry after a join reads both pipelines ({len(infos)} Object Info nodes)")
    bpy.context.scene.frame_set(20)
    settle(3)
    n = point_count(obj)
    check(n == 1500, f"and outputs every particle from both ({n:,} of 1,500)")
    check(real.label.startswith("To Points"), f"it labels itself ({real.label})")
    bpy.data.objects.remove(obj)


def test_function_fanout():
    """One Wind Field feeding a particle behaviour and a mesh stage in another chain."""
    grass_me = bpy.data.meshes.new("Grass")
    import bmesh
    bm = bmesh.new()
    rng = np.random.default_rng(3)
    for i in range(300):
        x, y = rng.uniform(-2, 2, 2)
        v0 = bm.verts.new((x - 0.02, y, 0.0))
        v1 = bm.verts.new((x + 0.02, y, 0.0))
        v2 = bm.verts.new((x, y, 0.5 + rng.uniform(0, 0.3)))
        bm.faces.new((v0, v1, v2))
    bm.to_mesh(grass_me)
    bm.free()
    obj, tree = host("Windy", (0.0, 6.0, 0.0), grass_me)
    wind = add(tree, 'STAGE', "Wind Field", -300, 300)
    ps = add(tree, 'PARTICLES', "Swirl", 0, 150)
    set_count(ps, 800)
    push = add(tree, 'STAGE', "Push by Field", 300, 150)
    sway = add(tree, 'STAGE', "Sway by Field", 300, -150)
    gin = next(n for n in tree.nodes if n.type == 'GROUP_INPUT')
    wire(tree, ps, push)
    tree.links.new(gin.outputs[0], sway.inputs["Mesh"])
    tree.links.new(wind.outputs["wind"], push.inputs["field"])
    tree.links.new(wind.outputs["wind"], sway.inputs["field"])
    settle(4)
    wname = src(wind).name
    pf = links.CHAINS.get(src(ps).name, {}).get("funcs", {})
    sf = links.CHAINS.get(src(sway).name, {}).get("funcs", {})
    check(any(v[0] == wname for v in pf.values()), "the Wind Field feeds the particle chain")
    check(any(v[0] == wname for v in sf.values()), "and the mesh chain (a function output feeds several nodes)")
    check(src(sway).name in links.MESH_HEADS, "Sway by Field wired to plain geometry heads a mesh chain")
    bpy.context.scene.frame_set(24)
    settle(4)
    check(not errors_of(src(ps), src(sway)), f"both compile and run {errors_of(src(ps), src(sway))}")
    comp_p, _ = links.composite(src(ps))
    comp_m, _ = links.composite(src(sway))
    check("wind" in comp_p.source and "wind" in comp_m.source, "each program includes the wind function once")
    # make the swayed grass real: the mesh program (with the wind's noise) must compile and run
    real = gn_link.insert(tree, gn_link.build_make_real(), (600, -150))
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(sway.outputs["Mesh"], real.inputs[0])
    tree.links.new(real.outputs[0], gout.inputs[0])
    settle(4)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    gs = obj.evaluated_get(dg).evaluated_geometry()
    nv = len(gs.mesh.vertices) if gs.mesh is not None else 0
    check(not errors_of(src(sway)) and nv == len(grass_me.vertices),
          f"the swayed grass is made real by To Mesh ({nv} of {len(grass_me.vertices)} vertices) {errors_of(src(sway))}")
    bpy.data.objects.remove(obj)


def test_mid_chain_geometry():
    """To Geometry partway along a chain outputs the stream at that point; the rest still draws live."""
    obj, tree = host("Mid", (6.0, 6.0, 0.0))
    ps = add(tree, 'PARTICLES', "Swirl", 0)
    set_count(ps, 700)
    wander = add(tree, 'STAGE', "Wander", 250)
    bend = add(tree, 'STAGE', "Bend", 500)
    look = add(tree, 'STAGE', "Glow Look", 750)
    real = gn_link.insert(tree, gn_link.build_make_real(), (500, -250))
    wire(tree, ps, wander)
    wire(tree, wander, bend)
    wire(tree, bend, look)
    tree.links.new(wander.outputs["Particles"], real.inputs[0])
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(real.outputs[0], gout.inputs[0])
    settle(4)
    pipes = links.pipelines_of(src(ps).name)
    check(len(pipes) == 2, f"a To Geometry partway along makes its own pipeline ({len(pipes)})")
    main = links.CHAINS[pipes[0]]
    mid = links.CHAINS[pipes[1]]
    check(len(main["stages"]) == 3 and len(mid["stages"]) == 1,
          "the live one runs every stage, the one made real only the stages before it")
    br = bpy.data.objects[pipes[1]]
    check(br.codenodes.real_mode in ('EVERY_FRAME', 'ON_CHANGE'), f"the partway one is made real ({br.codenodes.real_mode})")
    check(src(ps).codenodes.real_mode == 'NONE', "the full chain still draws live")
    bpy.context.scene.frame_set(12)
    settle(3)
    check(point_count(obj) == 700, "and To Geometry outputs the particles as they are at that point")
    bpy.data.objects.remove(obj)


def test_cache():
    obj, tree = host("Cached", (-6.0, 0.0, 0.0))
    ps = add(tree, 'PARTICLES', "Swirl", 0)
    s = src(ps)
    set_count(ps, 20000)
    wander = add(tree, 'STAGE', "Wander", 250)
    cache = gn_link.insert(tree, gpu_cache.build_group(), (500, 0))
    look = add(tree, 'STAGE', "Glow Look", 800)
    wire(tree, ps, wander)
    wire(tree, wander, cache)
    wire(tree, cache, look)
    cache.inputs["Start"].default_value = 1
    cache.inputs["End"].default_value = 48
    settle(3)
    check(links.CHAINS[s.name].get("cache"), "the pipeline knows it passes through a GPU Cache")
    check(cache.label.startswith("GPU Cache"), f"the cache node shows its state ({cache.label})")
    cache.inputs[gpu_cache.BAKE].default_value = True
    t0 = time.perf_counter()
    settle(2)
    for _ in range(20):
        if gpu_cache.manifest(s.name):
            break
        gpu_cache._run_pending()
        settle(1)
    m = gpu_cache.manifest(s.name)
    check(m is not None, "⟳ Bake Now bakes the simulation")
    check(not cache.inputs[gpu_cache.BAKE].default_value, "and switches itself back off, like a button")
    mb = m["bytes"] / 2 ** 20
    NUMBERS["cache_20k_48f_mb"] = round(mb, 2)
    NUMBERS["cache_20k_48f_bytes_per_particle_frame"] = round(m["bytes"] / (20000 * 48), 2)
    NUMBERS["cache_bake_s"] = m["seconds"]
    check(m["end"] - m["start"] + 1 == 48, f"48 frames baked ({mb:.1f} MB, {m['seconds']:.1f} s)")
    # the live simulation at frame 30, then the cached one: they match to float16 precision
    bpy.context.scene.frame_set(30)
    comp, values = links.composite(s)
    state_live, _st = particles.simulate("cn_test_live", comp.source, s.codenodes.count, 30, 1,
                                         bpy.context.scene.render.fps, values, s.codenodes.substeps,
                                         s.codenodes.stagger, s.codenodes.prewarm, None)
    menu_set = False
    for item in ("Cached",):
        cache.inputs["Mode"].default_value = item
        menu_set = True
    settle(3)
    check(menu_set and gpu_cache.STATE.get(s.name, {}).get("mode") == 'CACHED', "Mode switches to Cached")
    check(cache.label.startswith("Cached 1–48"), f"the header says what's cached ({cache.label})")
    cached = gpu_cache.load(s.name, 30)
    err = float(np.abs(cached["position"] - state_live["position"]).max())
    scale = float(np.abs(state_live["position"]).max())
    NUMBERS["cache_max_error_m"] = err
    check(err <= max(2e-3, scale * 1e-3), f"cached frame 30 matches the live simulation ({err:.2e} m)")
    t0 = time.perf_counter()
    for f in (40, 5, 22):
        bpy.context.scene.frame_set(f)
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    NUMBERS["cache_scrub_ms_per_frame"] = round((time.perf_counter() - t0) / 3 * 1000, 1)
    check(not errors_of(s), f"cached frames play back live when scrubbing ({NUMBERS['cache_scrub_ms_per_frame']} ms/frame)")
    # To Geometry downstream reads the cached frame
    real = gn_link.insert(tree, gn_link.build_make_real(), (1100, 0))
    tree.links.new(look.outputs["Particles"], real.inputs[0])
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(real.outputs[0], gout.inputs[0])
    bpy.context.scene.frame_set(30)
    settle(4)
    check(point_count(obj) == 20000, "To Geometry after a cached simulation outputs the baked particles")
    cache.inputs[gpu_cache.CLEAR].default_value = True
    settle(2)
    gpu_cache._run_pending()
    check(gpu_cache.manifest(s.name) is None, "✕ Clear deletes the baked frames")
    bpy.data.objects.remove(obj)


def _positions(obj):
    obj.update_tag()                # a script's frame_set doesn't refresh relations the way the UI does
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    gs = obj.evaluated_get(dg).evaluated_geometry()
    pc = gs.pointcloud
    if pc is None or not len(pc.points):
        return np.zeros((0, 3), np.float32)
    co = np.empty(len(pc.points) * 3, np.float32)
    pc.attributes["position"].data.foreach_get("vector", co)
    return co.reshape(-1, 3)


def test_bake_node():
    """Blender's own Bake node after To Geometry: bake a range, scrub it, render it."""
    scene = bpy.context.scene
    obj, tree = host("BakeNode", (-6.0, 6.0, 0.0))
    ps = add(tree, 'PARTICLES', "Fountain", 0)
    set_count(ps, 2000)
    real = gn_link.insert(tree, gn_link.build_make_real(), (300, 0))
    bake = tree.nodes.new("GeometryNodeBake")
    if not any(s.name == "Geometry" for s in bake.inputs):
        bake.bake_items.new('GEOMETRY', "Geometry")      # a new Bake node has no items yet
    bake.location = (600, 0)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(ps.outputs["Particles"], real.inputs[0])
    tree.links.new(real.outputs[0], bake.inputs["Geometry"])
    tree.links.new(bake.outputs["Geometry"], gout.inputs[0])
    scene.frame_start, scene.frame_end = 1, 24
    scene.frame_set(1)
    settle(4)
    mod = obj.modifiers["GeometryNodes"]
    mod.bake_directory = os.path.join(TMP, "bake")
    try:
        mod.bake_target = 'DISK'
    except (TypeError, AttributeError):
        pass
    item = next(b for b in mod.bakes if b.node == bake)
    item.bake_mode = 'ANIMATION'
    bake_id = item.bake_id
    for o in bpy.context.view_layer.objects:
        o.select_set(o == obj)
    bpy.context.view_layer.objects.active = obj
    t0 = time.perf_counter()
    win, area = view3d()
    with bpy.context.temp_override(window=win, area=area, object=obj, active_object=obj):
        res = bpy.ops.object.geometry_node_bake_single(session_uid=obj.session_uid, modifier_name=mod.name,
                                                       bake_id=bake_id)
    NUMBERS["bake_node_24f_s"] = round(time.perf_counter() - t0, 2)
    check('FINISHED' in res, f"Blender's Bake node bakes a To Geometry result ({NUMBERS['bake_node_24f_s']} s)")
    files = []
    for root, _d, fs in os.walk(os.path.join(TMP, "bake")):
        files += fs
    check(len(files) >= 24, f"and writes the frames to disk ({len(files)} files)")
    s = src(ps)
    scene.frame_set(6)
    before = _positions(obj)
    scene.frame_set(18)
    before18 = _positions(obj)
    print(f"  (baked, node still on: {len(before)} points, move 6->18 "
          f"{float(np.abs(before - before18).max()) if len(before) == len(before18) and len(before) else -1:.3f} m)",
          flush=True)
    s.codenodes.enabled = False                 # the GPU node is out of the picture from here on
    s.data.clear_geometry()                     # and its last result is gone: only the bake remains
    s.data.update()
    scene.frame_set(6)
    a = _positions(obj)
    scene.frame_set(18)
    b = _positions(obj)
    check(len(a) == len(b) == 2000, f"scrubbing plays the baked points (the code node switched off) "
                                    f"({len(a)}, {len(b)})")
    check(float(np.abs(a - b).max()) > 0.1, f"and they move from frame to frame "
                                             f"(largest move {float(np.abs(a - b).max()):.3f} m)")
    scene.render.engine = 'BLENDER_EEVEE' if 'BLENDER_EEVEE' in {e.identifier for e in
                                                                 bpy.types.RenderSettings.bl_rna.properties[
                                                                     'engine'].enum_items} else scene.render.engine
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 180, 100
    cam = bpy.data.objects.new("BakeCam", bpy.data.cameras.new("BakeCam"))
    scene.collection.objects.link(cam)
    cam.location = (obj.location.x + 6.0, obj.location.y - 6.0, 3.0)
    from mathutils import Vector
    cam.rotation_euler = (Vector((obj.location.x, obj.location.y, 1.5)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    scene.camera = cam
    scene.render.filepath = os.path.join(TMP, "baked_render.png")
    gpu_before = __import__("codenodes.gpu_guard", fromlist=["x"]).dispatch_count() \
        if hasattr(__import__("codenodes.gpu_guard", fromlist=["x"]), "dispatch_count") else None
    bpy.ops.render.render(write_still=True)
    check(os.path.exists(scene.render.filepath), "an F12 render of a baked frame works")
    s.codenodes.enabled = True
    bpy.data.objects.remove(cam)
    bpy.data.objects.remove(obj)


def test_lights():
    scene = bpy.context.scene
    ldata = bpy.data.lights.new("Key", 'POINT')
    ldata.energy = 500.0
    lamp = bpy.data.objects.new("Key", ldata)
    scene.collection.objects.link(lamp)
    lamp.location = (2.0, -2.0, 3.0)
    obj, tree = host("Lit", (0.0, -6.0, 0.0))
    lt = add(tree, 'STAGE', "Scene Lights", -300, 300)
    ps = add(tree, 'PARTICLES', "Swirl", 0)
    set_count(ps, 500)
    ml = add(tree, 'STAGE', "Material Look", 300)
    wire(tree, ps, ml)
    tree.links.new(lt.outputs["light"], ml.inputs["light"])
    mat = bpy.data.materials.new("Copper")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.9, 0.45, 0.2, 1.0)
    bsdf.inputs["Metallic"].default_value = 1.0
    bsdf.inputs["Roughness"].default_value = 0.3
    ml.inputs["mat"].default_value = mat
    settle(4)
    lsrc, msrc = src(lt), src(ml)
    vals = {p.name: p.value for p in lsrc.codenodes.params}
    check(vals.get("l0_type") == 1.0, f"Scene Lights reads the point lamp (type {vals.get('l0_type')})")
    local = obj.matrix_world.inverted() @ lamp.matrix_world.translation
    check(abs(vals["l0_px"] - local.x) < 1e-4 and abs(vals["l0_pz"] - local.z) < 1e-4,
          "in the space of the object holding the node")
    check(abs(vals["l0_r"] - 500.0) < 1e-3, "with its power as the colour's energy")
    lamp.location = (-1.0, 1.0, 2.0)
    bpy.context.view_layer.update()
    lights.sync()
    vals2 = {p.name: p.value for p in lsrc.codenodes.params}
    local2 = obj.matrix_world.inverted() @ lamp.matrix_world.translation
    check(abs(vals2["l0_px"] - local2.x) < 1e-4, "moving the lamp updates the light data (no recompile)")
    mvals = {p.name: p.value for p in msrc.codenodes.params}
    check(abs(mvals["mat_base_r"] - 0.9) < 1e-4 and mvals["mat_metallic"] == 1.0,
          "Material Look reads the material's Principled BSDF values")
    sockets = [s.name for s in ml.inputs]
    check("mat_base_r" not in sockets and "l0_px" not in [s.name for s in lt.inputs],
          "hidden inputs never show as sockets")
    comp, _v = links.composite(src(ps))
    check("brdf" in comp.source, "the lit look's program includes the scene's lighting function")
    settle(3)
    check(not errors_of(src(ps), msrc, lsrc), f"lit particles compile and draw {errors_of(src(ps), msrc, lsrc)}")
    # a GPU Surface lit by the scene, with the material
    surf = add(tree, 'MESH', "Donut", 0, -300)
    tree.links.new(lt.outputs["light"], surf.inputs["Lights"])
    tree.links.new(ml.outputs["material"], surf.inputs["Material"])
    settle(3)
    s_src = src(surf)
    check(s_src.get("cn_lights_src") == lsrc.name and s_src.get("cn_material_src") == msrc.name,
          "a GPU Surface knows its Scene Lights and Material Look")
    lit, m = lights.surface_uniforms(s_src, scene)
    check(lit is not None and len(lit) == 132 and m is not None and abs(m[0] - 0.9) < 1e-4,
          "and hands them to the raymarcher")
    look_at(obj.location, 6.0)
    settle(3)
    check(not errors_of(s_src), f"the lit surface draws {errors_of(s_src)}")
    shot("graph_lit_surface.png")
    bpy.data.objects.remove(obj)
    bpy.data.objects.remove(lamp)


def main():
    status = 0
    try:
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 120
        test_branches()
        test_merge()
        test_function_fanout()
        test_mid_chain_geometry()
        test_cache()
        test_bake_node()
        test_lights()
        print(f"ALL {_checks} CHECKS PASSED", flush=True)
    except Fail as exc:
        print(f"FAIL: {exc}", flush=True)
        status = 1
    except Exception:
        traceback.print_exc()
        print("FAIL: an exception", flush=True)
        status = 1
    finally:
        out = os.environ.get("CODENODES_NUMBERS")
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(NUMBERS, fh, indent=1)
        print("numbers:", json.dumps(NUMBERS), flush=True)
        shutil.rmtree(TMP, ignore_errors=True)
    bpy.ops.wm.quit_blender() if status == 0 else os._exit(status)


def _start():
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.app.timers.register(lambda: (main(), None)[1], first_interval=0.5)


_start()
