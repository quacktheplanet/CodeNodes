"""The surface an assistant drives CodeNodes through.

Any Blender MCP that can run Python can call these. Every function returns a plain
dict and never raises for a user-fixable problem, so a model can read the error, fix
its code and try again:

    from bl_ext.user_default.codenodes import agent       # installed as an extension
    agent.help()                                          # what can be written, and how
    agent.make("mesh", "float sdf(vec3 p){ return sdSphere(p, 1.0); }", name="Ball")
    agent.look_at("Ball"); agent.light("studio")
    agent.render()                                        # -> {"path": "...png"} to look at

The point of `render` and `viewport` is the loop: make something, look at it, fix it.
"""

from __future__ import annotations

import math
import os
import tempfile

import bpy
from mathutils import Vector

from . import api, cache, particles, sdf_code, shapes, volume
from .sdf_code import SdfCodeError

KINDS = ("mesh", "shape", "particles", "volume")


def _out_path(path, stem):
    if path:
        return bpy.path.abspath(path)
    folder = os.path.join(tempfile.gettempdir(), "codenodes_views")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{stem}_{bpy.context.scene.frame_current:04d}.png")


def help(kind=None):
    """What you can write, and the helpers available. Give it to the model verbatim."""
    guides = {
        "mesh": ("Define `float sdf(vec3 p)` — negative inside the surface, positive outside, in "
                 "Blender units. Built with a GPU grid, so keep it a real distance field. Good for "
                 "organic and blended forms; it rounds sharp corners and has no useful UVs."),
        "shape": ("Not GLSL — the shape language. Declare sliders with `param name default min max`, "
                  "describe a 2D profile with move/line/arc/curve, then `revolve` or `extrude` it. "
                  "Any number can be maths. Exact edges, clean quads and real UVs, so this is the "
                  "one for lamps, bottles, columns, walls and anything turned or extruded."),
        "particles": ("Define `void spawn(inout Particle p)` and `void update(inout Particle p, "
                      "float dt)`. Particle has position, velocity, age, life, seed. Ageing and "
                      "respawning happen for you. Shade with the `speed` attribute, not `velocity` "
                      "(Blender reserves that name for motion blur)."),
        "volume": ("Define `float density(vec3 p)` — 0 empty, ~1 thick. Fall to 0 before the edge "
                   "of the bounds or you get a visible box. Emission must be driven by the density "
                   "attribute, or the whole box glows."),
    }
    helpers = [line.split("{")[0].strip() for line in sdf_code.PRELUDE.splitlines()
               if line.startswith(("float ", "vec3 "))]
    helpers += [line.split("{")[0].strip() for line in particles.PARTICLE_PRELUDE.splitlines()
                if line.startswith(("float ", "vec3 "))]
    templates = {"mesh": sdf_code.TEMPLATE, "particles": particles.TEMPLATE,
                 "volume": volume.TEMPLATE, "shape": shapes.TEMPLATE}
    return {
        "kinds": list(KINDS),
        "how": guides if kind is None else {kind: guides.get(kind, "unknown kind")},
        "helpers": helpers,
        "shape_language": {
            "commands": sorted(shapes.KEYWORDS),
            "keywords": {k: list(v) for k, v in sorted(shapes.KEYWORDS.items()) if v},
            "functions": sorted(shapes.expr.FUNCTIONS) if hasattr(shapes, "expr") else [],
        },
        "params": ("GLSL kinds declare a slider with `// @param name default min max`; the shape "
                   "language uses `param name default min max`. Values survive code edits, so "
                   "rewriting the code will not reset them."),
        "time": "uTime is seconds since the start frame; uFrame is the frame number (GLSL kinds).",
        "templates": templates if kind is None else {kind: templates.get(kind, "")},
    }


