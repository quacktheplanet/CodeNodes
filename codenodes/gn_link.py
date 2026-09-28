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

MAKE_REAL = "codenodes_make_real"   # on a Make Real node group
MAKE_REAL_NAME = "Make Real"
GPU_KINDS = ('MESH', 'PARTICLES', 'DEFORM')
TAP = "codenodes_tap"              # on a hidden tap tree: which GPU Mesh source it feeds

KINDS = {
    'PARTICLES': ("GPU Particles", "Particles moved by code you write, on the GPU. Drawn live in the viewport; "
                                   "add Make Real to use them in nodes or renders", 'PARTICLES'),
    'MESH': ("GPU Surface (SDF)", "A surface from a signed distance function, raymarched live on the GPU; "
                                  "add Make Real to turn it into a mesh", 'SCRIPT'),
    'DEFORM': ("GPU Mesh", "Code run on every vertex of the mesh wired into it (deform, displace, recolour); "
                           "drawn live, add Make Real for the modified mesh", 'MOD_WAVE'),
    'SHAPE': ("Code Shape", "A model built from a parametric description: exact edges, clean quads "
                            "(real geometry straight away)", 'MESH_CYLINDER'),
}

CASTLE = """\
// The Aerie citadel as a GPU surface: a ring wall with towers, a keep and roofs.
// Units: the citadel is about 25 across; `scale` is metres per unit.
// @param towers 6 3 12
// @param radius 10 6 12.5
// @param wallHeight 4.5 2 9
// @param keepHeight 14 6 26
// @param scale 0.1 0.02 1.0

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
// @param radius 1.0 0.3 3.0
// @param relief 0.05 0.0 0.25
// @param sea 0.5 0.3 0.7
// @param seed 3.0 0.0 100.0

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
// @param radius 0.9 0.1 2.0
// @param ring 1.55 0.5 3.0
// @param width 0.35 0.02 1.0
// @param tilt 0.45 0.0 1.5

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
// @param size 2.4 0.5 10.0
// @param twist 2.4 0.0 6.0
// @param spin 0.9 0.0 3.0

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
  vec3 c = bulge > 0.5 ? old * 0.55 : mix(mix(old, young, smoothstep(0.15, 0.9, r)), rosy, step(0.985, rnd) * 0.9);
  c *= (0.25 + 0.75 * smoothstep(0.0, 0.7, r)) * (0.6 + 0.8 * rnd);
  return vec4(c, 1.0);
}
"""

FLOW = """\
// Flow: particles ride a curl-noise current (as in Myriad): crisp filaments that never clump.
// @param scale 0.55 0.1 3.0
// @param speed 1.9 0.1 6.0
// @param radius 2.2 0.5 8.0

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
// @param size 2.1 0.5 8.0
// @param rate 0.45 0.05 2.0

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
    "Galaxy": {"count": 1_000_000, "color_by": 'CODE', "gain": 0.45, "point_px": 1.0},
    "Flow": {"count": 1_000_000, "color_by": 'CODE', "gain": 0.3, "point_px": 1.0, "prewarm": 3.0},
    "Attractor": {"count": 600_000, "color_by": 'CODE', "gain": 0.3, "point_px": 1.0, "prewarm": 4.0},
    "Swirl": {"count": 200_000, "gain": 0.4},
    "Fountain": {"count": 100_000, "blend": 'SOLID', "point_px": 3.0, "color_by": 'AGE',
                 "color_a": (0.6, 0.85, 1.0), "color_b": (0.1, 0.3, 0.9), "gain": 1.0},
    "Castle": {"bounds_min": (-1.6, -1.6, -0.15), "bounds_max": (1.6, 1.6, 2.35), "resolution": 192},
    "Planet": {"bounds_min": (-1.45, -1.45, -1.45), "bounds_max": (1.45, 1.45, 1.45), "resolution": 160},
    "Saturn": {"bounds_min": (-2.2, -2.2, -1.2), "bounds_max": (2.2, 2.2, 1.2), "resolution": 160},
}

TEMPLATES = {
    'DEFORM': {},                # filled from deform.TEMPLATES below
    'MESH': {
        "Castle": CASTLE,
        "Planet": PLANET,
        "Saturn": SATURN,
        "Donut": """\
// A donut. sdf(p) is the distance to the surface: negative inside, positive outside (metres).
// Each @param line below becomes an input on the node.
// @param major 0.8 0.2 2.0
// @param minor 0.3 0.05 1.0
float sdf(vec3 p) {
  return sdTorus(p, major, minor);
}
""",
        "Rounded Box": """\
