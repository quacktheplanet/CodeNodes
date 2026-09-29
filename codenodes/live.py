"""Rebuilding Code -> Mesh objects: on request, when their code text changes, and
per frame. Everything runs from timers or handlers on the main thread, and no
error escapes into Blender."""

from __future__ import annotations

import hashlib
import time
import traceback

import bpy

from . import build, props
from .sdf_code import SdfCodeError

DEBOUNCE_S = 0.15
POLL_S = 0.5
_pending: set[str] = set()


def scene_time(scene=None):
    """(seconds since the start frame, frame number) — what uTime and uFrame get."""
    scene = scene or bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    return (scene.frame_current - scene.frame_start) / fps, scene.frame_current


def compute_object(obj):
    """(MeshResult, stats) for this object at the current frame. Raises SdfCodeError."""
    s = obj.codenodes
    if s.text is None:
        raise SdfCodeError("no code: pick a text block")
    source = s.text.as_string()
    s.code_hash = hashlib.sha1(source.encode()).hexdigest()
    values = props.sync_params(s, source)
    seconds, frame = scene_time()
    return build.compute(source, tuple(s.bounds_min), tuple(s.bounds_max), s.resolution,
                         time_s=seconds, frame=frame, values=values)


def simulate_object(obj):
    """Bring a particle object's simulation (its whole chain) to the current frame and show it as
    points carrying velocity, age and every per-particle attribute the chain declares."""
    from . import gpu_live, links, particles
    s = obj.codenodes
    if s.text is None:
        raise SdfCodeError("no code: pick a text block")
    source = s.text.as_string()
    s.code_hash = hashlib.sha1(source.encode()).hexdigest()
    props.sync_params(s, source)
    comp, values = links.composite(obj)
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    emitter = gpu_live.emitter_for(obj) if s.emitter is not None else None
    limit = min(s.real_limit or s.count, particles.MAX_REAL)
    attrs = tuple(name for name, _d in comp.attrs)
    from . import gpu_cache
    owner, sim_comp, _sim_values = gpu_live.sim_owner(obj)
    cached = gpu_cache.playback(owner, sim_comp, scene)
    if cached is not None:                  # a GPU Cache plays back the baked frame
        t0 = time.perf_counter()
        state = cached.read(limit, values, attrs, (scene.frame_current - scene.frame_start) / fps,
                            scene.frame_current)
        st = {"count": len(state["position"]), "steps": 0, "sim_s": time.perf_counter() - t0,
              "frame": scene.frame_current}
    else:
        state, st = particles.simulate(obj.name, comp.source, s.count, scene.frame_current, scene.frame_start,
                                       fps, values, s.substeps, s.stagger, s.prewarm, emitter, limit, attrs)
    keep = []
    if s.get("real_keep_vel", True):
        keep += ["velocity", "speed"]
    if s.get("real_keep_age", True):
        keep += ["age", "life"]
    keep += list(attrs)
    particles.ensure_object(obj.name, s.point_radius)
    particles.fill_points(obj.data, state, extra=tuple(keep))
    return state, st


def build_deform(obj):
    """GPU Mesh made real: the incoming mesh (topology, UVs, attributes kept) with the code's positions,
    a `color` and a `value` attribute."""
    import numpy as np
    from . import gn_link, gpu_live
    s = obj.codenodes
    if s.text is None:
        raise SdfCodeError("no code: pick a text block")
    source = s.text.as_string()
    s.code_hash = hashlib.sha1(source.encode()).hexdigest()
    props.sync_params(s, source)
    tap = gn_link.tap_of(obj)
    if tap is None:
        raise SdfCodeError("nothing is wired into the Mesh input")
    from . import links
    links.composite(obj)                    # raises with the node and line if the chain has a mistake
    inp = gpu_live.deform_input(obj)
    if inp is None or len(inp[0]) == 0:
        raise SdfCodeError("the mesh wired into the Mesh input is empty")
    state = gpu_live.run_deform(obj, bpy.context.scene)
    if state is None:
        raise SdfCodeError("the GPU Mesh code could not run")
    pos, value, color = state.read()
    deps = bpy.context.evaluated_depsgraph_get()
    new = bpy.data.meshes.new_from_object(tap.evaluated_get(deps), preserve_all_data_layers=True,
                                          depsgraph=deps)
    if len(new.vertices) != len(pos):
        bpy.data.meshes.remove(new)
        raise SdfCodeError("the incoming mesh changed while the code ran; try again")
    new.vertices.foreach_set("co", np.ascontiguousarray(pos, np.float32).ravel())
    for name, data, kind in (("color", color, 'FLOAT_COLOR'), ("value", value, 'FLOAT')):
        attr = new.attributes.get(name)
        if attr is not None and (attr.domain != 'POINT' or attr.data_type != kind):
            new.attributes.remove(attr)
            attr = None
        if attr is None:
            attr = new.attributes.new(name, kind, 'POINT')
        attr.data.foreach_set("color" if kind == 'FLOAT_COLOR' else "value",
                              np.ascontiguousarray(data, np.float32).ravel())
    try:                                    # the colour shows in Solid view (Attribute) and renders by default
        ca = new.color_attributes.get("color")
        if ca is not None:
            new.color_attributes.active_color = ca
            new.color_attributes.render_color_index = new.color_attributes.active_color_index
    except (AttributeError, TypeError):
        pass
    new.update()
    old = obj.data
    old_name = old.name
    for mat in old.materials:
        new.materials.append(mat)
    obj.data = new
    if old.users == 0:
        bpy.data.meshes.remove(old)
    new.name = old_name
    return {"verts": len(new.vertices), "faces": len(new.polygons)}