def make(kind, code, name=None, **options):
    """Build or update something from code. kind is 'mesh', 'particles' or 'volume'."""
    if kind not in KINDS:
        return {"ok": False, "error": f"kind must be one of {', '.join(KINDS)}"}
    try:
        if kind == "mesh":
            return api.code_to_mesh(code, name=name or "CodeMesh", **options)
        if kind == "shape":
            return api.code_to_shape(code, name=name or "CodeShape", **options)
        if kind == "particles":
            return api.code_to_particles(code, name=name or "CodeParticles", **options)
        return api.code_to_volume(code, name=name or "CodeVolume", **options)
    except TypeError as exc:
        return {"ok": False, "error": f"bad option: {exc}"}
    except SdfCodeError as exc:
        return {"ok": False, "error": str(exc)}


def set_params(name, **values):
    return api.set_params(name, **values)


def bake(name, frame_start=None, frame_end=None):
    return api.bake(name, frame_start, frame_end)


def scene():
    """What is in the scene right now, and how it will render."""
    s = bpy.context.scene
    objects = []
    for obj in s.objects:
        entry = {"name": obj.name, "type": obj.type,
                 "location": [round(v, 3) for v in obj.location]}
        cn = getattr(obj, "codenodes", None)
        if cn is not None and cn.enabled:
            entry["codenodes"] = {"kind": cn.kind, "baked": cache.is_baked(obj),
                                  "stats": cn.stats, "error": cn.last_error,
                                  "params": {p.name: round(p.value, 4) for p in cn.params}}
        if obj.type == 'MESH':
            entry["polygons"] = len(obj.data.polygons)
        objects.append(entry)
    return {"ok": True, "frame": s.frame_current, "range": [s.frame_start, s.frame_end],
            "fps": round(s.render.fps / (s.render.fps_base or 1.0), 3),
            "engine": s.render.engine, "camera": s.camera.name if s.camera else None,
            "resolution": [s.render.resolution_x, s.render.resolution_y], "objects": objects}


def frame(number):
    bpy.context.scene.frame_set(int(number))
    return {"ok": True, "frame": bpy.context.scene.frame_current}


def look_at(target, distance=None, azimuth=35.0, elevation=22.0, lens=50.0):
    """Point the camera at an object (or the origin) from a sensible angle.

    `distance` defaults to whatever frames the object. Angles are degrees:
    azimuth swings around it, elevation lifts above it.
    """
    s = bpy.context.scene
    # Anything moved or rotated a moment ago still has a stale matrix_world until the
    # scene is updated, and aiming at that puts the subject out of frame.
    bpy.context.view_layer.update()
    cam = s.camera
    if cam is None:
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        s.collection.objects.link(cam)
        s.camera = cam
    obj = bpy.data.objects.get(target) if isinstance(target, str) else None
    if isinstance(target, str) and obj is None:
        return {"ok": False, "error": f"no object named '{target}'"}
    if obj is not None:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        centre = sum(corners, Vector((0, 0, 0))) / 8.0
        radius = max((c - centre).length for c in corners) or 1.0
    else:
        centre, radius = Vector((0, 0, 0)), 2.0
    cam.data.lens = lens
    if distance is None:
        # fit the object's radius into the frame, with a margin
        half_fov = math.atan(cam.data.sensor_width / (2.0 * lens))
        distance = radius / max(math.tan(half_fov), 1e-3) * 1.6
    az, el = math.radians(azimuth), math.radians(elevation)
    offset = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    cam.location = centre + offset * distance
    direction = centre - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    return {"ok": True, "camera": cam.name, "distance": round(distance, 3),
            "target": target if isinstance(target, str) else "origin"}


