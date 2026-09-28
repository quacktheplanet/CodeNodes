"""The GPU nodes demo: a raymarched castle on a GPU-shaped plateau, with fireflies spawned from the
plateau's surface. Builds a .blend that opens straight into the Geometry Nodes workspace.

    blender --factory-startup --python examples/gpu_demo.py -- <out folder>

Needs a window (the GPU isn't available with -b). Writes codenodes_gpu_demo.blend, a render made
with Render › Render Image with CodeNodes, and viewport screenshots, then quits.

What's in it, all Geometry Nodes with code nodes inside:
  * Plateau: a flat grid → GPU Mesh (code lifts and roughens it, colours it) → Make Real → Set Material
  * Castle: GPU Surface (the Aerie citadel), drawn live; Make Real set to Only for Render
  * Fireflies: GPU Particles emitted from the Plateau (Emit From) → Make Real → Instance on Points
"""
import math
import os
import sys
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gn_sockets, gpu_live, live  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(args[0] if args else os.path.join(ROOT, "examples", "gpu_demo_out"))
os.makedirs(OUT, exist_ok=True)
print("CodeNodes demo: building (this window closes by itself)", flush=True)

PLATEAU = """\
// A rocky plateau rising out of a flat grid: a soft-edged mesa, roughened by noise and coloured by height.
// @param height 1.1 0.0 4.0
// @param radius 2.4 0.5 6.0
// @param rough 0.22 0.0 1.0
void deform(inout Vertex v) {
  vec3 p = v.position;
  float r = length(p.xy) + (fbm3(p * 1.1 + 3.0) - 0.5) * 0.9;
  float mesa = 1.0 - smoothstep(radius * 0.8, radius * 1.1, r);
  float cliff = mesa * height + (fbm3(p * 3.2) - 0.5) * rough * (0.4 + mesa);
  float ground = (fbm3(p * 0.6 + 7.0) - 0.5) * 0.25;
  float top = smoothstep(radius * 0.35, radius * 0.1, length(p.xy));    // flatten the summit for the castle
  v.position.z += mix(cliff, height, top * mesa) + ground;
  v.value = v.position.z;
  float t = clamp(v.position.z / max(height, 1e-3), 0.0, 1.0);
  vec3 rock = mix(vec3(0.30, 0.27, 0.23), vec3(0.66, 0.60, 0.50), t);
  v.color = vec4(mix(vec3(0.20, 0.30, 0.14), rock, smoothstep(0.05, 0.3, t)), 1.0);
}
"""

FIREFLIES = """\
// Fireflies: born on the Emit From surface, they drift up and wander on a gentle curl-noise breeze.
// @param rise 0.35 0.0 2.0
// @param wander 0.6 0.0 3.0
void spawn(inout Particle p) {
  p.position = emitPoint(p.seed) + emitNormal(p.seed) * 0.05;
  p.life = 4.0 + rand1(p.seed * 1.7) * 4.0;
}
void update(inout Particle p, float dt) {
  p.velocity = vec3(0.0, 0.0, rise) + curlNoise(p.position * 0.8 + vec3(0.0, 0.0, uTime * 0.1)) * wander;
  p.position += p.velocity * dt;
}
vec4 look(Particle p) {
  float fade = smoothstep(0.0, 0.5, p.age) * smoothstep(0.0, 1.0, p.life - p.age);
  return vec4(vec3(1.0, 0.75, 0.3) * fade, 1.0);
}
"""