def is_live_only(obj):
    """A GPU node that nothing makes real right now: drawn by gpu_live, nothing to build."""
    from . import links
    s = obj.codenodes
    return links.is_gpu(obj) and s.real_mode in ("NONE", "RENDER_ONLY")


def clear_live(obj):
    """Empty a live-only source's mesh, so no stale real result shows next to the live one."""
    me = obj.data
    if me is not None and (len(me.vertices) or len(me.polygons)):
        me.clear_geometry()
        me.update()


def build_shape(obj):
    """Build a parametric shape and put it in the object's mesh. (solid, stats)."""
    from . import shape_build
    s = obj.codenodes
    if s.text is None:
        raise SdfCodeError("no code: pick a text block")
    source = s.text.as_string()
    s.code_hash = hashlib.sha1(source.encode()).hexdigest()
    values = props.sync_params(s, source, 'SHAPE')
    if obj.mode == 'EDIT':
        raise SdfCodeError(f"'{obj.name}' is in Edit Mode; leave Edit Mode to rebuild it")
    shape_build.build_into(obj.data, source, values, s.smooth)
    return None, shape_build.mesh_stats(obj.data)


def rebuild(obj):
    """Rebuild one object now. Returns "" or an error message (also stored on the object)."""
    s = obj.codenodes
    from . import links
    kind = links.ekind(obj)
    try:
        if s.kind == 'STAGE' and kind != 'DEFORM':
            # a stage is compiled into the chains it belongs to: sync its sliders, then let them update
            if s.text is not None:
                source = s.text.as_string()
                s.code_hash = hashlib.sha1(source.encode()).hexdigest()
                props.sync_params(s, source)
            heads = links.heads_of(obj)
            s.last_error = ""
            s.stats = (f"in {len(heads)} chain{'s' if len(heads) != 1 else ''}" if heads else
                       "not connected: wire it after a GPU Particles node, or to a mesh")
            for h in heads:
                ho = bpy.data.objects.get(h)
                if ho is not None and not is_live_only(ho):
                    request(ho)
            from . import gpu_live
            gpu_live.redraw()
            return ""
        if is_live_only(obj) and not _force_real[0]:
            if s.text is not None:
                source = s.text.as_string()
                s.code_hash = hashlib.sha1(source.encode()).hexdigest()
                props.sync_params(s, source)
            clear_live(obj)
            s.last_error = ""
            what = {'PARTICLES': f"{s.count:,} particles", 'MESH': "raymarched", 'DEFORM': "vertex code"}[kind]
            s.stats = f"live on the GPU · {what} · add To Geometry to use it in nodes or renders"
            try:
                links.composite(obj)                # report a mistake anywhere in the chain on the node
            except SdfCodeError as exc:
                s.last_error = str(exc)
                return s.last_error
            from . import gpu_live
            gpu_live.redraw()
            return ""
        t_make = time.perf_counter()
        if kind == 'DEFORM':
            st = build_deform(obj)
            s.last_error = ""
            s.stats = (f"{st['verts']:,} vertices · {st['faces']:,} faces · "
                       f"made real in {(time.perf_counter() - t_make) * 1000:.0f} ms")
            return ""
        if s.kind == 'SHAPE':
            _solid, st = build_shape(obj)
            s.last_error = ""
            s.stats = (f"{st['quads']:,} quads + {st['tris']:,} tris · "
                       f"{st['sharp_edges']:,} sharp · "
                       f"{st['size'][0]:g} × {st['size'][1]:g} × {st['size'][2]:g} m")
            return ""
        if s.kind == 'PARTICLES':
            state, st = simulate_object(obj)
            s.last_error = ""
            s.stats = (f"{st['count']:,} particles · frame {st['frame']} · "
                       f"{st['steps']} step{'s' if st['steps'] != 1 else ''} · "
                       f"made real in {(time.perf_counter() - t_make) * 1000:.0f} ms")
            return ""
        result, st = compute_object(obj)
        build.swap_mesh(obj, result, s.smooth)
    except SdfCodeError as exc:
        s.last_error = str(exc)
        return s.last_error
    except Exception as exc:   # never let a bug take Blender down; report it instead
        traceback.print_exc()
        s.last_error = f"internal error ({type(exc).__name__}): {exc}"
        return s.last_error
    nx, ny, nz = st["dims"]
    s.last_error = ""
    s.stats = (f"{st['faces']:,} faces · grid {nx}×{ny}×{nz} · "
               f"GPU {st['sample_s'] * 1000:.0f} ms · mesh {st['mesh_s'] * 1000:.0f} ms · "
               f"made real in {(time.perf_counter() - t_make) * 1000:.0f} ms")
    if not st["faces"]:
        s.stats = "no surface inside the bounds · " + s.stats
    return ""


