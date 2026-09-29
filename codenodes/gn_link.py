"""CodeNodes inside Geometry Nodes.

Blender doesn't let add-ons define nodes inside a Geometry Nodes tree, so a code node
there is an ordinary Group node:

    [ Code · Donut ]  (a node group, one per inserted node)
      inputs:  Resolution + one per `@param` / `param` line in the code
      inside:  Object Info -> Group Output
      reads:   a hidden source object in the "CodeNodes Sources" collection, which the
               usual Code Mesh / Code Shape / Code Particles machinery builds

Changing a value on the group node (or on a modifier using the group), or editing the
code, rebuilds the source object, and Object Info hands the new geometry to the rest of
the tree. Renders read the stored result and never run GPU code.

Values that come in through a link are followed back to a Value / Integer node, a
reroute, or the host tree's Group Input (the modifier's value). Anything computed by
other nodes can't be read from Python, so the value typed on the socket is used instead.
"""

from __future__ import annotations

import traceback

import bpy

from . import live, props, sdf_code

SOURCES = "CodeNodes Sources"
TAG = "codenodes_source"         # on the node group: the source object's name
KIND_KEY = "codenodes_kind"
PREFIX = "Code · "
POLL_S = 0.25

MAKE_REAL = "codenodes_make_real"   # on a To Geometry node group (called Make Real before 0.3)
MAKE_REAL_NAME = "To Geometry"
GPU_KINDS = ('MESH', 'PARTICLES', 'DEFORM')
CODE_KINDS = GPU_KINDS + ('STAGE',)     # kept as text and run on demand (not built at once like a shape)
TAP = "codenodes_tap"              # on a hidden tap tree: which GPU Mesh source it feeds
JOIN = "codenodes_join"            # on a Join Particles node group
CACHE = "codenodes_gpu_cache"      # on a GPU Cache node group
PIPE_KEY = "cn_pipe_key"           # on a branch object: head name | stage names
PIPE_HEAD = "cn_pipe_head"         # on a branch object: its head source name

KINDS = {
    'PARTICLES': ("GPU Particles", "A particle source you write. Runs and draws on the GPU (live) by default; "
                                   "wire stages after it (what the particles do, how they look). Only a To "
                                   "Geometry node turns it into real points", 'PARTICLES'),
    'STAGE': ("GPU Stage", "One step you write in a chain of code nodes: a behaviour, a look, a warp, a mesh "
                           "deform or functions for other nodes. Its code declares its own inputs and outputs; "
                           "it runs on the GPU as part of the chain", 'NODETREE'),
    'MESH': ("GPU Surface (SDF)", "A surface from a signed distance function. Raymarched on the GPU (live) by "
                                  "default; only a To Geometry node turns it into a mesh", 'SCRIPT'),
    'DEFORM': ("GPU Mesh", "Code run on every vertex of the mesh wired into it (deform, displace, recolour). "
                           "Runs and draws on the GPU (live) by default; only a To Geometry node gives the "
                           "modified mesh", 'MOD_WAVE'),
    'SHAPE': ("Code Shape", "A model built from a parametric description: exact edges, clean quads "
                            "(real geometry straight away)", 'MESH_CYLINDER'),
}

CASTLE = """\
// The Aerie citadel as a GPU surface: a ring wall with towers, a keep and roofs.
// Units: the citadel is about 25 across; `scale` is metres per unit.
// @param towers 6 3 12  "Number of towers on the ring wall"
// @param radius 10 6 12.5  "Radius of the ring wall"
// @param wallHeight 4.5 2 9  "Height of the wall"
// @param keepHeight 14 6 26  "Height of the central keep"
// @param scale 0.1 0.02 1.0  "Overall size (the castle is modelled in tens of metres)"

float citadel(vec3 p) {
  float ring = abs(length(p.xy) - radius) - 0.7;
  float wall = max(ring, max(-p.z - 1.0, p.z - wallHeight));
  vec3 m = aroundZ(p, floor(towers) * 7.0);
  wall = min(wall, sdBox(m - vec3(radius, 0.0, wallHeight + 0.45), vec3(0.7, 0.42, 0.45)));
  float gate = min(sdBox(p - vec3(0.0, -radius, 1.0), vec3(1.4, 2.0, 2.0)),
                   sdCylinder(rotateX(p - vec3(0.0, -radius, 3.0), 1.5708), 1.4, 2.0));
  wall = max(wall, -gate);
  vec3 t = aroundZ(p, floor(towers));
  float towerH = wallHeight + 3.5;
  float tower = sdCylinder(t - vec3(radius, 0.0, towerH * 0.5), 2.0, towerH * 0.5 + 0.5);
  vec3 s = aroundZ(t - vec3(radius, 0.0, 0.0), 4.0);
  tower = max(tower, -sdBox(s - vec3(2.0, 0.0, towerH * 0.62), vec3(0.5, 0.11, 0.6)));
  float keep = sdRoundBox(p - vec3(0.0, 0.0, keepHeight * 0.5), vec3(3.6, 3.6, keepHeight * 0.5), 0.25);
  vec3 k = aroundZ(p, 4.0);
  float arcade = min(sdBox(k - vec3(3.6, 0.0, 1.1), vec3(0.8, 1.05, 1.1)),
                     sdCylinder(rotateY(k - vec3(3.6, 0.0, 2.2), 1.5708), 1.05, 0.8));
  float windows = sdBox(k - vec3(3.6, 0.0, keepHeight * 0.72), vec3(0.8, 0.35, 0.9));
  keep = max(keep, -min(arcade, windows));
  return min(min(wall, tower), keep);
}

float roofs(vec3 p) {
  vec3 t = aroundZ(p, floor(towers));
  float towerH = wallHeight + 3.5;
  float caps = sdCone(t - vec3(radius, 0.0, towerH + 2.4), 2.4, 2.55, 0.05);
  float spire = sdCone(p - vec3(0.0, 0.0, keepHeight + 3.4), 3.4, 4.0, 0.08);
  return min(caps, spire);
}

float sdf(vec3 p) {
  vec3 q = p / scale;
  return min(citadel(q), roofs(q)) * scale;
}

// colour for the live view: terracotta roofs, weathered stone
vec3 color(vec3 p) {
  vec3 q = p / scale;
  if (roofs(q) < citadel(q)) return vec3(0.72, 0.30, 0.18);
  return vec3(0.80, 0.74, 0.64) * (0.82 + 0.18 * noise3(q * 1.7));
}
"""

PLANET = """\
// A small planet, Tellus-style: continents from fractal noise, shallow seas, snow on the peaks
// and at the poles.
// @param radius 1.0 0.3 3.0  "Radius of the planet, in metres"
// @param relief 0.05 0.0 0.25  "Height of the mountains compared to the radius"
// @param sea 0.5 0.3 0.7  "Sea level: higher floods more land"
// @param seed 3.0 0.0 100.0  "Changes the continents"

float land(vec3 n) { return fbm3(n * 2.3 + seed); }

float sdf(vec3 p) {
  vec3 n = normalize(p);
  float h = max(land(n), sea);
  return (length(p) - radius - (h - sea) * relief * 2.0) * 0.8;
}

vec3 color(vec3 p) {
  vec3 n = normalize(p);
  float h = land(n);
  if (h < sea) return mix(vec3(0.02, 0.07, 0.22), vec3(0.06, 0.32, 0.45), smoothstep(sea - 0.07, sea, h));
  float e = (h - sea) / (1.0 - sea);
  vec3 c = mix(vec3(0.16, 0.40, 0.12), vec3(0.50, 0.42, 0.28), smoothstep(0.05, 0.35, e));
  return mix(c, vec3(0.95), smoothstep(0.55, 0.75, e + abs(n.z) * 0.55));
}
"""

SATURN = """\
// A ringed planet: a banded sphere and a tilted ring, one surface.
// @param radius 0.9 0.1 2.0  "Radius of the planet"
// @param ring 1.55 0.5 3.0  "Radius of the ring"
// @param width 0.35 0.02 1.0  "Width of the ring"
// @param tilt 0.45 0.0 1.5  "Tilt of the ring, in radians"

float planet(vec3 p) { return sdSphere(p, radius) + 0.012 * sin(p.z * 18.0 + uTime * 3.0); }
float band(vec3 p) {
  vec3 q = rotateX(p, tilt);
  vec2 r = vec2(length(q.xy) - ring, q.z);
  return sdBox(vec3(r, 0.0), vec3(width, 0.02, 1.0));
}
float sdf(vec3 p) { return min(planet(p), band(p)); }

vec3 color(vec3 p) {
  if (band(p) < planet(p)) {
    float r = length(rotateX(p, tilt).xy);
    return vec3(0.85, 0.78, 0.62) * (0.7 + 0.3 * sin(r * 60.0));
  }
  return mix(vec3(0.85, 0.66, 0.42), vec3(0.95, 0.88, 0.7), 0.5 + 0.5 * sin(p.z * 18.0 + uTime * 3.0));
}
"""

