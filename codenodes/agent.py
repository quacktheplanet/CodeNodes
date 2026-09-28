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


def bake_to_nodes(name):
    return api.bake_to_nodes(name)


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
    if obj is not None and any(m.type == 'NODES' for m in obj.modifiers):
        # frame what the modifiers made; a curve object's own box is just its curve
        info = measure(obj)                  # already in world space
        centre = Vector(info["centre"])
        radius = (Vector(info["size"]).length / 2.0) or 1.0
    elif obj is not None:
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
    elif style == "outdoor":
        # a low afternoon sun and a pale sky: long shadows read terrain and height well
        bg.inputs[0].default_value = (0.42, 0.56, 0.78, 1)
        bg.inputs[1].default_value = 0.9
        sun = bpy.data.lights.new("CN Sun", 'SUN')
        sun.energy = 4.0 * strength
        sun.angle = math.radians(1.5)
        sun.color = (1.0, 0.93, 0.84)
        obj = bpy.data.objects.new("CN Sun", sun)
        s.collection.objects.link(obj)
        obj.rotation_euler = (math.radians(52), 0.0, math.radians(-38))
    else:
        return {"ok": False, "error": "style must be 'studio', 'dark', 'flat' or 'outdoor'"}
    return {"ok": True, "style": style}


def collect(name, objects, parent=None, keep_in_scene=False):
    """Put objects in a collection — to scatter from (keep_in_scene=False takes them out
    of the scene, so the originals do not render), or to name a group of things, like
    everything the trees should keep clear of (keep_in_scene=True).

    A collection inside another (parent=...) is picked as one piece by scatter and
    along_curve, so a tree made of a trunk and a crown stays together.
    """
    if parent and parent == name:
        return {"ok": False, "error": "a collection cannot be inside itself"}
    col = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    missing = [n for n in objects if n not in bpy.data.objects]
    if missing:
        return {"ok": False, "error": f"no object called {', '.join(missing)}"}
    for obj_name in objects:
        obj = bpy.data.objects[obj_name]
        if obj.name not in col.objects:
            col.objects.link(obj)
        if not keep_in_scene:
            for other in list(obj.users_collection):
                if other != col:
                    other.objects.unlink(obj)
    if parent:
        outer = bpy.data.collections.get(parent) or bpy.data.collections.new(parent)
        if col.name not in outer.children:
            outer.children.link(col)
    return {"ok": True, "collection": col.name, "objects": [o.name for o in col.objects],
            "parent": parent}


def material(name, color=(0.8, 0.8, 0.8), roughness=0.5, metallic=0.0, emission=None,
             emission_strength=1.0, variation=0.0, variation_scale=1.0, objects=None):
    """Make or update a simple material, by name, for anything that takes one.

    variation (0-1) breaks up a flat colour with soft noise — ground, stone and wood
    read far better with a little. emission=[r, g, b] makes it glow. objects=[names]
    also puts it on those objects.
    """
    try:
        rgb = [float(c) for c in color][:3]
    except Exception:
        return {"ok": False, "error": "color should be [r, g, b] with each 0-1"}
    try:
        roughness, metallic = float(roughness), float(metallic)
        emission_strength = float(emission_strength)
        emission = [float(c) for c in emission][:3] if emission else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "roughness, metallic and emission_strength are numbers; "
                                      "emission is [r, g, b]"}
    existing = bpy.data.materials.get(name)
    if existing is not None and existing.node_tree is not None and not any(
            n.type == 'BSDF_PRINCIPLED' for n in existing.node_tree.nodes):
        return {"ok": False, "error": f"'{name}' is someone's own shader (it has no Principled "
                                      "BSDF); pick another name rather than replace it"}
    mat = existing or bpy.data.materials.new(name)
    if not getattr(mat, "use_nodes", True):
        mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None) or \
            nodes.new("ShaderNodeOutputMaterial")
        links.new(bsdf.outputs[0], out.inputs[0])
    for node in [n for n in nodes if n.label == "CN variation"]:
        nodes.remove(node)
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Metallic"].default_value = float(metallic)
    if variation:
        v = max(0.0, min(1.0, float(variation)))
        coord = nodes.new("ShaderNodeTexCoord")
        noise = nodes.new("ShaderNodeTexNoise")
        mix = nodes.new("ShaderNodeMix")
        for node in (coord, noise, mix):
            node.label = "CN variation"
        noise.inputs["Scale"].default_value = float(variation_scale)
        noise.inputs["Detail"].default_value = 6.0
        mix.data_type = 'RGBA'

        def by_id(sockets, identifier):      # [name] would find the Mix node's float "A"
            return next(s for s in sockets if s.identifier == identifier)
        by_id(mix.inputs, "A_Color").default_value = (*[c * (1 - 0.5 * v) for c in rgb], 1.0)
        by_id(mix.inputs, "B_Color").default_value = (*[min(1.0, c * (1 + 0.6 * v))
                                                        for c in rgb], 1.0)
        links.new(coord.outputs["Object"], noise.inputs["Vector"])
        links.new(noise.outputs["Fac"], by_id(mix.inputs, "Factor_Float"))
        links.new(by_id(mix.outputs, "Result_Color"), bsdf.inputs["Base Color"])
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*[float(c) for c in emission][:3], 1.0)
        bsdf.inputs["Emission Strength"].default_value = float(emission_strength)
    else:
        bsdf.inputs["Emission Strength"].default_value = 0.0
    mat.diffuse_color = (*rgb, 1.0)
    assigned = []
    for obj_name in objects or []:
        obj = bpy.data.objects.get(obj_name)
        if obj is None or not hasattr(obj.data, "materials"):
            return {"ok": False, "error": f"no object called '{obj_name}' that can take a material"}
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
        assigned.append(obj.name)
    return {"ok": True, "material": mat.name, "on": assigned}


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
    try:
        _draw_viewport(area, out)
        how = "drawn"
    except Exception:
        # A plain screenshot catches anything on top (the splash, a menu), so it is the fallback.
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.screenshot_area(filepath=out)
        how = "screenshot"
    return {"ok": True, "path": out, "exists": os.path.exists(out), "how": how}


