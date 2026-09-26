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
    """Bring a particle object's simulation to the current frame and show it."""
    from . import particles
    s = obj.codenodes
    if s.text is None:
        raise SdfCodeError("no code: pick a text block")
    source = s.text.as_string()
    s.code_hash = hashlib.sha1(source.encode()).hexdigest()
    values = props.sync_params(s, source)
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    state, st = particles.simulate(obj.name, source, s.count, scene.frame_current, scene.frame_start,
                                   fps, values, s.substeps, s.stagger)
    particles.ensure_object(obj.name, s.point_radius)
    particles.fill_points(obj.data, state)
    return state, st


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
    try:
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
                       f"{st['steps']} step{'s' if st['steps'] != 1 else ''} · {st['sim_s'] * 1000:.0f} ms")
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
               f"GPU {st['sample_s'] * 1000:.0f} ms · mesh {st['mesh_s'] * 1000:.0f} ms")
    if not st["faces"]:
        s.stats = "no surface inside the bounds · " + s.stats
    return ""


def _flush():
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


def _rendering():
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
    if _rendering():
        return
    try:
        for obj in scene.objects:
            s = getattr(obj, "codenodes", None)
            if s is not None and s.enabled and s.animate:
                rebuild(obj)
    except Exception:
        traceback.print_exc()


def register():
    bpy.app.handlers.frame_change_post.append(_on_frame)
    bpy.app.timers.register(_poll_text, first_interval=POLL_S, persistent=True)


def unregister():
    if _on_frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame)
    for fn in (_poll_text, _flush):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