GALAXY = """\
// Galaxy: a million stars on twisted ellipses (a density wave, as in Myriad). Each ring of orbits is
// turned a little more than the one inside it, so the spiral arms never wind up.
// @param size 2.4 0.5 10.0  "Radius of the galaxy"
// @param twist 2.4 0.0 6.0  "How strongly the orbits twist into spiral arms"
// @param spin 0.9 0.0 3.0  "How fast it turns"

void spawn(inout Particle p) { p.life = 1e9; }        // stars live forever

void update(inout Particle p, float dt) {
  float s = p.seed;
  float bulge = step(rand1(s * 1.7 + 9.0), 0.14);
  float h = rand1(s * 2.3 + 0.5);
  float a = bulge > 0.5 ? pow(h, 1.6) * 0.45 + 0.02 : -log(1.0 - h * 0.99) * 0.6 + 0.05;
  float z = (rand1(s * 3.1 + 3.0) - 0.5) * (bulge > 0.5 ? 0.35 * (1.0 - a) : 0.05 * exp(-a));
  float phase = rand1(s * 4.7 + 1.0) * 6.2831853 + spin / (a + 0.25) * uTime;
  float squash = bulge > 0.5 ? 0.92 : 0.78;
  float turn = a * twist + 0.3;
  vec2 e = vec2(cos(phase) * a, sin(phase) * a * squash);
  float c = cos(turn), sn = sin(turn);
  vec3 q = vec3(c * e.x - sn * e.y, sn * e.x + c * e.y, z);
  if (bulge > 0.5) q.z += sin(phase * 1.7 + rand1(s * 5.3) * 20.0) * 0.08 * (1.0 - a);
  vec3 np = q * size;
  p.velocity = (np - p.position) / max(dt, 1e-4);
  p.position = np;
}

// old yellow stars in the middle, young blue ones out on the arms, a few rosy ones
vec4 look(Particle p) {
  float s = p.seed;
  float bulge = step(rand1(s * 1.7 + 9.0), 0.14);
  float r = length(p.position.xy) / size;
  float rnd = rand1(s * 6.1 + 21.0);
  vec3 old = vec3(1.0, 0.78, 0.5), young = vec3(0.55, 0.7, 1.0), rosy = vec3(1.0, 0.55, 0.75);
  vec3 c = bulge > 0.5 ? old * 0.12 : mix(mix(old, young, smoothstep(0.15, 0.9, r)), rosy, step(0.985, rnd) * 0.9);
  // the middle is where most stars are: dim each one there, so their sum glows instead of clipping
  c *= (0.04 + 0.96 * smoothstep(0.02, 0.85, r)) * (0.6 + 0.8 * rnd);
  return vec4(c, 1.0);
}
"""

FLOW = """\
// Flow: particles ride a curl-noise current (as in Myriad): crisp filaments that never clump.
// @param scale 0.55 0.1 3.0  "Size of the current's swirls"
// @param speed 1.9 0.1 6.0  "How fast particles ride the current"
// @param radius 2.2 0.5 8.0  "Size of the region they live in"

void spawn(inout Particle p) {
  p.position = randSphere(p.seed + uTime * 13.7) * radius * (0.9 + 0.2 * rand1(p.seed * 3.7));
  p.life = 4.0 + rand1(p.seed * 1.9 + uTime) * 5.0;
}

void update(inout Particle p, float dt) {
  vec3 q = p.position;
  vec3 flow = curlNoise(q * scale + vec3(0.0, 0.0, uTime * 0.05)) * 0.55
            - q * 0.04 * max(length(q) - radius * 1.1, 0.0);
  p.velocity = flow * speed;
  p.position += p.velocity * dt;
  if (length(p.position) > radius * 2.7) p.age = p.life;     // wandered off: respawn
}

vec4 look(Particle p) {
  float t = clamp(length(p.velocity) * 1.1, 0.0, 1.0);
  vec3 a = vec3(0.15, 0.25, 0.9), b = vec3(0.3, 0.95, 0.85), c = vec3(1.0, 0.95, 0.7);
  vec3 col = t < 0.5 ? mix(a, b, t * 2.0) : mix(b, c, t * 2.0 - 1.0);
  return vec4(col * smoothstep(0.0, 1.0, p.life - p.age), 1.0);
}
"""

ATTRACTOR = """\
// Attractor: the Aizawa strange attractor, integrated on the GPU (as in Myriad).
// @param size 2.1 0.5 8.0  "Size of the attractor"
// @param rate 0.45 0.05 2.0  "How fast particles move along it"

vec3 toWorld(vec3 q) { return (q - vec3(0.0, 0.0, 0.6)) * size; }
vec3 toAizawa(vec3 w) { return w / size + vec3(0.0, 0.0, 0.6); }

void spawn(inout Particle p) {
  p.position = toWorld((rand3(p.seed + uTime * 7.3) - 0.5) * 0.4 + vec3(0.1, 0.0, 0.0));
  p.life = 8.0 + rand1(p.seed * 2.9 + uTime) * 20.0;
}

void update(inout Particle p, float dt) {
  vec3 q = toAizawa(p.position);
  const float a = 0.95, b = 0.7, c = 0.6, d = 3.5, e = 0.25, f = 0.1;
  for (int k = 0; k < 3; k++) {
    vec3 dq = vec3((q.z - b) * q.x - d * q.y, d * q.x + (q.z - b) * q.y,
                   c + a * q.z - q.z * q.z * q.z / 3.0 - (q.x * q.x + q.y * q.y) * (1.0 + e * q.z) + f * q.z * q.x * q.x * q.x);
    q += dq * dt * rate;
  }
  vec3 np = toWorld(q);
  p.velocity = (np - p.position) / max(dt, 1e-4);
  p.position = np;
  if (length(q) > 4.0) p.age = p.life;
}

vec4 look(Particle p) {
  float t = clamp(length(p.velocity) / size * 0.35, 0.0, 1.0);
  vec3 a = vec3(0.6, 0.12, 0.45), b = vec3(1.0, 0.45, 0.25), c = vec3(1.0, 0.95, 0.8);
  return vec4(t < 0.5 ? mix(a, b, t * 2.0) : mix(b, c, t * 2.0 - 1.0), 1.0);
}
"""

# Settings a template starts with (anything not listed keeps the defaults)
TEMPLATE_SETTINGS = {
    "Galaxy": {"count": 1_000_000, "color_by": 'CODE', "gain": 0.28, "point_px": 1.0},
    "Flow": {"count": 1_000_000, "color_by": 'CODE', "gain": 0.3, "point_px": 1.0, "prewarm": 3.0},
    "Attractor": {"count": 600_000, "color_by": 'CODE', "gain": 0.3, "point_px": 1.0, "prewarm": 4.0},
    "Swirl": {"count": 200_000, "gain": 0.4},
    "Firefly Swarm": {"count": 2500, "color_by": 'CODE', "point_px": 3.0, "gain": 1.0},
    "Spark Ball": {"count": 100_000, "color_by": 'CODE', "gain": 0.5, "point_px": 1.5},
    "Fountain": {"count": 100_000, "blend": 'SOLID', "point_px": 3.0, "color_by": 'AGE',
                 "color_a": (0.6, 0.85, 1.0), "color_b": (0.1, 0.3, 0.9), "gain": 1.0},
    "Castle": {"bounds_min": (-1.6, -1.6, -0.15), "bounds_max": (1.6, 1.6, 2.35), "resolution": 192},
    "Planet": {"bounds_min": (-1.45, -1.45, -1.45), "bounds_max": (1.45, 1.45, 1.45), "resolution": 160},
    "Saturn": {"bounds_min": (-2.2, -2.2, -1.2), "bounds_max": (2.2, 2.2, 1.2), "resolution": 160},
}