def _draw_viewport(area, out, max_side=1600):
    """Draw the 3D view offscreen, as it is shaded, from its own camera — unaffected by
    popups, other windows, or Blender being minimised."""
    import gpu
    import numpy as np
    space = area.spaces.active
    region = next(r for r in area.regions if r.type == 'WINDOW')
    scale = min(1.0, max_side / max(region.width, region.height))
    w, h = max(1, int(region.width * scale)), max(1, int(region.height * scale))
    rv3d = space.region_3d
    offscreen = gpu.types.GPUOffScreen(w, h)
    try:
        ctx = bpy.context
        offscreen.draw_view3d(ctx.scene, ctx.view_layer, space, region, rv3d.view_matrix,
                              rv3d.window_matrix, do_color_management=True)
        with offscreen.bind():
            buf = gpu.state.active_framebuffer_get().read_color(0, 0, w, h, 4, 0, 'UBYTE')
        buf.dimensions = w * h * 4
        pixels = np.asarray(buf, dtype=np.float32) / 255.0
    finally:
        offscreen.free()
    pixels[3::4] = 1.0
    image = bpy.data.images.new("codenodes viewport", w, h, alpha=False)
    try:
        image.pixels.foreach_set(pixels)
        image.filepath_raw = out
        image.file_format = 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)


# ---- Geometry Nodes ---------------------------------------------------------------------
# Blender's own nodes, read and written as plain data, so a setup can be built, understood
# and edited rather than only generated.

