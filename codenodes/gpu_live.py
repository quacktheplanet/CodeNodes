"""Drawing GPU nodes live in the 3D viewport, straight from GPU memory.

A GPU node (GPU Particles, GPU Surface) that no To Geometry node turns into geometry is shown live:
particles are stepped by a compute shader and drawn as points from the same textures (no copy to
Blender), surfaces are raymarched (raymarch.py). Both are drawn in the space of the object whose
Geometry Nodes hold the node, and depth-tested against the scene.

Nothing here runs while a render is going (gpu_guard). Emitter geometry (Emit From) is read on
the main thread from a timer, never from the draw callback.
"""

from __future__ import annotations

import time
import traceback

import bpy
import numpy as np

from . import gpu_guard

# source object name -> [host object names] (filled by gn_link.sync)
hosts: dict[str, list[str]] = {}
# source object name -> (points, normals, key) in the first host's space (filled from a timer)
emitters: dict[str, tuple] = {}
_emit_dirty: set[str] = set()
# source name -> (co, normals, tris, key) read from its tap (what a GPU Mesh node receives)
deform_inputs: dict[str, tuple] = {}
_deform_dirty: set[str] = set()
_handle = [None]
_fps = {"frames": 0, "t0": time.perf_counter(), "value": 0.0, "last_ms": 0.0}

GPU_KINDS = ('MESH', 'PARTICLES', 'DEFORM')


def is_gpu_source(obj):
    from . import links
    s = getattr(obj, "codenodes", None)
    return s is not None and s.enabled and links.is_gpu(obj) and s.real_mode != "STANDALONE"


def shows_live(obj):
    """Drawn in the viewport by us: no To Geometry makes it real in the viewport."""
    return is_gpu_source(obj) and obj.codenodes.real_mode in ("NONE", "RENDER_ONLY")


def redraw():
    wm = bpy.context.window_manager
    for win in getattr(wm, "windows", ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def fps():
    return _fps["value"]


# ---- emitters ---------------------------------------------------------------------------------

def sample_emitter(emitter, host, n=None):
    """(points, normals) spread evenly by area over `emitter`'s evaluated surface (its vertices if it
    has no faces), in `host`'s object space."""
    from .particles import EMIT_SAMPLES
    n = n or EMIT_SAMPLES
    deps = bpy.context.evaluated_depsgraph_get()
    ev = emitter.evaluated_get(deps)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32)
    try:
        m = (host.matrix_world.inverted_safe() @ emitter.matrix_world) if host is not None else emitter.matrix_world
        mat = np.array(m, dtype=np.float64)
        rot = np.array(m.to_3x3().inverted_safe().transposed(), dtype=np.float64)
        nv = len(me.vertices)
        co = np.empty(nv * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3).astype(np.float64)
        me.calc_loop_triangles()
        nt = len(me.loop_triangles)
        rng = np.random.default_rng(7)
        if nt == 0:
            if nv == 0:
                return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32)
            idx = rng.integers(0, nv, n) if nv > n else np.arange(nv)
            pts = co[idx]
            nrm = np.tile([0.0, 0.0, 1.0], (len(idx), 1))
        else:
            tri = np.empty(nt * 3, np.int32)
            me.loop_triangles.foreach_get("vertices", tri)
            tri = tri.reshape(-1, 3)
            a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
            cross = np.cross(b - a, c - a)
            area = np.linalg.norm(cross, axis=1)
            total = area.sum()
            if total <= 0:
                return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32)
            pick = rng.choice(nt, size=n, p=area / total)
            u, v = rng.random(n), rng.random(n)
            flip = u + v > 1.0
            u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
            pts = a[pick] + (b[pick] - a[pick]) * u[:, None] + (c[pick] - a[pick]) * v[:, None]
            nrm = cross[pick] / np.maximum(area[pick], 1e-12)[:, None]
        pts_h = np.c_[pts, np.ones(len(pts))] @ mat.T
        nrm = nrm @ rot.T
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)[:, None]
        return pts_h[:, :3].astype(np.float32), nrm.astype(np.float32)
    finally:
        ev.to_mesh_clear()