TEMPLATES = {
    'STAGE': {},                 # filled from stage_templates below
    'DEFORM': {},                # filled from deform.TEMPLATES below
    'MESH': {
        "Castle": CASTLE,
        "Planet": PLANET,
        "Saturn": SATURN,
        "Donut": """\
// A donut. sdf(p) is the distance to the surface: negative inside, positive outside (metres).
// Each @param line below becomes an input on the node.
// @param major 0.8 0.2 2.0  "Radius from the centre to the middle of the tube"
// @param minor 0.3 0.05 1.0  "Radius of the tube"
float sdf(vec3 p) {
  return sdTorus(p, major, minor);
}
""",
        "Rounded Box": """\
// A box with rounded edges.
// @param size 0.8 0.1 2.0  "Half the box's width"
// @param roundness 0.15 0.0 0.5  "Radius of the rounded edges"
float sdf(vec3 p) {
  return sdRoundBox(p, vec3(size), min(roundness, size));
}
""",
        "Gyroid Ball": """\
// A sphere carved into a gyroid lattice.
// @param radius 1.0 0.2 2.0  "Radius of the ball"
// @param cells 6.0 1.0 16.0  "Number of lattice cells across it"
// @param thickness 0.05 0.01 0.3  "Thickness of the lattice walls"
float sdf(vec3 p) {
  vec3 q = p * cells;
  float g = abs(dot(sin(q), cos(q.yzx))) / cells - thickness;
  return max(sdSphere(p, radius), g);
}
""",
        "Blob": sdf_code.TEMPLATE,
    },
    'SHAPE': {
        "Desk Lamp": None,          # filled in from shapes.TEMPLATE at first use
        "Vase": """\
# A vase: one curved profile, spun around the vertical axis.
param height  0.40  0.10 1.00
param belly   0.12  0.03 0.40
param neck    0.05  0.02 0.20

part body
  profile
    move 0, 0
    line belly * 0.7, 0
    curve x = belly * 0.7 + (neck - belly * 0.7) * t + belly * 0.5 * sin(t * pi)   y = height * t   steps 24
    line 0, height
    close
  revolve segments 64
""",
    },
    'PARTICLES': {
        "Galaxy": GALAXY,
        "Flow": FLOW,
        "Attractor": ATTRACTOR,
        "Swirl": None,              # particles.TEMPLATE
        "Firefly Swarm": None,      # stage_templates.FIREFLY_SWARM
        "Spark Ball": None,         # stage_templates.SPARK_BALL
        "Fountain": """\
// A fountain: particles shoot up from the origin and fall back under gravity.
// @param power 4.0 0.5 10.0  "Launch speed upwards"
// @param gravity 9.8 0.0 20.0  "Pull back down (9.8 = Earth)"
void spawn(inout Particle p) {
  vec3 r = rand3(p.seed);
  p.position = vec3(0.0);
  p.velocity = vec3((r.x - 0.5) * 1.2, (r.y - 0.5) * 1.2, power * (0.8 + 0.4 * r.z));
  p.life = 1.5 + rand1(p.seed * 7.1);
}
void update(inout Particle p, float dt) {
  p.velocity.z -= gravity * dt;
  p.position += p.velocity * dt;
}
""",
    },
}
DEFAULT_TEMPLATE = {'MESH': "Donut", 'SHAPE': "Desk Lamp", 'PARTICLES': "Galaxy", 'DEFORM': "Wave",
                    'STAGE': "Wander"}


def _fill_deform_templates():
    from . import stage_templates
    from .deform import TEMPLATES as DT
    TEMPLATES['DEFORM'].update(DT)
    TEMPLATES['STAGE'].update(stage_templates.STAGES)
    TEMPLATES['PARTICLES']["Firefly Swarm"] = stage_templates.FIREFLY_SWARM
    TEMPLATES['PARTICLES']["Spark Ball"] = stage_templates.SPARK_BALL


_fill_deform_templates()


def apply_template_settings(obj, key):
    """Give a new source the settings its template was designed with (counts, colours, bounds)."""
    s = obj.codenodes
    for name, value in TEMPLATE_SETTINGS.get(key or "", {}).items():
        try:
            setattr(s, name, value)
        except (AttributeError, TypeError, ValueError):
            pass


def template(kind, key=None):
    key = key or DEFAULT_TEMPLATE[kind]
    src = TEMPLATES[kind].get(key)
    if src is None and kind == 'SHAPE':
        from .shapes import TEMPLATE as src
    elif src is None and kind == 'PARTICLES':
        from .particles import TEMPLATE as src
    if src is None:
        raise KeyError(f"no {kind} template called '{key}'")
    return src


# ---- finding things ----------------------------------------------------------------------

def is_code_group(group):
    return group is not None and getattr(group, "bl_idname", "") == "GeometryNodeTree" and TAG in group


def is_make_real(group):
    return group is not None and getattr(group, "bl_idname", "") == "GeometryNodeTree" and MAKE_REAL in group


def is_gpu_group(group):
    obj = source_of(group)
    return obj is not None and obj.codenodes.kind in GPU_KINDS


def source_of(group):
    """The hidden object a code group reads, or None."""
    if not is_code_group(group):
        return None
    return bpy.data.objects.get(group[TAG])


def code_node(context):
    """The active node in the Node Editor, if it's a code node: (node, group, source)."""
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None)
    node = tree.nodes.active if tree is not None else None
    if node is None or node.type != 'GROUP' or not is_code_group(node.node_tree):
        return None, None, None
    return node, node.node_tree, source_of(node.node_tree)


def users():
    """{group name: [('node', tree, node) | ('mod', object, modifier)]} for every code group in use."""
    found = {}
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "GeometryNodeTree" or TAP in tree:
            continue
        for node in tree.nodes:
            if node.type == 'GROUP' and is_code_group(node.node_tree):
                found.setdefault(node.node_tree.name, []).append(('node', tree, node))
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == 'NODES' and is_code_group(mod.node_group):
                found.setdefault(mod.node_group.name, []).append(('mod', obj, mod))
    return found


# ---- making them -------------------------------------------------------------------------

def sources_collection(scene=None):
    scene = scene or bpy.context.scene
    col = bpy.data.collections.get(SOURCES)
    if col is None:
        col = bpy.data.collections.new(SOURCES)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    col.hide_viewport = True          # still evaluated for Object Info, never drawn or rendered itself
    col.hide_render = True
    _hide_from_outliner(col, scene)
    return col


def _hide_from_outliner(col, scene):
    """Keep the helpers out of the way: the collection is excluded from every view layer (Object Info
    still pulls in the objects it reads) and shown last, collapsed."""
    for vl in scene.view_layers:
        lc = _find_layer_collection(vl.layer_collection, col.name)
        if lc is not None and not lc.exclude:
            lc.exclude = True


def _find_layer_collection(lc, name):
    if lc.collection.name == name:
        return lc
    for child in lc.children:
        got = _find_layer_collection(child, name)
        if got is not None:
            return got
    return None


def _unique(name, pool):
    if name not in pool:
        return name
    n = 2
    while f"{name} {n}" in pool:
        n += 1
    return f"{name} {n}"


def _move_to_sources(obj):
    col = sources_collection()
    for c in list(obj.users_collection):
        if c != col:
            c.objects.unlink(obj)
    if obj.name not in col.objects:
        col.objects.link(obj)
    obj.location = (0.0, 0.0, 0.0)


def make_source(kind, source, label, key=None):
    """Build a hidden code source object from `source`. (object, error or "").

    GPU kinds (particles, SDF surfaces) start as live-only: nothing is simulated or meshed into
    Blender until a To Geometry node asks for it."""
    from . import api
    name = _unique(f"CN · {label}", bpy.data.objects)
    if kind in CODE_KINDS:
        # the text and settings, without running it: live drawing and To Geometry run it on demand
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(obj)
        s = obj.codenodes
        ext = {'PARTICLES': 'particles', 'DEFORM': 'vertex', 'STAGE': 'stage'}.get(kind, 'sdf')
        text = bpy.data.texts.new(f"{name}.{ext}")
        text.from_string(source)
        s.kind = kind
        s.template_key = key or ""
        s.real_mode = "NONE"
        s.text = text
        s.enabled = True
        apply_template_settings(obj, key)
        err = ""
        try:
            from . import props
            props.sync_params(s, source)
        except Exception as exc:              # bad @param lines: keep the source, report the problem
            err = str(exc)
        s.last_error = err
        _move_to_sources(obj)
        return obj, err
    if kind == 'SHAPE':
        r = api.code_to_shape(source, name=name)
    else:
        r = api.code_to_mesh(source, name=name)
    obj = bpy.data.objects.get(r.get("object") or name)
    if obj is None:                   # the code didn't even parse: make an empty source to fix later
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(obj)
        s = obj.codenodes
        text = bpy.data.texts.new(f"{name}.{'shape' if kind == 'SHAPE' else 'sdf'}")
        text.from_string(source)
        s.enabled, s.text = True, text
        s.kind = kind
        s.last_error = r.get("error") or ""
    obj.codenodes.template_key = key or ""
    _move_to_sources(obj)
    return obj, r.get("error") or ""


def wanted_inputs(obj):
    """[(name, socket type, default, min, max)] the group should expose for this source."""
    from . import gn_sockets
    return [(w[1], w[2], w[3], w[4], w[5]) for w in gn_sockets.spec(obj)]


def sync_interface(group, obj):
    """Make the group's inputs match the code and its settings. Returns True if anything changed."""
    from . import gn_sockets
    return gn_sockets.sync_interface(group, obj)


def build_group(obj, label):
    """A node group whose Geometry output is `obj`'s code result."""
    group = bpy.data.node_groups.new(_unique(PREFIX + label, bpy.data.node_groups), "GeometryNodeTree")
    group[TAG] = obj.name
    group[KIND_KEY] = obj.codenodes.kind
    group.description = ("CodeNodes: geometry made by code. Select the node and open the "
                         "Node Editor sidebar (N) › CodeNodes to edit the code")
    if hasattr(group, "color_tag"):
        try:
            group.color_tag = 'SCRIPT'
        except TypeError:
            pass
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    sync_interface(group, obj)
    nodes = group.nodes
    gin, gout = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-400, 0), (150, 0)
    if obj.codenodes.kind in CODE_KINDS:
        # A GPU node's result lives on the GPU and is drawn live; its outputs stay empty until a
        # To Geometry node downstream turns it into real geometry. Its code declares its sockets.
        group.description = ("CodeNodes GPU node: drawn live in the viewport. Add To Geometry after it to use "
                             "it in nodes or renders. Switch on ✎ Edit Code to open its code")
        sync_interface(group, obj)
        return group
    info = nodes.new("GeometryNodeObjectInfo")
    info.name = info.label = "Code Result"
    info.transform_space = 'ORIGINAL'
    info.inputs["Object"].default_value = obj
    group.links.new(info.outputs["Geometry"], gout.inputs["Geometry"])
    info.location = (-150, 0)
    return group