def gn_object(name, mesh=None):
    obj = bpy.data.objects.new(name, mesh or bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(f"{name} Nodes", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-700, 0), (900, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree, gin, gout


def material(name, color_attr=None, color=(0.8, 0.8, 0.8), emission=0.0):
    m = bpy.data.materials.new(name)
    nt = m.node_tree if m.node_tree is not None else None
    if nt is None:
        m.use_nodes = True
        nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.85
    if color_attr:
        attr = nt.nodes.new("ShaderNodeAttribute")
        attr.attribute_name = color_attr
        nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    return m


def build():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    # --- the plateau: grid → GPU Mesh → Make Real → Set Material
    grid = bpy.data.meshes.new("Plateau")
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=220, y_segments=220, size=6.0)
    bm.to_mesh(grid)
    bm.free()
    plateau, ptree, gin, gout = gn_object("Plateau", grid)
    group, err = gn_link.create('DEFORM', None, label="Plateau", source=PLATEAU)
    pnode = gn_link.insert(ptree, group, (-350, 0))
    ptree.links.new(gin.outputs[0], pnode.inputs["Mesh"])
    ptree.links.new(pnode.outputs["Geometry"], gout.inputs[0])
    preal = gn_link.insert_make_real(ptree, pnode)
    setmat = ptree.nodes.new("GeometryNodeSetMaterial")
    setmat.inputs["Material"].default_value = material("Plateau Rock", color_attr="color")
    ptree.links.remove(next(l for l in ptree.links if l.to_node == gout))
    ptree.links.new(preal.outputs["Geometry"], setmat.inputs["Geometry"])
    ptree.links.new(setmat.outputs["Geometry"], gout.inputs[0])
    smooth = ptree.nodes.new("GeometryNodeSetShadeSmooth")
    ptree.links.remove(next(l for l in ptree.links if l.to_node == gout))
    ptree.links.new(setmat.outputs["Geometry"], smooth.inputs["Geometry"])
    ptree.links.new(smooth.outputs["Geometry"], gout.inputs[0])
    pnode.location, preal.location, setmat.location, smooth.location = (-350, 0), (-50, 0), (250, 0), (550, 0)

    # --- the castle on top: GPU Surface, live in the viewport, made real only for renders
    castle, ctree, _cgin, cgout = gn_object("Castle")
    castle.location = (0.0, 0.0, 1.1)
    group, err = gn_link.create('MESH', "Castle")
    cnode = gn_link.insert(ctree, group, (-300, 0))
    ctree.links.new(cnode.outputs["Geometry"], cgout.inputs[0])
    creal = gn_link.insert_make_real(ctree, cnode)
    csrc = gn_link.source_of(group)
    csrc.codenodes.surface_color = (0.8, 0.75, 0.66)
    cset = ctree.nodes.new("GeometryNodeSetMaterial")
    cset.inputs["Material"].default_value = material("Castle Stone", color=(0.78, 0.72, 0.64))
    ctree.links.remove(next(l for l in ctree.links if l.to_node == cgout))
    ctree.links.new(creal.outputs["Geometry"], cset.inputs["Geometry"])
    ctree.links.new(cset.outputs["Geometry"], cgout.inputs[0])
    creal.location, cset.location = (0, 0), (300, 0)

    # --- fireflies: GPU Particles from the plateau → Make Real → Instance on Points
    flies, ftree, _fgin, fgout = gn_object("Fireflies")
    group, err = gn_link.create('PARTICLES', None, label="Fireflies", source=FIREFLIES)
    fnode = gn_link.insert(ftree, group, (-450, 0))
    fsrc = gn_link.source_of(group)
    ftree.links.new(fnode.outputs["Geometry"], fgout.inputs[0])
    freal = gn_link.insert_make_real(ftree, fnode)
    inst = ftree.nodes.new("GeometryNodeInstanceOnPoints")
    ico = ftree.nodes.new("GeometryNodeMeshIcoSphere")
    ico.inputs["Radius"].default_value = 0.011
    ico.inputs["Subdivisions"].default_value = 1
    fmat = ftree.nodes.new("GeometryNodeSetMaterial")
    fmat.inputs["Material"].default_value = material("Firefly Glow", color=(1.0, 0.42, 0.08), emission=4.0)
    ftree.links.remove(next(l for l in ftree.links if l.to_node == fgout))
    ftree.links.new(freal.outputs["Geometry"], inst.inputs["Points"])
    ftree.links.new(ico.outputs["Mesh"], inst.inputs["Instance"])
    ftree.links.new(inst.outputs["Instances"], fmat.inputs["Geometry"])
    ftree.links.new(fmat.outputs["Geometry"], fgout.inputs[0])
    fnode.location, freal.location, ico.location, inst.location, fmat.location = \
        (-450, 0), (-150, 0), (-150, -250), (150, 0), (450, 0)

    gn_link.sync()
    # settings on the nodes themselves
    creal.inputs["When"].default_value = "Only for Render"
    freal.inputs["Max Points"].default_value = 2500
    fnode.inputs["Count"].default_value = 2500
    fnode.inputs["Emit From"].default_value = plateau
    fnode.inputs["Pre-warm"].default_value = 4.0
    fnode.inputs["Colour By"].default_value = "Code"
    for _ in range(4):
        gn_link.sync()
        live._flush()
    gpu_live.refresh_deform_inputs()
    live.rebuild(gn_link.source_of(pnode.node_tree))
    gpu_live.mark_emitters_dirty()
    gpu_live.refresh_emitters()
    live.rebuild(fsrc)

    # camera, light, world, view
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    cam.location = (7.2, -7.8, 4.6)
    from mathutils import Vector
    cam.rotation_euler = (Vector((0.0, 0.0, 1.6)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.lens = 42
    scene.camera = cam
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN'))
    sun.data.energy = 3.0
    sun.data.color = (1.0, 0.86, 0.7)
    sun.rotation_euler = (math.radians(58), 0.0, math.radians(-35))
    scene.collection.objects.link(sun)
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.color = (0.18, 0.22, 0.32)
    if world.node_tree is not None and "Background" in world.node_tree.nodes:
        world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.18, 0.22, 0.32, 1.0)
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.frame_start, scene.frame_end = 1, 120
    scene.frame_set(60)
    return plateau, castle, flies


def arrange_workspace():
    ws = bpy.data.workspaces.get("Geometry Nodes")
    win = bpy.context.window_manager.windows[0]
    if ws is not None:
        win.workspace = ws
    return win


def views(win):
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            sp = area.spaces.active
            sp.shading.type = 'SOLID'
            sp.shading.color_type = 'VERTEX'          # the plateau's GPU-made colour attribute
            sp.shading.light = 'STUDIO'
            sp.overlay.show_floor = False
            sp.overlay.show_axis_x = sp.overlay.show_axis_y = False
            r3d = sp.region_3d
            r3d.view_perspective = 'PERSP'
            from mathutils import Euler, Vector
            r3d.view_location = Vector((0.0, 0.0, 1.4))
            r3d.view_distance = 11.0
            r3d.view_rotation = Euler((math.radians(66), 0, math.radians(38))).to_quaternion()
        if area.type == 'NODE_EDITOR':
            sp = area.spaces.active
            sp.tree_type = 'GeometryNodeTree'


STEP = {"n": 0}


def tick():
    try:
        STEP["n"] += 1
        n = STEP["n"]
        if n == 1:
            path = os.path.join(OUT, "start.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)        # closes the splash screen
            return 0.8
        if n == 2:
            STEP["objs"] = build()
            win = arrange_workspace()
            STEP["win"] = win
            return 1.0
        if n == 3:
            win = bpy.context.window_manager.windows[0]
            views(win)
            plateau, castle, flies = STEP["objs"]
            for o in bpy.context.view_layer.objects:
                o.select_set(False)
            flies.select_set(True)
            bpy.context.view_layer.objects.active = flies
            return 1.5
        if n == 4:
            win = bpy.context.window_manager.windows[0]
            for area in win.screen.areas:
                if area.type in ('VIEW_3D', 'NODE_EDITOR'):
                    with bpy.context.temp_override(window=win, area=area):
                        if area.type == 'NODE_EDITOR':
                            region = next(r for r in area.regions if r.type == 'WINDOW')
                            with bpy.context.temp_override(window=win, area=area, region=region):
                                bpy.ops.node.view_all()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=3)
            with bpy.context.temp_override(window=win):
                bpy.ops.screen.screenshot(filepath=os.path.join(OUT, "demo_workspace.png"))
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    with bpy.context.temp_override(window=win, area=area):
                        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "demo_viewport.png"))
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "codenodes_gpu_demo.blend"))
            return 0.5
        if n == 5:
            scene = bpy.context.scene
            scene.render.filepath = os.path.join(OUT, "demo_render.png")
            bpy.ops.codenodes.render('EXEC_DEFAULT', animation=False)
            img = bpy.data.images.get("Render Result")
            if img is not None:
                img.save_render(os.path.join(OUT, "demo_render.png"), scene=scene)
            print("DEMO DONE", OUT, flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception:
        traceback.print_exc()
        print("DEMO FAILED", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return None


codenodes.register()
bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
