"""The CodeNodes demo: code nodes you write, wired together like any Geometry Nodes.

    blender --factory-startup --window-geometry 0 0 1600 960 --python examples/gpu_demo.py -- <out folder>

Needs a window (the GPU isn't available with -b). Writes codenodes_demo_v3.blend, renders and
screenshots, then quits. Two scenes:

Fireflies (night falls on the Aerie castle):
  * Plateau: a flat grid -> GPU Mesh "Mesa" (code lifts, roughens and colours it) -> To Geometry
  * Castle:  GPU Surface (the Aerie citadel), drawn live; To Geometry only for renders
  * Fireflies, one chain of nodes you can open and rewrite:
        Firefly Swarm (born on the plateau) -> Wander -> Rise -> Push by Field <- Wind Field
        -> Blink (adds brightness and phase) -> Firefly Look (glow, halo, flapping wings)
    Live in the viewport it's drawn straight from the GPU. For renders, To Geometry hands the points
    (with brightness and phase) to ordinary nodes that place a little firefly model on each, flap
    its wings from the phase and time, and light its abdomen from the brightness.

Bend (the same node on particles and on a mesh):
  * Bent Galaxy:  GPU Particles "Galaxy" -> Bend (curled and twisted along X), live
  * Bent Column:  a Geometry Nodes cylinder -> Bend -> To Geometry
"""
import math
import os
import sys
import traceback

import bmesh
import bpy
from mathutils import Euler, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gpu_live, live  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(args[0] if args else os.path.join(ROOT, "examples", "gpu_demo_out"))
os.makedirs(OUT, exist_ok=True)
BLEND = os.path.join(OUT, "codenodes_demo_v3.blend")
print("CodeNodes demo: building (this window closes by itself)", flush=True)
FIREFLY_SIZE = 0.03


# ---- helpers --------------------------------------------------------------------------------------------

def gn_object(name, mesh=None, scene=None):
    scene = scene or bpy.context.scene
    obj = bpy.data.objects.new(name, mesh or bpy.data.meshes.new(name))
    scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(f"{name} Nodes", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-900, 0), (1500, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree, gin, gout


def code_node(tree, kind, key, x, y=0):
    group, err = gn_link.create(kind, key)
    if err:
        raise RuntimeError(f"{key}: {err}")
    node = gn_link.insert(tree, group, (x, y))
    node.width = 200
    return node


def link_out(tree, sock, gout):
    for l in [l for l in tree.links if l.to_node == gout]:
        tree.links.remove(l)
    tree.links.new(sock, gout.inputs[0])


def material(name, color=(0.8, 0.8, 0.8), rough=0.85, color_attr=None, emission=0.0, emission_attr=None,
             alpha=1.0):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    if color_attr:
        a = nt.nodes.new("ShaderNodeAttribute")
        a.attribute_name = color_attr
        nt.links.new(a.outputs["Color"], bsdf.inputs["Base Color"])
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
        if emission_attr:                  # per-instance brightness (from To Geometry's points)
            a = nt.nodes.new("ShaderNodeAttribute")
            a.attribute_type = 'INSTANCER'
            a.attribute_name = emission_attr
            mul = nt.nodes.new("ShaderNodeMath")
            mul.operation = 'MULTIPLY_ADD'
            mul.inputs[1].default_value = emission
            mul.inputs[2].default_value = emission * 0.06
            nt.links.new(a.outputs["Fac"], mul.inputs[0])
            nt.links.new(mul.outputs[0], bsdf.inputs["Emission Strength"])
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        try:
            m.surface_render_method = 'BLENDED'
        except (AttributeError, TypeError):
            pass
    return m


def ellipsoid(name, scale, offset=(0.0, 0.0, 0.0), segments=16, rings=8):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=0.5)
    for v in bm.verts:
        v.co = Vector((v.co.x * scale[0] + offset[0], v.co.y * scale[1] + offset[1], v.co.z * scale[2] + offset[2]))
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    return me


