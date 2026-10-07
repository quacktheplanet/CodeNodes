"""GPU nodes in real Blender: live drawing, settings on the node, To Geometry, Emit From, GPU Mesh,
the Edit Code pop-up, and rendering with CodeNodes. Needs a window (the GPU isn't available with -b):

    blender --factory-startup --python tests/test_gpu_nodes.py

Runs from timers once the window is up, prints each check, ends with "ALL n CHECKS PASSED" or
"FAIL: ...", then quits. Set CODENODES_SHOTS to a folder to save screenshots, and
CODENODES_NUMBERS to a .json path to save the measured frame rates and timings.
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
from codenodes import gn_link, gn_sockets, gn_ui, gpu_guard, gpu_live, live, render_ops  # noqa: E402

print("CodeNodes test: GPU nodes (this window closes by itself)", flush=True)
_checks = 0
SHOTS = os.environ.get("CODENODES_SHOTS", "")
NUMBERS = {}
TMP = tempfile.mkdtemp(prefix="cn_gpu_")
SAVE = os.path.join(TMP, "gpu_nodes.blend")


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


def shot(name, area=None, window=None):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    win, a = view3d()
    area = area or a
    window = window or win or bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(SHOTS, name))
    print(f"  screenshot: {os.path.join(SHOTS, name)}", flush=True)


def evaluated_mesh(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    return obj.evaluated_get(dg).data


def coords(me):
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def node_editor(tree):
    screen = bpy.context.window.screen
    area = next((a for a in screen.areas if a.type == 'NODE_EDITOR'), None)
    if area is None:
        area = next(a for a in screen.areas if a.type in ('DOPESHEET_EDITOR', 'TIMELINE', 'OUTLINER'))
        area.type = 'NODE_EDITOR'
    sp = area.spaces.active
    sp.tree_type = 'GeometryNodeTree'
    sp.pin = True
    sp.node_tree = tree
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def ctx(area, region):
    return bpy.context.temp_override(window=bpy.context.window, area=area, region=region)


def host(name):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(f"{name} Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-600, 0), (700, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree


def settle(n=3):
    for _ in range(n):
        gn_link.sync()
        live._flush()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def measure_fps(seconds=2.0):
    """Whole-window redraws per second (draw + swap, the way Blender shows a frame), counting only
    redraws in which the live drawing really ran; plus the CPU time of the last live draw."""
    t0 = time.perf_counter()
    start = gpu_live._fps.get("total", 0)
    while time.perf_counter() - t0 < seconds:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    frames = gpu_live._fps.get("total", 0) - start
    return frames / (time.perf_counter() - t0), gpu_live._fps["last_ms"]


# ---- the phases ------------------------------------------------------------------------------------

def phase_menu_and_particles(st):
    win, area3d = view3d()
    sp = area3d.spaces.active
    sp.shading.type = 'SOLID'
    r3d = sp.region_3d
    from mathutils import Euler
    r3d.view_rotation = Euler((0.35, 0, 0.3)).to_quaternion()
    r3d.view_distance = 16.0
    obj, tree = host("Host")
    area, region = node_editor(tree)
    st.update(area=area, tree=tree.name)
    check(gn_ui._add_menu in bpy.types.NODE_MT_add._dyn_ui_initialize(), "Add › CodeNodes is in the Add menu")
    labels = []

    class L:
        operator_context = ''

        def operator(self, idname, text="", icon=''):
            labels.append((idname, text))

            class O:
                pass
            return O()

        def label(self, text="", icon=''):
            labels.append(("label", text))

        def separator(self):
            pass

        def menu(self, idname, text="", icon=''):
            labels.append(("menu", text))

    gn_ui.CODENODES_MT_gn_add.draw(type("M", (), {"layout": L()})(), bpy.context)
    texts = [t for _i, t in labels]
    check(texts[0] == "" and labels[0][0] == "codenodes.gn_add_make_real" and "GPU Particles" in texts
          and "GPU Surface (SDF)" in texts and "GPU Mesh" in texts,
          "the menu offers To Geometry and the GPU Particles / Surface / Mesh templates")
    with ctx(area, region):
        res = bpy.ops.codenodes.gn_add('EXEC_DEFAULT', kind='PARTICLES', template="Galaxy", use_transform=False)
    node = tree.nodes.active                # particles travel on a Particles socket: drawn live, no link needed
    settle()
    names = [s.name for s in node.inputs]
    check(res == {'FINISHED'} and names[:4] == [gn_sockets.EDIT, "Template", "Count", "Emit From"]
          and {"size", "twist", "spin", "Glow", "Brightness", "Pre-warm"} <= set(names),
          f"a GPU Particles node: Edit Code, Template, Count, Emit From, its sliders, then settings ({names[:8]}…)")
    src = gn_link.source_of(node.node_tree)
    check(src.codenodes.real_mode == "NONE" and "live" in node.label and "1.0M" in node.label,
          f"alone it's drawn live, and its header says so ('{node.label}')")
    check(len(src.data.vertices) == 0, "nothing is copied into Blender while it's live")
    st["node"] = node.name
    return True


def phase_live_fps(st):
    tree = bpy.data.node_groups[st["tree"]]
    node = tree.nodes[st["node"]]
    src = gn_link.source_of(node.node_tree)
    fps1, ms1 = measure_fps()
    check(fps1 > 5 and gpu_guard.stats()["draws"] > 0 and not src.codenodes.last_error,
          f"1,000,000 particles draw live straight from the GPU ({fps1:.0f} fps, {ms1:.2f} ms per draw)")
    NUMBERS["particles_1M_fps"] = round(fps1, 1)
    shot("gpu_galaxy_live.png")
    node.inputs["Count"].default_value = 4_194_304
    settle()
    check(src.codenodes.count == 4_194_304, "the node's Count socket sets the particle count")
    fps4, ms4 = measure_fps()
    NUMBERS["particles_4M_fps"] = round(fps4, 1)
    check(fps4 > 2 and not src.codenodes.last_error, f"4,194,304 particles still draw live ({fps4:.0f} fps)")
    node.inputs["Count"].default_value = 1_000_000
    node.inputs["Glow"].default_value = False
    settle()
    check(src.codenodes.blend == 'SOLID' and src.codenodes.count == 1_000_000, "the Glow toggle on the node works")
    node.inputs["Glow"].default_value = True
    settle()
    return True


def phase_template_and_frames(st):
    tree = bpy.data.node_groups[st["tree"]]
    node = tree.nodes[st["node"]]
    src = gn_link.source_of(node.node_tree)
    check(node.get("cn_menu_ok_Template") and node.inputs["Template"].default_value == "Galaxy",
          "the Template dropdown shows the node's template")
    node.inputs["Template"].default_value = "Flow"
    settle()
    code = src.codenodes.text.as_string()
    names = [s.name for s in node.inputs]
    check("curlNoise" in code and src.codenodes.template_key == "Flow" and node.node_tree.name.startswith("Code · Flow")
          and {"scale", "speed", "radius"} <= set(names) and "twist" not in names,
          f"picking Flow in the dropdown loads that template and its sliders ('{node.node_tree.name}')")
    src.codenodes.text.from_string(code.replace("0.55 0.1 3.0", "0.6 0.1 3.0"))
    node.inputs["Template"].default_value = "Attractor"
    settle()
    backups = [t for t in bpy.data.texts if "(before Attractor)" in t.name]
    check(backups and "0.6 0.1 3.0" in backups[0].as_string(),
          f"switching away from edited code keeps it in a backup text ('{backups[0].name if backups else ''}')")
    node.inputs["Template"].default_value = "Galaxy"
    settle()
    group = node.node_tree
    frame = group.nodes.get("Code")
    status = group.nodes.get("Status")
    check(frame is not None and frame.type == 'FRAME' and frame.text == src.codenodes.text and status is not None
          and status.text is not None, "Tab into the node: a frame shows the code, another the status")
    return True


def phase_edit_popup(st):
    tree = bpy.data.node_groups[st["tree"]]
    node = tree.nodes[st["node"]]
    src = gn_link.source_of(node.node_tree)
    before = len(bpy.context.window_manager.windows)
    node.inputs[gn_sockets.EDIT].default_value = True
    gn_link._dirty[0] = True
    st["t"] = time.perf_counter()
    st["before"] = before
    return True


def phase_edit_popup_check(st):
    wm = bpy.context.window_manager
    tree = bpy.data.node_groups[st["tree"]]
    node = tree.nodes[st["node"]]
    src = gn_link.source_of(node.node_tree)
    pop = next((w for w in wm.windows if w.screen.areas and w.screen.areas[0].type == 'TEXT_EDITOR'
                and len(w.screen.areas) == 1), None)
    if pop is None and time.perf_counter() - st["t"] < 5:
        return False
    check(pop is not None and pop.screen.areas[0].spaces.active.text == src.codenodes.text,
          "switching the node's ✎ Edit Code on pops up a Text Editor window with its code")
    check(node.inputs[gn_sockets.EDIT].default_value is False, "and the toggle switches itself off, like a button")
    area, region = st["area"], next(r for r in st["area"].regions if r.type == 'WINDOW')
    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node
    if SHOTS:                               # the node, big: borrow the 3D view's area for a moment
        _w, big = view3d()
        big.type = 'NODE_EDITOR'
        sp = big.spaces.active
        sp.tree_type, sp.pin, sp.node_tree = 'GeometryNodeTree', True, tree
        sp.show_region_ui = False
        breg = next(r for r in big.regions if r.type == 'WINDOW')
        with ctx(big, breg):
            bpy.ops.node.view_selected()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        shot("gpu_node_edit_button.png", area=big)
        big.type = 'VIEW_3D'
    shot("gpu_code_popup.png", area=pop.screen.areas[0], window=pop)
    with bpy.context.temp_override(window=pop):
        bpy.ops.wm.window_close()
    with ctx(area, region):
        res = bpy.ops.codenodes.edit_code_popup('EXEC_DEFAULT')
    pop2 = next((w for w in wm.windows if w.screen.areas and w.screen.areas[0].type == 'TEXT_EDITOR'
                 and len(w.screen.areas) == 1), None)
    check(res == {'FINISHED'} and pop2 is not None, "Ctrl+E / double-click (the same operator) opens it too")
    km = bpy.context.window_manager.keyconfigs.addon.keymaps.get("Node Editor")
    keys = {(k.type, k.value, k.ctrl) for k in km.keymap_items if k.idname == "codenodes.edit_code_popup"}
    check({('LEFTMOUSE', 'DOUBLE_CLICK', False), ('E', 'PRESS', True)} <= keys,
          "double-click and Ctrl+E are mapped in the Node Editor")
    with bpy.context.temp_override(window=pop2):
        bpy.ops.wm.window_close()
    return True


def phase_make_real_particles(st):
    tree = bpy.data.node_groups[st["tree"]]
    node = tree.nodes[st["node"]]
    src = gn_link.source_of(node.node_tree)
    node.inputs["Count"].default_value = 20_000
    mr = gn_link.insert_make_real(tree, node)
    settle()
    names = [s.name for s in mr.inputs]
    check(names == ["Particles", "When", "Max Points", "Keep Velocity", "Keep Age"],
          f"To Geometry after particles: Particles in, When, Max Points, Keep Velocity, Keep Age ({names})")
    check(src.codenodes.real_mode == 'EVERY_FRAME', "Automatic means every frame for particles")
    bpy.context.scene.frame_set(10)
    settle(1)
    me = src.data
    attrs = {a.name for a in me.attributes}
    check(len(me.vertices) == 20_000 and {"velocity", "speed", "age", "life"} <= attrs,
          f"it makes 20,000 real points with velocity, speed, age and life ({len(me.vertices):,})")
    check(mr.label.startswith("To Points") and "ms" in mr.label,
          f"its header says what it outputs and what that costs ('{mr.label}')")
    mr.inputs["Max Points"].default_value = 5000
    mr.inputs["Keep Age"].default_value = False
    settle()
    bpy.context.scene.frame_set(11)
    settle(1)
    attrs = {a.name for a in src.data.attributes}
    check(len(src.data.vertices) == 5000 and "age" not in attrs and "velocity" in attrs,
          "Max Points and Keep Age on the node limit what's made real")
    # further nodes after To Geometry: instance a small cube on every point
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    inst = tree.nodes.new("GeometryNodeInstanceOnPoints")
    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (0.02, 0.02, 0.02)
    real = tree.nodes.new("GeometryNodeRealizeInstances")
    tree.links.new(mr.outputs["Geometry"], inst.inputs["Points"])
    tree.links.new(cube.outputs["Mesh"], inst.inputs["Instance"])
    tree.links.new(inst.outputs["Instances"], real.inputs["Geometry"])
    tree.links.new(real.outputs["Geometry"], gout.inputs[0])
    inst.location, cube.location, real.location, mr.location = (350, 0), (150, -200), (550, 0), (150, 0)
    me = evaluated_mesh(bpy.data.objects["Host"])
    check(len(me.vertices) == 5000 * 8, f"later nodes use the real points: Instance on Points makes {len(me.vertices):,} verts")
    st["real"] = mr.name
    # Only for Render: live again in the viewport
    mr.inputs["When"].default_value = "Only for Render"
    settle()
    check(src.codenodes.real_mode == 'RENDER_ONLY' and len(src.data.vertices) == 0 and "render" in mr.label,
          f"When = Only for Render keeps it live in the viewport ('{mr.label}')")
    mr.inputs["When"].default_value = "Automatic"
    settle()
    bpy.context.scene.frame_set(1)
    settle(1)
    return True


def phase_emit_from(st):
    plane = bpy.data.objects.new("Emitter", bpy.data.meshes.new("Emitter"))
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=8, y_segments=8, size=1.5)
    bm.to_mesh(plane.data)
    bm.free()
    plane.location = (0, 0, 3.0)
    bpy.context.scene.collection.objects.link(plane)
    obj, tree = host("Sprayer")
    area, region = node_editor(tree)
    group, err = gn_link.create('PARTICLES', "Fountain")
    node = gn_link.insert(tree, group, (0, 0))
    src = gn_link.source_of(group)
    src.codenodes.text.from_string("""\
