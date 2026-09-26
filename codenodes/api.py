"""The scripting entry point, for people, scripts and Claude (through any Blender MCP
that can run Python):

    import codenodes.api as cn                # or: from bl_ext.user_default.codenodes import api as cn
    cn.code_to_mesh('''
    // @param radius 1.0 0.2 2.0
    float sdf(vec3 p) { return sdTorus(p, radius, 0.3); }
    ''', name="Ring", resolution=128)
    # -> {"ok": True, "object": "Ring", "faces": 18432, ...} or {"ok": False, "error": "line 3: ..."}

Never raises for problems in the code; errors come back in the result so the
caller can fix the code and try again.
"""

from __future__ import annotations

import bpy

from . import live, sdf_code


def code_to_mesh(source, name="CodeMesh", resolution=96, bounds_min=(-2, -2, -2), bounds_max=(2, 2, 2),
                 params=None, live_update=True, animate=False):
    """Create or update a mesh object from sdf code. Returns a result dict."""
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != 'MESH':
        return {"ok": False, "error": f"an object named '{name}' exists and isn't a mesh"}
    if obj is None:
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(obj)
    s = obj.codenodes
    text = s.text or bpy.data.texts.get(f"{name}.sdf") or bpy.data.texts.new(f"{name}.sdf")
    text.from_string(source)
    s.enabled = True
    # Written as ID properties so they don't each queue a live rebuild; we rebuild once below.
    for key, value in (("resolution", int(resolution)), ("bounds_min", tuple(map(float, bounds_min))),
                       ("bounds_max", tuple(map(float, bounds_max))), ("live", bool(live_update)),
                       ("animate", bool(animate))):
        s[key] = value
    s.text = text
    try:
        from .props import sync_params
        sync_params(s, source)
    except sdf_code.SdfCodeError:
        pass                                  # reported by rebuild below
    for pname, value in (params or {}).items():
        item = next((p for p in s.params if p.name == pname), None)
        if item is None:
            return {"ok": False, "error": f"no @param named '{pname}' in the code"}
        item["value"] = float(value)
    err = live.rebuild(obj)
    live._pending.discard(obj.name)
    result = {"ok": not err, "object": obj.name, "text": text.name, "error": err or None,
              "faces": len(obj.data.polygons), "verts": len(obj.data.vertices), "stats": s.stats,
              "params": {p.name: p.value for p in s.params}}
    return result


def set_params(name, **values):
    """Change @param sliders on an existing object and rebuild."""
    obj = bpy.data.objects.get(name)
    if obj is None or not obj.codenodes.enabled:
        return {"ok": False, "error": f"no Code -> Mesh object named '{name}'"}
    for pname, value in values.items():
        item = next((p for p in obj.codenodes.params if p.name == pname), None)
        if item is None:
            return {"ok": False, "error": f"no @param named '{pname}'"}
        item["value"] = float(value)
    err = live.rebuild(obj)
    return {"ok": not err, "error": err or None, "faces": len(obj.data.polygons), "stats": obj.codenodes.stats}


def bake(name, frame_start=None, frame_end=None):
    """Bake an object's animation to disk and play it back with plain Geometry Nodes.

    After this the object renders in F12 and on machines with no GPU, and every other
    Geometry Nodes node can work on the result.
    """
    obj = bpy.data.objects.get(name)
    if obj is None or not obj.codenodes.enabled:
        return {"ok": False, "error": f"no Code -> Mesh object named '{name}'"}
    scene = bpy.context.scene
    res = bpy.ops.codenodes.bake(
        'EXEC_DEFAULT', object_name=name,
        frame_start=int(frame_start if frame_start is not None else scene.frame_start),
        frame_end=int(frame_end if frame_end is not None else scene.frame_end))
    ok = 'FINISHED' in res
    from . import cache
    return {"ok": ok, "object": name, "baked": cache.is_baked(obj),
            "dir": cache.cache_dir(name, create=False), "stats": obj.codenodes.stats,
            "error": None if ok else obj.codenodes.last_error or "bake failed"}