def create(kind, key=None, label=None, source=None):
    """A new code group and its source. Returns (group, error or "")."""
    source = source if source is not None else template(kind, key)
    label = label or key or DEFAULT_TEMPLATE[kind]
    obj, err = make_source(kind, source, label, key or (DEFAULT_TEMPLATE[kind] if source is None else None))
    return build_group(obj, label), err


# ---- To Geometry ---------------------------------------------------------------------------------

def build_make_real():
    """A To Geometry node group: its input is a GPU node's result, its output real geometry.

    Inside: Object Info reads the upstream GPU node's source object, which the add-on fills with
    real points or a mesh; a Join passes through anything that is already real (e.g. after Make
    Native), so the node is harmless on ordinary geometry.
    """
    group = bpy.data.node_groups.new(_unique(MAKE_REAL_NAME, bpy.data.node_groups), "GeometryNodeTree")
    group[MAKE_REAL] = True
    group.description = ("CodeNodes: turns the GPU node (or chain) before it into real geometry: points with "
                         "every per-particle attribute, or a mesh. Later nodes and renders can use it, like "
                         "after Realize Instances")
    if hasattr(group, "color_tag"):
        try:
            group.color_tag = 'GEOMETRY'
        except TypeError:
            pass
    iface = group.interface
    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes = group.nodes
    gin, gout = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.name = info.label = "Real Result"
    info.transform_space = 'ORIGINAL'
    join = nodes.new("GeometryNodeJoinGeometry")
    group.links.new(gin.outputs["Geometry"], join.inputs[0])
    group.links.new(info.outputs["Geometry"], join.inputs[0])
    group.links.new(join.outputs[0], gout.inputs["Geometry"])
    gin.location, info.location, join.location, gout.location = (-420, 80), (-420, -120), (-120, 0), (120, 0)
    return group


REAL_INPUTS = {'MESH': ("Resolution", 8, 512), 'PARTICLES': ("Max Points", 0, 16_777_216)}


def sync_real_interface(group, kind, src=None):
    """To Geometry's inputs for the kind of GPU node feeding it (When, Resolution / Max Points, ...)."""
    from . import gn_sockets
    gn_sockets.sync_real_interface(group, kind, src)


def real_info_node(group):
    return next((n for n in group.nodes if n.type == 'OBJECT_INFO'), None)


def _feeding(tree, sock):
    return next((l for l in tree.links if l.to_socket == sock and not l.is_muted), None)


def upstream_code_node(tree, node, depth=0):
    """The code node at the head of whatever feeds a To Geometry node's first input: back through
    reroutes and through GPU Stage nodes to the chain's source. None if no code node feeds it."""
    if depth > 64 or not node.inputs:
        return None
    link = _feeding(tree, node.inputs[0])
    while link is not None and depth <= 64:
        depth += 1
        src = link.from_node
        if src.type == 'REROUTE':
            link = _feeding(tree, src.inputs[0])
            continue
        if src.type != 'GROUP' or not is_code_group(src.node_tree):
            return None
        obj = source_of(src.node_tree)
        if obj is not None and obj.codenodes.kind == 'STAGE':
            from . import links
            if obj.name in links.MESH_HEADS:
                return src
            stream = src.inputs.get(link.from_socket.name) or src.inputs.get("Particles") or src.inputs.get("Mesh")
            if stream is None:
                return None
            nxt = _feeding(tree, stream)
            if nxt is None:
                return src
            link = nxt
            continue
        return src
    return None


def _stream_out(node):
    """A code node's stream output (Particles, Mesh or Geometry)."""
    for name in ("Particles", "Mesh", "Geometry"):
        out = node.outputs.get(name)
        if out is not None:
            return out
    return node.outputs[0] if node.outputs else None


def insert_make_real(tree, code_node_, location=None):
    """Put a To Geometry node right after a code node, taking over its outgoing links."""
    group = build_make_real()
    node = tree.nodes.new("GeometryNodeGroup")
    node.node_tree = group
    node.location = location or (code_node_.location.x + code_node_.width + 60, code_node_.location.y)
    node.width = 160
    out = _stream_out(code_node_)
    src = source_of(code_node_.node_tree)
    head = upstream_code_node_from(tree, code_node_)
    head_src = source_of(head.node_tree) if head is not None else src
    from . import links as _links
    kind = _links.ekind(head_src) if head_src is not None else None
    sync_real_interface(group, kind if kind in GPU_KINDS else None, head_src)
    targets = [l.to_socket for l in tree.links if l.from_socket == out]
    for l in [l for l in tree.links if l.from_socket == out]:
        tree.links.remove(l)
    tree.links.new(out, node.inputs[0])
    for sock in targets:
        try:
            tree.links.new(node.outputs["Geometry"], sock)
        except RuntimeError:
            pass
    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node
    _dirty[0] = True
    return node


def upstream_code_node_from(tree, code_node_):
    """The head of the chain a code node belongs to (itself if it is a head)."""
    obj = source_of(code_node_.node_tree)
    if obj is None or obj.codenodes.kind != 'STAGE':
        return code_node_
    from . import links
    if obj.name in links.MESH_HEADS:
        return code_node_
    stream = code_node_.inputs.get("Particles") or code_node_.inputs.get("Mesh")
    link = _feeding(tree, stream) if stream is not None else None
    if link is None:
        return code_node_

    class _Probe:                       # a stand-in whose first input is that stream
        inputs = [stream]
    return upstream_code_node(tree, _Probe()) or code_node_


def make_real_node(context):
    """The active node in the Node Editor if it's a To Geometry node: (node, group, tree)."""
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None)
    node = tree.nodes.active if tree is not None else None
    if node is None or node.type != 'GROUP' or not is_make_real(node.node_tree):
        return None, None, None
    return node, node.node_tree, tree


def resolve_when(when, src):
    """AUTO -> EVERY_FRAME for particles and code that reads the time, ON_CHANGE otherwise."""
    if when != 'AUTO':
        return when
    s = src.codenodes
    if s.kind == 'PARTICLES':
        return 'EVERY_FRAME'
    code = s.text.as_string() if s.text is not None else ""
    return 'EVERY_FRAME' if ("uTime" in code or "uFrame" in code) else 'ON_CHANGE'


_RANK = {'NONE': 0, 'RENDER_ONLY': 1, 'ON_CHANGE': 2, 'EVERY_FRAME': 3}


def ensure_tree(context):
    """The tree the Node Editor is editing; if it's empty, give the active mesh a Geometry Nodes
    modifier with a fresh tree (like the editor's New button). None if there's nothing to use."""
    space = context.space_data
    tree = getattr(space, "edit_tree", None)
    if tree is not None:
        return tree
    obj = context.active_object
    if obj is None or obj.type not in {'MESH', 'CURVE', 'POINTCLOUD', 'CURVES'}:
        return None
    tree = bpy.data.node_groups.new("Geometry Nodes", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-300, 0), (300, 0)
    tree.links.new(gin.outputs[0], gout.inputs[0])
    mod = obj.modifiers.new("GeometryNodes", 'NODES')
    mod.node_group = tree
    try:
        space.node_tree = tree
    except (AttributeError, TypeError):
        pass
    return tree


HOST = "codenodes_host"          # on a Geometry Nodes tree made by add_object


def add_object(kind, key=None, source=None, label=None, location=(0.0, 0.0, 0.0), collection=None,
               make_real=False):
    """A new object whose geometry comes from a code node in its own Geometry Nodes tree.

    What Add › Mesh › CodeNodes makes. A GPU node is wired straight to the output and drawn live;
    `make_real` puts a To Geometry node between them so the object gets real geometry.
    Returns (object, tree, node, error or "").
    """
    label = label or key or DEFAULT_TEMPLATE[kind]
    group, err = create(kind, key, label, source)
    name = _unique(f"Code {label}", bpy.data.objects)
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    if collection is None:
        collection = getattr(bpy.context, "collection", None) or bpy.context.scene.collection
    collection.objects.link(obj)
    obj.location = location
    tree = bpy.data.node_groups.new(_unique(name, bpy.data.node_groups), "GeometryNodeTree")
    tree[HOST] = group.name
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-420, 0), (320, 0)
    node = insert(tree, group, (-200, 60))
    out = _stream_out(node)
    if out is not None and out.bl_idname == "NodeSocketGeometry":
        tree.links.new(out, gout.inputs["Geometry"])
    # particles travel on a Particles (bundle) socket: they are drawn live, and reach the geometry
    # output only through To Geometry
    if kind == 'DEFORM':
        # something for the vertex code to work on: a smooth sphere, wired into the Mesh input
        import bmesh
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=96, v_segments=48, radius=1.0)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.shade_smooth()
        tree.links.new(gin.outputs["Geometry"], node.inputs["Mesh"])
    mod = obj.modifiers.new("CodeNodes", 'NODES')
    mod.node_group = tree
    if make_real and kind in GPU_KINDS:
        real = insert_make_real(tree, node)
        if not real.outputs["Geometry"].is_linked:
            tree.links.new(real.outputs["Geometry"], gout.inputs["Geometry"])
        for n in tree.nodes:
            n.select = False
        node.select = True
        tree.nodes.active = node
    sync()
    return obj, tree, node, err