def emitter_for(src):
    """(points, normals, key) for a source's Emit From object, or None."""
    s = src.codenodes
    em = s.emitter
    if em is None or s.kind != 'PARTICLES':
        emitters.pop(src.name, None)
        return None
    cached = emitters.get(src.name)
    if cached is None or src.name in _emit_dirty:
        host_names = hosts.get(src.name) or []
        host = bpy.data.objects.get(host_names[0]) if host_names else None
        pts, nrm = sample_emitter(em, host)
        key = f"{em.name}:{len(pts)}:{time.perf_counter():.6f}"
        cached = (pts, nrm, key)
        emitters[src.name] = cached
        _emit_dirty.discard(src.name)
    return cached


# ---- stepping and drawing ----------------------------------------------------------------------

def _values(src):
    return {p.name: p.value for p in src.codenodes.params}


def _source_text(src):
    s = src.codenodes
    return s.text.as_string() if s.text is not None else None


def sim_owner(src):
    """(object whose GPU simulation a pipeline uses, that pipeline's composite and values).

    A branch whose simulation part matches its head's (same source and born/behave stages, the same
    functions and attributes) shares the head's simulation: it only adds its own looks and warps."""
    from . import links
    comp, values = links.composite(src)
    base = links.base_obj(src)
    if base is not src:
        try:
            bcomp, bvalues = links.composite(base)
        except Exception:
            bcomp = None
        if bcomp is not None and bcomp.sim_key and bcomp.sim_key == comp.sim_key:
            return base, bcomp, bvalues
    return src, comp, values


def step_particles(src, scene):
    """Advance a live particle pipeline to the scene's frame on the GPU (no readback).
    Returns (sim, composite, values): the composite and values are this pipeline's own (its look and
    warps); the sim may be its head's when the two share a simulation."""
    from . import gpu_cache, particles
    s = src.codenodes
    if s.text is None:
        return None
    from . import links
    comp, values = links.composite(src)
    owner, sim_comp, sim_values = sim_owner(src)
    os_ = owner.codenodes
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    cached = gpu_cache.playback(owner, sim_comp, scene)
    if cached is not None:
        return cached, comp, values
    emitter = emitters.get(owner.name) if os_.emitter is not None else None
    sim, _steps = particles.advance(owner.name, sim_comp.source, os_.count, scene.frame_current,
                                    scene.frame_start, fps, sim_values, os_.substeps, os_.stagger,
                                    os_.prewarm, emitter)
    return sim, comp, values


_comp_params: dict[str, list] = {}


def comp_params(source):
    """The @param slots of a composed source (cached)."""
    import hashlib
    from .sdf_code import parse_params
    key = hashlib.sha1(source.encode()).hexdigest()
    got = _comp_params.get(key)
    if got is None:
        got = parse_params(source)
        if len(_comp_params) > 64:
            _comp_params.clear()
        _comp_params[key] = got
    return got


_DRAW_MAIN = """
// ---- CodeNodes live drawing (every local is cn-prefixed: attribute names are macros) ----
void main() {
  int cnI = int(idx);
  int cnRowW = cnInts.x;
  ivec2 cnIJ = ivec2(cnI % cnRowW, cnI / cnRowW);
  vec4 cnA = texelFetch(cnPosT, cnIJ, 0), cnB = texelFetch(cnVelT, cnIJ, 0);
  Particle p;
  p.position = cnA.xyz; p.age = cnA.w; p.velocity = cnB.xyz; p.life = cnB.w; p.seed = float(cnI) + 0.5;
#ifdef CN_HAS_X
  p.cnX = texelFetch(cnExtT, cnIJ, 0);
#else
  p.cnX = CN_XDEFAULT;
#endif
  gl_Position = cnMVP * vec4(cnWarp(p.position), 1.0);
  gl_PointSize = cnColA.w;
  vec4 cnC = vec4(1.0);
#ifdef CN_LOOK
  if (cnInts.y == 2) cnC = look(p); else
#endif
  {
    float cnT = cnInts.y == 0 ? clamp(length(p.velocity) / cnMisc.x, 0.0, 1.0)
                              : clamp(p.age / max(p.life, 1e-4), 0.0, 1.0);
    cnC = vec4(mix(cnColA.rgb, cnColB.rgb, cnT), 1.0);
  }
  vColor = vec4(cnC.rgb * cnColB.w, cnC.a);
  cnSolidF = cnInts.z != 0 ? 1.0 : 0.0;
}
"""

