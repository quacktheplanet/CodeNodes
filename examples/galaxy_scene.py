"""The Galaxy scene: what code nodes add to Geometry Nodes, shown with a solar system.

Four ideas, each in its own labelled frame in the Solar System's node tree:

  Shared Orbit   One Orbits node gives out functions: planetPos(i, t), planetRadius(i), planetMass(i),
                 planetCount(). The planets' placement, the asteroids' gravity and collisions, and the
                 ring all call the same functions. Pick Figure Eights in its Template and all of them
                 follow the new paths.
  Reuse          One Planet Terrain node, used three times with different inputs, makes a rocky world,
                 an ocean world and an ice world. Colour by Height paints each.
  Native + GPU   Ordinary nodes and code nodes take turns: Ico Sphere (native) -> Planet Terrain (GPU)
                 -> Colour by Height (GPU) -> To Mesh -> Set Material (native) -> Geometry to Instance
                 -> Instance on Points (native), on points a GPU node places (Points from Function).
  Interaction    An asteroid belt feels the planets' gravity and bounces off them (Gravity to Bodies,
                 Collide with Bodies), then becomes rocks (Instance on Points). A ring of dust follows
                 the second planet (Follow Body).

Around it: a Sun (a GPU Surface lit by its own emission, with a real Point light in the same place, so
the real planets are lit), a spiral galaxy of a million stars coloured by temperature (Galaxy -> Star
Colours) and a nebula. Everything is drawn live on the GPU; To Geometry makes real geometry where the
Geometry Nodes after it need it, and "Only for Render" where only a render needs it.

`build(scene)` makes it all in `scene` and returns the objects. Used by examples/galaxy_demo.py and
tests/test_galaxy.py (needs a window: the GPU isn't available with -b).
"""
import math

import bpy
from mathutils import Vector

from codenodes import gn_link, gpu_live, live

FPS = 24
FRAMES = 288                          # a 12-second fly-through
PLANETS = [
    # name, Planet Terrain inputs, Colour by Height inputs
    ("Rocky", {"seed": 3.0, "height": 0.11, "sea": 0.28, "scale": 2.3, "rough": 0.85},
     {"lowland": (0.42, 0.28, 0.16), "highland": (0.55, 0.45, 0.36), "snowline": 0.9,
      "shallow": (0.1, 0.3, 0.35), "deep": (0.03, 0.1, 0.15)}),
    ("Ocean", {"seed": 17.0, "height": 0.06, "sea": 0.74, "scale": 1.6, "rough": 0.5},
     {"lowland": (0.14, 0.4, 0.12), "highland": (0.35, 0.33, 0.22), "snowline": 0.75}),
    ("Ice", {"seed": 41.0, "height": 0.07, "sea": 0.42, "scale": 2.8, "rough": 0.6},
     {"lowland": (0.62, 0.68, 0.74), "highland": (0.78, 0.82, 0.88), "snowline": 0.3, "ice": 0.55,
      "shallow": (0.3, 0.5, 0.62), "deep": (0.1, 0.22, 0.38)}),
]
ORBITS = {"planets": 3, "first": 3.4, "gap": 2.4, "speed": 0.6, "size": 0.75, "incline": 0.06, "density": 0.4}
STAR_MASS = 30.0
BELT = {"inner": 7.2, "outer": 10.8, "thickness": 0.45, "starMass": STAR_MASS}


def planet_radius(i, size=ORBITS["size"]):
    """Orbits' planetRadius(i), in Python (for the ring's size and the tests)."""
    x = math.sin(i * 12.9898 + 1.3) * 43758.5453
    return size * (0.7 + 0.6 * (x - math.floor(x)))


# ---- helpers -------------------------------------------------------------------------------------------------

