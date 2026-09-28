"""Drawing GPU nodes live in the 3D viewport, straight from GPU memory.

A GPU node (GPU Particles, GPU Surface) that no Make Real node turns into geometry is shown live:
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
    s = getattr(obj, "codenodes", None)
    return s is not None and s.enabled and s.kind in GPU_KINDS and s.real_mode != "STANDALONE"


def shows_live(obj):
    """Drawn in the viewport by us: no Make Real makes it real in the viewport."""
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


def step_particles(src, scene):
    """Advance a live particle system to the scene's frame on the GPU (no readback)."""
    from . import particles, props
    s = src.codenodes
    source = _source_text(src)
    if source is None:
        return None
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    emitter = emitters.get(src.name) if s.emitter is not None else None
    sim, _steps = particles.advance(src.name, source, s.count, scene.frame_current, scene.frame_start, fps,
                                    _values(src), s.substeps, s.stagger, s.prewarm, emitter)
    return sim


_DRAW_MAIN = """
// ---- CodeNodes live drawing ----
void main() {
  int i = int(idx);
  int row = cnInts.x;
  ivec2 ij = ivec2(i % row, i / row);
  vec4 a = texelFetch(cnPosT, ij, 0), b = texelFetch(cnVelT, ij, 0);
  Particle p;
  p.position = a.xyz; p.age = a.w; p.velocity = b.xyz; p.life = b.w; p.seed = float(i) + 0.5;
  gl_Position = cnMVP * vec4(p.position, 1.0);
  gl_PointSize = cnColA.w;
  vec4 c = vec4(1.0);
#ifdef CN_LOOK
  if (cnInts.y == 2) c = look(p); else
#endif
  {
    float k = cnInts.y == 0 ? clamp(length(p.velocity) / cnMisc.x, 0.0, 1.0)
                            : clamp(p.age / max(p.life, 1e-4), 0.0, 1.0);
    c = vec4(mix(cnColA.rgb, cnColB.rgb, k), 1.0);
  }
  vColor = vec4(c.rgb * cnColB.w, c.a);
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


def particle_draw_shader(sim):
    """The point shader for a sim, including the user's look() when it has one."""
    import gpu
    from . import particles, sampler
    from .sdf_code import PRELUDE, SdfCodeError, param_defines, user_errors
    look = particles.has_look(sim.source)
    key = (sim.key, look)
    if sim.draw_shader is not None and sim.draw_key == key:
        return sim.draw_shader
    head = (("#define CN_LOOK\n" if look else "")
            + "#define uTime (cnMisc.y)\n#define uFrame (cnMisc.z)\n#define cnEmitCount (cnInts.w)\n"
            + PRELUDE + particles.PARTICLE_PRELUDE + param_defines(sim.params))
    code = head + (sim.source if look else "") + "\n" + _DRAW_MAIN
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
    # exactly 128 bytes of push constants (the most every backend guarantees)
    for kind, name in (('MAT4', "cnMVP"), ('VEC4', "cnColA"), ('VEC4', "cnColB"), ('VEC4', "cnMisc"),
                       ('IVEC4', "cnInts")):
        info.push_constant(kind, name)
    info.fragment_out(0, 'VEC4', "fragColor")
    info.vertex_source(code)
    info.fragment_source(_DRAW_FRAG)
    shader, err, log = sampler._compile_capturing(info)
    if shader is None:
        raise SdfCodeError("the look() code didn't compile:\n" + user_errors(log, head.count("\n"), sim.source))
    sim.draw_shader, sim.draw_key = shader, key
    return shader


_batches: dict[int, object] = {}


def _index_batch(n):
    import gpu
    b = _batches.get(n)
    if b is None:
        fmt = gpu.types.GPUVertFormat()
        fmt.attr_add(id="idx", comp_type='F32', len=1, fetch_mode='FLOAT')
        vbo = gpu.types.GPUVertBuf(fmt, n)
        vbo.attr_fill("idx", np.arange(n, dtype=np.float32))
        b = gpu.types.GPUBatch(type='POINTS', buf=vbo)
        if len(_batches) > 6:
            _batches.clear()
        _batches[n] = b
    return b


def draw_particles(src, host, rv3d, scene):
    import gpu
    s = src.codenodes
    sim = step_particles(src, scene)
    if sim is None:
        return
    sh = particle_draw_shader(sim)
    gpu_guard.note_draw()
    slots = np.zeros(256, np.float32)
    vals = _values(src)
    for i, prm in enumerate(sim.params):
        slots[i] = float(vals.get(prm.name, prm.default))
    ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 256, slots.tolist()))
    fps_ = scene.render.fps / (scene.render.fps_base or 1.0)
    solid = s.blend == 'SOLID'
    gpu.state.program_point_size_set(True)
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(solid)
    gpu.state.blend_set('NONE' if solid else 'ADDITIVE')
    sh.bind()
    sh.uniform_block("cnParams", ubo)
    sh.uniform_sampler("cnPosT", sim.pos)
    sh.uniform_sampler("cnVelT", sim.vel)
    for name, tex in (("cnEmitP", sim.emit_p), ("cnEmitN", sim.emit_n)):
        try:
            sh.uniform_sampler(name, tex)
        except ValueError:
            pass
    mode = {'SPEED': 0, 'AGE': 1, 'CODE': 2}[s.color_by]
    if mode == 2 and not sim.draw_key[1]:
        mode = 0                                  # "Code" colours, but the code has no look(): use speed
    for setter, name, value in (
            (sh.uniform_float, "cnMVP", rv3d.perspective_matrix @ host.matrix_world),
            (sh.uniform_float, "cnColA", (*s.color_a, float(s.point_px))),
            (sh.uniform_float, "cnColB", (*s.color_b, float(s.gain))),
            (sh.uniform_float, "cnMisc", (float(s.speed_range), (scene.frame_current - scene.frame_start) / fps_,
                                          float(scene.frame_current), 0.0)),
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


def draw_surface(src, host, region, rv3d, scene):
    from . import raymarch
    s = src.codenodes
    source = _source_text(src)
    if source is None:
        return
    fps_ = scene.render.fps / (scene.render.fps_base or 1.0)
    raymarch.draw(src.name + "|" + host.name, s, source, _values(src), host.matrix_world, region, rv3d, scene,
                  (scene.frame_current - scene.frame_start) / fps_, scene.frame_current)


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
                kind = src.codenodes.kind
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
    from . import deform
    s = src.codenodes
    source = _source_text(src)
    inp = deform_inputs.get(src.name)
    if source is None or inp is None or len(inp[0]) == 0:
        return None
    co, nrm, tris, key = inp
    state = deform.state_for(src.name, co, nrm, tris, key)
    fps_ = scene.render.fps / (scene.render.fps_base or 1.0)
    state.run(source, _values(src), (scene.frame_current - scene.frame_start) / fps_, scene.frame_current)
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
    for name in changed:                         # a tap re-evaluated: its GPU Mesh input changed
        if name.startswith("CN Tap · "):
            _deform_dirty.add(name[len("CN Tap · "):])
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
