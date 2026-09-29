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

MAX_COUNT = 16_777_216          # live on the GPU; Real Geometry is capped lower (MAX_REAL)
MAX_REAL = 4_194_304
ROW = 256                       # particles per texture row (default; big systems use wider rows)
GROUP = 16
MAX_CATCHUP_STEPS = 600         # a scrub that needs more than this restarts instead
EMIT_SAMPLES = 65536            # points sampled from an Emit From object


def row_for(count):
    """Texture row width: 256 keeps small systems compact, wider rows keep big ones under the
    GPU's texture height limit."""
    return ROW if count <= ROW * 8192 else 2048


NOISE_PRELUDE = """\
// ---- CodeNodes random and noise helpers (particles and mesh stages) ----
// An integer hash (PCG) of the float's bits: sin()-based hashes lose precision at the seeds of
// millions of particles, and neighbouring particles then line up in streaks.
uint cnPcg(uint v) { uint s = v * 747796405u + 2891336453u; uint w = ((s >> ((s >> 28u) + 4u)) ^ s) * 277803737u; return (w >> 22u) ^ w; }
float rand1(float n) { return float(cnPcg(floatBitsToUint(n) ^ 0x9E3779B9u) >> 8) / 16777216.0; }
vec3 rand3(float n) { return vec3(rand1(n * 1.03 + 0.13), rand1(n * 1.71 + 2.71), rand1(n * 3.11 + 5.37)); }
vec3 randBall(float n) {          // roughly even through a unit ball
  vec3 r = rand3(n) * 2.0 - 1.0;
  return normalize(r + 1e-6) * pow(rand1(n * 7.77 + 1.23), 0.3333);
}
vec3 randSphere(float n) {        // evenly on a unit sphere
  vec2 h = vec2(rand1(n * 5.13 + 0.7), rand1(n * 9.41 + 3.3));
  float z = h.x * 2.0 - 1.0, a = h.y * 6.2831853, r = sqrt(max(0.0, 1.0 - z * z));
  return vec3(r * cos(a), r * sin(a), z);
}
// gradient noise and its curl: a flow that never piles up (as in Myriad)
vec3 cnHash33(vec3 p) { p = fract(p * vec3(0.1031, 0.1030, 0.0973)); p += dot(p, p.yxz + 33.33); return fract((p.xxy + p.yxx) * p.zyx) * 2.0 - 1.0; }
float gnoise(vec3 p) {
  vec3 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(dot(cnHash33(i), f), dot(cnHash33(i + vec3(1, 0, 0)), f - vec3(1, 0, 0)), u.x),
                 mix(dot(cnHash33(i + vec3(0, 1, 0)), f - vec3(0, 1, 0)), dot(cnHash33(i + vec3(1, 1, 0)), f - vec3(1, 1, 0)), u.x), u.y),
             mix(mix(dot(cnHash33(i + vec3(0, 0, 1)), f - vec3(0, 0, 1)), dot(cnHash33(i + vec3(1, 0, 1)), f - vec3(1, 0, 1)), u.x),
                 mix(dot(cnHash33(i + vec3(0, 1, 1)), f - vec3(0, 1, 1)), dot(cnHash33(i + vec3(1, 1, 1)), f - vec3(1, 1, 1)), u.x), u.y), u.z);
}
vec3 cnPotential(vec3 p) { return vec3(gnoise(p), gnoise(p + vec3(31.4, 7.1, 11.9)), gnoise(p + vec3(-17.3, 23.9, 5.3))); }
vec3 curlNoise(vec3 p) {
  const float e = 0.08;
  vec3 dx = vec3(e, 0, 0), dy = vec3(0, e, 0), dz = vec3(0, 0, e);
  vec3 px0 = cnPotential(p - dx), px1 = cnPotential(p + dx), py0 = cnPotential(p - dy), py1 = cnPotential(p + dy);
  vec3 pz0 = cnPotential(p - dz), pz1 = cnPotential(p + dz);
  return vec3((py1.z - py0.z) - (pz1.y - pz0.y), (pz1.x - pz0.x) - (px1.z - px0.z), (px1.y - px0.y) - (py1.x - py0.x)) / (2.0 * e);
}
"""