_DRAW_FRAG = """
void main() {
  if (cnSolidF > 0.5) {
    vec2 q = gl_PointCoord * 2.0 - 1.0;
    if (dot(q, q) > 1.0) discard;
  }
  fragColor = vColor;
}
"""


_draw_shaders: dict = {}


def particle_draw_shader(sim, comp=None):
    """The point shader for a pipeline, including its look() when it has one. `comp` is the
    pipeline's own composite (its look and warps); the sim supplies the particle state."""
    import hashlib
    import gpu
    from . import particles, sampler
    from .sdf_code import PRELUDE, SdfCodeError, param_defines, user_errors
    source = comp.source if comp is not None else sim.source
    look = particles.has_look(source)
    key = (hashlib.sha1(source.encode()).hexdigest(), sim.has_x, look)
    got = _draw_shaders.get(key)
    if got is not None:
        return got
    head = (("#define CN_LOOK\n" if look else "")
            + "#define uTime (cnMisc.y)\n#define uFrame (cnMisc.z)\n#define cnEmitCount (cnInts.w)\n"
            + PRELUDE + particles.PARTICLE_PRELUDE + param_defines(comp_params(source)))
    code = head + source + "\n" + _DRAW_MAIN
    iface = gpu.types.GPUStageInterfaceInfo("cn_pt_iface")
    iface.smooth('VEC4', "vColor")
    iface.flat('FLOAT', "cnSolidF")
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source("struct CNParams { vec4 v[64]; };")
    info.uniform_buf(0, "CNParams", "cnParams")
    info.vertex_in(0, 'FLOAT', "idx")
    info.vertex_out(iface)
    info.sampler(0, 'FLOAT_2D', "cnPosT")
    info.sampler(1, 'FLOAT_2D', "cnVelT")
    info.sampler(2, 'FLOAT_2D', "cnEmitP")
    info.sampler(3, 'FLOAT_2D', "cnEmitN")
    if sim.has_x:
        info.sampler(4, 'FLOAT_2D', "cnExtT")
    # exactly 128 bytes of push constants (the most every backend guarantees)
    for kind, name in (('MAT4', "cnMVP"), ('VEC4', "cnColA"), ('VEC4', "cnColB"), ('VEC4', "cnMisc"),
                       ('IVEC4', "cnInts")):
        info.push_constant(kind, name)
    info.fragment_out(0, 'VEC4', "fragColor")
    info.vertex_source(code)
    info.fragment_source(_DRAW_FRAG)
    shader, err, log = sampler._compile_capturing(info)
    if shader is None:
        raise SdfCodeError("the look() code didn't compile:\n" + user_errors(log, head.count("\n"), source))
    if len(_draw_shaders) > 32:
        _draw_shaders.clear()
    _draw_shaders[key] = shader
    return shader


_batches: dict[int, object] = {}


def _index_batch(n, prim='POINTS'):
    import gpu
    b = _batches.get((n, prim))
    if b is None:
        fmt = gpu.types.GPUVertFormat()
        fmt.attr_add(id="idx", comp_type='F32', len=1, fetch_mode='FLOAT')
        vbo = gpu.types.GPUVertBuf(fmt, n)
        vbo.attr_fill("idx", np.arange(n, dtype=np.float32))
        b = gpu.types.GPUBatch(type=prim, buf=vbo)
        if len(_batches) > 8:
            _batches.clear()
        _batches[(n, prim)] = b
    return b


def _bind_particles(sh, sim):
    sh.uniform_sampler("cnPosT", sim.pos)
    sh.uniform_sampler("cnVelT", sim.vel)
    for name, tex in (("cnEmitP", sim.emit_p), ("cnEmitN", sim.emit_n), ("cnExtT", sim.ext)):
        if tex is None:
            continue
        try:
            sh.uniform_sampler(name, tex)
        except ValueError:
            pass


def _ubo(sim, values, cam=None, comp=None):
    import gpu
    slots = np.zeros(256, np.float32)
    params = comp_params(comp.source) if comp is not None else sim.params
    for i, prm in enumerate(params[:252]):
        slots[i] = float(values.get(prm.name, prm.default))
    if cam is not None:
        slots[252:255] = tuple(cam)
    return gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 256, slots.tolist()))