def host_code_nodes(obj):
    """[(tree, node)] for every code node in `obj`'s Geometry Nodes modifiers."""
    out = []
    for mod in getattr(obj, "modifiers", ()):
        if mod.type != 'NODES' or mod.node_group is None:
            continue
        for node in mod.node_group.nodes:
            if node.type == 'GROUP' and is_code_group(node.node_tree):
                out.append((mod.node_group, node))
    return out


def find_user(group):
    """(tree, node) of the first Group node using a code group, or (None, None)."""
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "GeometryNodeTree":
            continue
        for node in tree.nodes:
            if node.type == 'GROUP' and node.node_tree == group:
                return tree, node
    return None, None


def insert(tree, group, location=(0.0, 0.0)):
    node = tree.nodes.new("GeometryNodeGroup")
    node.node_tree = group
    node.location = location
    node.width = 180
    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node
    return node


# ---- keeping them in sync ----------------------------------------------------------------

def _host_trees_value(tree, socket_identifier):
    """A host tree's Group Input value, read from the first modifier using the tree."""
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == 'NODES' and mod.node_group == tree:
                try:
                    return mod[socket_identifier]
                except KeyError:
                    return None
    return None


def resolve(tree, socket, depth=0):
    """A group node input's value, following simple links. None when it can't be known."""
    if not socket.is_linked:
        return getattr(socket, "default_value", None)
    if depth > 16:
        return None
    link = next((l for l in tree.links if l.to_socket == socket and not l.is_muted), None)
    if link is None:
        return getattr(socket, "default_value", None)
    src_node, out = link.from_node, link.from_socket
    if src_node.type == 'REROUTE':
        return resolve(tree, src_node.inputs[0], depth + 1)
    if src_node.type == 'VALUE':
        return out.default_value
    if src_node.bl_idname in ("FunctionNodeInputInt", "FunctionNodeInputFloat"):
        return getattr(src_node, "integer", None) if src_node.bl_idname == "FunctionNodeInputInt" \
            else getattr(src_node, "value", None)
    if src_node.type == 'GROUP_INPUT':
        return _host_trees_value(tree, out.identifier)
    return None                        # computed by other nodes: not readable from Python


def read_values(user):
    """{input name: value} from a group node or modifier, for inputs whose value is known."""
    kind, owner, thing = user
    values = {}
    if kind == 'node':
        for sock in thing.inputs:
            v = resolve(owner, sock)
            if v is not None:
                values[sock.name] = v
    else:
        for item in thing.node_group.interface.items_tree:
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT':
                try:
                    values[item.name] = thing[item.identifier]
                except KeyError:
                    pass
    return values


def apply_values(obj, values, user=None):
    """Write known input values onto the source's settings and sliders. True if something changed."""
    from . import gn_sockets
    return gn_sockets.apply(obj, values, user)


def split(group, user):
    """Give a duplicated node its own group, source object and code text."""
    obj = source_of(group)
    kind, owner, thing = user
    new_obj = obj.copy()
    new_obj.data = obj.data.copy()
    new_obj.name = _unique(obj.name, bpy.data.objects)
    s = new_obj.codenodes
    if obj.codenodes.text is not None:
        old = obj.codenodes.text
        text = bpy.data.texts.new(_unique(old.name, bpy.data.texts))
        text.from_string(old.as_string())
        s.text = text
    sources_collection().objects.link(new_obj)
    new_group = group.copy()
    new_group.name = _unique(group.name, bpy.data.node_groups)
    new_group[TAG] = new_obj.name
    info = next((n for n in new_group.nodes if n.type == 'OBJECT_INFO'), None)
    if info is not None:
        info.inputs["Object"].default_value = new_obj
    if kind == 'node':
        values = {sock.identifier: getattr(sock, "default_value", None) for sock in thing.inputs}
        thing.node_tree = new_group
        for sock in thing.inputs:              # keep what was typed on the copy
            if values.get(sock.identifier) is not None:
                sock.default_value = values[sock.identifier]
    else:
        thing.node_group = new_group
    live.request(new_obj)
    return new_group


def _hosts_of(tree):
    return [o for o in bpy.data.objects
            if any(m.type == 'NODES' and m.node_group == tree for m in getattr(o, "modifiers", ()))]


def _migrate_gpu_group(group):
    """Older files: a GPU group that still hands its source straight to its output. Now the output
    stays empty and To Geometry carries the result."""
    for link in list(group.links):
        if link.from_node.type == 'OBJECT_INFO' and link.from_node.name not in ("Tap",)                 and link.to_node.type in ('GROUP_OUTPUT', 'JOIN_GEOMETRY') and link.from_node.name != "CodeNodes render link":
            group.links.remove(link)


def _code_nodes(tree):
    out = []
    for node in tree.nodes:
        if node.type == 'GROUP' and is_code_group(node.node_tree):
            obj = source_of(node.node_tree)
            if obj is not None:
                out.append((node, obj))
    return out


def _next_stage(tree, node, out_name, in_name):
    """The GPU Stage node wired to `node`'s `out_name` output through its `in_name` input, or None."""
    out = node.outputs.get(out_name)
    if out is None:
        return None
    for l in tree.links:
        if l.from_socket != out or l.is_muted:
            continue
        to = l.to_node
        while to.type == 'REROUTE':
            nxt = next((m for m in tree.links if m.from_node == to and not m.is_muted), None)
            if nxt is None:
                break
            l, to = nxt, nxt.to_node
        if to.type == 'GROUP' and is_code_group(to.node_tree) and l.to_socket.name == in_name:
            obj = source_of(to.node_tree)
            if obj is not None and obj.codenodes.kind == 'STAGE':
                return to
    return None


def _targets(tree, sock):
    """[(node, input socket)] that `sock` feeds, followed through reroutes (muted links skipped)."""
    out, todo, seen = [], [l for l in tree.links if l.from_socket == sock and not l.is_muted], set()
    while todo:
        l = todo.pop()
        to = l.to_node
        if to.type == 'REROUTE':
            if to.name in seen:
                continue
            seen.add(to.name)
            todo.extend(m for m in tree.links if m.from_node == to and not m.is_muted)
            continue
        out.append((to, l.to_socket))
    return out


def is_join(group):
    return group is not None and JOIN in group


def is_cache(group):
    return group is not None and CACHE in group


def build_join():
    """A Join Particles node group: two particle streams in, one out. The stages after it run on
    both (each source keeps its own simulation; they're drawn and made real together)."""
    group = bpy.data.node_groups.new(_unique("Join Particles", bpy.data.node_groups), "GeometryNodeTree")
    group[JOIN] = True
    group.description = ("CodeNodes: merges two particle streams. Everything wired after it applies to both, "
                         "and a To Geometry after it outputs both")
    if hasattr(group, "color_tag"):
        try:
            group.color_tag = 'GEOMETRY'
        except TypeError:
            pass
    iface = group.interface
    for name in ("Particles A", "Particles B"):
        iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketBundle")
    iface.new_socket("Particles", in_out="OUTPUT", socket_type="NodeSocketBundle")
    gin, gout = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-300, 0), (200, 0)
    from . import gn_sockets
    gn_sockets.apply_tips(group, gn_sockets.JOIN_TIPS, gn_sockets.JOIN_TIPS_OUT)
    return group


def _stream_names(obj):
    """(output a code node's stream leaves by, input a stage takes it by, pipeline kind)."""
    kind = obj.codenodes.kind
    if kind == 'PARTICLES':
        return "Particles", "Particles", 'PARTICLES'
    if kind == 'DEFORM':
        return "Geometry", "Mesh", 'DEFORM'
    return "Mesh", "Mesh", 'DEFORM'