PARTICLE_PRELUDE = """\
// ---- CodeNodes particle helpers ----
struct Particle { vec3 position; vec3 velocity; float age; float life; float seed; vec4 cnX; };
// the viewing camera in the particles' object space (set when drawing live; the origin otherwise)
#define cnCamera (cnParams.v[63].xyz)
""" + NOISE_PRELUDE + """\
// Emit From: points spread evenly over another object's surface (zero when there's none)
int cnEmitIndex(float seed) { return int(rand1(seed * 3.91 + 0.37) * float(max(cnEmitCount, 1))) % max(cnEmitCount, 1); }
vec3 emitPoint(float seed) { if (cnEmitCount == 0) return vec3(0.0); int i = cnEmitIndex(seed); return texelFetch(cnEmitP, ivec2(i % 256, i / 256), 0).xyz; }
vec3 emitNormal(float seed) { if (cnEmitCount == 0) return vec3(0.0, 0.0, 1.0); int i = cnEmitIndex(seed); return texelFetch(cnEmitN, ivec2(i % 256, i / 256), 0).xyz; }
// ---- your code ----
"""

PARTICLE_MAIN = """
// ---- CodeNodes stepper ----
void main() {
  ivec2 cnIJ = ivec2(gl_GlobalInvocationID.xy);
  int cnIdx = cnIJ.y * cnRow + cnIJ.x;
  if (cnIdx >= cnCount) return;
  vec4 cnA = imageLoad(cnPos, cnIJ);
  vec4 cnB = imageLoad(cnVel, cnIJ);
  Particle p;
  p.position = cnA.xyz;
  p.age = cnA.w;
  p.velocity = cnB.xyz;
  p.life = cnB.w;
  p.seed = float(cnIdx) + 0.5;
#ifdef CN_HAS_X
  p.cnX = imageLoad(cnExt, cnIJ);
#else
  p.cnX = CN_XDEFAULT;
#endif
  if (cnReset != 0) {
    p.position = vec3(0.0);
    p.velocity = vec3(0.0);
    p.age = 0.0;
    p.life = 5.0;
    p.cnX = CN_XDEFAULT;
    cnSpawn(p);
    p.age = rand1(p.seed * 2.17) * p.life * cnStagger;   // so they don't all die together
    cnUpdate(p, 0.0);        // place it (kinematic motions set the position here) without moving it on
  } else {
    p.age += cnDt;
    if (p.age >= p.life) {
      p.age = 0.0;
      p.velocity = vec3(0.0);
      p.cnX = CN_XDEFAULT;
      cnSpawn(p);
    }
    cnUpdate(p, cnDt);
  }
  imageStore(cnPos, cnIJ, vec4(p.position, p.age));
  imageStore(cnVel, cnIJ, vec4(p.velocity, max(p.life, 1e-4)));
#ifdef CN_HAS_X
  imageStore(cnExt, cnIJ, p.cnX);
#endif
}
"""