// A box with rounded edges.
// @param size 0.8 0.1 2.0
// @param roundness 0.15 0.0 0.5
float sdf(vec3 p) {
  return sdRoundBox(p, vec3(size), min(roundness, size));
}
""",
        "Gyroid Ball": """\
// A sphere carved into a gyroid lattice.
// @param radius 1.0 0.2 2.0
// @param cells 6.0 1.0 16.0
// @param thickness 0.05 0.01 0.3
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
        "Fountain": """\
// A fountain: particles shoot up from the origin and fall back under gravity.
// @param power 4.0 0.5 10.0
// @param gravity 9.8 0.0 20.0
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
DEFAULT_TEMPLATE = {'MESH': "Donut", 'SHAPE': "Desk Lamp", 'PARTICLES': "Galaxy", 'DEFORM': "Wave"}


def _fill_deform_templates():
    from .deform import TEMPLATES as DT
    TEMPLATES['DEFORM'].update(DT)


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
    return col


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
    Blender until a Make Real node asks for it."""
    from . import api
    name = _unique(f"CN · {label}", bpy.data.objects)
    if kind in GPU_KINDS:
        # the text and settings, without running it: live drawing and Make Real run it on demand
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(obj)
        s = obj.codenodes
        ext = {'PARTICLES': 'particles', 'DEFORM': 'vertex'}.get(kind, 'sdf')
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
    if obj.codenodes.kind in GPU_KINDS:
        # A GPU node's result lives on the GPU and is drawn live; its Geometry output stays empty
        # until a Make Real node downstream turns it into real geometry.
        group.description = ("CodeNodes GPU node: drawn live in the viewport. Add Make Real after it to use "
                             "it in nodes or renders. Edit the code from the Node Editor sidebar (N)")
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


# ---- Make Real ---------------------------------------------------------------------------------

def build_make_real():
    """A Make Real node group: its input is a GPU node's result, its output real geometry.

    Inside: Object Info reads the upstream GPU node's source object, which the add-on fills with
    real points or a mesh; a Join passes through anything that is already real (e.g. after Make
    Native), so the node is harmless on ordinary geometry.
    """
    group = bpy.data.node_groups.new(_unique(MAKE_REAL_NAME, bpy.data.node_groups), "GeometryNodeTree")
    group[MAKE_REAL] = True
    group.description = ("CodeNodes: turns the GPU node before it into real geometry (points or a mesh) that "
                         "later nodes and renders can use, like Realize Instances. Options in the sidebar (N)")
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
    """Make Real's inputs for the kind of GPU node feeding it (When, Resolution / Max Points, ...)."""
    from . import gn_sockets
    gn_sockets.sync_real_interface(group, kind, src)


def real_info_node(group):
    return next((n for n in group.nodes if n.type == 'OBJECT_INFO'), None)


def upstream_code_node(tree, node, depth=0):
    """The code group node feeding a Make Real node's Geometry input (through reroutes), or None."""
    if depth > 32 or not node.inputs:
        return None
    sock = node.inputs[0]
    link = next((l for l in tree.links if l.to_socket == sock and not l.is_muted), None)
    if link is None:
        return None
    src = link.from_node
    if src.type == 'REROUTE':
        return upstream_code_node(tree, src, depth + 1)
    if src.type == 'GROUP' and is_code_group(src.node_tree):
        return src
    return None


def insert_make_real(tree, code_node_, location=None):
    """Put a Make Real node right after a code node, taking over its outgoing links."""
    group = build_make_real()
    node = tree.nodes.new("GeometryNodeGroup")
    node.node_tree = group
    node.location = location or (code_node_.location.x + code_node_.width + 60, code_node_.location.y)
    node.width = 160
    out = code_node_.outputs["Geometry"]
    targets = [l.to_socket for l in tree.links if l.from_socket == out]
    for l in [l for l in tree.links if l.from_socket == out]:
        tree.links.remove(l)
    tree.links.new(out, node.inputs["Geometry"])
    for sock in targets:
        tree.links.new(node.outputs["Geometry"], sock)
    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node
    _dirty[0] = True
    return node


