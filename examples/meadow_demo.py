"""The Windy Meadow: the modular workflow in one scene (use lines, lists, Explode).

    blender -b --factory-startup --python examples/meadow_demo.py -- <out folder>    (Blender 5.2+)

Writes meadow.blend and renders meadow.png (frame 72) with a plain command-line render, so it runs
with no window. What's in it:

  * One Wind Field, used by 3 nodes, each its own way (use lines):
      - the grass:      Sway by Field   use: vec3(field(p).xy, 0.0) * 1.4     (sideways only, stronger)
      - the fireflies:  Push by Field   use: field(p) * 0.25                  (a gentle drift)
      - the smoke:      Push by Field   use: field(p) * 1.2 + vec3(0.0, 0.0, rise)   (bends and rises)
  * A List, Lanterns: a table of lantern positions and pulls. The fireflies' Attract to List reads it,
    and native Geometry Nodes put a lantern on every row (Get List Item -> Points -> Instance on Points).
    Add a row and you get another lantern, and fireflies gathering round it.
  * Drift, exploded: Tab (or double-click) into it to see its pieces: the CALM constant as a Value node
    and its loop() function as a node of its own. Right-click › Collapse Into Code puts them back.
"""
import math
import os
import subprocess
import sys
import random

import bmesh
import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import explode_ops, gn_link, gn_sockets, live  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(args[0] if args else os.path.join(ROOT, "examples", "meadow_out"))
os.makedirs(OUT, exist_ok=True)
BLEND = os.path.join(OUT, "meadow.blend")
FRAME = 72

DRIFT = """// Drift: fireflies wander in lazy loops
// @in float speed 0.5 0.0 4.0  "How fast they loop"
const float CALM = 0.94;
vec3 loop(vec3 p, float seed) {
  float t = uSceneTime;
  return vec3(sin(t * 0.7 + seed * 6.28 + p.y), cos(t * 0.5 + seed * 3.1 + p.x), 0.4 * sin(t + seed * 9.0));
}
void behave(inout Particle p, float dt) {
  p.velocity = mix(loop(p.position, p.seed) * speed, p.velocity, CALM);
}
"""

SMOKE = """// Chimney Smoke: puffs born at the chimney top that rise and fade
// @in float spread 0.08 0.0 1.0  "How wide the chimney is"
void spawn(inout Particle p) {
  p.position = vec3(-2.6, 2.2, 1.55) + randBall(p.seed) * spread;
  p.velocity = vec3(0.0, 0.0, 0.35) + randBall(p.seed + 1.7) * 0.05;
  p.life = 4.0 + rand1(p.seed * 3.3) * 2.0;
}
vec4 look(Particle p) {
  float a = 1.0 - p.age / p.life;
  return vec4(vec3(0.75, 0.72, 0.7) * a, a * 0.6);
}
"""

LANTERNS = """// Lanterns: where the lanterns hang, and how strongly each one draws the fireflies.
// Add a row for another lantern.
// @list
name     position:3        strength  radius
Gate     -1.2 -1.0 0.9     0.10      1.2
Well      1.6  0.4 0.8     0.14      1.4
Cottage  -1.4  1.4 1.1     0.08      1.0
"""