def light(style="studio", strength=1.0):
    """A lighting setup that photographs well. Renders come out black without one."""
    s = bpy.context.scene
    for obj in [o for o in s.objects if o.type == 'LIGHT' and o.name.startswith("CN ")]:
        bpy.data.objects.remove(obj, do_unlink=True)
    world = s.world
    if world is None:
        world = s.world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")

    def add(name, kind, location, energy, size=5.0, colour=(1, 1, 1)):
        data = bpy.data.lights.new(f"CN {name}", kind)
        data.energy = energy * strength
        data.color = colour
        if kind == 'AREA':
            data.size = size
        obj = bpy.data.objects.new(f"CN {name}", data)
        s.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (-Vector(location)).to_track_quat('-Z', 'Y').to_euler()
        return obj

    if style == "studio":
        bg.inputs[0].default_value = (0.05, 0.05, 0.06, 1)
        bg.inputs[1].default_value = 1.0
        add("Key", 'AREA', (4, -5, 4), 900)
        add("Fill", 'AREA', (-5, -3, 1.5), 250, size=6, colour=(0.8, 0.86, 1.0))
        add("Rim", 'AREA', (0, 5, 3.5), 500, colour=(1.0, 0.92, 0.85))
    elif style == "dark":
        bg.inputs[0].default_value = (0.006, 0.006, 0.02, 1)
        bg.inputs[1].default_value = 1.0
        add("Key", 'AREA', (4, -4, 3), 700, colour=(0.85, 0.9, 1.0))
    elif style == "flat":
        bg.inputs[0].default_value = (0.5, 0.5, 0.52, 1)
        bg.inputs[1].default_value = 1.0
    else:
        return {"ok": False, "error": "style must be 'studio', 'dark' or 'flat'"}
    return {"ok": True, "style": style}


def render(path=None, samples=48, width=800, engine='CYCLES', denoise=True, aspect=0.66):
    """Render the current frame to a PNG and return its path, to be looked at."""
    s = bpy.context.scene
    if s.camera is None:
        look_at(None)
    previous = (s.render.engine, s.render.resolution_x, s.render.resolution_y, s.render.filepath)
    try:
        s.render.engine = engine
    except TypeError:
        return {"ok": False, "error": f"unknown render engine '{engine}'"}
    if engine == 'CYCLES':
        s.cycles.samples = int(samples)
        s.cycles.use_denoising = bool(denoise)
    s.render.resolution_x = int(width)
    s.render.resolution_y = max(1, int(width * aspect))
    out = _out_path(path, "render")
    s.render.filepath = out
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        s.render.engine, s.render.resolution_x, s.render.resolution_y, s.render.filepath = previous
    return {"ok": True, "path": out, "frame": s.frame_current, "engine": engine,
            "exists": os.path.exists(out)}


def viewport(path=None):
    """A screenshot of the 3D viewport — quicker than a render, and shows overlays."""
    window = next((w for w in bpy.context.window_manager.windows), None)
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None) if window else None
    if area is None:
        return {"ok": False, "error": "no 3D viewport is open (is Blender running with a window?)"}
    out = _out_path(path, "viewport")
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.screen.screenshot_area(filepath=out)
    return {"ok": True, "path": out, "exists": os.path.exists(out)}


# ---- Geometry Nodes ---------------------------------------------------------------------
# Blender's own nodes, read and written as plain data, so a setup can be built, understood
# and edited rather than only generated.

def nodes_help():
    """How many node types there are, where the stateful ones are, and how to read a tree."""
    from .gn import catalog
    info = catalog.summary()
    info["how"] = ("nodes_find(words) to look up node types; nodes_read(group) for an existing "
                   "tree as plain data; change that data and pass it to nodes_write to edit it; "
                   "nodes_check(group) to see what geometry comes out.")
    return info


def nodes_find(words="", detail=False, limit=40):
    """Look up node types by plain words — 'distribute points', 'curve to mesh', 'noise'.

    With detail=True each one comes back with its sockets, which of them take a field,
    and what its dropdowns accept.
    """
    from .gn import catalog
    return catalog.search(words, limit=limit, detail=detail)


def nodes_describe(name):
    """One node type in full: every socket, whether it takes a field, and its settings."""
    from .gn import catalog
    return {"ok": "error" not in catalog.describe(name), **catalog.describe(name)}


def nodes_list():
    """The node groups in this file."""
    groups = []
    for tree in bpy.data.node_groups:
        groups.append({"name": tree.name, "kind": tree.bl_idname, "nodes": len(tree.nodes),
                       "users": tree.users,
                       "inputs": [i.name for i in tree.interface.items_tree
                                  if i.item_type == 'SOCKET' and i.in_out == 'INPUT']})
    return {"ok": True, "groups": groups}