def draw_particles(src, host, rv3d, scene):
    import gpu
    s = src.codenodes
    got = step_particles(src, scene)
    if got is None:
        return
    sim, comp, values = got
    fps_ = scene.render.fps / (scene.render.fps_base or 1.0)
    t = (scene.frame_current - scene.frame_start) / fps_
    shape = comp.shape if comp.has_look else None
    if shape in ("glow", "firefly", "streak"):
        draw_sprites(sim, comp, values, host, rv3d, scene, t, shape)
        return
    sh = particle_draw_shader(sim, comp)
    gpu_guard.note_draw()
    cam = host.matrix_world.inverted_safe() @ rv3d.view_matrix.inverted().translation
    ubo = _ubo(sim, values, cam, comp)
    solid = s.blend == 'SOLID'
    gpu.state.program_point_size_set(True)
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(solid)
    gpu.state.blend_set('NONE' if solid else 'ADDITIVE')
    sh.bind()
    sh.uniform_block("cnParams", ubo)
    _bind_particles(sh, sim)
    mode = {'SPEED': 0, 'AGE': 1, 'CODE': 2}[s.color_by]
    if comp.has_look and s.color_by != 'AGE':
        mode = 2                                  # a Look stage in the chain decides the colour
    if mode == 2 and not comp.has_look:
        mode = 0                                  # "Code" colours, but the code has no look(): use speed
    for setter, name, value in (
            (sh.uniform_float, "cnMVP", rv3d.perspective_matrix @ host.matrix_world),
            (sh.uniform_float, "cnColA", (*s.color_a, float(s.point_px))),
            (sh.uniform_float, "cnColB", (*s.color_b, float(s.gain))),
            (sh.uniform_float, "cnMisc", (float(s.speed_range), t, float(scene.frame_current), 0.0)),
            (sh.uniform_int, "cnInts", (sim.row, mode, 1 if solid else 0, sim.emit_count))):
        try:
            setter(name, value)
        except ValueError:
            pass
    _index_batch(sim.count).draw(sh)
    gpu.state.blend_set('NONE')
    gpu.state.program_point_size_set(False)
    gpu.state.depth_mask_set(False)
    gpu.state.depth_test_set('NONE')