# Made real: where the particles are *shown* (after the chain's warps), without touching the state.
WARP_MAIN = """
// ---- CodeNodes warp for reading out ----
void main() {
  ivec2 cnIJ = ivec2(gl_GlobalInvocationID.xy);
  int cnIdx = cnIJ.y * cnRow + cnIJ.x;
  if (cnIdx >= cnCount) return;
  vec4 cnA = imageLoad(cnPos, cnIJ);
  vec4 cnB = imageLoad(cnVel, cnIJ);
  vec3 cnQ = cnWarp(cnA.xyz);
  vec3 cnAhead = cnWarp(cnA.xyz + cnB.xyz * 0.01);
  imageStore(cnOutP, cnIJ, vec4(cnQ, cnA.w));
  imageStore(cnOutV, cnIJ, vec4((cnAhead - cnQ) * 100.0, cnB.w));
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
    """Raw particle code (a source node's own code) or a composed chain (which always has cnSpawn)."""
    for bad in ("imageStore", "imageLoad", "gl_GlobalInvocationID"):
        if bad in source:
            raise SdfCodeError(f"'{bad}' isn't allowed here; just set the particle's fields")
    if "void cnSpawn(" not in source:
        compose_single(source)          # raises a clear message: no spawn(), a bad declaration, ...


def full_source(source, params, main=None):
    check_source(source)
    head = PRELUDE + PARTICLE_PRELUDE + param_defines(params)
    return head + source + "\n" + (main or PARTICLE_MAIN), head.count("\n")


def compose_single(code, values=None, name="code"):
    """A lone particle node's code as a chain of one (what the old single-node path runs)."""
    from . import chain
    comp = chain.compose_particles(chain.Unit(name, code, values))
    return comp


def has_look(source):
    import re
    return re.search(r"\bvec4\s+look\s*\(\s*Particle\s+\w+\s*\)", source) is not None


def _empty_emitter():
    import gpu
    zero = gpu.types.Buffer('FLOAT', 4, [0.0, 0.0, 0.0, 0.0])
    return (gpu.types.GPUTexture((1, 1), format='RGBA32F', data=zero),
            gpu.types.GPUTexture((1, 1), format='RGBA32F', data=zero), 0)


def emitter_textures(points, normals):
    """Upload emit points and normals ((n, 3) arrays, n <= EMIT_SAMPLES) as 256-wide textures."""
    import gpu
    n = len(points)
    if n == 0:
        return _empty_emitter()
    rows = max(1, math.ceil(n / 256))
    out = []
    for arr in (points, normals):
        data = np.zeros((rows * 256, 4), np.float32)
        data[:n, :3] = arr
        out.append(gpu.types.GPUTexture((256, rows), format='RGBA32F',
                                        data=gpu.types.Buffer('FLOAT', data.size, data.ravel())))
    return out[0], out[1], n


class Sim:
    """One particle system's GPU state.

    `source` is a composed chain (chain.compose_particles). With per-particle attributes it also keeps
    a third texture (cnExt: four floats per particle)."""

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
        self.has_x = "#define CN_HAS_X" in source
        warp_at = source.find("vec3 cnWarp(")
        self.has_warp = warp_at >= 0 and "  q = " in source[warp_at:warp_at + 4000]
        self.shader = self._compile(PARTICLE_MAIN)
        self.row = row_for(self.count)
        self.rows = max(1, math.ceil(self.count / self.row))
        self.pos = gpu.types.GPUTexture((self.row, self.rows), format='RGBA32F')
        self.vel = gpu.types.GPUTexture((self.row, self.rows), format='RGBA32F')
        self.ext = gpu.types.GPUTexture((self.row, self.rows), format='RGBA32F') if self.has_x else None
        self.emit_p, self.emit_n, self.emit_count = _empty_emitter()
        self.emit_key = None          # what the emitter textures were made from
        self.frame = None            # the frame this state represents
        self.draw_shader = None      # made on first draw (gpu_live)
        self.draw_key = None
        self.warp_shader = None      # made when read out with a warp in the chain

    def _compile(self, main, extra_images=()):
        import gpu
        from . import sampler
        code, offset = full_source(self.source, self.params, main)
        info = gpu.types.GPUShaderCreateInfo()
        info.typedef_source("struct CNParams { vec4 v[64]; };")
        info.uniform_buf(0, "CNParams", "cnParams")
        info.image(0, 'RGBA32F', 'FLOAT_2D', "cnPos", qualifiers={'READ', 'WRITE'})
        info.image(1, 'RGBA32F', 'FLOAT_2D', "cnVel", qualifiers={'READ', 'WRITE'})
        info.sampler(2, 'FLOAT_2D', "cnEmitP")
        info.sampler(3, 'FLOAT_2D', "cnEmitN")
        slot = 4
        if self.has_x and main is PARTICLE_MAIN:
            info.image(slot, 'RGBA32F', 'FLOAT_2D', "cnExt", qualifiers={'READ', 'WRITE'})
            slot += 1
        for name in extra_images:
            info.image(slot, 'RGBA32F', 'FLOAT_2D', name, qualifiers={'WRITE'})
            slot += 1
        for kind, name in (('FLOAT', "cnDt"), ('FLOAT', "uTime"), ('FLOAT', "uFrame"),
                           ('FLOAT', "cnStagger"), ('INT', "cnCount"), ('INT', "cnRow"), ('INT', "cnReset"),
                           ('INT', "cnEmitCount")):
            info.push_constant(kind, name)
        info.local_group_size(GROUP, GROUP, 1)
        info.compute_source(code)
        shader, err, log = sampler._compile_capturing(info)
        if shader is None:
            raise SdfCodeError("the code didn't compile:\n" + chain_errors(log, offset, self.source))
        return shader

    def set_emitter(self, points, normals, key):
        self.emit_p, self.emit_n, self.emit_count = emitter_textures(points, normals)
        self.emit_key = key

    def _bind_common(self, sh, values, dt=0.0, time_s=0.0, frame=0, reset=False, stagger=1.0):
        import gpu
        slots = np.zeros(256, np.float32)
        for i, prm in enumerate(self.params):
            slots[i] = float((values or {}).get(prm.name, prm.default))
        # keep a reference: a temporary would be freed before the dispatch runs
        self._ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 256, slots.tolist()))
        sh.uniform_block("cnParams", self._ubo)
        for name, tex in (("cnEmitP", self.emit_p), ("cnEmitN", self.emit_n)):
            try:
                sh.uniform_sampler(name, tex)
            except ValueError:
                pass
        for setter, name, value in ((sh.uniform_float, "cnDt", float(dt)),
                                    (sh.uniform_float, "uTime", float(time_s)),
                                    (sh.uniform_float, "uFrame", float(frame)),
                                    (sh.uniform_float, "cnStagger", float(stagger)),
                                    (sh.uniform_int, "cnCount", self.count),
                                    (sh.uniform_int, "cnRow", self.row),
                                    (sh.uniform_int, "cnReset", 1 if reset else 0),
                                    (sh.uniform_int, "cnEmitCount", self.emit_count)):
            try:
                setter(name, value)
            except ValueError:
                pass                  # the compiler drops uniforms the code never reads

    def _dispatch(self, dt, time_s, frame, reset, values, stagger=1.0):
        from . import gpu_guard
        if not gpu_guard.allowed():
            return
        sh = self.shader
        sh.image("cnPos", self.pos)
        sh.image("cnVel", self.vel)
        if self.ext is not None:
            sh.image("cnExt", self.ext)
        self._bind_common(sh, values, dt, time_s, frame, reset, stagger)
        gpu_guard.dispatch(sh, math.ceil(self.row / GROUP), math.ceil(self.rows / GROUP), 1)

    def reset(self, time_s=0.0, frame=0, values=None, stagger=1.0):
        self._dispatch(0.0, time_s, frame, True, values, stagger)

    def step(self, dt, time_s=0.0, frame=0, values=None):
        self._dispatch(dt, time_s, frame, False, values)

    def _read_tex(self, tex, n):
        buf = tex.read()
        buf.dimensions = self.row * self.rows * 4
        return np.frombuffer(buf, dtype=np.float32).reshape(-1, 4)[:n]

    def read(self, limit=0, values=None, attrs=(), time_s=0.0, frame=0):
        """{'position', 'velocity', 'speed', 'age', 'life', <attributes>} as numpy arrays of length
        count (or the first `limit` particles). Positions are where the particles are shown: after the
        chain's warps (a Bend), which never change the simulation itself.

        ``speed`` is there because Blender reserves the name ``velocity`` for motion
        blur, so a shader cannot read it back: shade with ``speed`` instead.
        """
        import gpu
        from . import gpu_guard
        n = self.count if not limit else min(self.count, int(limit))
        pos_tex, vel_tex = self.pos, self.vel
        if self.has_warp and gpu_guard.allowed():
            if self.warp_shader is None:
                self.warp_shader = self._compile(WARP_MAIN, extra_images=("cnOutP", "cnOutV"))
            out_p = gpu.types.GPUTexture((self.row, self.rows), format='RGBA32F')
            out_v = gpu.types.GPUTexture((self.row, self.rows), format='RGBA32F')
            sh = self.warp_shader
            sh.image("cnPos", self.pos)
            sh.image("cnVel", self.vel)
            sh.image("cnOutP", out_p)
            sh.image("cnOutV", out_v)
            self._bind_common(sh, values, 0.0, time_s, frame)
            gpu_guard.dispatch(sh, math.ceil(self.row / GROUP), math.ceil(self.rows / GROUP), 1)
            pos_tex, vel_tex = out_p, out_v
        out = {}
        for tex, names in ((pos_tex, ("position", "age")), (vel_tex, ("velocity", "life"))):
            arr = self._read_tex(tex, n)
            out[names[0]] = np.ascontiguousarray(arr[:, :3])
            out[names[1]] = np.ascontiguousarray(arr[:, 3])
        out["speed"] = np.linalg.norm(out["velocity"], axis=1).astype(np.float32)
        if self.ext is not None and attrs:
            arr = self._read_tex(self.ext, n)
            for i, name in enumerate(list(attrs)[:4]):
                out[name] = np.ascontiguousarray(arr[:, i])
        return out