def find_pipelines(tree):
    """The code-node graph of one tree, as pipelines.

    Returns (pipes, mesh_heads, terminals):
      pipes       {(head name, (stage names...)): {"kind", "funcs", "leaf": bool, "cache": node name or None}}
      mesh_heads  stage sources that head a mesh chain (a warp or deform wired to plain geometry)
      terminals   {To Geometry node name: [pipeline keys it outputs]}

    A pipeline is every path from a source through stages (and Join Particles / GPU Cache nodes, which
    pass the stream on) to where it ends: a node nothing continues from (drawn live), or a node wired
    into a To Geometry (made real there). A stream wired into two stages splits into two pipelines.
    """
    from . import gn_sockets
    nodes = _code_nodes(tree)
    by_node = {n.name: o for n, o in nodes}
    funcs = {}
    for node, obj in nodes:
        for sock in node.inputs:
            if sock.bl_idname != "NodeSocketClosure" or not sock.is_linked:
                continue
            l = _feeding(tree, sock)
            while l is not None and l.from_node.type == 'REROUTE':
                l = _feeding(tree, l.from_node.inputs[0])
            if l is None or l.from_node.name not in by_node:
                continue
            funcs[(obj.name, sock.name)] = (by_node[l.from_node.name].name, l.from_socket.name)

    def reachable(names):
        todo, seen, out = list(names), set(), {}
        while todo:
            c = todo.pop()
            if c in seen:
                continue
            seen.add(c)
            for (cons, inp), prov in funcs.items():
                if cons == c:
                    out[(cons, inp)] = prov
                    todo.append(prov[0])
        return out

    pipes, terminals, mesh_heads = {}, {}, set()

    def record(key, kind, leaf, cache):
        info = pipes.get(key)
        if info is None:
            info = pipes[key] = {"kind": kind, "funcs": reachable([key[0], *key[1]]), "leaf": False,
                                 "cache": None}
        info["leaf"] = info["leaf"] or leaf
        if cache is not None and info["cache"] is None:
            info["cache"] = cache

    def explore(node, out_name, in_name, kind, head, path, cache, depth):
        if depth > 64:
            return
        out = node.outputs.get(out_name)
        children, ends = [], []
        for to, sock in (_targets(tree, out) if out is not None else []):
            g = to.node_tree if to.type == 'GROUP' else None
            if g is None:
                continue
            if is_code_group(g):
                obj = by_node.get(to.name)
                if obj is not None and obj.codenodes.kind == 'STAGE' and sock.name == in_name \
                        and obj.name not in path:
                    children.append((to, path + (obj.name,), cache))
            elif (is_join(g) or is_cache(g)) and sock.bl_idname == "NodeSocketBundle" and kind == 'PARTICLES':
                children.append((to, path, f"{tree.name}\x00{to.name}" if is_cache(g) else cache))
            elif is_make_real(g):
                ends.append(to.name)
        key = (head, path)
        if ends or not children:
            record(key, kind, not children, cache)
        for t in ends:
            lst = terminals.setdefault(t, [])
            if key not in lst:
                lst.append(key)
        for child, p, c in children:
            explore(child, in_name, in_name, kind, head, p, c, depth + 1)

    for node, obj in nodes:
        kind = obj.codenodes.kind
        if kind in ('PARTICLES', 'DEFORM'):
            out_name, in_name, k = _stream_names(obj)
            explore(node, out_name, in_name, k, obj.name, (), None, 0)
        elif kind == 'STAGE' and gn_sockets.decls_of(obj).takes_mesh():
            mesh_in = node.inputs.get("Mesh")
            l = _feeding(tree, mesh_in) if mesh_in is not None else None
            while l is not None and l.from_node.type == 'REROUTE':
                l = _feeding(tree, l.from_node.inputs[0])
            if l is None:
                continue
            up = by_node.get(l.from_node.name)
            if up is not None and up.codenodes.kind in ('DEFORM', 'STAGE'):
                continue                         # part of a mesh chain started further up
            # a warp or deform wired straight to ordinary geometry: it heads its own mesh chain
            mesh_heads.add(obj.name)
            explore(node, "Mesh", "Mesh", 'DEFORM', obj.name, (), None, 0)
    return pipes, mesh_heads, terminals


def find_chains(tree):
    """{head source name: chain info} for the main pipeline of each head (older callers)."""
    pipes, mesh_heads, _t = find_pipelines(tree)
    chains = {}
    for (head, stages), info in sorted(pipes.items(), key=_pipe_order):
        if head not in chains:
            chains[head] = {"stages": list(stages), "kind": info["kind"], "funcs": info["funcs"]}
    return chains, mesh_heads


def _pipe_order(kv):
    """Which of a head's pipelines is its main one: drawn-live ends first, then the longest path, then
    by name (stable while you wire)."""
    (head, stages), info = kv
    return (head, 0 if info["leaf"] else 1, -len(stages), stages)


def _branch_label(stages):
    return (stages[-1].replace(PREFIX, "").replace("CN · ", "") if stages else "source")[:28]


def branch_object(head, stages):
    """The hidden object standing for one extra branch of a head (created on first use)."""
    pkey = head.name + "|" + ">".join(stages)
    col = sources_collection()
    for o in col.objects:
        if o.get(PIPE_KEY) == pkey:
            return o
    name = _unique(f"{head.name} › {_branch_label(stages)}", bpy.data.objects)
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    col.objects.link(obj)
    obj[PIPE_KEY] = pkey
    obj[PIPE_HEAD] = head.name
    mirror_settings(head, obj)
    return obj


_MIRROR = ("kind", "count", "text", "template_key", "color_by", "color_a", "color_b", "speed_range", "blend",
           "point_px", "gain", "prewarm", "emitter", "quality", "surface_color", "shadows", "ao", "fog", "sky",
           "substeps", "point_radius", "stagger", "resolution", "bounds_min", "bounds_max", "live", "animate",
           "smooth")


def mirror_settings(head, branch):
    """A branch runs with its head's settings: copy only what differs (setting unchanged values would
    queue needless rebuilds)."""
    hs, bs = head.codenodes, branch.codenodes
    if not bs.enabled:
        bs.enabled = True
    for name in _MIRROR:
        a = getattr(hs, name)
        b = getattr(bs, name)
        same = (tuple(a) == tuple(b)) if hasattr(a, "__len__") and not isinstance(a, str) else a == b
        if not same:
            setattr(bs, name, a)
    have = {p.name: p for p in bs.params}
    for p in hs.params:
        q = have.get(p.name)
        if q is None:
            q = bs.params.add()
            q.name = p.name
        if q.value != p.value:
            q.value = p.value


def is_branch_object(obj):
    return obj is not None and PIPE_KEY in obj


def _migrate_make_real_names():
    """Files from before 0.3 call the node Make Real."""
    for g in bpy.data.node_groups:
        if MAKE_REAL in g and g.name.startswith("Make Real"):
            g.name = _unique(MAKE_REAL_NAME + g.name[len("Make Real"):], bpy.data.node_groups)


def _set_real_objects(group, objs):
    """Point a To Geometry group's Object Info node(s) at `objs` (one per pipeline it outputs; a To
    Geometry after a Join Particles outputs several)."""
    infos = sorted((n for n in group.nodes if n.type == 'OBJECT_INFO'), key=lambda n: n.name)
    join = next((n for n in group.nodes if n.type == 'JOIN_GEOMETRY'), None)
    want = max(1, len(objs))
    while len(infos) < want and join is not None:
        info = group.nodes.new("GeometryNodeObjectInfo")
        info.name = info.label = f"Real Result {len(infos) + 1}"
        info.transform_space = 'ORIGINAL'
        info.location = (-420, -120 - 180 * len(infos))
        group.links.new(info.outputs["Geometry"], join.inputs[0])
        infos.append(info)
    for extra in infos[want:]:
        group.nodes.remove(extra)
    for info, obj in zip(infos, list(objs) + [None] * want):
        if info.inputs["Object"].default_value != obj:
            info.inputs["Object"].default_value = obj


def _sync_caches(trees, chains):
    """Every GPU Cache node: the simulations passing through it, their mode and range, its buttons."""
    from . import gpu_cache, gpu_live
    seen = set()
    for tree in trees:
        for node in tree.nodes:
            if node.type != 'GROUP' or not is_cache(node.node_tree):
                continue
            key = f"{tree.name}\x00{node.name}"
            owners, names = [], set()
            for name, info in chains.items():
                if info.get("cache") != key:
                    continue
                obj = bpy.data.objects.get(name)
                if obj is None:
                    continue
                try:
                    owner = gpu_live.sim_owner(obj)[0]
                except Exception:
                    owner = obj
                if owner.name not in names:
                    names.add(owner.name)
                    owners.append(owner)
            gpu_cache.sync(tree, node, owners)
            seen |= names
    gpu_cache.forget_missing(seen)