# ---- sprites: a glowing billboard per particle, and for fireflies two flapping wings ---------------------
_SPRITE_MAIN = """
// ---- CodeNodes sprites (every local is cn-prefixed: attribute names are macros) ----
void main() {
  int cnV = int(idx);
  int cnPer = cnInts.z;                      // vertices per particle: 6 (glow) or 12 (two wings)
  int cnI = cnV / cnPer, cnK = cnV % cnPer;
  int cnRowW = cnInts.x;
  ivec2 cnIJ = ivec2(cnI % cnRowW, cnI / cnRowW);
  vec4 cnA = texelFetch(cnPosT, cnIJ, 0), cnB = texelFetch(cnVelT, cnIJ, 0);
  Particle p;
  p.position = cnA.xyz; p.age = cnA.w; p.velocity = cnB.xyz; p.life = cnB.w; p.seed = float(cnI) + 0.5;
#ifdef CN_HAS_X
  p.cnX = texelFetch(cnExtT, cnIJ, 0);
#else
  p.cnX = CN_XDEFAULT;
#endif
  vec4 cnCol = look(p);
  vec3 cnC = cnWarp(p.position);
  // the six corners of a quad, as two triangles
  int cnQ = cnK % 6;
  vec2 cnCorner = vec2((cnQ == 1 || cnQ == 2 || cnQ == 4) ? 1.0 : -1.0, (cnQ == 2 || cnQ == 4 || cnQ == 5) ? 1.0 : -1.0);
  vUV = cnCorner;
  float cnSize = cnMisc.x;
  if (cnInts.y == 0) {                       // glow: a billboard facing the camera
    vec4 cnClip = cnMVP * vec4(cnC, 1.0);
    // its depth is taken a halo's width towards the camera, so the glow isn't sliced off where it
    // meets the ground (the camera position in this object's space sits in the last parameter slot)
    vec3 cnToCam = cnParams.v[63].xyz - cnC;
    vec4 cnNear = cnMVP * vec4(cnC + normalize(cnToCam + 1e-6) * min(cnSize * 2.5, 0.9 * length(cnToCam)), 1.0);
    cnClip.xy += cnCorner * cnSize * cnProj.xy * cnProj.z;
    cnClip.z = cnNear.z / cnNear.w * cnClip.w;
    gl_Position = cnClip;
    vColor = cnCol;
    vKind = 0.0;
  } else if (cnInts.y == 2) {                // streak: a quad from where it was to where it is
    vec3 cnTail = cnWarp(p.position - p.velocity * cnMisc.z);
    vec4 cnH = cnMVP * vec4(cnC, 1.0), cnT = cnMVP * vec4(cnTail, 1.0);
    vec2 cnD = cnH.xy / max(cnH.w, 1e-6) - cnT.xy / max(cnT.w, 1e-6);
    if (dot(cnD, cnD) < 1e-12) cnD = vec2(1.0, 0.0);
    vec2 cnN = normalize(vec2(-cnD.y, cnD.x));
    float cnAlong = cnCorner.y * 0.5 + 0.5;          // 0 at the tail, 1 at the head
    vec4 cnClip = mix(cnT, cnH, cnAlong);
    cnClip.xy += cnN * cnCorner.x * cnSize * (0.35 + 0.65 * cnAlong) * cnProj.xy * cnProj.z;
    gl_Position = cnClip;
    vUV = vec2(cnCorner.x, cnAlong);
    vColor = cnCol;
    vKind = 2.0;
  } else {                                   // wings: two quads flapping about the direction of travel
    vec3 cnAhead = cnWarp(p.position + p.velocity * 0.02) - cnC;
    vec3 cnF = length(cnAhead) > 1e-6 ? normalize(cnAhead) : vec3(1.0, 0.0, 0.0);
    vec3 cnUp = abs(cnF.z) > 0.95 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 0.0, 1.0);
    vec3 cnR = normalize(cross(cnF, cnUp));
    cnUp = cross(cnR, cnF);
    float cnPh = rand1(p.seed * 5.31);
    // a fast beat with a short pause at the top of each stroke: reads as flapping, not shimmering
    float cnBeat = fract(uTime * cnMisc.y + cnPh);
    float cnFlap = 0.95 * (1.0 - pow(abs(sin(cnBeat * 3.14159265)), 0.6)) + 0.1;
    float cnSide = cnK < 6 ? 1.0 : -1.0;
    vec3 cnW = cnR * cnSide * cos(cnFlap) + cnUp * sin(cnFlap);
    float cnSpan = cnSize * cnMisc.z, cnChord = cnSpan * 0.42;
    // the wing root sits on the thorax, a little ahead of the glowing abdomen
    vec3 cnRoot = cnC + cnF * cnSize * 0.9 + cnUp * cnSize * 0.25;
    vec3 cnP = cnRoot + cnW * (0.5 + 0.5 * cnCorner.x) * cnSpan
             + (-cnF * 0.55 + cnF * cnCorner.y * 0.5) * cnChord;
    gl_Position = cnMVP * vec4(cnP, 1.0);
    // lit a little by the insect's own glow near the root
    vColor = vec4(mix(vec3(0.95, 0.85, 0.55), vec3(0.78, 0.9, 1.0), 0.5 + 0.5 * cnCorner.x), 0.6);
    vKind = 1.0;
  }
}
"""

