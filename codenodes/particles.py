"""Code Particles: a GPU particle solver you write yourself.

You write two functions; CodeNodes keeps the state on the GPU and steps it:

    void spawn(inout Particle p)            // where a particle starts
    void update(inout Particle p, float dt) // how it moves, once per step

``Particle`` has ``position``, ``velocity``, ``age``, ``life`` and a per-particle
``seed``. Ageing and respawning are handled for you: when ``age`` passes ``life``
the particle is spawned again.

State lives in two RGBA32F textures (position+age, velocity+life) that the compute
shader reads and writes in place, so nothing crosses the CPU until we read the
result out to build the points.
"""

from __future__ import annotations

import hashlib
import math
import time

import numpy as np

from .sdf_code import PRELUDE, SdfCodeError, param_defines, parse_params, user_errors

MAX_COUNT = 2_000_000
ROW = 256                       # particles per texture row
GROUP = 16
MAX_CATCHUP_STEPS = 600         # a scrub that needs more than this restarts instead

PARTICLE_PRELUDE = """\
// ---- CodeNodes particle helpers ----
struct Particle { vec3 position; vec3 velocity; float age; float life; float seed; };
float rand1(float n) { return fract(sin(n * 12.9898 + 4.1414) * 43758.5453123); }
vec3 rand3(float n) { return vec3(rand1(n * 1.03 + 0.13), rand1(n * 1.71 + 2.71), rand1(n * 3.11 + 5.37)); }
vec3 randBall(float n) {          // roughly even through a unit ball
  vec3 r = rand3(n) * 2.0 - 1.0;
  return normalize(r + 1e-6) * pow(rand1(n * 7.77 + 1.23), 0.3333);
}
// ---- your code ----
"""

PARTICLE_MAIN = """
// ---- CodeNodes stepper ----
void main() {
  ivec2 ij = ivec2(gl_GlobalInvocationID.xy);
  int idx = ij.y * cnRow + ij.x;
  if (idx >= cnCount) return;
  vec4 a = imageLoad(cnPos, ij);
  vec4 b = imageLoad(cnVel, ij);
  Particle p;
  p.position = a.xyz;
  p.age = a.w;
  p.velocity = b.xyz;
  p.life = b.w;
  p.seed = float(idx) + 0.5;
  if (cnReset != 0) {
    p.position = vec3(0.0);
    p.velocity = vec3(0.0);
    p.age = 0.0;
    p.life = 5.0;
    spawn(p);
    p.age = rand1(p.seed * 2.17) * p.life * cnStagger;   // so they don't all die together
  } else {
    p.age += cnDt;
    if (p.age >= p.life) {
      p.age = 0.0;
      p.velocity = vec3(0.0);
      spawn(p);
    }
    update(p, cnDt);
  }
  imageStore(cnPos, ij, vec4(p.position, p.age));
  imageStore(cnVel, ij, vec4(p.velocity, max(p.life, 1e-4)));
}
"""

TEMPLATE = """\
// Particles. spawn() places one; update() moves it, called once per step.
// Particle: position, velocity, age, life, seed.
// Helpers: rand1 rand3 randBall noise3 fbm3, plus uTime. Sliders are declared below.
// @param speed 1.0 0.0 4.0
// @param swirl 1.6 0.1 5.0
// @param drag 0.9 0.5 1.0

vec3 curl(vec3 p) {                       // a divergence-free field: particles swirl, never pile up
  float e = 0.35;
  float n1 = noise3(p * swirl + vec3(0.0, 0.0, uTime * 0.2));
  float n2 = noise3((p + vec3(0.0, e, 0.0)) * swirl + vec3(0.0, 0.0, uTime * 0.2));
  float n3 = noise3((p + vec3(0.0, 0.0, e)) * swirl + vec3(5.2, 1.3, uTime * 0.2));
  float n4 = noise3((p + vec3(e, 0.0, 0.0)) * swirl + vec3(5.2, 1.3, uTime * 0.2));
  return vec3(n2 - n1, n3 - n4, n1 - n3) / e;
}

void spawn(inout Particle p) {
  p.position = randBall(p.seed) * 1.8;
  p.life = 4.0 + rand1(p.seed * 3.3) * 3.0;
}

void update(inout Particle p, float dt) {
  p.velocity = mix(curl(p.position) * speed, p.velocity, drag);
  p.position += p.velocity * dt;
}
"""


def check_source(source):
    import re
    for sig, name in ((r"\bvoid\s+spawn\s*\(\s*inout\s+Particle\s+\w+\s*\)", "void spawn(inout Particle p)"),
                      (r"\bvoid\s+update\s*\(\s*inout\s+Particle\s+\w+\s*,\s*float\s+\w+\s*\)",
                       "void update(inout Particle p, float dt)")):
        if not re.search(sig, source):
            raise SdfCodeError(f"the code must define:  {name} {{ ... }}")
    for bad in ("imageStore", "imageLoad", "gl_GlobalInvocationID"):
        if bad in source:
            raise SdfCodeError(f"'{bad}' isn't allowed here; just set the particle's fields")