def wing_mesh(name):
    """One wing: a flat, slightly swept ellipse in the XY plane from the body (y=0) out to y=1.6."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    n = 24
    centre = bm.verts.new((-0.15, 0.8, 0.0))
    ring = [bm.verts.new((-0.15 + 0.42 * math.cos(2 * math.pi * i / n) - 0.12 * math.sin(math.pi * i / n),
                          0.8 + 0.8 * math.sin(2 * math.pi * i / n), 0.0)) for i in range(n)]
    for i in range(n):
        bm.faces.new((centre, ring[i], ring[(i + 1) % n]))
    bm.to_mesh(me)
    bm.free()
    return me


def firefly_parts():
    """A tiny firefly (body along +X, 1 unit long) and one wing, in a collection kept out of the view
    layer: To Geometry's points place them."""
    col = bpy.data.collections.new("Firefly Model")
    bpy.context.scene.collection.children.link(col)
    dark = material("Firefly Body", color=(0.06, 0.045, 0.035), rough=0.4)
    glow = material("Firefly Glow", color=(1.0, 0.78, 0.22), rough=0.3, emission=60.0, emission_attr="brightness")
    wing = material("Firefly Wing", color=(0.85, 0.9, 0.95), rough=0.2, alpha=0.35)
    body_me = ellipsoid("Firefly Body", (0.55, 0.28, 0.24), (0.18, 0.0, 0.0))
    head = ellipsoid("Firefly Head", (0.18, 0.2, 0.18), (0.5, 0.0, 0.02))
    abdo = ellipsoid("Firefly Abdomen", (0.5, 0.3, 0.26), (-0.28, 0.0, -0.02))
    parts = []
    for me, mat in ((body_me, dark), (head, dark), (abdo, glow)):
        me.materials.append(mat)
        o = bpy.data.objects.new(me.name, me)
        col.objects.link(o)
        parts.append(o)
    # join the three into one body object
    body = parts[0]
    bm = bmesh.new()
    for o in parts:
        tmp = bmesh.new()
        tmp.from_mesh(o.data)
        idx = list(body.data.materials).index(o.data.materials[0]) if o.data.materials[0].name in \
            [m.name for m in body.data.materials] else None
        if idx is None:
            body.data.materials.append(o.data.materials[0])
            idx = len(body.data.materials) - 1
        for f in tmp.faces:
            f.material_index = idx
        me2 = bpy.data.meshes.new("tmp")
        tmp.to_mesh(me2)
        tmp.free()
        bm.from_mesh(me2)
        bpy.data.meshes.remove(me2)
    joined = bpy.data.meshes.new("Firefly")
    bm.to_mesh(joined)
    bm.free()
    for m in body.data.materials:
        joined.materials.append(m)
    for p in joined.polygons:
        p.use_smooth = True
    for o in parts:
        bpy.data.objects.remove(o)
    fly = bpy.data.objects.new("Firefly", joined)
    col.objects.link(fly)
    wme = wing_mesh("Firefly Wing")
    wme.materials.append(wing)
    w = bpy.data.objects.new("Firefly Wing", wme)
    col.objects.link(w)
    for vl in bpy.context.scene.view_layers:
        lc = gn_link._find_layer_collection(vl.layer_collection, col.name)
        if lc is not None:
            lc.exclude = True
    return fly, w