def gn_object(name, scene, mesh=None):
    obj = bpy.data.objects.new(name, mesh or bpy.data.meshes.new(name))
    scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(f"{name} Nodes", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-2400, 0), (1300, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree, gin, gout


def code_node(tree, kind, key, loc, label=None):
    group, err = gn_link.create(kind, key)
    if err:
        raise RuntimeError(f"{key}: {err}")
    node = gn_link.insert(tree, group, loc)
    node.width = 200
    if label:
        node.label = label
    return node


def node(tree, idname, loc, label=None, **inputs):
    n = tree.nodes.new(idname)
    n.location = loc
    if label:
        n.label = label
    for k, v in inputs.items():
        n.inputs[k].default_value = v
    return n


def set_in(node_, name, value):
    """Set an input's value; a colour given as (r, g, b) gets alpha 1."""
    sock = node_.inputs[name]
    if isinstance(value, tuple) and len(value) == 3 and sock.type == 'RGBA':
        value = (*value, 1.0)
    sock.default_value = value


def to_geometry(tree, after, loc):
    tg = gn_link.insert_make_real(tree, after, loc)
    return tg


def frame(tree, label, nodes, color):
    fr = tree.nodes.new("NodeFrame")
    fr.label = label
    fr.use_custom_color = True
    fr.color = color
    fr.label_size = 28
    fr.shrink = True
    for n in nodes:
        n.parent = fr
    return fr


def material(name, color=(0.8, 0.8, 0.8), rough=0.8, color_attr=None, emission=0.0, emission_color=None):
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
        bsdf.inputs["Emission Color"].default_value = (*(emission_color or color), 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    return m


def starlight_material():
    """Stars after To Points: each glows in the blackbody colour of its 'temp' (thousands of kelvin), dimmer
    towards the crowded core (as the live look does), so the core glows instead of blowing out."""
    m = bpy.data.materials.new("Starlight")
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    bb = nt.nodes.new("ShaderNodeBlackbody")
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = 1000.0
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "temp"
    nt.links.new(attr.outputs["Fac"], mul.inputs[0])
    nt.links.new(mul.outputs[0], bb.inputs["Temperature"])
    nt.links.new(bb.outputs["Color"], em.inputs["Color"])
    tex = nt.nodes.new("ShaderNodeTexCoord")
    ln = nt.nodes.new("ShaderNodeVectorMath")
    ln.operation = 'LENGTH'
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.interpolation_type = 'SMOOTHSTEP'
    ramp.inputs["From Min"].default_value = 0.05
    ramp.inputs["From Max"].default_value = 2.0            # the galaxy's size, in its own units
    ramp.inputs["To Min"].default_value = 0.06
    ramp.inputs["To Max"].default_value = 0.9
    nt.links.new(tex.outputs["Object"], ln.inputs[0])
    nt.links.new(ln.outputs["Value"], ramp.inputs["Value"])
    nt.links.new(ramp.outputs["Result"], em.inputs["Strength"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return m


def nebula_material():
    """Nebula sprites after To Points: faint glowing gas, pink to blue by a noise field."""
    m = bpy.data.materials.new("Nebula Gas")
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    mix = nt.nodes.new("ShaderNodeMixShader")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    em = nt.nodes.new("ShaderNodeEmission")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.05
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.95, 0.25, 0.55, 1.0)
    ramp.color_ramp.elements[1].color = (0.25, 0.5, 1.0, 1.0)
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    nt.links.new(geo.outputs["Position"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
    em.inputs["Strength"].default_value = 1.5
    mix.inputs["Fac"].default_value = 0.035
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    try:
        m.surface_render_method = 'BLENDED'
    except (AttributeError, TypeError):
        pass
    return m


def settle(n=6):
    for _ in range(n):
        gn_link.sync()
        live._flush()


def rebuild_all():
    """Make every code node's geometry current (the To Geometry results the scene shows)."""
    col = bpy.data.collections.get(gn_link.SOURCES)
    for o in list(col.objects) if col is not None else []:
        s = getattr(o, "codenodes", None)
        if s is not None and s.enabled:
            live.rebuild(o)


# ---- the solar system ------------------------------------------------------------------------------------------

def build_solar_system(scene):
    solar, t, _gin, gout = gn_object("Solar System", scene)
    N = {}

    # Shared Orbit
    N["orbits"] = code_node(t, 'STAGE', "Orbits", (-2700, 900), "Orbits")
    N["eights"] = code_node(t, 'STAGE', "Figure Eights", (-2700, 300), "Figure Eights (spare: swap it in)")

    # Reuse: three planets from the same two nodes
    planet_mat = material("Planet Surface", rough=0.75, color_attr="color")
    geo2inst = node(t, "GeometryNodeGeometryToInstance", (-350, 1250), "Planets as instances")
    reuse = []
    for row, (name, terrain_in, colour_in) in enumerate(PLANETS):
        y = 2700 - row * 560
        ico = node(t, "GeometryNodeMeshIcoSphere", (-2150, y), f"{name}: sphere", Radius=1.0, Subdivisions=6)
        ter = code_node(t, 'DEFORM', "Planet Terrain", (-1850, y), f"Planet Terrain ({name})")
        col = code_node(t, 'STAGE', "Colour by Height", (-1550, y), f"Colour by Height ({name})")
        t.links.new(ico.outputs["Mesh"], ter.inputs["Mesh"])
        t.links.new(ter.outputs["Geometry"], col.inputs["Mesh"])
        tg = to_geometry(t, col, (-1250, y))
        setm = node(t, "GeometryNodeSetMaterial", (-980, y), f"{name}: material", Material=planet_mat)
        smooth = node(t, "GeometryNodeSetShadeSmooth", (-760, y))
        t.links.new(tg.outputs["Geometry"], setm.inputs["Geometry"])
        t.links.new(setm.outputs["Geometry"], smooth.inputs["Geometry"])
        t.links.new(smooth.outputs["Geometry"], geo2inst.inputs["Geometry"])
        N[f"terrain{row}"], N[f"colour{row}"], N[f"tg{row}"] = ter, col, tg
        reuse += [ico, ter, col, tg, setm, smooth]

    # Native + GPU: a GPU node places the planets, Geometry Nodes instances them
    pts = code_node(t, 'PARTICLES', "Points from Function", (-1850, 900), "Planet positions")
    ptg = to_geometry(t, pts, (-1550, 900))
    index = node(t, "GeometryNodeInputIndex", (-980, 780))
    size = node(t, "GeometryNodeInputNamedAttribute", (-980, 680), "bodySize (from the GPU node)")
    size.data_type = 'FLOAT'
    size.inputs["Name"].default_value = "bodySize"
    clock = node(t, "GeometryNodeInputSceneTime", (-980, 500), "Spin with time")
    spin = node(t, "ShaderNodeMath", (-760, 500), "Spin rate")
    spin.operation = 'MULTIPLY'
    spin.inputs[1].default_value = 0.35
    rot = node(t, "ShaderNodeCombineXYZ", (-560, 500))
    t.links.new(clock.outputs["Seconds"], spin.inputs[0])
    t.links.new(spin.outputs[0], rot.inputs["Z"])
    iop = node(t, "GeometryNodeInstanceOnPoints", (-120, 1000), "Planets on their orbits")
    iop.inputs["Pick Instance"].default_value = True
    t.links.new(ptg.outputs["Geometry"], iop.inputs["Points"])
    t.links.new(geo2inst.outputs["Instances"], iop.inputs["Instance"])
    t.links.new(index.outputs["Index"], iop.inputs["Instance Index"])
    t.links.new(size.outputs["Attribute"], iop.inputs["Scale"])
    t.links.new(rot.outputs["Vector"], iop.inputs["Rotation"])
    for out_name, in_name in (("planetPos", "place"), ("planetRadius", "size"), ("planetCount", "count")):
        t.links.new(N["orbits"].outputs[out_name], pts.inputs[in_name])
    native = [pts, ptg, index, size, clock, spin, rot, geo2inst, iop]
    N["points"], N["points_tg"], N["planets_iop"] = pts, ptg, iop

    # Interaction: an asteroid belt that feels the planets and bounces off them
    rock_mat = material("Asteroid Rock", color=(0.33, 0.3, 0.27), rough=0.9)
    belt = code_node(t, 'PARTICLES', "Belt", (-2150, -200), "Asteroid belt")
    grav = code_node(t, 'STAGE', "Gravity to Bodies", (-1850, -200), "Gravity to Bodies")
    coll = code_node(t, 'STAGE', "Collide with Bodies", (-1550, -200), "Collide with Bodies")
    lights = code_node(t, 'STAGE', "Scene Lights", (-1550, -750), "Scene Lights")
    look = code_node(t, 'STAGE', "Material Look", (-1250, -200), "Rocky look (live)")
    for a, b in ((belt, grav), (grav, coll), (coll, look)):
        t.links.new(a.outputs["Particles"], b.inputs["Particles"])
    for out_name, in_name in (("planetPos", "bodyPos"), ("planetMass", "bodyMass"), ("planetCount", "bodyCount")):
        t.links.new(N["orbits"].outputs[out_name], grav.inputs[in_name])
    for out_name, in_name in (("planetPos", "bodyPos"), ("planetRadius", "bodyRadius"), ("planetCount", "bodyCount")):
        t.links.new(N["orbits"].outputs[out_name], coll.inputs[in_name])
    t.links.new(lights.outputs["light"], look.inputs["light"])
    atg = to_geometry(t, look, (-980, -200))
    rock = node(t, "GeometryNodeMeshIcoSphere", (-980, -620), "Rock", Radius=0.035, Subdivisions=2)
    lump = node(t, "ShaderNodeTexNoise", (-1250, -1000), "Lumps")
    lump.inputs["Scale"].default_value = 30.0
    lump_off = node(t, "ShaderNodeVectorMath", (-760, -1000))
    lump_off.operation = 'SCALE'
    lump_off.inputs["Scale"].default_value = 0.02
    center = node(t, "ShaderNodeVectorMath", (-980, -1000))
    center.operation = 'SUBTRACT'
    center.inputs[1].default_value = (0.5, 0.5, 0.5)
    setpos = node(t, "GeometryNodeSetPosition", (-760, -620), "Lumpy rock")
    rset = node(t, "GeometryNodeSetMaterial", (-340, -620), "Rock material", Material=rock_mat)
    rcol = node(t, "GeometryNodeStoreNamedAttribute", (-560, -620), "Rock colour (for the viewport)")
    rcol.data_type = 'FLOAT_COLOR'
    rcol.inputs["Name"].default_value = "color"
    rcol.inputs["Value"].default_value = (0.3, 0.27, 0.24, 1.0)
    t.links.new(lump.outputs["Color"], center.inputs[0])
    t.links.new(center.outputs[0], lump_off.inputs[0])
    t.links.new(lump_off.outputs[0], setpos.inputs["Offset"])
    t.links.new(rock.outputs["Mesh"], setpos.inputs["Geometry"])
    t.links.new(setpos.outputs["Geometry"], rcol.inputs["Geometry"])
    t.links.new(rcol.outputs["Geometry"], rset.inputs["Geometry"])
    rrot = node(t, "FunctionNodeRandomValue", (-560, -200), "Random turn")
    rrot.data_type = 'FLOAT_VECTOR'
    rrot.inputs["Max"].default_value = (6.283, 6.283, 6.283)
    rscale = node(t, "FunctionNodeRandomValue", (-560, -380), "Random size")
    rscale.data_type = 'FLOAT'
    rscale.inputs[2].default_value = 0.35
    rscale.inputs[3].default_value = 1.6
    iop2 = node(t, "GeometryNodeInstanceOnPoints", (-120, -250), "Rocks on the asteroids")
    t.links.new(atg.outputs["Geometry"], iop2.inputs["Points"])
    t.links.new(rset.outputs["Geometry"], iop2.inputs["Instance"])
    t.links.new(rrot.outputs["Value"], iop2.inputs["Rotation"])
    t.links.new(rscale.outputs[1], iop2.inputs["Scale"])
    interaction = [belt, grav, coll, lights, look, atg, rock, lump, lump_off, center, setpos, rcol, rset, rrot, rscale, iop2]
    N["belt"], N["grav"], N["coll"], N["asteroid_tg"], N["look"] = belt, grav, coll, atg, look

    # Interaction: a ring of dust around the second planet, following it
    ring = code_node(t, 'PARTICLES', "Ring", (-2150, -1500), "Ring dust")
    follow = code_node(t, 'STAGE', "Follow Body", (-1850, -1500), "Follow Body (planet 2)")
    glow = code_node(t, 'STAGE', "Glow Look", (-1550, -1500), "Dust glow (live)")
    t.links.new(ring.outputs["Particles"], follow.inputs["Particles"])
    t.links.new(follow.outputs["Particles"], glow.inputs["Particles"])
    t.links.new(N["orbits"].outputs["planetPos"], follow.inputs["bodyPos"])
    rtg = to_geometry(t, glow, (-1250, -1500))
    rrad = node(t, "GeometryNodeSetPointRadius", (-980, -1500), "Dust size", Radius=0.007)
    rmat = node(t, "GeometryNodeSetMaterial", (-760, -1500), "Dust material",
                Material=material("Ring Dust", color=(0.62, 0.58, 0.5), rough=0.9, emission=0.08))
    t.links.new(rtg.outputs["Geometry"], rrad.inputs["Points"])
    t.links.new(rrad.outputs["Points"], rmat.inputs["Geometry"])
    ring_nodes = [ring, follow, glow, rtg, rrad, rmat]
    N["ring"], N["follow"], N["ring_tg"] = ring, follow, rtg

    join = node(t, "GeometryNodeJoinGeometry", (300, 300), "Everything")
    for sock in (iop.outputs["Instances"], iop2.outputs["Instances"], rmat.outputs["Geometry"]):
        t.links.new(sock, join.inputs["Geometry"])
    t.links.new(join.outputs["Geometry"], gout.inputs[0])
    gout.location = (520, 300)
    t.nodes.remove(_gin)                   # the object has no geometry of its own to pass in

    frame(t, "Shared Orbit: one Orbits node, its functions called by every system below", [N["orbits"], N["eights"]],
          (0.28, 0.22, 0.1))
    frame(t, "Reuse: one Planet Terrain node, three planets (rocky, ocean, ice)", reuse, (0.12, 0.24, 0.16))
    frame(t, "Native + GPU: a GPU node places the planets, Geometry Nodes instances them", native,
          (0.14, 0.18, 0.3))
    frame(t, "Interaction: asteroids feel the planets' gravity and bounce off them", interaction, (0.3, 0.14, 0.14))
    frame(t, "Interaction: a ring of dust that follows the second planet", ring_nodes, (0.24, 0.16, 0.28))

    settle()
    orbits = N["orbits"]
    for k, v in ORBITS.items():
        orbits.inputs[k].default_value = v
        N["eights"].inputs[k].default_value = v
    for row, (_name, terrain_in, colour_in) in enumerate(PLANETS):
        for k, v in terrain_in.items():
            N[f"terrain{row}"].inputs[k].default_value = v
        for k, v in colour_in.items():
            set_in(N[f"colour{row}"], k, v)
    pts.inputs["Count"].default_value = ORBITS["planets"]
    for k, v in BELT.items():
        belt.inputs[k].default_value = v
    belt.inputs["Count"].default_value = 5000
    belt.inputs["Pre-warm"].default_value = 2.0
    grav.inputs["starMass"].default_value = STAR_MASS
    coll.inputs["starRadius"].default_value = 1.15
    look.inputs["mat"].default_value = rock_mat
    look.inputs["size"].default_value = 0.025
    look.inputs["brightness"].default_value = 0.7
    r1 = planet_radius(1)
    ring.inputs["inner"].default_value = r1 * 1.45
    ring.inputs["outer"].default_value = r1 * 2.35
    ring.inputs["Count"].default_value = 15_000
    follow.inputs["body"].default_value = 1
    set_in(glow, "tint", (0.9, 0.84, 0.72))
    glow.inputs["intensity"].default_value = 0.12
    glow.inputs["size"].default_value = 0.02
    atg.inputs["Max Points"].default_value = 5000
    rtg.inputs["When"].default_value = "Only for Render"
    rtg.inputs["Max Points"].default_value = 15_000
    settle()
    return solar, N


# ---- the sun, the galaxy and the nebula -------------------------------------------------------------------------

def build_sun(scene):
    sun, t, _gin, gout = gn_object("Sun", scene)
    glow_mat = material("Sun Glow", color=(1.0, 0.62, 0.25), emission=8.0, emission_color=(1.0, 0.62, 0.25))
    live_mat = material("Sun (live view)", color=(1.0, 0.55, 0.2), emission=0.55, emission_color=(1.0, 0.7, 0.35))
    surf = code_node(t, 'MESH', "Sun", (-600, 0), "Sun (GPU Surface)")
    mlook = code_node(t, 'STAGE', "Material Look", (-900, -250), "Sun material")
    t.links.new(mlook.outputs["material"], surf.inputs["Material"])
    tg = to_geometry(t, surf, (-300, 0))
    setm = node(t, "GeometryNodeSetMaterial", (0, 0), Material=glow_mat)
    t.links.new(tg.outputs["Geometry"], setm.inputs["Geometry"])
    t.links.new(setm.outputs["Geometry"], gout.inputs[0])
    settle()
    mlook.inputs["mat"].default_value = live_mat     # the viewport has no tone mapping: keep it below white
    surf.inputs["Live Resolution"].default_value = 1.0
    tg.inputs["When"].default_value = "Only for Render"
    light = bpy.data.objects.new("Sunlight", bpy.data.lights.new("Sunlight", 'POINT'))
    light.data.energy = 2600.0
    light.data.color = (1.0, 0.86, 0.7)
    light.data.shadow_soft_size = 1.0
    scene.collection.objects.link(light)
    light.parent = sun                    # always where the Sun is
    sun.visible_shadow = False            # the light is inside the Sun's mesh: it mustn't block it
    settle()
    return sun


def build_milky_way(scene):
    gal, t, _gin, gout = gn_object("Milky Way", scene)
    gal.location = (-820.0, 1900.0, -420.0)
    gal.rotation_euler = (math.radians(68), math.radians(6), math.radians(20))
    gal.scale = (55.0, 55.0, 55.0)
    src = code_node(t, 'PARTICLES', "Galaxy", (-900, 0), "Galaxy (a million stars)")
    stars = code_node(t, 'STAGE', "Star Colours", (-650, 0), "Star Colours")
    t.links.new(src.outputs["Particles"], stars.inputs["Particles"])
    tg = to_geometry(t, stars, (-400, 0))
    rad = node(t, "GeometryNodeSetPointRadius", (-150, 0), Radius=0.012)
    setm = node(t, "GeometryNodeSetMaterial", (50, 0), Material=starlight_material())
    t.links.new(tg.outputs["Geometry"], rad.inputs["Points"])
    t.links.new(rad.outputs["Points"], setm.inputs["Geometry"])
    t.links.new(setm.outputs["Geometry"], gout.inputs[0])
    settle()
    tg.inputs["When"].default_value = "Only for Render"
    tg.inputs["Max Points"].default_value = 600_000
    stars.inputs["reach"].default_value = src.inputs["size"].default_value
    stars.inputs["brightness"].default_value = 0.45
    settle()
    return gal, src


def nebula_volume_material():
    """The nebula in renders: glowing gas in a volume, pink to blue."""
    m = bpy.data.materials.new("Nebula Volume")
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    dens = nt.nodes.new("ShaderNodeAttribute")
    dens.attribute_name = "density"
    glow = nt.nodes.new("ShaderNodeMath")
    glow.operation = 'MULTIPLY'
    glow.inputs[1].default_value = 0.35
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1.5
    tex = nt.nodes.new("ShaderNodeTexCoord")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.95, 0.22, 0.5, 1.0)
    ramp.color_ramp.elements[1].color = (0.2, 0.45, 1.0, 1.0)
    nt.links.new(tex.outputs["Generated"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], vol.inputs["Emission Color"])
    nt.links.new(ramp.outputs["Color"], vol.inputs["Color"])
    nt.links.new(dens.outputs["Fac"], glow.inputs[0])
    nt.links.new(glow.outputs[0], vol.inputs["Emission Strength"])
    vol.inputs["Density"].default_value = 0.015
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return m


def build_nebula(scene):
    """Live: GPU sprites (Nebula). Renders: a real volume made by ordinary nodes (Volume Cube from noise),
    switched on only for renders with Is Viewport."""
    radius = 70.0
    neb, t, _gin, gout = gn_object("Nebula", scene)
    neb.location = (60.0, 420.0, -95.0)
    neb.rotation_euler = (math.radians(20), 0.0, math.radians(35))
    src = code_node(t, 'PARTICLES', "Nebula", (-900, 300), "Nebula (glowing gas, live)")
    pos = node(t, "GeometryNodeInputPosition", (-1100, -150))
    noise = node(t, "ShaderNodeTexNoise", (-900, -100), "Wisps")
    noise.inputs["Scale"].default_value = 1.4 / radius
    noise.inputs["Detail"].default_value = 6.0
    length = node(t, "ShaderNodeVectorMath", (-900, -350))
    length.operation = 'LENGTH'
    fall = node(t, "ShaderNodeMapRange", (-700, -350), "Fade at the edge")
    fall.interpolation_type = 'SMOOTHSTEP'
    fall.inputs["From Min"].default_value = radius * 0.35
    fall.inputs["From Max"].default_value = radius
    fall.inputs["To Min"].default_value = 1.0
    fall.inputs["To Max"].default_value = 0.0
    thick = node(t, "ShaderNodeMapRange", (-700, -100), "Only the thick gas")
    thick.inputs["From Min"].default_value = 0.5
    thick.inputs["From Max"].default_value = 0.72
    dens = node(t, "ShaderNodeMath", (-500, -200), "Density")
    dens.operation = 'MULTIPLY'
    t.links.new(pos.outputs["Position"], noise.inputs["Vector"])
    t.links.new(pos.outputs["Position"], length.inputs[0])
    t.links.new(length.outputs["Value"], fall.inputs["Value"])
    t.links.new(noise.outputs["Fac"], thick.inputs["Value"])
    t.links.new(thick.outputs["Result"], dens.inputs[0])
    t.links.new(fall.outputs["Result"], dens.inputs[1])
    cube = node(t, "GeometryNodeVolumeCube", (-300, -200), "Volume Cube")
    cube.inputs["Min"].default_value = (-radius, -radius, -radius * 0.6)
    cube.inputs["Max"].default_value = (radius, radius, radius * 0.6)
    for axis in ("Resolution X", "Resolution Y"):
        cube.inputs[axis].default_value = 80
    cube.inputs["Resolution Z"].default_value = 48
    t.links.new(dens.outputs[0], cube.inputs["Density"])
    setm = node(t, "GeometryNodeSetMaterial", (-100, -200), Material=nebula_volume_material())
    t.links.new(cube.outputs["Volume"], setm.inputs["Geometry"])
    isview = node(t, "GeometryNodeIsViewport", (-100, 0))
    switch = node(t, "GeometryNodeSwitch", (120, -100), "Renders only")
    switch.input_type = 'GEOMETRY'
    t.links.new(isview.outputs[0], switch.inputs["Switch"])
    t.links.new(setm.outputs["Geometry"], switch.inputs["False"])
    t.links.new(switch.outputs[0], gout.inputs[0])
    frame(t, "Live: GPU sprites", [src], (0.26, 0.16, 0.24))
    frame(t, "Renders: a real volume from ordinary nodes", [pos, noise, length, fall, thick, dens, cube, setm,
                                                            isview, switch], (0.16, 0.18, 0.26))
    settle()
    src.inputs["radius"].default_value = radius
    src.inputs["thickness"].default_value = 0.6
    src.inputs["Count"].default_value = 60_000
    try:
        scene.eevee.volumetric_end = 1500.0
        scene.eevee.volumetric_samples = 96
    except AttributeError:
        pass
    settle()
    return neb


def space_world(scene):
    world = bpy.data.worlds.new("Deep Space")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.0015, 0.0018, 0.004, 1.0)
        bg.inputs["Strength"].default_value = 1.0


def compositor_bloom(scene):
    ng = bpy.data.node_groups.new("Galaxy Bloom", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    rl = ng.nodes.new("CompositorNodeRLayers")
    gl = ng.nodes.new("CompositorNodeGlare")
    go = ng.nodes.new("NodeGroupOutput")
    for name, value in (("Type", "Bloom"), ("Quality", "High"), ("Threshold", 1.0), ("Strength", 0.8),
                        ("Size", 0.7)):
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


def fly_through(scene):
    """A slow orbit and dolly round the solar system, the galaxy behind it."""
    target = bpy.data.objects.new("Look At", None)
    scene.collection.objects.link(target)
    rig = bpy.data.objects.new("Camera Rig", None)
    scene.collection.objects.link(rig)
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    cam.parent = rig
    cam.data.lens = 32
    cam.data.clip_end = 5000.0
    con = cam.constraints.new('TRACK_TO')
    con.target = target
    con.track_axis, con.up_axis = 'TRACK_NEGATIVE_Z', 'UP_Y'
    keys = [(1, (0.0, -22.0, 8.5), 0.0, (0.0, 3.0, 0.0)),
            (140, (0.0, -17.0, 5.5), math.radians(-35), (0.0, 1.5, 0.0)),
            (FRAMES, (0.0, -11.5, 2.4), math.radians(-80), (1.5, 0.0, 0.3))]
    for f, loc, turn, look in keys:
        cam.location = loc
        cam.keyframe_insert("location", frame=f)
        rig.rotation_euler = (0.0, 0.0, turn)
        rig.keyframe_insert("rotation_euler", frame=f)
        target.location = look
        target.keyframe_insert("location", frame=f)
    scene.camera = cam
    return cam


def build(scene):
    """The whole Galaxy scene in `scene`. Returns a dict of the main objects and nodes."""
    scene.name = "Galaxy"
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    scene.render.fps = FPS
    scene.frame_start, scene.frame_end = 1, FRAMES
    solar, nodes = build_solar_system(scene)
    sun = build_sun(scene)
    gal, gal_src = build_milky_way(scene)
    neb = build_nebula(scene)
    space_world(scene)
    compositor_bloom(scene)
    cam = fly_through(scene)
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    try:
        scene.eevee.taa_render_samples = 32
    except AttributeError:
        pass
    for _ in range(3):
        settle()
    gpu_live.refresh_deform_inputs()
    rebuild_all()
    scene.frame_set(1)
    return {"solar": solar, "sun": sun, "galaxy": gal, "galaxy_src": gal_src, "nebula": neb, "camera": cam,
            "nodes": nodes}