def _flush():
    if _rendering():                       # never run GPU code while a render is going
        return 0.5
    names = list(_pending)
    _pending.clear()
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.codenodes.enabled:
            rebuild(obj)
    return None


def request(obj):
    """Rebuild soon (collapses bursts of slider changes into one rebuild)."""
    _pending.add(obj.name)
    if not bpy.app.timers.is_registered(_flush):
        bpy.app.timers.register(_flush, first_interval=DEBOUNCE_S)


_render_active = [False]
_force_real = [False]          # set by the CodeNodes render loop: make live-only nodes real too


def make_real_now(obj):
    """Build a GPU node's real geometry now even if it's live-only (render loop)."""
    _force_real[0] = True
    try:
        return rebuild(obj)
    finally:
        _force_real[0] = False


@bpy.app.handlers.persistent
def _render_started(*_args):
    _render_active[0] = True


@bpy.app.handlers.persistent
def _render_ended(*_args):
    _render_active[0] = False


def _rendering():
    """True while any render runs: background jobs and blocking F12 / bpy.ops.render.render."""
    if _render_active[0]:
        return True
    try:
        return bpy.app.is_job_running('RENDER')
    except Exception:
        return False


def _poll_text():
    """Rebuild live objects whose code text changed (e.g. edited by Claude)."""
    try:
        if not _rendering():
            for obj in bpy.data.objects:
                s = getattr(obj, "codenodes", None)
                if s is None or not s.enabled or not s.live or s.text is None:
                    continue
                h = hashlib.sha1(s.text.as_string().encode()).hexdigest()
                if h != s.code_hash:
                    request(obj)
    except Exception:
        traceback.print_exc()
    return POLL_S


@bpy.app.handlers.persistent
def _on_frame(scene, depsgraph=None):
    # Runs before Blender evaluates the new frame (frame_change_pre), so the geometry made real here is
    # what that frame shows, and what Blender's Bake node captures when it bakes a To Geometry result.
    if _rendering():
        return
    try:
        for obj in scene.objects:
            s = getattr(obj, "codenodes", None)
            if s is not None and s.enabled and (s.animate or s.real_mode == 'EVERY_FRAME'):
                rebuild(obj)
    except Exception:
        traceback.print_exc()


_RENDER_HANDLERS = (("render_init", _render_started), ("render_complete", _render_ended),
                    ("render_cancel", _render_ended))


def register():
    for name, fn in _RENDER_HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)
    bpy.app.handlers.frame_change_pre.append(_on_frame)
    bpy.app.timers.register(_poll_text, first_interval=POLL_S, persistent=True)


def unregister():
    for name, fn in _RENDER_HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
    if _on_frame in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(_on_frame)
    for fn in (_poll_text, _flush):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