def host(name, mesh=None, location=(0.0, 0.0, 0.0)):
    obj = bpy.data.objects.new(name, mesh or bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    tree = bpy.data.node_groups.new(f"{name} Nodes", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-900, 0), (1500, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree, gin, gout


def code(tree, kind, key, x, y=0, source=None, label=None):
    group, err = gn_link.create(kind, key, label, source)
    if err:
        raise RuntimeError(f"{key or label}: {err}")
    return gn_link.insert(tree, group, (x, y))


def material(name, color, rough=0.8, emission=0.0, color_attr=None):
    m = bpy.data.materials.new(name)
    if not m.node_tree:
        m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    if color_attr:
        attr = m.node_tree.nodes.new("ShaderNodeAttribute")
        attr.attribute_name = color_attr
        m.node_tree.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    return m


def grass_mesh(n=9000, half=4.0, seed=3):
    rnd = random.Random(seed)
    me = bpy.data.meshes.new("Grass")
    bm = bmesh.new()
    for _ in range(n):
        x, y = rnd.uniform(-half, half), rnd.uniform(-half, half)
        h = rnd.uniform(0.18, 0.5)
        a = rnd.uniform(0, math.pi)
        w = 0.02
        dx, dy = math.cos(a) * w, math.sin(a) * w
        v0, v1 = bm.verts.new((x - dx, y - dy, 0.0)), bm.verts.new((x + dx, y + dy, 0.0))
        v2 = bm.verts.new((x + dx * 0.4, y + dy * 0.4, h * 0.6))
        v3 = bm.verts.new((x - dx * 0.4, y - dy * 0.4, h * 0.6))
        v4 = bm.verts.new((x + rnd.uniform(-0.04, 0.04), y + rnd.uniform(-0.04, 0.04), h))
        bm.faces.new((v0, v1, v2, v3))
        bm.faces.new((v3, v2, v4))
    bm.to_mesh(me)
    bm.free()
    return me


def settle(n=5):
    for _ in range(n):
        gn_link.sync()
        live._flush()


def build():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    scene.frame_start, scene.frame_end = 1, 150

    # ground, a cottage with a chimney, lantern posts come from the List below
    bpy.ops.mesh.primitive_plane_add(size=9.0)
    ground = bpy.context.active_object
    ground.name = "Ground"
    ground.data.materials.append(material("Ground", (0.06, 0.09, 0.04), 0.95))
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-2.6, 2.2, 0.5))
    cottage = bpy.context.active_object
    cottage.name = "Cottage"
    cottage.scale = (1.4, 1.0, 1.0)
    cottage.data.materials.append(material("Whitewash", (0.55, 0.5, 0.45)))
    bpy.ops.mesh.primitive_cylinder_add(radius=0.12, depth=0.6, location=(-2.6, 2.2, 1.25))
    bpy.context.active_object.name = "Chimney"
    bpy.context.active_object.data.materials.append(material("Brick", (0.35, 0.14, 0.08)))

    meadow, tree, gin, gout = host("Meadow", grass_mesh())
    wind = code(tree, 'STAGE', "Wind Field", -600, 600)
    wind.label = "Wind Field"
    # the grass: the mesh swayed by the wind, sideways only
    sway = code(tree, 'STAGE', "Sway by Field", -200, -500)
    real = gn_link.insert(tree, gn_link.build_make_real(), (150, -500))
    setm = tree.nodes.new("GeometryNodeSetMaterial")
    setm.location = (450, -500)
    setm.inputs["Material"].default_value = material("Grass", (0.2, 0.42, 0.12), 0.8)
    tree.links.new(gin.outputs[0], sway.inputs["Mesh"])
    tree.links.new(wind.outputs["wind"], sway.inputs["field"])
    tree.links.new(sway.outputs["Mesh"], real.inputs[0])
    tree.links.new(real.outputs[0], setm.inputs["Geometry"])
    # the fireflies: swarm -> Drift -> Push by Field -> Attract to List -> Blink -> Firefly Look
    swarm = code(tree, 'PARTICLES', "Firefly Swarm", -600, 200)
    drift = code(tree, 'STAGE', None, -300, 200, source=DRIFT, label="Drift")
    push = code(tree, 'STAGE', "Push by Field", 0, 200)
    attract = code(tree, 'STAGE', "Attract to List", 300, 200)
    blink = code(tree, 'STAGE', "Blink", 600, 200)
    look = code(tree, 'STAGE', "Firefly Look", 900, 200)
    for a, b in ((swarm, drift), (drift, push), (push, attract), (attract, blink), (blink, look)):
        tree.links.new(a.outputs["Particles"], b.inputs["Particles"])
    tree.links.new(wind.outputs["wind"], push.inputs["field"])
    # the lanterns: one table for the fireflies' code and for native nodes
    lanterns = code(tree, 'STAGE', None, -600, -150, source=LANTERNS, label="Lanterns")
    tree.links.new(lanterns.outputs[gn_sockets.TABLE_OUT], attract.inputs["targets"])
    smoke = code(tree, 'PARTICLES', None, -600, 900, source=SMOKE, label="Chimney Smoke")
    spush = code(tree, 'STAGE', "Push by Field", -250, 900)
    tree.links.new(smoke.outputs["Particles"], spush.inputs["Particles"])
    tree.links.new(wind.outputs["wind"], spush.inputs["field"])
    settle(4)
    join = lantern_points(tree, lanterns, setm, gout)
    # for renders: the fireflies made real (To Geometry after the look; live drawing carries on) and lit
    # by their own brightness
    ftg = gn_link.insert_make_real(tree, look)
    ftg.location = (1150, 200)
    rad = tree.nodes.new("GeometryNodeSetPointRadius")
    rad.inputs["Radius"].default_value = 0.016
    rad.location = (1350, 200)
    fmat = tree.nodes.new("GeometryNodeSetMaterial")
    fmat.inputs["Material"].default_value = firefly_material()
    fmat.location = (1550, 200)
    tree.links.new(ftg.outputs["Geometry"], rad.inputs["Points"])
    tree.links.new(rad.outputs[0], fmat.inputs["Geometry"])
    tree.links.new(fmat.outputs[0], join.inputs[0])
    settle(4)

    # each node uses the one wind its own way
    sway.inputs[gn_sockets.use_socket("field")].default_value = "vec3(field(p).xy, 0.0) * 1.4"
    push.inputs[gn_sockets.use_socket("field")].default_value = "field(p) * 0.25"
    spush.inputs[gn_sockets.use_socket("field")].default_value = "field(p) * 1.2 + vec3(0.0, 0.0, amount * 0.4)"
    swarm.inputs["Count"].default_value = 420
    swarm.inputs["radius"].default_value = 3.0
    swarm.inputs["height"].default_value = 0.4
    smoke.inputs["Count"].default_value = 4000
    sway.inputs["amount"].default_value = 0.35
    sway.inputs["height"].default_value = 0.45
    wind.inputs["strength"].default_value = 0.8
    attract.inputs["amount"].default_value = 1.0
    look.inputs["size"].default_value = 0.04
    look.inputs["intensity"].default_value = 8.0
    settle(6)
    # Drift, exploded: Tab into it to see CALM and loop() as nodes
    explode_ops.explode_node(tree, drift)
    settle(6)
    return scene, tree