NODES_GUIDE = """\
Working with Geometry Nodes here:

1. Look before you build. nodes_library() lists ready-made, tested capabilities (terrain
   with river/road carving, scatter, walls and wall networks, rooms from a floor plan,
   roofs, stairs, bridges over gaps, water…); nodes_use builds one. Prefer composing those over
   writing everything from scratch. A floor plan or wall network is one curve object with
   several splines: curve(name, splines=[[...], [...]]).
2. nodes_find("words") finds node types; nodes_describe(type) gives exact socket names,
   which take a field ("field": true), and what each dropdown accepts. Never guess a
   socket name.
3. nodes_write builds a tree from a description:
     {"interface": [{"socket": "Geometry", "type": "NodeSocketGeometry"},
                    {"socket": "Height", "type": "NodeSocketFloat", "default_value": 2,
                     "min_value": 0, "max_value": 10, "description": "..."},
                    {"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
      "nodes": [{"name": "in", "type": "NodeGroupInput"},
                {"name": "grid", "type": "GeometryNodeMeshGrid",
                 "values": {"Size X": 10, "Vertices X": 50}},
                {"name": "math", "type": "ShaderNodeMath", "settings": {"operation": "MULTIPLY"}},
                {"name": "out", "type": "NodeGroupOutput"}],
      "links": [{"from": ["grid", "Mesh"], "to": ["out", "Geometry"]}]}
   Sockets can be named plainly. Positions are optional; it lays the tree out. Group
   inputs become the sliders on the modifier, so expose what someone would tune.
   Nodes with sockets you add (Capture Attribute, Repeat/Simulation zones, Menu Switch,
   Index Switch, Bake) take "items", e.g. {"capture_items": [{"name": "Height",
   "data_type": "FLOAT"}]}; a zone's input node says "pairs_with": its output node.
4. To understand an existing tree: nodes_explain(group) in words, nodes_read(group) as data.
5. To change one: nodes_edit(group, [ops]) for small changes — it keeps everything else,
   including values tuned on the modifier. Rewrite with nodes_write only for big changes.
6. Always nodes_check afterwards: it reports what geometry came out (mesh, curves,
   points, instances, size) and any problems, like a field wired into a socket that
   takes a single value. Then render or grab the viewport and look at it.

Common traps: Distribute Points in Poisson mode has "Density Max", not "Density";
Index Switch starts as Float, so set data_type to GEOMETRY to switch geometry;
instances are cheap, so only Realize Instances when something needs the real mesh.
"""


def nodes_help():
    """How to work with Geometry Nodes here, and how big the surface is."""
    from .gn import catalog
    info = catalog.summary()
    info["guide"] = NODES_GUIDE
    try:
        from .gn import library
        info["capabilities"] = sorted(library.CAPABILITIES)
    except ImportError:
        pass
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
        return _no_group(group)
    return {"ok": True, **serialize.read(tree)}


def nodes_explain(group):
    """An existing tree in plain words: what goes in, what each node does, what comes out,
    what is not connected to anything, and anything that looks wrong."""
    from .gn import explain
    tree = bpy.data.node_groups.get(group)
    if tree is None:
        return _no_group(group)
    return {"ok": True, **explain.explain(tree)}


def _no_group(group):
    return {"ok": False, "error": f"no node group called '{group}'. There is: "
                                  f"{', '.join(t.name for t in bpy.data.node_groups) or 'nothing'}"}


def nodes_write(description, name=None, apply_to=None):
    """Build a node tree from plain data, replacing one of the same name.

    For a small change to an existing tree, nodes_edit is safer and much shorter.
    """
    from .gn import serialize
    warnings = []
    try:
        tree = serialize.write(description, name=name, warnings=warnings)
    except serialize.BuildError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result = {"ok": True, "group": tree.name, "nodes": len(tree.nodes), "links": len(tree.links),
              "warnings": warnings}
    if apply_to:
        result["applied"] = nodes_apply(apply_to, tree.name)
    return result


def nodes_edit(group, ops):
    """Small changes to an existing tree — set values, add, link, insert, remove, rename,
    add or change group inputs. All or nothing: if one step fails, none are applied."""
    from .gn import edit, serialize
    tree = bpy.data.node_groups.get(group)
    if tree is None:
        return _no_group(group)
    try:
        result = edit.apply(tree, ops)
    except serialize.BuildError as exc:
        return {"ok": False, "error": str(exc), "note": "nothing was changed"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "note": "nothing was changed"}
    for obj in bpy.data.objects:
        if any(m.type == 'NODES' and m.node_group == tree for m in obj.modifiers):
            obj.update_tag()
    return {"ok": True, "group": tree.name, **result}


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


# ---- buildings: spec -> plan -> building -> equipment -> verify (factory/) ---------------------

def plan_site(spec=None, name="Factory", seed=1, example=None):
    """Plan a building from a spec: site size, spaces with areas and types, closeness
    ratings (A E I O U X), material flow, equipment and rules. Returns the plan as a text
    grid, every space's rectangle, measured problems and a picture. `example` starts from
    one of the example specs (see `assets`); keys in `spec` then replace the example's."""
    from .factory import tools
    return tools.plan_site(spec, name, seed, example)


def edit_plan(name, ops, seed=None):
    """Change a planned building: swap / move / resize / add_space / remove_space /
    relation / flow / rule / site / add_equipment / remove_equipment / set_equipment /
    resolve. Only swaps and moves keep the arrangement exactly; other edits re-solve
    starting from it, so untouched spaces tend to stay."""
    from .factory import tools
    return tools.edit_plan(name, ops, seed)