_SPRITE_FRAG = """
void main() {
  float r2 = dot(vUV, vUV);
  if (vKind < 0.5) {
    if (r2 > 1.0) discard;
    // a hot core and a wide soft halo: reads as glow even without bloom
    float core = exp(-r2 * 70.0), halo = exp(-r2 * 9.0) * 0.16;
    fragColor = vec4(vColor.rgb * (core * 2.0 + halo), 1.0);
  } else if (vKind < 1.5) {
    // a wing: a teardrop outline (wide near the tip, narrow at the root), a bright rim, and veins
    vec2 e = vec2(vUV.x * 0.5 + 0.5, vUV.y);          // e.x: root 0 -> tip 1, e.y: across (-1..1)
    float width = 0.35 + 0.65 * sin(clamp(e.x, 0.0, 1.0) * 2.6);
    float d = abs(e.y) / max(width, 1e-3);
    if (d > 1.0 || e.x > 0.98) discard;
    float tip = smoothstep(0.98, 0.8, e.x);
    float rim = smoothstep(0.7, 1.0, d) + smoothstep(0.8, 0.98, e.x) * 0.6;
    float vein = 0.0;
    for (int i = 0; i < 3; i++) {
      float off = (float(i) - 1.0) * 0.45 * e.x;
      vein = max(vein, smoothstep(0.035, 0.0, abs(e.y - off)) * smoothstep(0.05, 0.25, e.x));
    }
    float a = vColor.a * (0.28 + 0.55 * rim + 0.35 * vein) * tip;
    fragColor = vec4(vColor.rgb * (0.55 + 0.6 * rim + 0.4 * vein), a);
  } else {
    // a streak: brightest at the head, fading to nothing at the tail and the edges
    float a = (1.0 - vUV.x * vUV.x) * vUV.y * vUV.y;
    fragColor = vec4(vColor.rgb * a, 1.0);
  }
}
"""

_sprite_shaders: dict = {}


def sprite_shader(sim, comp=None):
    import gpu
    from . import particles, sampler
    from .sdf_code import PRELUDE, SdfCodeError, param_defines
    import hashlib
    source = comp.source if comp is not None else sim.source
    skey = (hashlib.sha1(source.encode()).hexdigest(), sim.has_x)
    got = _sprite_shaders.get(skey)
    if got is not None:
        return got
    head = ("#define uTime (cnMisc.w)\n#define uFrame (0.0)\n#define cnEmitCount (cnInts.w)\n"
            + PRELUDE + particles.PARTICLE_PRELUDE + param_defines(comp_params(source)))
    code = head + source + "\n" + _SPRITE_MAIN
    iface = gpu.types.GPUStageInterfaceInfo("cn_sp_iface")
    iface.smooth('VEC4', "vColor")
    iface.smooth('VEC2', "vUV")
    iface.flat('FLOAT', "vKind")
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source("struct CNParams { vec4 v[64]; };")
    info.uniform_buf(0, "CNParams", "cnParams")
    info.vertex_in(0, 'FLOAT', "idx")
    info.vertex_out(iface)
    info.sampler(0, 'FLOAT_2D', "cnPosT")
    info.sampler(1, 'FLOAT_2D', "cnVelT")
    info.sampler(2, 'FLOAT_2D', "cnEmitP")
    info.sampler(3, 'FLOAT_2D', "cnEmitN")
    if sim.has_x:
        info.sampler(4, 'FLOAT_2D', "cnExtT")
    # 128 bytes: MVP, (size, flap Hz, wing, time), projection scale, ints
    for kind, name in (('MAT4', "cnMVP"), ('VEC4', "cnMisc"), ('VEC4', "cnProj"), ('IVEC4', "cnInts")):
        info.push_constant(kind, name)
    info.fragment_out(0, 'VEC4', "fragColor")
    info.vertex_source(code)
    info.fragment_source(_SPRITE_FRAG)
    shader, err, log = sampler._compile_capturing(info)
    if shader is None:
        from .sdf_code import user_errors
        raise SdfCodeError("the look() code didn't compile:\n" + user_errors(log, head.count("\n"), source))
    if len(_sprite_shaders) > 32:
        _sprite_shaders.clear()
    _sprite_shaders[skey] = shader
    return shader