def flap_rotation(tree, x, y, phase_from, sign):
    """Rotation per point: face along the velocity, then flap the wing about the body's axis."""
    n = tree.nodes
    vel = n.new("GeometryNodeInputNamedAttribute")
    vel.data_type = 'FLOAT_VECTOR'
    vel.inputs["Name"].default_value = "velocity"
    align = n.new("FunctionNodeAlignRotationToVector")
    align.axis = 'X'
    ph = n.new("GeometryNodeInputNamedAttribute")
    ph.data_type = 'FLOAT'
    ph.inputs["Name"].default_value = phase_from
    t = n.new("GeometryNodeInputSceneTime")
    m1 = n.new("ShaderNodeMath")
    m1.operation = 'MULTIPLY_ADD'
    m1.inputs[1].default_value = 18.0              # flaps per second, as the live look
    s1 = n.new("ShaderNodeMath")
    s1.operation = 'MULTIPLY'
    s1.inputs[1].default_value = 2 * math.pi
    sn = n.new("ShaderNodeMath")
    sn.operation = 'SINE'
    amp = n.new("ShaderNodeMath")
    amp.operation = 'MULTIPLY_ADD'
    amp.inputs[1].default_value = 0.9
    amp.inputs[2].default_value = 0.25
    ang = amp
    if sign < 0:                                    # the other wing: mirrored across the body
        mir = n.new("ShaderNodeMath")
        mir.operation = 'SUBTRACT'
        mir.inputs[0].default_value = math.pi
        tree.links.new(amp.outputs[0], mir.inputs[1])
        ang = mir
    axis_angle = n.new("FunctionNodeAxisAngleToRotation")
    axis_angle.inputs["Axis"].default_value = (1.0, 0.0, 0.0)
    rot = n.new("FunctionNodeRotateRotation")
    rot.rotation_space = 'LOCAL'
    L = tree.links
    L.new(vel.outputs["Attribute"], align.inputs["Vector"])
    L.new(t.outputs["Seconds"], m1.inputs[0])
    L.new(ph.outputs["Attribute"], m1.inputs[2])
    L.new(m1.outputs[0], s1.inputs[0])
    L.new(s1.outputs[0], sn.inputs[0])
    L.new(sn.outputs[0], amp.inputs[0])
    L.new(ang.outputs[0], axis_angle.inputs["Angle"])
    L.new(align.outputs["Rotation"], rot.inputs["Rotation"])
    L.new(axis_angle.outputs["Rotation"], rot.inputs["Rotate By"])
    for i, node in enumerate((vel, ph, t, m1, s1, sn, amp, axis_angle, align, rot)):
        node.location = (x + (i % 4) * 170, y - (i // 4) * 170)
        node.hide = True
    return rot, align


def compositor_bloom(scene):
    """Glare on the render: the fireflies' emission blooms like the live halo."""
    try:
        ng = bpy.data.node_groups.new("CodeNodes Bloom", "CompositorNodeTree")
        ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = ng.nodes.new("CompositorNodeRLayers")
        gl = ng.nodes.new("CompositorNodeGlare")
        go = ng.nodes.new("NodeGroupOutput")
        for name, value in (("Type", "Bloom"), ("Quality", "High"), ("Threshold", 0.8), ("Strength", 0.9),
                            ("Size", 0.6)):
            sock = gl.inputs.get(name)
            if sock is not None:
                try:
                    sock.default_value = value
                except (TypeError, ValueError):
                    if name == "Type":
                        try:
                            sock.default_value = "Fog Glow"
                        except (TypeError, ValueError):
                            pass
        ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
        ng.links.new(gl.outputs["Image"], go.inputs[0])
        rl.location, gl.location, go.location = (-300, 0), (0, 0), (300, 0)
        scene.compositing_node_group = ng
        scene.render.use_compositing = True
    except Exception:
        traceback.print_exc()


def night(scene):
    world = bpy.data.worlds.new(f"{scene.name} Night")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.012, 0.018, 0.04, 1.0)
        bg.inputs["Strength"].default_value = 1.0
    moon = bpy.data.objects.new("Moonlight", bpy.data.lights.new("Moonlight", 'SUN'))
    moon.data.energy = 0.35
    moon.data.color = (0.62, 0.72, 1.0)
    moon.rotation_euler = (math.radians(55), 0.0, math.radians(-40))
    scene.collection.objects.link(moon)
    dusk = bpy.data.objects.new("Last Light", bpy.data.lights.new("Last Light", 'SUN'))
    dusk.data.energy = 0.25
    dusk.data.color = (1.0, 0.55, 0.3)
    dusk.rotation_euler = (math.radians(84), 0.0, math.radians(150))
    scene.collection.objects.link(dusk)


def camera(scene, name, loc, target, lens=42):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    scene.collection.objects.link(cam)
    cam.location = loc
    cam.rotation_euler = (Vector(target) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.lens = lens
    return cam


# ---- the fireflies scene ------------------------------------------------------------------------------------

def build_fireflies(scene):
    scene.name = "Fireflies"
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    # plateau: grid -> Mesa -> To Geometry -> material
    grid = bpy.data.meshes.new("Plateau")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=220, y_segments=220, size=6.0)
    bm.to_mesh(grid)
    bm.free()
    plateau, ptree, gin, gout = gn_object("Plateau", grid)
    pnode = code_node(ptree, 'DEFORM', "Mesa", -500)
    ptree.links.new(gin.outputs[0], pnode.inputs["Mesh"])
    link_out(ptree, pnode.outputs["Geometry"], gout)
    preal = gn_link.insert_make_real(ptree, pnode)
    setmat = ptree.nodes.new("GeometryNodeSetMaterial")
    setmat.inputs["Material"].default_value = material("Plateau Rock", color_attr="color")
    smooth = ptree.nodes.new("GeometryNodeSetShadeSmooth")
    ptree.links.new(preal.outputs["Geometry"], setmat.inputs["Geometry"])
    ptree.links.new(setmat.outputs["Geometry"], smooth.inputs["Geometry"])
    link_out(ptree, smooth.outputs["Geometry"], gout)
    preal.location, setmat.location, smooth.location = (-200, 0), (100, 0), (400, 0)

    # castle: GPU Surface, live; To Geometry only for renders
    castle, ctree, _g, cgout = gn_object("Castle")
    castle.location = (0.0, 0.0, 1.1)
    cnode = code_node(ctree, 'MESH', "Castle", -400)
    link_out(ctree, cnode.outputs["Geometry"], cgout)
    creal = gn_link.insert_make_real(ctree, cnode)
    cset = ctree.nodes.new("GeometryNodeSetMaterial")
    cset.inputs["Material"].default_value = material("Castle Stone", color=(0.78, 0.72, 0.64))
    ctree.links.new(creal.outputs["Geometry"], cset.inputs["Geometry"])
    link_out(ctree, cset.outputs["Geometry"], cgout)
    creal.location, cset.location = (-100, 0), (200, 0)

    # fireflies: the chain
    flies, ftree, _g, fgout = gn_object("Fireflies")
    keys = [('PARTICLES', "Firefly Swarm"), ('STAGE', "Wander"), ('STAGE', "Rise"), ('STAGE', "Push by Field"),
            ('STAGE', "Blink"), ('STAGE', "Firefly Look")]
    chain = [code_node(ftree, k, key, -1500 + i * 240, 260) for i, (k, key) in enumerate(keys)]
    for a, b in zip(chain, chain[1:]):
        ftree.links.new(a.outputs["Particles"], b.inputs["Particles"])
    wind = code_node(ftree, 'STAGE', "Wind Field", -1020, -120)
    ftree.links.new(wind.outputs["wind"], chain[3].inputs["field"])
    look = chain[-1]
    freal = gn_link.insert_make_real(ftree, look)
    freal.location = (-1500 + 6 * 240 + 20, 260)

    # render side: a firefly on each point, wings flapping from its phase, abdomen lit by brightness
    fly, wing = firefly_parts()
    n = ftree.nodes
    info_body = n.new("GeometryNodeObjectInfo")
    info_body.inputs["Object"].default_value = fly
    info_wing = n.new("GeometryNodeObjectInfo")
    info_wing.inputs["Object"].default_value = wing
    for inf in (info_body, info_wing):
        inf.transform_space = 'ORIGINAL'
    body_vel = n.new("GeometryNodeInputNamedAttribute")
    body_vel.data_type = 'FLOAT_VECTOR'
    body_vel.inputs["Name"].default_value = "velocity"
    body_align = n.new("FunctionNodeAlignRotationToVector")
    body_align.axis = 'X'
    ftree.links.new(body_vel.outputs["Attribute"], body_align.inputs["Vector"])
    body_vel.location, body_align.location = (100, -150), (300, -150)
    lw_rot, _ = flap_rotation(ftree, -300, -800, "phase", 1)
    rw_rot, _ = flap_rotation(ftree, -300, -1100, "phase", -1)
    inst_body = n.new("GeometryNodeInstanceOnPoints")
    inst_lw = n.new("GeometryNodeInstanceOnPoints")
    inst_rw = n.new("GeometryNodeInstanceOnPoints")
    for inst, info, rot in ((inst_body, info_body, None), (inst_lw, info_wing, lw_rot), (inst_rw, info_wing, rw_rot)):
        ftree.links.new(freal.outputs["Geometry"], inst.inputs["Points"])
        ftree.links.new(info.outputs["Geometry"], inst.inputs["Instance"])
        inst.inputs["Scale"].default_value = (FIREFLY_SIZE,) * 3
        if rot is not None:
            ftree.links.new(rot.outputs["Rotation"], inst.inputs["Rotation"])
    # the body just faces along its velocity (no flap)
    ftree.links.new(body_align.outputs["Rotation"], inst_body.inputs["Rotation"])
    join = n.new("GeometryNodeJoinGeometry")
    for inst in (inst_body, inst_lw, inst_rw):
        ftree.links.new(inst.outputs["Instances"], join.inputs[0])
    link_out(ftree, join.outputs["Geometry"], fgout)
    info_body.location, info_wing.location = (400, -300), (400, -600)
    inst_body.location, inst_lw.location, inst_rw.location, join.location = (700, 0), (700, -400), (700, -800), (1100, 0)
    fgout.location = (1350, 0)
    live_frame = n.new("NodeFrame")
    live_frame.label = "Fireflies: a chain of code nodes, drawn live (edit any node's code)"
    live_frame.label_size = 20
    for nd in chain + [wind, freal]:
        nd.parent = live_frame
    for nd in list(n):                             # the render side sits right of the live chain
        if nd.type not in ('FRAME', 'GROUP_INPUT', 'GROUP_OUTPUT') and nd not in chain and nd not in (wind, freal):
            nd.location.x += 650
    fgout.location.x += 650
    render_frame = n.new("NodeFrame")
    render_frame.label = "For renders: a little firefly on each point, wings flapping from its phase"
    render_frame.label_size = 20
    for nd in list(n):
        if nd.type not in ('FRAME', 'GROUP_INPUT', 'GROUP_OUTPUT') and nd.parent is None and nd not in chain \
                and nd not in (wind, freal):
            nd.parent = render_frame

    gn_link.sync()
    creal.inputs["When"].default_value = "Only for Render"
    freal.inputs["When"].default_value = "Only for Render"    # live glow and wings in the viewport
    freal.inputs["Max Points"].default_value = 1500
    chain[0].inputs["Count"].default_value = 1500
    chain[0].inputs["Emit From"].default_value = plateau
    chain[0].inputs["Pre-warm"].default_value = 4.0
    chain[0].inputs["height"].default_value = 0.6
    chain[1].inputs["strength"].default_value = 0.35
    chain[2].inputs["climb"].default_value = 0.1
    chain[2].inputs["ceiling"].default_value = 3.2
    look.inputs["size"].default_value = FIREFLY_SIZE
    look.inputs["intensity"].default_value = 3.0
    wind.inputs["strength"].default_value = 0.15
    for _ in range(5):
        gn_link.sync()
        live._flush()
    gpu_live.refresh_deform_inputs()
    live.rebuild(gn_link.source_of(pnode.node_tree))
    gpu_live.mark_emitters_dirty()
    gpu_live.refresh_emitters()
    live.rebuild(gn_link.source_of(chain[0].node_tree))

    night(scene)
    compositor_bloom(scene)
    wide = camera(scene, "Camera", (7.2, -7.8, 4.6), (0.0, 0.0, 1.6))
    close = camera(scene, "Close-up", (3.3, -3.6, 0.42), (0.2, 0.1, 1.2), lens=45)
    close.data.dof.use_dof = True
    close.data.dof.focus_distance = 1.2
    close.data.dof.aperture_fstop = 2.8
    scene.camera = wide
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.frame_start, scene.frame_end = 1, 120
    scene.frame_set(60)
    return {"flies": flies, "castle": castle, "plateau": plateau, "tree": ftree.name}


# ---- the bend scene --------------------------------------------------------------------------------------------

def build_bend():
    scene = bpy.data.scenes.new("Bend")
    win = bpy.context.window_manager.windows[0]
    win.scene = scene                     # new code nodes put their helpers in the current scene
    galaxy, gtree, _g, ggout = gn_object("Bent Galaxy", scene=scene)
    gal = code_node(gtree, 'PARTICLES', "Galaxy", -500)
    bend = code_node(gtree, 'STAGE', "Bend", -200)
    gtree.links.new(gal.outputs["Particles"], bend.inputs["Particles"])
    col, ctree, cgin, cgout = gn_object("Bent Column", scene=scene)
    col.location = (-4.5, 0.0, 0.0)
    cyl = ctree.nodes.new("GeometryNodeMeshCylinder")
    cyl.inputs["Vertices"].default_value = 48
    cyl.inputs["Side Segments"].default_value = 80
    cyl.inputs["Radius"].default_value = 0.28
    cyl.inputs["Depth"].default_value = 3.2
    xf = ctree.nodes.new("GeometryNodeTransform")
    xf.inputs["Translation"].default_value = (0.0, 0.0, 1.6)
    ctree.links.new(cyl.outputs["Mesh"], xf.inputs["Geometry"])
    cbend = code_node(ctree, 'STAGE', "Bend", -200)
    ctree.links.new(xf.outputs["Geometry"], cbend.inputs["Mesh"])
    creal = gn_link.insert_make_real(ctree, cbend)
    cmat = ctree.nodes.new("GeometryNodeSetMaterial")
    cmat.inputs["Material"].default_value = material("Column", color=(0.55, 0.6, 0.72), rough=0.5)
    ctree.links.new(creal.outputs["Geometry"], cmat.inputs["Geometry"])
    link_out(ctree, cmat.outputs["Geometry"], cgout)
    cyl.location, xf.location, creal.location, cmat.location = (-800, 0), (-550, 0), (100, 0), (400, 0)
    gn_link.sync()
    gal.inputs["Count"].default_value = 600_000
    gal.inputs["size"].default_value = 1.2
    bend.inputs["axis"].default_value = 0
    bend.inputs["angle"].default_value = 1.6
    bend.inputs["span"].default_value = 2.5
    bend.inputs["stretch"].default_value = 1.4
    bend.inputs["twist"].default_value = 0.6
    cbend.inputs["angle"].default_value = 1.4
    cbend.inputs["span"].default_value = 3.2
    cbend.inputs["twist"].default_value = 1.0
    for _ in range(5):
        gn_link.sync()
        live._flush()
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN'))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), 0.0, math.radians(30))
    scene.collection.objects.link(sun)
    world = bpy.data.worlds.new("Bend Sky")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.03, 0.035, 0.05, 1.0)
    scene.camera = camera(scene, "Bend Camera", (-1.5, -11.0, 3.2), (-1.8, 0.0, 1.2), lens=35)
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.frame_start, scene.frame_end = 1, 120
    scene.frame_set(40)
    return scene