def build_plan(name):
    """Build a planned building in Blender: slabs, painted zones, walls with the plan's
    doors, docks and windows cut in, roof, stairs, columns, lights."""
    from .factory import tools
    return tools.build_plan(name)


def place_equipment(name, items=None, seed=None, repair=True):
    """Lay out the plan's equipment (plus `items`, added to the spec) and build it:
    conveyors, fenced robot cells, rack rows with forklift aisles, machines in rows."""
    from .factory import tools
    return tools.place_equipment(name, items, seed, repair)


def verify(name, renders=True, repair=True, views=("plan", "iso", "walk")):
    """Check a building by measurement: areas, outside walls, collisions, aisles, service
    clearance, the walk to an exit, robot reach, floating parts, mesh intersections and
    that every opening is really cut. Repairs the layout when it can; renders set views."""
    from .factory import tools
    return tools.verify(name, renders, repair, tuple(views))


def assets(kind=None):
    """The parametric generators (equipment and building parts) with their sliders,
    clearances and joints, the space types, the rules, and example specs to start from."""
    from .factory import tools
    return tools.assets(kind)


def nodes_library():
    """The ready-made capabilities: what each does, and its inputs with their ranges."""
    from .gn import library
    return {"ok": True, "capabilities": library.manifest(),
            "how": "nodes_use(capability, object=..., values={input name: value})"}


def _set_modifier_inputs(obj, mod, values):
    """Set a Geometry Nodes modifier's inputs by their names. Returns what went wrong."""
    problems = []
    tree = mod.node_group
    inputs = {i.name: i for i in tree.interface.items_tree
              if i.item_type == 'SOCKET' and i.in_out == 'INPUT'}
    places = {"NodeSocketObject": "objects", "NodeSocketCollection": "collections",
              "NodeSocketMaterial": "materials", "NodeSocketImage": "images"}
    for label, value in (values or {}).items():
        item = inputs.get(label)
        if item is None:
            problems.append(f"'{tree.name}' has no input '{label}'. It has: "
                            + ", ".join(n for n in inputs if inputs[n].socket_type
                                        != "NodeSocketGeometry"))
            continue
        if item.socket_type in places:
            if value in (None, ""):
                value = None
            else:
                found = getattr(bpy.data, places[item.socket_type]).get(str(value))
                if found is None:
                    problems.append(f"'{label}' wants a {places[item.socket_type][:-1]} called "
                                    f"'{value}', and there is none")
                    continue
                value = found
        elif isinstance(value, (list, tuple)):
            value = list(value)
        try:
            mod[item.identifier] = value
        except Exception as exc:
            problems.append(f"'{label}' will not take {value!r}: {exc}")
    obj.update_tag()
    return problems