def draw_sprites(sim, comp, values, host, rv3d, scene, t, shape):
    import gpu
    sh = sprite_shader(sim, comp)
    gpu_guard.note_draw()
    cam = host.matrix_world.inverted_safe() @ rv3d.view_matrix.inverted().translation
    ubo = _ubo(sim, values, cam, comp)
    lu = comp.look_unit or ""
    size = float(values.get(lu + "size", 0.02))
    flap = float(values.get(lu + "flap", 16.0))
    wing = float(values.get(lu + "wing", 1.5))
    trail = float(values.get(lu + "trail", 0.25))
    mvp = rv3d.perspective_matrix @ host.matrix_world
    win = rv3d.window_matrix
    # a world-size quad in clip space: the projection's x/y scale (object scale ignored)
    proj = (float(win[0][0]), float(win[1][1]), max(host.matrix_world.to_scale()), 0.0)
    passes = [(0, 6, 'ADDITIVE', False)]
    if shape == "firefly":
        passes.append((1, 12, 'ALPHA', False))         # the glow, then the wings over it (they're on top)
    elif shape == "streak":
        passes = [(2, 6, 'ADDITIVE', False)]
    gpu.state.depth_test_set('LESS_EQUAL')
    sh.bind()
    sh.uniform_block("cnParams", ubo)
    _bind_particles(sh, sim)
    for kind, per, blend, depth_write in passes:
        gpu.state.blend_set(blend)
        gpu.state.depth_mask_set(depth_write)
        for setter, name, value in ((sh.uniform_float, "cnMVP", mvp),
                                    (sh.uniform_float, "cnMisc", (size * (1.0 if kind else 3.0), flap,
                                                                  trail if kind == 2 else wing, t)),
                                    (sh.uniform_float, "cnProj", proj),
                                    (sh.uniform_int, "cnInts", (sim.row, kind, per, sim.emit_count))):
            try:
                setter(name, value)
            except ValueError:
                pass
        _index_batch(sim.count * per, 'TRIS').draw(sh)
    gpu.state.blend_set('NONE')
    gpu.state.depth_mask_set(False)
    gpu.state.depth_test_set('NONE')


def draw_surface(src, host, region, rv3d, scene):
    from . import raymarch
    s = src.codenodes
    source = _source_text(src)
    if source is None:
        return
    from . import lights
    fps_ = scene.render.fps / (scene.render.fps_base or 1.0)
    lit, mat = lights.surface_uniforms(src, scene)
    raymarch.draw(src.name + "|" + host.name, s, source, _values(src), host.matrix_world, region, rv3d, scene,
                  (scene.frame_current - scene.frame_start) / fps_, scene.frame_current, lit, mat)


def _draw():
    if gpu_guard.rendering():
        return                                  # never touch the GPU while a render runs
    ctx = bpy.context
    region, rv3d, scene = ctx.region, ctx.region_data, ctx.scene
    if rv3d is None or scene is None:
        return
    t0 = time.perf_counter()
    drew = False
    view_layer = ctx.view_layer
    for src_name, host_names in list(hosts.items()):
        src = bpy.data.objects.get(src_name)
        if src is None or not shows_live(src):
            continue
        for hname in host_names:
            host = bpy.data.objects.get(hname)
            if host is None or host.name not in view_layer.objects or not host.visible_get(view_layer=view_layer):
                continue
            try:
                from . import links
                kind = links.ekind(src)
                if kind == 'PARTICLES':
                    draw_particles(src, host, rv3d, scene)
                elif kind == 'DEFORM':
                    draw_deform(src, host, rv3d, scene)
                else:
                    draw_surface(src, host, region, rv3d, scene)
                drew = True
                if src.codenodes.last_error.startswith("live: "):
                    src.codenodes.last_error = ""
            except Exception as exc:  # a bad shader must never take the viewport down
                from .sdf_code import SdfCodeError
                if not src.codenodes.last_error:
                    src.codenodes.last_error = "live: " + (str(exc) or type(exc).__name__)
                    if not isinstance(exc, SdfCodeError):
                        traceback.print_exc()
    if drew:
        _fps["frames"] += 1
        _fps["total"] = _fps.get("total", 0) + 1
        _fps["last_ms"] = (time.perf_counter() - t0) * 1000
        now = time.perf_counter()
        if now - _fps["t0"] > 1.0:
            _fps["value"] = _fps["frames"] / (now - _fps["t0"])
            _fps["frames"], _fps["t0"] = 0, now


def mark_deform_dirty(name):
    _deform_dirty.add(name)