def sync_make_real():
    """Find the code-node pipelines, point every To Geometry node at the pipelines ending at it, and
    work out for each pipeline whether (and when) it is made real. Returns {pipeline name: [host names]}."""
    from . import gn_sockets, gpu_live, links
    _migrate_make_real_names()
    trees = [t for t in bpy.data.node_groups if t.bl_idname == "GeometryNodeTree" and TAP not in t]
    per_tree, all_pipes, all_mesh_heads = {}, {}, set()
    for tree in trees:
        pipes, mesh_heads, terminals = find_pipelines(tree)
        per_tree[tree.name] = terminals
        for key, info in pipes.items():
            if key in all_pipes:
                all_pipes[key]["leaf"] = all_pipes[key]["leaf"] or info["leaf"]
            else:
                all_pipes[key] = info
        all_mesh_heads |= mesh_heads
    # name the pipelines: a head's main pipeline is the head itself, every other one a branch object
    names, chains, used_branches = {}, {}, set()
    for key, info in sorted(all_pipes.items(), key=_pipe_order):
        head_name, stages = key
        head = bpy.data.objects.get(head_name)
        if head is None:
            continue
        if head_name not in chains:
            name = head_name
        else:
            b = branch_object(head, stages)
            mirror_settings(head, b)
            used_branches.add(b.name)
            name = b.name
        names[key] = name
        chains[name] = {"source": head_name, "stages": list(stages), "kind": info["kind"],
                        "funcs": info["funcs"], "cache": info.get("cache")}
    col = bpy.data.collections.get(SOURCES)
    for obj in (list(col.objects) if col is not None else []):
        if is_branch_object(obj) and obj.name not in used_branches:
            from . import particles
            particles.forget(obj.name)
            gpu_live.hosts.pop(obj.name, None)
            me = obj.data
            bpy.data.objects.remove(obj)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
    changed = links.set_chains(chains, all_mesh_heads)
    for name in changed:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            from . import particles
            particles.forget(name)
            if obj.codenodes.real_mode not in ("NONE", "RENDER_ONLY", "STANDALONE"):
                live.request(obj)
    if changed:
        gpu_live.redraw()
    modes, host_map, limits, real_nodes = {}, {}, {}, {}
    for tree in trees:
        hosts_ = None
        seen_real = set()
        terminals = per_tree.get(tree.name, {})
        for node in tree.nodes:
            if node.type != 'GROUP' or node.node_tree is None:
                continue
            group = node.node_tree
            if is_code_group(group):
                src = source_of(group)
                if src is not None and links.is_gpu(src) and (src.codenodes.kind != 'STAGE'
                                                              or src.name in all_mesh_heads):
                    hosts_ = _hosts_of(tree) if hosts_ is None else hosts_
                    lst = host_map.setdefault(src.name, [])
                    lst.extend(h.name for h in hosts_ if h.name not in lst)
                    if links.ekind(src) == 'DEFORM':
                        ensure_tap(src, tree, node, hosts_)
            elif is_make_real(group):
                if group.name in seen_real or (group.users > 1 and _other_real_user(group, tree, node)):
                    new = group.copy()                  # each To Geometry node keeps its own settings
                    new.name = _unique(MAKE_REAL_NAME, bpy.data.node_groups)
                    node.node_tree = new
                    group = new
                seen_real.add(group.name)
                objs = [bpy.data.objects.get(names[k]) for k in terminals.get(node.name, []) if k in names]
                objs = [o for o in objs if o is not None]
                if not objs:
                    up = upstream_code_node(tree, node)
                    src = source_of(up.node_tree) if up is not None else None
                    if src is not None and links.is_gpu(src):
                        objs = [src]
                    else:
                        src = None                      # a Code Shape is real already: pass it through
                _set_real_objects(group, objs)
                src = objs[0] if objs else None
                sync_real_interface(group, links.ekind(src) if src is not None else None, src)
                if src is None:
                    up = upstream_code_node(tree, node)
                    label = "To Geometry (nothing to convert)" if up is None else MAKE_REAL_NAME
                    if node.label != label:
                        node.label = label
                    continue
                for src in objs:
                    s = src.codenodes
                    real_nodes.setdefault(src.name, []).append(node)
                    want = REAL_INPUTS.get(links.ekind(src), (None,))[0]
                    sock = node.inputs.get(want) if want else None
                    val = resolve(tree, sock) if sock is not None else None
                    if val is not None:
                        limits.setdefault(src.name, int(val))
                    when_sock = node.inputs.get("When")
                    when_name = resolve(tree, when_sock) if when_sock is not None else None
                    mode = resolve_when(gn_sockets.WHEN_MAP.get(when_name or "Automatic", 'AUTO'), src)
                    if _RANK[mode] > _RANK.get(modes.get(src.name, 'NONE'), 0):
                        modes[src.name] = mode
                        for key, name in (("real_keep_vel", "Keep Velocity"), ("real_keep_age", "Keep Age")):
                            ks = node.inputs.get(name)
                            kv = resolve(tree, ks) if ks is not None else None
                            s[key] = True if kv is None else bool(kv)
    for name, info in chains.items():                  # a branch is shown where its head is
        if info["source"] != name and info["source"] in host_map:
            host_map[name] = list(host_map[info["source"]])
    _sync_caches(trees, chains)
    col = bpy.data.collections.get(SOURCES)
    for src in (list(col.objects) if col is not None else []):
        s = getattr(src, "codenodes", None)
        if s is None or not s.enabled or not links.is_gpu(src):
            if s is not None and s.enabled and s.kind == 'STAGE' and s.real_mode != "NONE":
                s.real_mode = "NONE"                 # a stage inside a chain is never made real on its own
            continue
        mode = modes.get(src.name, 'NONE')
        changed = False
        if s.real_mode != mode:
            s.real_mode = mode
            changed = True
        lim = limits.get(src.name)
        if lim is None and s.kind == 'PARTICLES' and s.real_limit and mode == 'NONE':
            s.real_limit = 0                           # no To Geometry any more: no Max Points either
        if lim is not None:
            if s.kind == 'MESH' and max(8, min(512, lim)) != s.resolution:
                s["resolution"] = max(8, min(512, lim))
                changed = True
            elif s.kind == 'PARTICLES' and lim != s.real_limit:
                s.real_limit = lim
                changed = True
        if changed:
            live.request(src)
            gpu_live.redraw()
        cost = _made_real_ms(s.stats)
        what = real_label(src)
        for node in real_nodes.get(src.name, []):
            if s.real_mode == 'RENDER_ONLY':
                label = f"{what} · for render"
            else:
                label = what + (f" · {cost} ms" if cost is not None else "")
            if node.label != label:
                node.label = label
    return host_map


def real_label(src):
    """What a To Geometry node's header says: what it outputs."""
    from . import links
    return "To Points" if links.ekind(src) == 'PARTICLES' else "To Mesh"


def _made_real_ms(stats):
    import re
    m = re.search(r"made real in (\d+) ms", stats or "")
    return int(m.group(1)) if m else None


def _other_real_user(group, tree, node):
    """True if another node (in any tree) uses this To Geometry group too (Shift D copies share it)."""
    for t in bpy.data.node_groups:
        if t.bl_idname != "GeometryNodeTree" or TAP in t:
            continue
        for n in t.nodes:
            if n != node and n.type == 'GROUP' and n.node_tree == group:
                return True
    return False


# ---- taps: what a GPU Mesh node receives ------------------------------------------------------------

def _upstream_nodes(tree, socket):
    """Names of every node that feeds `socket` (directly or indirectly)."""
    found, todo = set(), [socket]
    while todo:
        sock = todo.pop()
        for link in tree.links:
            if link.to_socket == sock and link.from_node.name not in found:
                found.add(link.from_node.name)
                todo.extend(link.from_node.inputs)
    return found


def _tap_signature(tree, node):
    sock = node.inputs.get("Mesh")
    if sock is None:
        return None
    names = _upstream_nodes(tree, sock)
    parts = [tree.name]
    for n in sorted(names):
        nd = tree.nodes[n]
        vals = []
        for inp in nd.inputs:
            v = getattr(inp, "default_value", None)
            try:
                vals.append(repr(tuple(v)) if hasattr(v, "__len__") and not isinstance(v, str) else repr(v))
            except TypeError:
                vals.append(repr(v))
        group_name = nd.node_tree.name if getattr(nd, "node_tree", None) is not None else ""
        parts.append(f"{n}:{nd.bl_idname}:{group_name}:{vals}")
    parts.append(repr(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                             for l in tree.links if l.to_node.name in names or l.to_node == node)))
    return "|".join(parts)


def ensure_tap(src, tree, node, hosts_):
    """Keep a hidden object whose Geometry Nodes end at this GPU Mesh node's Mesh input."""
    if not hosts_:
        return None
    host = hosts_[0]
    name = f"CN Tap · {src.name}"
    tap = bpy.data.objects.get(name)
    sig = _tap_signature(tree, node)
    if tap is not None and tap.get("cn_tap_sig") == sig and tap.data == host.data:
        _copy_modifier_values(host, tree, tap)
        return tap
    if tap is None:
        tap = bpy.data.objects.new(name, host.data)
        sources_collection().objects.link(tap)
    tap.data = host.data
    old = tap.modifiers.get("CodeNodes Tap")
    old_tree = old.node_group if old is not None else None
    tap_tree = tree.copy()
    tap_tree.name = _unique(f".CN Tap · {src.name}", bpy.data.node_groups)
    tap_tree[TAP] = src.name
    copy_node = tap_tree.nodes.get(node.name)
    keep = _upstream_nodes(tap_tree, copy_node.inputs["Mesh"]) if copy_node is not None else set()
    from_socket = None
    if copy_node is not None:
        link = next((l for l in tap_tree.links if l.to_socket == copy_node.inputs["Mesh"]), None)
        from_socket = link.from_socket if link is not None else None
    gout = next((n for n in tap_tree.nodes if n.type == 'GROUP_OUTPUT'), None)
    if gout is not None:
        keep.add(gout.name)
    for n in list(tap_tree.nodes):
        if n.name not in keep:
            tap_tree.nodes.remove(n)
    if gout is not None and from_socket is not None:
        geo_in = next((sk for sk in gout.inputs if sk.bl_idname == "NodeSocketGeometry"), None)
        if geo_in is not None:
            tap_tree.links.new(from_socket, geo_in)
    if old is None:
        old = tap.modifiers.new("CodeNodes Tap", 'NODES')
    old.node_group = tap_tree
    if old_tree is not None and old_tree.users == 0:
        bpy.data.node_groups.remove(old_tree)
    tap["cn_tap_sig"] = sig
    tap["cn_tap_host"] = host.name
    _copy_modifier_values(host, tree, tap)
    # the tap lives in the hidden Sources collection, which Blender only evaluates for objects
    # something depends on: an Object Info inside the GPU Mesh group makes it a dependency
    group = node.node_tree
    link = group.nodes.get("Tap")
    if link is None:
        link = group.nodes.new("GeometryNodeObjectInfo")
        link.name = link.label = "Tap"
        link.location = (-150, -250)
    if link.inputs["Object"].default_value != tap:
        link.inputs["Object"].default_value = tap
    from . import gpu_live
    gpu_live.mark_deform_dirty(src.name)
    return tap