// spawn on the Emit From surface, drift up a little
// @param lift 0.2 0.0 2.0
void spawn(inout Particle p) {
  p.position = emitPoint(p.seed) + emitNormal(p.seed) * 0.001;
  p.life = 3.0;
}
void update(inout Particle p, float dt) { p.velocity = vec3(0.0, 0.0, lift); p.position += p.velocity * dt; }
""")
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    mr = gn_link.insert_make_real(tree, node)
    tree.links.new(mr.outputs["Geometry"], gout.inputs[0])
    node.inputs["Count"].default_value = 3000
    node.inputs["Emit From"].default_value = plane
    settle()
    gpu_live.refresh_emitters()
    live.rebuild(src)
    co = coords(src.data)
    host_loc = obj.location
    ok = len(co) == 3000 and np.all(np.abs(co[:, 0]) <= 1.51) and np.all(np.abs(co[:, 1]) <= 1.51) \
        and np.all(np.abs(co[:, 2] - 3.0) < 0.3)
    check(ok, f"Emit From: particles spawn on the other object's surface (z {co[:, 2].min():.2f}…{co[:, 2].max():.2f})")
    node_editor(bpy.data.node_groups[st["tree"]])
    return True


def phase_surface(st):
    obj, tree = host("Castle Host")
    obj.location = (7, 0, 0)
    area, region = node_editor(tree)
    with ctx(area, region):
        bpy.ops.codenodes.gn_add('EXEC_DEFAULT', kind='MESH', template="Castle", use_transform=False)
    node = tree.nodes.active
    tree.links.new(node.outputs["Geometry"], next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT').inputs[0])
    settle()
    src = gn_link.source_of(node.node_tree)
    fps, ms = measure_fps(1.5)
    NUMBERS["castle_live_fps"] = round(fps, 1)
    check(not src.codenodes.last_error and "live" in node.label,
          f"the Castle raymarches live with no errors ({fps:.0f} fps with the galaxy)")
    shot("gpu_castle_live.png")
    mr = gn_link.insert_make_real(tree, node)
    settle()
    t0 = time.perf_counter()
    live.rebuild(src)
    NUMBERS["castle_make_real_ms"] = round((time.perf_counter() - t0) * 1000)
    me = evaluated_mesh(obj)
    check(len(me.polygons) > 20_000 and mr.inputs["Resolution"].default_value == 192,
          f"To Geometry meshes it at the template's resolution ({len(me.polygons):,} faces at "
          f"{mr.inputs['Resolution'].default_value}, {NUMBERS['castle_make_real_ms']} ms)")
    st["castle_host"] = obj.name
    return True


def phase_gpu_mesh(st):
    obj, tree = host("Wave Host")
    obj.location = (-7, 0, 0)
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=60, y_segments=60, size=2.0)
    bm.to_mesh(obj.data)
    bm.free()
    area, region = node_editor(tree)
    gin = next(n for n in tree.nodes if n.type == 'GROUP_INPUT')
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    sub = tree.nodes.new("GeometryNodeSubdivideMesh")
    sub.inputs["Level"].default_value = 1
    group, err = gn_link.create('DEFORM', "Wave")
    node = gn_link.insert(tree, group, (0, 0))
    tree.links.new(gin.outputs[0], sub.inputs["Mesh"])
    tree.links.new(sub.outputs["Mesh"], node.inputs["Mesh"])
    tree.links.new(node.outputs["Geometry"], gout.inputs[0])
    settle()
    gpu_live.refresh_deform_inputs()
    src = gn_link.source_of(group)
    inp = gpu_live.deform_inputs.get(src.name)
    check(inp is not None and len(inp[0]) == 121 * 121, f"GPU Mesh receives the subdivided mesh from the tree "
          f"({len(inp[0]) if inp else 0} verts through its tap)")
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    check(not src.codenodes.last_error and "live" in node.label, "the waves draw live")
    shot("gpu_mesh_live.png")
    mr = gn_link.insert_make_real(tree, node)
    setmat = tree.nodes.new("GeometryNodeSetMaterial")
    mat = bpy.data.materials.new("Wave Paint")
    setmat.inputs["Material"].default_value = mat
    tree.links.remove(next(l for l in tree.links if l.to_node == gout))
    tree.links.new(mr.outputs["Geometry"], setmat.inputs["Geometry"])
    tree.links.new(setmat.outputs["Geometry"], gout.inputs[0])
    settle()
    live.rebuild(src)
    me = evaluated_mesh(obj)
    co = coords(me)
    attrs = {a.name for a in me.attributes}
    check(len(me.vertices) == 121 * 121 and len(me.polygons) == 120 * 120 and {"color", "value"} <= attrs
          and float(np.ptp(co[:, 2])) > 0.05 and me.materials and me.materials[0].name == mat.name,
          f"mesh → GPU Mesh → Make Real → Set Material: same topology, displaced (z range {np.ptp(co[:, 2]):.3f}), "
          f"color and value attributes, material set ({len(me.vertices)} v, {len(me.polygons)} f, "
          f"{sorted(attrs)}, {[m.name if m else None for m in me.materials]})")
    return True


def phase_render(st):
    scene = bpy.context.scene
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    scene.collection.objects.link(cam)
    cam.location, cam.rotation_euler = (0, -22, 9), (1.18, 0, 0)
    scene.camera = cam
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN'))
    scene.collection.objects.link(sun)
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 180, 100
    # the galaxy goes back to live-only (no Make Real) to test render-only realising
    tree = bpy.data.node_groups[st["tree"]]
    tree.nodes.remove(tree.nodes[st["real"]])
    node = tree.nodes[st["node"]]
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    for n in [n for n in tree.nodes if n.bl_idname in ("GeometryNodeInstanceOnPoints", "GeometryNodeMeshCube",
                                                      "GeometryNodeRealizeInstances")]:
        tree.nodes.remove(n)
    settle()                                # a live-only particle node feeds nothing in the tree
    src = gn_link.source_of(node.node_tree)
    check(src.codenodes.real_mode == "NONE", "the galaxy is live-only again")
    seen = {}

    def during(*_a):
        seen.setdefault("verts", len(src.data.vertices))
    bpy.app.handlers.render_pre.append(during)
    gpu_guard.reset_stats()
    for engine in ('BLENDER_EEVEE', 'CYCLES'):
        scene.render.engine = engine
        if engine == 'CYCLES':
            scene.cycles.samples = 4
        seen.clear()
        t0 = time.perf_counter()
        res = bpy.ops.codenodes.render('EXEC_DEFAULT', animation=False)
        NUMBERS[f"render_image_{engine}_s"] = round(time.perf_counter() - t0, 2)
        img = bpy.data.images.get("Render Result")
        check(res == {'FINISHED'} and img is not None and seen.get("verts", 0) == 20_000 and len(src.data.vertices) == 0
              and render_ops.RENDER_LINK not in node.node_tree.nodes,
              f"Render Image with CodeNodes ({engine}): the live galaxy is made real just for the render "
              f"({seen.get('verts', 0):,} points), then goes back to live ({NUMBERS[f'render_image_{engine}_s']} s)")
    scene.render.engine = 'BLENDER_EEVEE'
    scene.frame_start, scene.frame_end = 1, 3
    out = os.path.join(TMP, "anim_")
    scene.render.filepath = out
    scene.render.image_settings.file_format = 'PNG'
    t0 = time.perf_counter()
    res = bpy.ops.codenodes.render('EXEC_DEFAULT', animation=True)
    NUMBERS["render_anim_3_frames_s"] = round(time.perf_counter() - t0, 2)
    files = sorted(f for f in os.listdir(TMP) if f.startswith("anim_"))
    check(res == {'FINISHED'} and len(files) == 3, f"Render Animation with CodeNodes writes every frame ({files})")
    if SHOTS:
        import shutil
        shutil.copy(os.path.join(TMP, files[-1]), os.path.join(SHOTS, "gpu_render_frame3.png"))
    check(gpu_guard.stats()["during_render"] == 0,
          f"no GPU work ran while the renderer worked ({gpu_guard.stats()})")
    bpy.app.handlers.render_pre.remove(during)
    # Blender's own F12 with a live-only node: it simply isn't in the picture, and nothing crashes
    gpu_guard.reset_stats()
    bpy.ops.render.render(write_still=False)
    check(gpu_guard.stats()["during_render"] == 0, f"Blender's own F12 runs no GPU code while it renders "
          f"({gpu_guard.stats()})")
    menu = bpy.types.TOPBAR_MT_render._dyn_ui_initialize()
    check(menu and menu[0] is render_ops._render_menu_draw,
          "the Render menu's Render Image / Render Animation go through CodeNodes (and F12 / Ctrl+F12 too)")
    bpy.ops.wm.save_as_mainfile(filepath=SAVE)
    return True


def phase_reopen(st):
    bpy.ops.wm.open_mainfile(filepath=SAVE)
    st["t"] = time.perf_counter()
    return True


def phase_after_reopen(st):
    gn_link.sync()
    tree = bpy.data.node_groups.get(st["tree"])
    node = tree.nodes.get(st["node"]) if tree else None
    if node is None:
        raise Fail("the GPU node is missing after reopening")
    src = gn_link.source_of(node.node_tree)
    if not gpu_live.hosts and time.perf_counter() - st["t"] < 5:
        return False
    fps, _ms = measure_fps(1.0)
    check(src.name in gpu_live.hosts and fps > 1 and node.inputs["Template"].default_value == "Galaxy",
          f"after reopening, the galaxy draws live again ({fps:.0f} fps) with its settings on the node")
    return True


def phase_blank(st):
    """Open a blank saved file: that closes the splash screen, which would cover the screenshots."""
    path = os.path.join(TMP, "start.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    return True


PLAN = [phase_blank, phase_menu_and_particles, phase_live_fps, phase_template_and_frames, phase_edit_popup,
        phase_edit_popup_check, phase_make_real_particles, phase_emit_from, phase_surface, phase_gpu_mesh,
        phase_render, phase_reopen, phase_after_reopen]
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
    only = os.environ.get("CODENODES_ONLY")          # e.g. "phase_gpu_mesh": run just that (after the blank file)
    if only:
        PLAN[:] = [phase_blank] + [f for f in PLAN if f.__name__ in only.split(",")]
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.app.timers.register(driver, first_interval=1.0, persistent=True)


main()
