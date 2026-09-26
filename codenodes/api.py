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


def reference():
    """What sdf code can use: a cheat sheet to hand to Claude."""
    return (sdf_code.__doc__ + "\nHelpers available:\n" + "\n".join(
        line for line in sdf_code.PRELUDE.splitlines() if line.startswith(("float ", "vec3 "))))