def nodes_use(capability, object=None, values=None, name=None, refresh=False):
    """Build a ready-made capability and put it on an object, with its inputs set by name.

    With no object, a new empty one is made for it. Curve-following capabilities (wall,
    wall_network, rooms, stairs, water, path_bridge, along_curve) belong on a curve object
    — make one with `curve` (several splines for wall_network and rooms). The
    capability's group is shared: if it exists it is reused as it is (keeping any edits);
    refresh=True rebuilds it from the library.
    """
    from .gn import library, serialize
    warnings = []
    try:
        tree = library.build(capability, name=name, warnings=warnings, refresh=refresh)
    except KeyError as exc:
        return {"ok": False, "error": str(exc.args[0])}
    except serialize.BuildError as exc:
        return {"ok": False, "error": f"the capability did not build: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"the capability did not build: {type(exc).__name__}: {exc}"}
    obj = bpy.data.objects.get(object) if object else None
    if object and obj is None:
        return {"ok": False, "error": f"no object called '{object}'. Make a curve with `curve`, "
                                      "or leave object out to get a new empty object"}
    if obj is None:
        obj = bpy.data.objects.new(tree.name.replace("CN ", ""), bpy.data.meshes.new(tree.name))
        bpy.context.scene.collection.objects.link(obj)
    mod = next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group == tree), None)
    if mod is None:
        mod = obj.modifiers.new(tree.name.replace("CN ", ""), 'NODES')
        mod.node_group = tree
    problems = _set_modifier_inputs(obj, mod, values)
    result = {"ok": not problems, "object": obj.name, "group": tree.name, "modifier": mod.name,
              "warnings": warnings}
    if problems:
        result["error"] = "; ".join(problems)
    try:
        result["made"] = measure(obj)
    except Exception as exc:
        result["made"] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def _plain(v):
    """A socket value as plain JSON: numbers stay, vectors and colours become lists, objects names."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bpy.types.ID):
        return v.name
    try:
        return [float(x) for x in v]
    except TypeError:
        return str(v)


def code_node(kind="mesh", code=None, template=None, name=None, object=None, values=None, make_real=True):
    """A code node inside Geometry Nodes: geometry made by code, with the code's settings and
    sliders as inputs on the node. What a person gets from Add › Mesh › CodeNodes.

    Without `object` (or with a name that doesn't exist yet) it adds a new object named `name`
    whose Geometry Nodes tree holds one code node wired to the output. With the name of an
    object that already has a code node it updates that node: `code` replaces its code,
    `values` sets its inputs by name ({"major": 1.2, "Count": 500000}). kind is "mesh" (a GPU
    Surface: `float sdf(vec3 p)`), "particles" (GPU Particles: spawn/update), "deform" (GPU
    Mesh: `void deform(inout Vertex v)` on the mesh wired into it) or "shape"; `template`
    picks a starting template by name instead of `code`. GPU nodes are drawn live on the GPU;
    `make_real` (default on) puts a Make Real node after a new one so it's real geometry that
    renders and that later nodes can use. The result names the object, its tree, the node
    group and the hidden source object whose code it runs.
    """
    from . import gn_link, live
    kinds = {"mesh": 'MESH', "shape": 'SHAPE', "particles": 'PARTICLES', "deform": 'DEFORM'}
    if kind not in kinds:
        return {"ok": False, "error": f"kind must be one of {', '.join(kinds)}"}
    obj = bpy.data.objects.get(object) if object else None
    found = gn_link.host_code_nodes(obj) if obj is not None else []
    if obj is not None and not found:
        return {"ok": False, "error": f"'{object}' has no code node. Leave object out to make a new "
                                      "one, or use nodes_* to edit its Geometry Nodes"}
    err = ""
    if found:
        tree, node = found[0]
        src = gn_link.source_of(node.node_tree)
        if code is not None or template:
            try:
                source = code if code is not None else gn_link.template(src.codenodes.kind, template)
            except KeyError as exc:
                return {"ok": False, "error": str(exc.args[0])}
            src.codenodes.text.from_string(source)
            err = live.rebuild(src) or ""
            gn_link.sync_interface(node.node_tree, src)
    else:
        try:
            source = code if code is not None else None
            label = name or template or None
            obj, tree, node, err = gn_link.add_object(kinds[kind], template or None, source=source,
                                                      label=label, make_real=bool(make_real))
        except KeyError as exc:
            return {"ok": False, "error": str(exc.args[0])}
        wanted = object or name           # the object gets exactly the name asked for
        if wanted and obj.name != wanted and wanted not in bpy.data.objects:
            obj.name = wanted
        src = gn_link.source_of(node.node_tree)
        gn_link.sync()
        live.rebuild(src)
    real = next((l.to_node for l in tree.links if l.from_node == node and l.to_node.type == 'GROUP'
                 and gn_link.is_make_real(l.to_node.node_tree)), None)
    problems = []
    for key, value in (values or {}).items():
        sock = node.inputs.get(key)
        if sock is None and real is not None:
            sock = real.inputs.get(key)          # Resolution / Max Points / When live on Make Real
        if sock is None:
            have = [s.name for s in node.inputs] + ([s.name for s in real.inputs] if real is not None else [])
            problems.append(f"no input '{key}' (it has: {', '.join(have)})")
            continue
        try:
            if sock.bl_idname == "NodeSocketObject" and isinstance(value, str):
                value = bpy.data.objects.get(value)
            sock.default_value = value
        except (TypeError, ValueError) as exc:
            problems.append(f"{key}: {exc}")
    if values:
        gn_link.sync()
        err = live.rebuild(src) or err
    s = src.codenodes
    result = {"ok": not err and not problems, "object": obj.name, "tree": tree.name,
              "node": node.node_tree.name, "source": src.name, "kind": s.kind.lower(),
              "inputs": {sock.name: _plain(getattr(sock, "default_value", None)) for sock in node.inputs},
              "status": node.label, "stats": s.stats}
    if real is not None:
        result["make_real"] = {sock.name: _plain(getattr(sock, "default_value", None)) for sock in real.inputs}
    elif s.kind in gn_link.GPU_KINDS:
        result["note"] = ("drawn live on the GPU only; call again with make_real on a new object, or add a Make "
                          "Real node after it, to get real geometry")
    if err or problems:
        result["error"] = "; ".join([e for e in (err,) if e] + problems)
    return result


def nodes_set_inputs(object, values, modifier=None):
    """Change the sliders on an object's Geometry Nodes modifier, by input name."""
    obj = bpy.data.objects.get(object)
    if obj is None:
        return {"ok": False, "error": f"no object called '{object}'"}
    mods = [m for m in obj.modifiers if m.type == 'NODES' and m.node_group is not None]
    if modifier:
        mods = [m for m in mods if m.name == modifier]
    if not mods:
        return {"ok": False, "error": f"'{object}' has no Geometry Nodes modifier"
                                      + (f" called '{modifier}'" if modifier else "")}
    if len(mods) > 1 and not modifier:
        return {"ok": False, "error": "it has several; say which modifier: "
                                      + ", ".join(m.name for m in mods)}
    problems = _set_modifier_inputs(obj, mods[0], values)
    if problems:
        return {"ok": False, "error": "; ".join(problems)}
    return {"ok": True, "object": obj.name, "modifier": mods[0].name, "made": measure(obj)}


def curve(name, points=None, cyclic=False, smooth=True, splines=None):
    """Make or reshape a curve object through the given points — a path, the line of a
    wall, a river. smooth=True makes an auto-handled Bezier, False a straight polyline.

    splines=[[points], [points], ...] makes several in one object instead: a network of
    walls (wall_network) or a floor plan of room outlines (rooms, with cyclic=True).
    """
    if splines is None:
        if points is None:
            return {"ok": False, "error": "give points, or splines (a list of point lists)"}
        splines = [points]
    parsed = []
    try:
        for pts in splines:
            parsed.append([tuple(float(c) for c in list(p)[:3]) + (0.0,) * (3 - len(list(p)[:3]))
                           for p in pts])
    except Exception:
        return {"ok": False, "error": "points should be a list of [x, y] or [x, y, z]"}
    if not parsed or any(len(pts) < 2 for pts in parsed):
        return {"ok": False, "error": "each curve needs at least two points"}
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != 'CURVE':
        return {"ok": False, "error": f"'{name}' exists and is not a curve"}
    if obj is None:
        data = bpy.data.curves.new(name, 'CURVE')
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
    data = obj.data
    data.dimensions = '3D'
    data.splines.clear()
    length = 0.0
    for pts in parsed:
        if smooth:
            spline = data.splines.new('BEZIER')
            spline.bezier_points.add(len(pts) - 1)
            for bp, p in zip(spline.bezier_points, pts):
                bp.co = p
                bp.handle_left_type = bp.handle_right_type = 'AUTO'
        else:
            spline = data.splines.new('POLY')
            spline.points.add(len(pts) - 1)
            for sp, p in zip(spline.points, pts):
                sp.co = (*p, 1.0)
        spline.use_cyclic_u = bool(cyclic)
        length += sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
                      for a, b in zip(pts, pts[1:] + (pts[:1] if cyclic else [])))
    result = {"ok": True, "object": obj.name, "points": sum(len(p) for p in parsed),
              "cyclic": bool(cyclic), "length": round(length, 3)}
    if len(parsed) > 1:
        result["splines"] = len(parsed)
    return result