def lantern_points(tree, lanterns, grass_out, gout):
    """Native nodes reading the List: a lantern (post and glowing globe) on every row."""
    ln = tree.nodes.new("GeometryNodeListLength")
    gi = tree.nodes.new("GeometryNodeListGetItem")
    for n in (ln, gi):
        try:
            n.socket_type = 'VECTOR'
        except (AttributeError, TypeError):
            pass
    idx = tree.nodes.new("GeometryNodeInputIndex")
    pts = tree.nodes.new("GeometryNodePoints")
    globe = tree.nodes.new("GeometryNodeMeshIcoSphere")
    globe.inputs["Radius"].default_value = 0.09
    globe.inputs["Subdivisions"].default_value = 2
    gmat = tree.nodes.new("GeometryNodeSetMaterial")
    gmat.inputs["Material"].default_value = material("Lantern Glow", (1.0, 0.62, 0.25), 0.5, emission=18.0)
    inst = tree.nodes.new("GeometryNodeInstanceOnPoints")
    post = tree.nodes.new("GeometryNodeMeshCylinder")
    post.inputs["Radius"].default_value = 0.025
    post.inputs["Depth"].default_value = 1.0
    pmat = tree.nodes.new("GeometryNodeSetMaterial")
    pmat.inputs["Material"].default_value = material("Post", (0.12, 0.08, 0.05))
    flat = tree.nodes.new("GeometryNodeSetPosition")       # posts stand on the ground under each globe
    sep = tree.nodes.new("ShaderNodeSeparateXYZ")
    comb = tree.nodes.new("ShaderNodeCombineXYZ")
    pos = tree.nodes.new("GeometryNodeInputPosition")
    mul = tree.nodes.new("ShaderNodeMath")
    mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = 0.5
    inst2 = tree.nodes.new("GeometryNodeInstanceOnPoints")
    scl = tree.nodes.new("ShaderNodeCombineXYZ")
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    for k, n in enumerate((ln, gi, idx, pts, globe, gmat, inst, post, pmat, flat, sep, comb, pos, mul, inst2, scl,
                           join)):
        n.location = (300 + 220 * (k % 6), -900 - 160 * (k // 6))
    L = tree.links.new
    L(lanterns.outputs["position"], ln.inputs[0])
    L(lanterns.outputs["position"], gi.inputs["List"])
    L(idx.outputs[0], gi.inputs["Index"])
    L(ln.outputs[0], pts.inputs["Count"])
    L(gi.outputs[0], pts.inputs["Position"])
    L(globe.outputs["Mesh"], gmat.inputs["Geometry"])
    L(pts.outputs[0], inst.inputs["Points"])
    L(gmat.outputs[0], inst.inputs["Instance"])
    # posts: the same points, moved down to half their height, a cylinder scaled to that height
    L(pos.outputs[0], sep.inputs[0])
    L(sep.outputs["X"], comb.inputs["X"])
    L(sep.outputs["Y"], comb.inputs["Y"])
    L(sep.outputs["Z"], mul.inputs[0])
    L(mul.outputs[0], comb.inputs["Z"])
    L(pts.outputs[0], flat.inputs["Geometry"])
    L(comb.outputs[0], flat.inputs["Position"])
    L(flat.outputs[0], inst2.inputs["Points"])
    L(post.outputs["Mesh"], pmat.inputs["Geometry"])
    L(pmat.outputs[0], inst2.inputs["Instance"])
    scl.inputs["X"].default_value = 1.0
    scl.inputs["Y"].default_value = 1.0
    sep2 = tree.nodes.new("ShaderNodeSeparateXYZ")
    sep2.location = (300, -1400)
    L(gi.outputs[0], sep2.inputs[0])
    L(sep2.outputs["Z"], scl.inputs["Z"])
    L(scl.outputs[0], inst2.inputs["Scale"])
    L(grass_out.outputs[0], join.inputs[0])
    L(inst.outputs[0], join.inputs[0])
    L(inst2.outputs[0], join.inputs[0])
    L(join.outputs[0], gout.inputs[0])
    return join


def firefly_material():
    m = bpy.data.materials.new("Firefly")
    if not m.node_tree:
        m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.05, 0.05, 0.02, 1.0)
    bsdf.inputs["Emission Color"].default_value = (0.75, 1.0, 0.25, 1.0)
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "brightness"
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = 30.0
    nt.links.new(attr.outputs["Fac"], mul.inputs[0])
    nt.links.new(mul.outputs[0], bsdf.inputs["Emission Strength"])
    return m


def night(scene):
    world = bpy.data.worlds.new("Dusk")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.02, 0.03, 0.07, 1.0)
    moon = bpy.data.objects.new("Moon", bpy.data.lights.new("Moon", 'SUN'))
    moon.data.energy = 0.5
    moon.data.color = (0.6, 0.7, 1.0)
    moon.rotation_euler = (math.radians(55), 0.0, math.radians(-30))
    scene.collection.objects.link(moon)
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    cam.location = (1.2, -5.6, 1.5)
    cam.rotation_euler = (Vector((-0.6, 0.8, 0.75)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.lens = 28
    scene.camera = cam
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.render.image_settings.file_format = 'PNG'
    bloom(scene)


def bloom(scene):
    """Glare in the compositor, so the lanterns and fireflies glow."""
    ng = bpy.data.node_groups.new("Meadow Bloom", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    rl, gl, go = ng.nodes.new("CompositorNodeRLayers"), ng.nodes.new("CompositorNodeGlare"), ng.nodes.new("NodeGroupOutput")
    for name, value in (("Type", "Bloom"), ("Quality", "High"), ("Threshold", 0.6), ("Strength", 1.0), ("Size", 0.7)):
        sock = gl.inputs.get(name)
        if sock is not None:
            try:
                sock.default_value = value
            except (TypeError, ValueError):
                pass
    ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
    ng.links.new(gl.outputs["Image"], go.inputs[0])
    scene.compositing_node_group = ng
    scene.render.use_compositing = True


def main():
    from codenodes import gpu_guard
    if bpy.app.background and not gpu_guard.available():
        raise SystemExit(f"the meadow needs Blender 5.2+ to use the GPU without a window ({gpu_guard._init['error']})")
    codenodes.register()
    scene, tree = build()
    night(scene)
    scene.frame_set(FRAME)
    settle(4)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    print(f"MEADOW saved {BLEND}", flush=True)
    enable = os.path.join(OUT, "enable.py")
    with open(enable, "w") as f:
        f.write(f"import sys\nsys.path.insert(0, {ROOT!r})\nimport codenodes\ncodenodes.register()\n")
    r = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", BLEND, "--python", enable,
                        "-o", os.path.join(OUT, "meadow_####"), "-F", "PNG", "-f", str(FRAME)],
                       capture_output=True, text=True, timeout=900)
    png = os.path.join(OUT, f"meadow_{FRAME:04d}.png")
    print(f"MEADOW render {'ok' if os.path.exists(png) else 'FAILED'} {png}", flush=True)
    if not os.path.exists(png):
        print(r.stdout[-3000:], r.stderr[-3000:])


main()
if bpy.app.background:
    sys.stdout.flush()
    os._exit(0)