# ---- layout ----------------------------------------------------------------------------------------------------

def arrange(win, focus_obj, view):
    """Geometry Nodes workspace, without the Spreadsheet: the 3D view on top, the node graph below."""
    ws = bpy.data.workspaces.get("Geometry Nodes")
    if ws is not None:
        win.workspace = ws
    screen = win.screen
    for area in list(screen.areas):
        if area.type == 'SPREADSHEET':
            try:
                with bpy.context.temp_override(window=win, screen=screen, area=area):
                    bpy.ops.screen.area_close()
            except Exception:
                traceback.print_exc()
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            sp = area.spaces.active
            sp.shading.type = 'SOLID'
            sp.shading.color_type = 'VERTEX'
            sp.shading.light = 'STUDIO'
            sp.shading.background_type = 'VIEWPORT'
            sp.shading.background_color = (0.02, 0.028, 0.05)
            sp.overlay.show_floor = False
            sp.overlay.show_axis_x = sp.overlay.show_axis_y = False
            r3d = sp.region_3d
            r3d.view_perspective = 'PERSP'
            r3d.view_location, r3d.view_distance, rot = view
            r3d.view_rotation = Euler(rot).to_quaternion()
        if area.type == 'NODE_EDITOR':
            area.spaces.active.tree_type = 'GeometryNodeTree'
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    focus_obj.select_set(True)
    bpy.context.view_layer.objects.active = focus_obj