def full_source(source, params):
    check_source(source)
    head = PRELUDE + PARTICLE_PRELUDE + param_defines(params)
    return head + source + "\n" + PARTICLE_MAIN, head.count("\n")


class Sim:
    """One particle system's GPU state."""

    def __init__(self, source, count):
        import gpu
        from . import sampler
        sampler._require_gpu()
        if not 1 <= count <= MAX_COUNT:
            raise SdfCodeError(f"particle count must be between 1 and {MAX_COUNT:,} (got {count:,})")
        self.source = source
        self.key = hashlib.sha1(f"{count}\0{source}".encode()).hexdigest()
        self.count = int(count)
        self.params = parse_params(source)
        code, offset = full_source(source, self.params)

        info = gpu.types.GPUShaderCreateInfo()
        info.typedef_source("struct CNParams { vec4 v[64]; };")
        info.uniform_buf(0, "CNParams", "cnParams")
        info.image(0, 'RGBA32F', 'FLOAT_2D', "cnPos", qualifiers={'READ', 'WRITE'})
        info.image(1, 'RGBA32F', 'FLOAT_2D', "cnVel", qualifiers={'READ', 'WRITE'})
        for kind, name in (('FLOAT', "cnDt"), ('FLOAT', "uTime"), ('FLOAT', "uFrame"),
                           ('FLOAT', "cnStagger"), ('INT', "cnCount"), ('INT', "cnRow"), ('INT', "cnReset")):
            info.push_constant(kind, name)
        info.local_group_size(GROUP, GROUP, 1)
        info.compute_source(code)
        shader, err, log = sampler._compile_capturing(info)
        if shader is None:
            raise SdfCodeError("the code didn't compile:\n" + user_errors(log, offset, source))
        self.shader = shader
        self.rows = max(1, math.ceil(self.count / ROW))
        self.pos = gpu.types.GPUTexture((ROW, self.rows), format='RGBA32F')
        self.vel = gpu.types.GPUTexture((ROW, self.rows), format='RGBA32F')
        self.frame = None            # the frame this state represents

    def _dispatch(self, dt, time_s, frame, reset, values, stagger=1.0):
        import gpu
        sh = self.shader
        sh.image("cnPos", self.pos)
        sh.image("cnVel", self.vel)
        slots = np.zeros(256, np.float32)
        for i, prm in enumerate(self.params):
            slots[i] = float((values or {}).get(prm.name, prm.default))
        # keep a reference: a temporary would be freed before the dispatch runs
        self._ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 256, slots.tolist()))
        sh.uniform_block("cnParams", self._ubo)
        for setter, name, value in ((sh.uniform_float, "cnDt", float(dt)),
                                    (sh.uniform_float, "uTime", float(time_s)),
                                    (sh.uniform_float, "uFrame", float(frame)),
                                    (sh.uniform_float, "cnStagger", float(stagger)),
                                    (sh.uniform_int, "cnCount", self.count),
                                    (sh.uniform_int, "cnRow", ROW),
                                    (sh.uniform_int, "cnReset", 1 if reset else 0)):
            try:
                setter(name, value)
            except ValueError:
                pass                  # the compiler drops uniforms the code never reads
        gpu.compute.dispatch(sh, math.ceil(ROW / GROUP), math.ceil(self.rows / GROUP), 1)

    def reset(self, time_s=0.0, frame=0, values=None, stagger=1.0):
        self._dispatch(0.0, time_s, frame, True, values, stagger)

    def step(self, dt, time_s=0.0, frame=0, values=None):
        self._dispatch(dt, time_s, frame, False, values)

    def read(self):
        """{'position', 'velocity', 'speed', 'age', 'life'} as numpy arrays of length count.

        ``speed`` is there because Blender reserves the name ``velocity`` for motion
        blur, so a shader cannot read it back — shade with ``speed`` instead.
        """
        out = {}
        for tex, names in ((self.pos, ("position", "age")), (self.vel, ("velocity", "life"))):
            buf = tex.read()
            buf.dimensions = ROW * self.rows * 4
            arr = np.frombuffer(buf, dtype=np.float32).reshape(-1, 4)[:self.count]
            out[names[0]] = np.ascontiguousarray(arr[:, :3])
            out[names[1]] = np.ascontiguousarray(arr[:, 3])
        out["speed"] = np.linalg.norm(out["velocity"], axis=1).astype(np.float32)
        return out


_sims: dict[str, Sim] = {}


def get_sim(name, source, count):
    """The cached Sim for this object, rebuilt when the code or count changes."""
    key = hashlib.sha1(f"{count}\0{source}".encode()).hexdigest()
    sim = _sims.get(name)
    if sim is None or sim.key != key:
        sim = Sim(source, count)
        _sims[name] = sim
    return sim