def make_real_node(context):
    """The active node in the Node Editor if it's a Make Real node: (node, group, tree)."""
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
    `make_real` puts a Make Real node between them so the object gets real geometry.
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
    tree.links.new(node.outputs["Geometry"], gout.inputs["Geometry"])
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
        insert_make_real(tree, node)
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
    stays empty and Make Real carries the result."""
    for link in list(group.links):
        if link.from_node.type == 'OBJECT_INFO' and link.from_node.name not in ("Tap",)                 and link.to_node.type in ('GROUP_OUTPUT', 'JOIN_GEOMETRY') and link.from_node.name != "CodeNodes render link":
            group.links.remove(link)


def sync_make_real():
    """Point every Make Real node at the GPU node feeding it, and work out for each GPU source
    whether (and when) it is made real. Returns {source name: [host object names]}."""
    from . import gn_sockets, gpu_live
    modes, host_map, limits, real_nodes = {}, {}, {}, {}
    for tree in [t for t in bpy.data.node_groups if t.bl_idname == "GeometryNodeTree" and TAP not in t]:
        hosts_ = None
        seen_real = set()
        for node in tree.nodes:
            if node.type != 'GROUP' or node.node_tree is None:
                continue
            group = node.node_tree
            if is_code_group(group):
                src = source_of(group)
                if src is not None and src.codenodes.kind in GPU_KINDS:
                    hosts_ = _hosts_of(tree) if hosts_ is None else hosts_
                    lst = host_map.setdefault(src.name, [])
                    lst.extend(h.name for h in hosts_ if h.name not in lst)
                    if src.codenodes.kind == 'DEFORM':
                        ensure_tap(src, tree, node, hosts_)
            elif is_make_real(group):
                if group.name in seen_real or (group.users > 1 and _other_real_user(group, tree, node)):
                    new = group.copy()                  # each Make Real node keeps its own settings
                    new.name = _unique(MAKE_REAL_NAME, bpy.data.node_groups)
                    node.node_tree = new
                    group = new
                seen_real.add(group.name)
                up = upstream_code_node(tree, node)
                src = source_of(up.node_tree) if up is not None else None
                if src is not None and src.codenodes.kind not in GPU_KINDS:
                    src = None                          # a Code Shape is real already: pass it through
                info = real_info_node(group)
                if info is not None and info.inputs["Object"].default_value != src:
                    info.inputs["Object"].default_value = src
                sync_real_interface(group, src.codenodes.kind if src is not None else None, src)
                if src is None:
                    label = "Make Real (nothing to make real)" if up is None else "Make Real"
                    if node.label != label:
                        node.label = label
                    continue
                s = src.codenodes
                real_nodes.setdefault(src.name, []).append(node)
                want = REAL_INPUTS.get(s.kind, (None,))[0]
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
    col = bpy.data.collections.get(SOURCES)
    for src in (list(col.objects) if col is not None else []):
        s = getattr(src, "codenodes", None)
        if s is None or not s.enabled or s.kind not in GPU_KINDS:
            continue
        mode = modes.get(src.name, 'NONE')
        changed = False
        if s.real_mode != mode:
            s.real_mode = mode
            changed = True
        lim = limits.get(src.name)
        if lim is None and s.kind == 'PARTICLES' and s.real_limit and mode == 'NONE':
            s.real_limit = 0                           # no Make Real any more: no Max Points either
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
        for node in real_nodes.get(src.name, []):
            if s.real_mode == 'RENDER_ONLY':
                label = "Make Real · for render"
            else:
                label = "Make Real" + (f" · {cost} ms" if cost is not None else "")
            if node.label != label:
                node.label = label
    return host_map


def _made_real_ms(stats):
    import re
    m = re.search(r"made real in (\d+) ms", stats or "")
    return int(m.group(1)) if m else None


def _other_real_user(group, tree, node):
    """True if another node (in any tree) uses this Make Real group too (Shift D copies share it)."""
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
    return bpy.data.objects.get(f"CN Tap · {src.name}")


def sync():
    """One pass: split duplicates, match inputs to code, push values, queue rebuilds, show status."""
    from . import gn_sockets, gpu_live
    if live._rendering():
        return
    groups = list(bpy.data.node_groups)
    if not any(is_code_group(g) or is_make_real(g) for g in groups):
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
        if s.enabled:
            sync_interface(group, obj)
            if user[0] == 'node':
                gn_sockets.set_menu_defaults(user[1], user[2], obj)
        if s.enabled and apply_values(obj, read_values(user), user):
            if s.kind in GPU_KINDS:
                gpu_live.redraw()
            if s.live:
                live.request(obj)
        if user[0] == 'node':
            gn_sockets.update_status(group, obj, [user[2]])
            edit = user[2].inputs.get(gn_sockets.EDIT)
            if edit is not None and not edit.is_linked and edit.default_value:
                edit.default_value = False            # behaves like a button
                request_popup(obj)
    host_map = sync_make_real()
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
        traceback.print_exc()
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
            if obj.codenodes.enabled and obj.name not in used:
                bpy.data.objects.remove(obj)
    except Exception:
        traceback.print_exc()


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