def web_page(path, sliders=None, objects=None, static=None, overrides=None, title="Level",
             subtitle=""):
    """Write the scene as a self-contained interactive web page (three.js), with sliders
    for modifier inputs: [{"object", "input", "values": [...], "label"?, "unit"?}].

    Every slider position is built here first; objects a slider changes indirectly are
    found and baked with it. static lists objects to export once regardless;
    overrides sets inputs only for the export (a lower terrain resolution, say).
    """
    from . import web
    try:
        return web.export_page(path, objects=objects, sliders=sliders or [], static=static or [],
                               overrides=overrides, title=title, subtitle=subtitle)
    except web.WebError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def web_shape(path, object=None, source=None, title=None, subtitle="", colors=None,
              color=None, roughness=0.45, metalness=0.0):
    """A page that rebuilds a Code Shape in the browser at any slider value — no baking.

    Give the shape object (its code and the slider values tuned on it), or the source.
    colors: {part name: [r, g, b]}. Parts that cut other parts are refused (they need
    Blender); use web_page for those.
    """
    from . import web
    values = None
    if object:
        obj = bpy.data.objects.get(object)
        if obj is None or obj.codenodes.text is None or obj.codenodes.kind != 'SHAPE':
            return {"ok": False, "error": f"'{object}' is not a Code Shape object"}
        source = obj.codenodes.text.as_string()
        values = {p.name: p.value for p in obj.codenodes.params}
        title = title or object
    if not source:
        return {"ok": False, "error": "give a shape object or its source"}
    kwargs = {"title": title or "Shape", "subtitle": subtitle, "colors": colors, "values": values,
              "roughness": roughness, "metalness": metalness}
    if color is not None:
        kwargs["color"] = color
    try:
        return web.shape_page(bpy.path.abspath(path), source, **kwargs)
    except web.WebError as exc:
        return {"ok": False, "error": str(exc)}