def code_to_shape(source, name="CodeShape", params=None, live_update=True):
    """A model built from a parametric description: profiles, revolve, extrude.

    Exact edges and clean quads, rather than a sampled field. See
    `codenodes.shapes.TEMPLATE` for the language.
    """
    from . import live
    from .shapes import ShapeError
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != 'MESH':
        return {"ok": False, "error": f"an object named '{name}' exists and isn't a mesh"}
    try:
        from .shapes import parse
        parse(source)
    except ShapeError as exc:
        return {"ok": False, "error": str(exc)}
    if obj is None:
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(obj)
    s = obj.codenodes
    text = s.text or bpy.data.texts.get(f"{name}.shape") or bpy.data.texts.new(f"{name}.shape")
    text.from_string(source)
    s.enabled = True
    s.kind = 'SHAPE'
    s.live, s.animate = bool(live_update), False
    s.text = text
    try:
        from .props import sync_params
        sync_params(s, source, 'SHAPE')
    except ShapeError:
        pass
    for pname, value in (params or {}).items():
        item = next((p for p in s.params if p.name == pname), None)
        if item is None:
            return {"ok": False, "error": f"no param named '{pname}' in the description"}
        item["value"] = float(value)
    err = live.rebuild(obj)
    live._pending.discard(obj.name)
    return {"ok": not err, "object": obj.name, "text": text.name, "error": err or None,
            "faces": len(obj.data.polygons), "verts": len(obj.data.vertices),
            "stats": s.stats, "params": {p.name: p.value for p in s.params}}


def code_to_particles(source, name="CodeParticles", count=20000, params=None, radius=0.02,
                      substeps=1, live_update=True):
    """A GPU particle system from `spawn(inout Particle p)` and `update(inout Particle p, float dt)`.

    The simulation steps as the frame changes. Bake it with `bake(name, ...)` to get
    a cache that renders anywhere.
    """
    from . import live, particles
    from .sdf_code import SdfCodeError
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != 'MESH':
        return {"ok": False, "error": f"an object named '{name}' exists and isn't a mesh"}
    try:
        particles.check_source(source)
    except SdfCodeError as exc:
        return {"ok": False, "error": str(exc)}
    particles.forget(name)
    obj = particles.ensure_object(name, radius)
    s = obj.codenodes
    text = s.text or bpy.data.texts.get(f"{name}.sdf") or bpy.data.texts.new(f"{name}.sdf")
    text.from_string(source)
    s.enabled = True
    s.kind = 'PARTICLES'                 # an enum: must be set by name, not as an ID property
    s.live, s.animate = bool(live_update), True
    for key, value in (("count", int(count)), ("substeps", int(substeps)),
                       ("point_radius", float(radius))):
        s[key] = value                   # as ID properties, so their update callbacks don't fire
    s.text = text
    try:
        from .props import sync_params
        sync_params(s, source)
    except SdfCodeError:
        pass
    for pname, value in (params or {}).items():
        item = next((p for p in s.params if p.name == pname), None)
        if item is None:
            return {"ok": False, "error": f"no @param named '{pname}' in the code"}
        item["value"] = float(value)
    err = live.rebuild(obj)
    live._pending.discard(obj.name)
    return {"ok": not err, "object": obj.name, "text": text.name, "error": err or None,
            "count": int(count), "points": len(obj.data.vertices), "stats": s.stats,
            "params": {p.name: p.value for p in s.params}}


def code_to_volume(source, name="CodeVolume", resolution=96, bounds_min=(-2, -2, -2),
                   bounds_max=(2, 2, 2), params=None, frame_start=None, frame_end=None):
    """Smoke, cloud or nebula from `float density(vec3 p)`.

    One frame by default; pass a frame range to write a sequence that Blender plays
    natively (no GPU and no add-on needed at render time).
    """
    from . import volume
    from .sdf_code import SdfCodeError, parse_params
    try:
        values = {p.name: p.default for p in parse_params(source)}
        values.update(params or {})
        scene = bpy.context.scene
        if frame_start is None and frame_end is None:
            fps = scene.render.fps / (scene.render.fps_base or 1.0)
            obj, stats = volume.build(name, source, tuple(bounds_min), tuple(bounds_max), int(resolution),
                                      time_s=(scene.frame_current - scene.frame_start) / fps,
                                      frame=scene.frame_current, values=values)
            return {"ok": True, "object": obj.name, "error": None, "filepath": stats["filepath"],
                    "max_density": round(stats["max_density"], 4), "bytes": stats["bytes"],
                    "dims": stats["dims"]}
        obj, info = volume.bake_sequence(name, source, tuple(bounds_min), tuple(bounds_max),
                                         int(resolution),
                                         frame_start if frame_start is not None else scene.frame_start,
                                         frame_end if frame_end is not None else scene.frame_end,
                                         values=values)
        return {"ok": True, "object": obj.name, "error": None, "sequence": True, **info}
    except SdfCodeError as exc:
        return {"ok": False, "error": str(exc)}


def reference():
    """What sdf code can use: a cheat sheet to hand to Claude."""
    return (sdf_code.__doc__ + "\nHelpers available:\n" + "\n".join(
        line for line in sdf_code.PRELUDE.splitlines() if line.startswith(("float ", "vec3 "))))