def _copy_modifier_values(host, tree, tap):
    hm = next((m for m in host.modifiers if m.type == 'NODES' and m.node_group == tree), None)
    tm = tap.modifiers.get("CodeNodes Tap")
    if hm is None or tm is None:
        return
    for item in tree.interface.items_tree:
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.socket_type != "NodeSocketGeometry":
            try:
                if tm.get(item.identifier) != hm.get(item.identifier):
                    tm[item.identifier] = hm[item.identifier]
            except (KeyError, TypeError):
                pass


_popup = []


def request_popup(src):
    """Open the code of `src` in a pop-up Text Editor on the next tick (never from inside a handler)."""
    _popup.append(src.name)

    def _open():
        from .gn_ui import open_code_popup
        while _popup:
            obj = bpy.data.objects.get(_popup.pop(0))
            if obj is not None and obj.codenodes.text is not None:
                open_code_popup(obj.codenodes.text)
        return None
    if not bpy.app.timers.is_registered(_open):
        bpy.app.timers.register(_open, first_interval=0.01)


def tap_of(src):
    """The tap object holding what a GPU Mesh (or mesh-heading stage) receives; a branch uses its head's."""
    from . import links
    return bpy.data.objects.get(f"CN Tap · {links.base_name(src)}")


def _fresh(user):
    """The same user (a group node or a modifier) looked up again by name, or None if it's gone. Blender
    objects are never trusted across a rebuild: sockets are freed when a group's interface changes."""
    kind, owner, thing = user
    try:
        if kind == 'node':
            tree = bpy.data.node_groups.get(owner.name)
            node = tree.nodes.get(thing.name) if tree is not None else None
            return (kind, tree, node) if node is not None and node.node_tree is not None else None
        obj = bpy.data.objects.get(owner.name)
        mod = obj.modifiers.get(thing.name) if obj is not None else None
        return (kind, obj, mod) if mod is not None and mod.node_group is not None else None
    except (ReferenceError, AttributeError):
        return None


def sync():
    """One pass: split duplicates, match inputs to code, push values, queue rebuilds, show status."""
    from . import gn_sockets, gpu_live
    if live._rendering():
        return
    groups = list(bpy.data.node_groups)
    if not any(is_code_group(g) or is_make_real(g) or is_join(g) or is_cache(g) for g in groups):
        if gpu_live.hosts:
            gpu_live.hosts.clear()
            gpu_live.redraw()
        return
    for gname, lst in users().items():
        group = bpy.data.node_groups.get(gname)
        obj = source_of(group)
        if obj is None:
            continue
        for user in lst[1:]:
            split(group, user)
        user = lst[0]
        s = obj.codenodes
        if s.kind in GPU_KINDS:
            _migrate_gpu_group(group)
        if s.enabled and s.kind in CODE_KINDS and s.text is not None:
            import hashlib
            code = s.text.as_string()
            if hashlib.sha1(code.encode()).hexdigest() != s.code_hash:
                try:                                  # the code changed: its sockets follow at once
                    props.sync_params(s, code)
                except Exception as exc:
                    s.last_error = str(exc)
                live.request(obj)
        if s.enabled:
            sync_interface(group, obj)
            if user[0] == 'node':
                gn_sockets.set_menu_defaults(user[1], user[2], obj)
        if s.enabled and apply_values(obj, read_values(user), user):
            if s.kind in CODE_KINDS:
                gpu_live.redraw()
            if s.live:
                live.request(obj)
        user = _fresh(user)                    # a template switch rebuilt the group: re-find everything
        if user is None:
            _dirty[0] = True
            continue
        group = user[2].node_tree if user[0] == 'node' else user[2].node_group
        if user[0] == 'node':
            gn_sockets.update_status(group, obj, [user[2]])
            edit = user[2].inputs.get(gn_sockets.EDIT)
            if edit is not None and not edit.is_linked and edit.default_value:
                edit.default_value = False            # behaves like a button
                request_popup(obj)
    for g in groups:                           # tooltips on the fixed CodeNodes nodes (also in older files)
        try:
            if is_cache(g):
                from . import gpu_cache
                gn_sockets.apply_tips(g, dict(gn_sockets.CACHE_TIPS, **{gpu_cache.BAKE: "Switch on to record Start–End "
                                      "now (it switches itself back off)", gpu_cache.CLEAR: "Switch on to delete the "
                                      "recording (it switches itself back off)"}), gn_sockets.CACHE_TIPS_OUT)
            elif is_join(g):
                gn_sockets.apply_tips(g, gn_sockets.JOIN_TIPS, gn_sockets.JOIN_TIPS_OUT)
        except ReferenceError:
            pass
    host_map = sync_make_real()
    try:
        from . import lights
        if lights.sync():
            gpu_live.redraw()
    except Exception:
        _report_exc()
    if host_map != gpu_live.hosts:
        gpu_live.hosts.clear()
        gpu_live.hosts.update(host_map)
        gpu_live.mark_emitters_dirty()
        gpu_live.redraw()


_dirty = [True]


def _poll():
    try:
        if _dirty[0]:
            _dirty[0] = False
            sync()
    except Exception:
        _report_exc()
    return POLL_S


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    for u in depsgraph.updates:
        if isinstance(u.id, (bpy.types.NodeTree, bpy.types.Object)):
            _dirty[0] = True
            return


@bpy.app.handlers.persistent
def _on_load(*_args):
    _dirty[0] = True


def _slow_tick():
    _dirty[0] = True                    # catches changes that send no depsgraph update (e.g. code text)
    return 1.0


# ---- Make Native -------------------------------------------------------------------------

def make_native(group):
    """Replace a Code Mesh group with a real Geometry Nodes network (via ExpressNode).

    Returns the native group. Raises bake_nodes.CannotConvert with a reason otherwise."""
    from . import bake_nodes
    obj = source_of(group)
    if obj is None or obj.codenodes.kind != 'MESH':
        raise bake_nodes.CannotConvert("only Code Mesh (SDF) nodes can be made native; shapes and "
                                       "particles have no node version yet")
    lst = users().get(group.name, [])
    before = [read_values(u) for u in lst]
    native = bake_nodes.build(obj)            # adds a modifier on the source; we take the group
    for mod in [m for m in obj.modifiers if m.type == 'NODES' and m.node_group == native]:
        obj.modifiers.remove(mod)
    native.name = _unique(group.name.replace(PREFIX, "Nodes · ", 1), bpy.data.node_groups)
    for (kind, owner, thing), values in zip(lst, before):
        if kind == 'node':
            thing.node_tree = native
            for sock in thing.inputs:
                if sock.name in values and not sock.is_linked:
                    sock.default_value = values[sock.name]
        else:
            thing.node_group = native
            for item in native.interface.items_tree:
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name in values:
                    thing[item.identifier] = values[item.name]
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    bpy.data.node_groups.remove(group)
    return native


# ---- cleaning up on save -----------------------------------------------------------------

@bpy.app.handlers.persistent
def _on_save(*_args):
    """Remove sources whose code group is gone (deleted node, then saved)."""
    try:
        used = {g[TAG] for g in bpy.data.node_groups if is_code_group(g) and g.users > 0}
        col = bpy.data.collections.get(SOURCES)
        if col is None:
            return
        for obj in list(col.objects):
            if is_branch_object(obj):
                if obj.get(PIPE_HEAD) not in used:
                    bpy.data.objects.remove(obj)
                continue
            if obj.codenodes.enabled and obj.name not in used:
                bpy.data.objects.remove(obj)
    except Exception:
        _report_exc()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.handlers.save_pre.append(_on_save)
    bpy.app.timers.register(_poll, first_interval=POLL_S, persistent=True)
    bpy.app.timers.register(_slow_tick, first_interval=1.0, persistent=True)


def unregister():
    for fn in (_poll, _slow_tick):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load), (bpy.app.handlers.save_pre, _on_save)):
        if fn in lst:
            lst.remove(fn)


def _report_exc():
    """Print the current error without letting Python touch freed Blender structs (see safe_errors)."""
    from .safe_errors import report
    report()