def fit_nodes(win):
    for area in win.screen.areas:
        if area.type == 'NODE_EDITOR':
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.node.view_all()


def screenshot(win, name, area_type=None):
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=3)
    if area_type is None:
        with bpy.context.temp_override(window=win):
            bpy.ops.screen.screenshot(filepath=os.path.join(OUT, name))
        return
    for area in win.screen.areas:
        if area.type == area_type:
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, name))
            return


def render(scene, name, cam=None):
    if cam is not None:
        scene.camera = bpy.data.objects[cam]
    scene.render.filepath = os.path.join(OUT, name)
    bpy.ops.codenodes.render('EXEC_DEFAULT', animation=False)
    img = bpy.data.images.get("Render Result")
    if img is not None:
        img.save_render(os.path.join(OUT, name), scene=scene)


FF_VIEW = (Vector((0.0, 0.0, 1.4)), 10.5, (math.radians(66), 0, math.radians(38)))
FF_CLOSE = (Vector((2.2, -2.6, 0.6)), 1.4, (math.radians(80), 0, math.radians(40)))
BEND_VIEW = (Vector((-2.0, 0.0, 1.2)), 9.5, (math.radians(72), 0, math.radians(8)))
STEP = {"n": 0}


def tick():
    try:
        STEP["n"] += 1
        n = STEP["n"]
        win = bpy.context.window_manager.windows[0]
        if n == 1:
            path = os.path.join(OUT, "start.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)        # closes the splash screen
            return 0.8
        if n == 2:
            STEP["ff"] = build_fireflies(bpy.context.scene)
            STEP["ff_scene"] = bpy.context.scene.name
            STEP["bend"] = build_bend().name
            win.scene = bpy.data.scenes[STEP["ff_scene"]]
            return 1.0
        if n == 3:
            ws = bpy.data.workspaces.get("Geometry Nodes")
            if ws is not None:
                win.workspace = ws
            return 1.0
        if n == 4:
            arrange(win, STEP["ff"]["flies"], FF_CLOSE)
            return 1.5
        if n == 5:
            screenshot(win, "fireflies_live_close.png", 'VIEW_3D')
            arrange(win, STEP["ff"]["flies"], FF_VIEW)
            return 1.0
        if n == 6:
            fit_nodes(win)
            screenshot(win, "demo_workspace.png")
            screenshot(win, "fireflies_live.png", 'VIEW_3D')
            win.scene = bpy.data.scenes[STEP["bend"]]
            return 1.0
        if n == 7:
            arrange(win, bpy.data.objects["Bent Galaxy"], BEND_VIEW)
            return 1.5
        if n == 8:
            fit_nodes(win)
            screenshot(win, "bend_live.png", 'VIEW_3D')
            screenshot(win, "bend_workspace.png")
            render(bpy.context.scene, "bend_render.png")
            win.scene = bpy.data.scenes[STEP["ff_scene"]]
            return 1.0
        if n == 9:
            arrange(win, STEP["ff"]["flies"], FF_VIEW)
            fit_nodes(win)
            bpy.ops.wm.save_as_mainfile(filepath=BLEND, relative_remap=True)
            return 0.5
        if n == 10:
            scene = bpy.context.scene
            render(scene, "fireflies_render_close.png", "Close-up")
            render(scene, "fireflies_render.png", "Camera")
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