def chain_errors(log, offset, source):
    """Compiler errors worded for the user. `source` is a composed chain, whose segments name the
    nodes; user_errors quotes the offending line, which carries the node's prefix (n2_...)."""
    from . import chain
    from .sdf_code import user_errors
    return chain.translate_errors(user_errors(log, offset, source), source)


_sims: dict[str, Sim] = {}


_single_cache: dict[str, tuple] = {}


def as_chain(source, values=None):
    """(composed source, values) for `source`: raw particle code (spawn / update, sliders by their own
    names) becomes a chain of one; a composed chain passes through unchanged."""
    if "void cnSpawn(" in source:
        return source, values
    key = hashlib.sha1(source.encode()).hexdigest()
    got = _single_cache.get(key)
    if got is None:
        comp = compose_single(source)
        got = (comp.source, {k[3:]: k for k in comp.values if k.startswith("n0_")})
        if len(_single_cache) > 64:
            _single_cache.clear()
        _single_cache[key] = got
    src, names = got
    vals = {}
    for raw, pref in names.items():
        if values and raw in values:
            vals[pref] = values[raw]
        elif values and raw.endswith("__i") and raw[:-3] in values:
            vals[pref] = values[raw[:-3]]
    return src, vals


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


def advance(name, source, count, frame, frame_start, fps, values=None, substeps=1, stagger=1.0,
            prewarm=0.0, emitter=None):
    """Bring the simulation to `frame` on the GPU, without reading anything back.

    Steps forward from where it already is; a jump backwards restarts from the start frame
    (pre-warming `prewarm` seconds first). `emitter` is (points, normals, key) or None.
    Returns (sim, steps).
    """
    source, values = as_chain(source, values)
    sim = get_sim(name, source, count)
    if emitter is not None and emitter[2] != sim.emit_key:
        sim.set_emitter(*emitter)
    elif emitter is None and sim.emit_key is not None:
        sim.set_emitter(np.zeros((0, 3)), np.zeros((0, 3)), None)
    substeps = max(1, min(int(substeps), 20))
    dt = 1.0 / (fps * substeps)
    frame = int(frame)
    start = int(frame_start)
    steps = 0

    def seconds(f):
        return (f - start) / fps

    if frame <= start or sim.frame is None or frame < sim.frame or frame - sim.frame > MAX_CATCHUP_STEPS:
        warm = int(round(max(0.0, prewarm) * fps))
        sim.reset(seconds(start) - warm / fps, start, values, stagger)
        for k in range(warm):                     # "opens in shape": run the warm-up before frame 1
            t = seconds(start) - (warm - k - 1) / fps
            for _ in range(substeps):
                sim.step(dt, t, start, values)
                steps += 1
        sim.frame = start
    while sim.frame < frame:
        nxt = sim.frame + 1
        for _ in range(substeps):
            sim.step(dt, seconds(nxt), nxt, values)
            steps += 1
        sim.frame = nxt
    return sim, steps


def simulate(name, source, count, frame, frame_start, fps, values=None, substeps=1, stagger=1.0,
             prewarm=0.0, emitter=None, limit=0, attrs=()):
    """Bring the simulation to `frame` and return its state. Returns (state dict, stats)."""
    t0 = time.perf_counter()
    source, values = as_chain(source, values)
    sim, steps = advance(name, source, count, frame, frame_start, fps, values, substeps, stagger,
                         prewarm, emitter)
    state = sim.read(limit, values, attrs, (frame - frame_start) / fps, frame)
    return state, {"count": len(state["position"]), "steps": steps, "sim_s": time.perf_counter() - t0,
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