def forget(name=None):
    if name is None:
        _sims.clear()
    else:
        _sims.pop(name, None)


def simulate(name, source, count, frame, frame_start, fps, values=None, substeps=1, stagger=1.0):
    """Bring the simulation to `frame` and return its state.

    Steps forward from where it already is; a jump backwards restarts from the
    start frame. Returns (state dict, stats).
    """
    sim = get_sim(name, source, count)
    substeps = max(1, min(int(substeps), 20))
    dt = 1.0 / (fps * substeps)
    t0 = time.perf_counter()
    frame = int(frame)
    start = int(frame_start)
    steps = 0

    def seconds(f):
        return (f - start) / fps

    if frame <= start or sim.frame is None or frame < sim.frame or frame - sim.frame > MAX_CATCHUP_STEPS:
        sim.reset(seconds(start), start, values, stagger)
        sim.frame = start
    while sim.frame < frame:
        nxt = sim.frame + 1
        for _ in range(substeps):
            sim.step(dt, seconds(nxt), nxt, values)
            steps += 1
        sim.frame = nxt
    state = sim.read()
    return state, {"count": sim.count, "steps": steps, "sim_s": time.perf_counter() - t0,
                   "frame": sim.frame}


# ---- Blender side -------------------------------------------------------------------

POINTS_MODIFIER = "CodeNodes Points"


def points_group(radius=0.02):
    """A tiny node group turning the vertices into renderable points.

    Mesh to Points drops the object's material, so a Set Material node puts it
    back; without it the points render unshaded and vanish in a dark scene.
    """
    import bpy
    name = "CodeNodes Points"
    tree = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.use_fake_user = True
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Radius", in_out="INPUT", socket_type="NodeSocketFloat")
    tree.interface.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin = tree.nodes.new("NodeGroupInput")
    gin.location = (-360, 0)
    gout = tree.nodes.new("NodeGroupOutput")
    gout.location = (360, 0)
    m2p = tree.nodes.new("GeometryNodeMeshToPoints")
    m2p.location = (-120, 0)
    m2p.inputs["Radius"].default_value = radius
    setmat = tree.nodes.new("GeometryNodeSetMaterial")
    setmat.location = (120, 0)
    tree.links.new(gin.outputs["Geometry"], m2p.inputs["Mesh"])
    tree.links.new(gin.outputs["Radius"], m2p.inputs["Radius"])
    tree.links.new(m2p.outputs[0], setmat.inputs["Geometry"])
    tree.links.new(gin.outputs["Material"], setmat.inputs["Material"])
    tree.links.new(setmat.outputs[0], gout.inputs[0])
    return tree


def socket_id(tree, name):
    """The modifier key for a group input, e.g. 'Socket_2'."""
    for item in tree.interface.items_tree:
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == name:
            return item.identifier
    return None


def apply_settings(obj, mod, radius):
    """Push the point size and the object's material into the modifier's sockets."""
    tree = mod.node_group
    key = socket_id(tree, "Radius")
    if key is not None:
        mod[key] = float(radius)
    key = socket_id(tree, "Material")
    if key is not None and obj.data.materials and mod.get(key) is None:
        mod[key] = obj.data.materials[0]


def fill_points(me, state, extra=("velocity", "speed", "age", "life")):
    """Put the particle state into a mesh as vertices plus point attributes."""
    me.clear_geometry()
    pos = state["position"]
    n = len(pos)
    if n:
        me.vertices.add(n)
        me.vertices.foreach_set("co", np.ascontiguousarray(pos, np.float32).ravel())
    for key in extra:
        values = state.get(key)
        if values is None:
            continue
        vector = values.ndim == 2
        attr = me.attributes.get(key)
        if attr is None or attr.domain != 'POINT':
            attr = me.attributes.new(key, 'FLOAT_VECTOR' if vector else 'FLOAT', 'POINT')
        if n:
            attr.data.foreach_set("vector" if vector else "value",
                                  np.ascontiguousarray(values, np.float32).ravel())
    me.update()
    return me


def ensure_points_modifier(obj, radius=0.02):
    """Give an object the modifier that turns its vertices into renderable points."""
    mod = obj.modifiers.get(POINTS_MODIFIER)
    if mod is None:
        mod = obj.modifiers.new(POINTS_MODIFIER, 'NODES')
        mod.node_group = points_group(radius)
        while obj.modifiers[0] != mod:          # before anything the user added
            obj.modifiers.move(len(obj.modifiers) - 1, 0)
    apply_settings(obj, mod, radius)
    return mod


def ensure_object(name, radius=0.02):
    import bpy
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != 'MESH':
        raise SdfCodeError(f"an object named '{name}' exists and isn't a mesh")
    if obj is None:
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(obj)
    ensure_points_modifier(obj, radius)
    return obj