def nodes_read(group):
    """An existing node tree as plain data — every node, setting, value and link."""
    from .gn import serialize
    tree = bpy.data.node_groups.get(group)
    if tree is None:
        return {"ok": False, "error": f"no node group called '{group}'. "
                                      f"There is: {', '.join(t.name for t in bpy.data.node_groups) or 'nothing'}"}
    return {"ok": True, **serialize.read(tree)}


def nodes_write(description, name=None, apply_to=None):
    """Build a node tree from plain data, replacing one of the same name.

    To edit rather than replace: nodes_read it, change the part you want, pass it back.
    """
    from .gn import serialize
    from .sdf_code import SdfCodeError
    try:
        tree = serialize.write(description, name=name)
    except (serialize.BuildError, SdfCodeError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result = {"ok": True, "group": tree.name, "nodes": len(tree.nodes), "links": len(tree.links)}
    if apply_to:
        result["applied"] = nodes_apply(apply_to, tree.name)
    return result


def nodes_apply(object_name, group):
    """Put a node group on an object as a Geometry Nodes modifier."""
    obj = bpy.data.objects.get(object_name)
    tree = bpy.data.node_groups.get(group)
    if obj is None:
        return {"ok": False, "error": f"no object called '{object_name}'"}
    if tree is None:
        return {"ok": False, "error": f"no node group called '{group}'"}
    mod = next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group == tree), None)
    if mod is None:
        mod = obj.modifiers.new(group, 'NODES')
        mod.node_group = tree
    return {"ok": True, "object": obj.name, "group": tree.name, "modifier": mod.name}


def nodes_check(group, on=None):
    """Build the group on an object and report what geometry actually comes out.

    The quickest way to tell whether a setup does anything at all.
    """
    from .gn import serialize
    tree = bpy.data.node_groups.get(group)
    if tree is None:
        return {"ok": False, "error": f"no node group called '{group}'"}
    temporary = None
    obj = bpy.data.objects.get(on) if on else None
    if obj is None:
        temporary = bpy.data.objects.new(f"{group} check", bpy.data.meshes.new("check"))
        bpy.context.scene.collection.objects.link(temporary)
        obj = temporary
        mod = obj.modifiers.new("check", 'NODES')
        mod.node_group = tree
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        verts, faces = len(mesh.vertices), len(mesh.polygons)
        size = [0.0, 0.0, 0.0]
        if verts:
            import numpy as np
            co = np.empty(verts * 3, np.float32)
            mesh.vertices.foreach_get("co", co)
            size = [round(float(v), 3) for v in np.ptp(co.reshape(-1, 3), axis=0)]
        evaluated.to_mesh_clear()
        note = None
        if not verts:
            note = ("nothing came out. Common causes: instances were never realized, a "
                    "Group Output is not connected, or a selection removed everything.")
        return {"ok": True, "group": group, "verts": verts, "faces": faces, "size": size,
                "note": note}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if temporary is not None:
            data = temporary.data
            bpy.data.objects.remove(temporary, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)


def code(name):
    """The code currently driving an object, so it can be edited rather than replaced."""
    obj = bpy.data.objects.get(name)
    if obj is None or not getattr(obj, "codenodes", None) or obj.codenodes.text is None:
        return {"ok": False, "error": f"no CodeNodes object named '{name}'"}
    return {"ok": True, "name": name, "kind": obj.codenodes.kind,
            "code": obj.codenodes.text.as_string(), "text": obj.codenodes.text.name}


def remove(name, delete_cache=False):
    obj = bpy.data.objects.get(name)
    if obj is None:
        return {"ok": False, "error": f"no object named '{name}'"}
    if delete_cache:
        cache.detach(obj, remove_files=True, name=name)
    particles.forget(name)
    bpy.data.objects.remove(obj, do_unlink=True)
    return {"ok": True, "removed": name}