def measure(obj):
    """What an object's modifiers actually produce: mesh, curves, points and instances,
    and the size of all of it together. Instances count — to_mesh() would miss them."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    info = {"verts": 0, "faces": 0, "curves": 0, "points": 0, "instances": 0}
    geometry = evaluated.evaluated_geometry()
    if geometry.mesh is not None:
        info["verts"], info["faces"] = len(geometry.mesh.vertices), len(geometry.mesh.polygons)
    if geometry.curves is not None:
        info["curves"] = len(geometry.curves.curves)
    if geometry.pointcloud is not None:
        info["points"] = len(geometry.pointcloud.points)
    instances = geometry.instances_pointcloud()
    if instances is not None:
        info["instances"] = len(instances.points)
    lo, hi, seen = [1e30] * 3, [-1e30] * 3, False
    for inst in depsgraph.object_instances:
        owner = inst.parent if inst.is_instance else inst.object
        if owner is None or owner.original != obj.original:
            continue
        if not inst.is_instance and (inst.object.type not in ('MESH', 'POINTCLOUD', 'CURVES')
                                     or not (info["verts"] or info["points"])):
            # an empty mesh sits at the origin, and a curve object's own box is its
            # legacy curve; what its modifiers made arrives as instances
            continue
        for corner in inst.object.bound_box:
            world = inst.matrix_world @ Vector(corner)
            lo = [min(a, b) for a, b in zip(lo, world)]
            hi = [max(a, b) for a, b in zip(hi, world)]
            seen = True
    info["size"] = [round(b - a, 3) for a, b in zip(lo, hi)] if seen else [0.0, 0.0, 0.0]
    info["centre"] = [round((a + b) / 2, 3) for a, b in zip(lo, hi)] if seen else [0.0] * 3
    return info


def nodes_check(group, on=None):
    """Build the group on an object and report what actually comes out, plus anything
    that looks wrong in the tree. The quickest way to tell whether a setup works."""
    from .gn import serialize
    tree = bpy.data.node_groups.get(group)
    if tree is None:
        return _no_group(group)
    temporary, added = None, None
    obj = bpy.data.objects.get(on) if on else None
    if on and obj is None:
        return {"ok": False, "error": f"no object called '{on}'"}
    if obj is None:
        temporary = bpy.data.objects.new(f"{group} check", bpy.data.meshes.new("check"))
        bpy.context.scene.collection.objects.link(temporary)
        obj = temporary
        mod = obj.modifiers.new("check", 'NODES')
        mod.node_group = tree
    elif not any(m.type == 'NODES' and m.node_group == tree for m in obj.modifiers):
        # measure the group on that object, not the object without it
        added = obj.modifiers.new("CodeNodes check", 'NODES')
        added.node_group = tree
    try:
        info = measure(obj)
        empty = not any(info[k] for k in ("verts", "curves", "points", "instances"))
        note = None
        if empty:
            note = ("nothing came out. Common causes: the Group Output is not connected, a "
                    "selection removed everything, or the setup expects input geometry — "
                    "try nodes_check with on= an object it is meant for.")
        return {"ok": True, "group": group, **info, "note": note,
                "problems": serialize.validate(tree), "unused": serialize.unused(tree)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if added is not None:
            obj.modifiers.remove(added)
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