def read_tap(src):
    """(co, normals, tris, key) of the mesh a GPU Mesh node receives, read from its tap object."""
    import hashlib
    from . import gn_link
    tap = gn_link.tap_of(src)
    if tap is None:
        return None
    deps = bpy.context.evaluated_depsgraph_get()
    ev = tap.evaluated_get(deps)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        return None
    try:
        n = len(me.vertices)
        co = np.empty(n * 3, np.float32)
        me.vertices.foreach_get("co", co)
        nrm = np.empty(n * 3, np.float32)
        me.vertex_normals.foreach_get("vector", nrm)
        me.calc_loop_triangles()
        tris = np.empty(len(me.loop_triangles) * 3, np.int32)
        me.loop_triangles.foreach_get("vertices", tris)
    finally:
        ev.to_mesh_clear()
    key = hashlib.sha1(co.tobytes() + tris.tobytes()).hexdigest()
    return co.reshape(-1, 3), nrm.reshape(-1, 3), tris.reshape(-1, 3), key


def deform_input(src, fresh=False):
    """The cached incoming mesh for a GPU Mesh source (re-read when its tap changed)."""
    got = deform_inputs.get(src.name)
    if got is None or fresh or src.name in _deform_dirty:
        got = read_tap(src)
        _deform_dirty.discard(src.name)
        if got is not None:
            deform_inputs[src.name] = got
    return got


def run_deform(src, scene):
    """Run a GPU Mesh node's code on its incoming mesh. Returns the MeshState, or None."""
    from . import deform, links
    s = src.codenodes
    inp = deform_inputs.get(src.name)
    if s.text is None or inp is None or len(inp[0]) == 0:
        return None
    comp, values = links.composite(src)
    co, nrm, tris, key = inp
    state = deform.state_for(src.name, co, nrm, tris, key)
    fps_ = scene.render.fps / (scene.render.fps_base or 1.0)
    state.run(comp.source, values, (scene.frame_current - scene.frame_start) / fps_, scene.frame_current)
    return state


def draw_deform(src, host, rv3d, scene):
    from . import deform
    state = run_deform(src, scene)
    if state is not None:
        deform.draw(state, host.matrix_world, rv3d)


def mark_emitters_dirty(names=None):
    if names is None:
        _emit_dirty.update(hosts.keys())
    else:
        _emit_dirty.update(names)


def refresh_emitters():
    """Re-read Emit From geometry for live sources that need it (main thread, from a timer)."""
    if gpu_guard.rendering():
        return
    for src_name in list(hosts.keys()):
        src = bpy.data.objects.get(src_name)
        if src is not None and is_gpu_source(src) and src.codenodes.emitter is not None:
            if src_name in _emit_dirty or src_name not in emitters:
                try:
                    emitter_for(src)
                    redraw()
                except Exception:
                    traceback.print_exc()


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    """An Emit From object changed: its samples are re-read on the next tick."""
    if not hosts:
        return
    changed = {u.id.original.name for u in depsgraph.updates
               if isinstance(u.id, bpy.types.Object) and (u.is_updated_geometry or u.is_updated_transform)}
    if not changed:
        return
    from . import links
    for name in changed:                         # a tap re-evaluated: its GPU Mesh input changed
        if name.startswith("CN Tap · "):
            head = name[len("CN Tap · "):]
            _deform_dirty.update(links.pipelines_of(head))
    for src_name in hosts:
        src = bpy.data.objects.get(src_name)
        s = getattr(src, "codenodes", None)
        if s is not None and s.emitter is not None and (s.emitter.name in changed
                                                          or any(h in changed for h in hosts[src_name])):
            _emit_dirty.add(src_name)


def refresh_deform_inputs():
    """Re-read the incoming mesh of GPU Mesh nodes whose tap changed (main thread, timer)."""
    if gpu_guard.rendering():
        return
    for name in list(_deform_dirty):
        src = bpy.data.objects.get(name)
        if src is None:
            _deform_dirty.discard(name)
            continue
        try:
            deform_input(src, fresh=True)
            if src.codenodes.real_mode not in ("NONE", "RENDER_ONLY"):
                from . import live
                live.request(src)
            redraw()
        except Exception:
            traceback.print_exc()
            _deform_dirty.discard(name)


def _tick():
    try:
        if _emit_dirty:
            refresh_emitters()
        if _deform_dirty:
            refresh_deform_inputs()
    except Exception:
        traceback.print_exc()
    return 0.2


def register():
    if _handle[0] is None:
        _handle[0] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.timers.register(_tick, first_interval=0.3, persistent=True)


def unregister():
    if _handle[0] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle[0], 'WINDOW')
        _handle[0] = None
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    hosts.clear()
    emitters.clear()
