"""Blender's lights and materials for GPU nodes.

Scene Lights is a code node (a stage with only a function output, `light`) whose light data are
hidden inputs: CodeNodes fills them from the scene's lamps and world, in the space of the object whose
Geometry Nodes hold the node, and refreshes them whenever a lamp changes. The code never changes, so
moving a lamp costs no recompile. Anything that calls `light(p, n, v, albedo, roughness, metallic)`
(a lit look such as Material Look) is then shaded by the scene's lamps with the main lobes of
Principled BSDF: Lambert diffuse and GGX specular.

A Material input (`// @in material mat`) brings in a Blender material's Principled BSDF values as
hidden inputs too (mat_base, mat_roughness, mat_metallic, mat_emit, mat_alpha).

A GPU Surface can take both: wire Scene Lights into its Lights input and a Material Look into its
Material input, and the raymarched surface is lit by the scene's lamps with that material's values,
instead of its own built-in sun and sky.

What it can't do: GPU nodes and Blender objects don't cast shadows on each other (a GPU Surface still
shadows itself), and lamp textures, IES profiles, light linking and nodes on materials beyond the
Principled BSDF's own values are ignored.
"""

from __future__ import annotations

import math

import bpy

LIGHTS_TEMPLATE = "Scene Lights"
SLOTS = 8
TYPES = {'SUN': 0.0, 'POINT': 1.0, 'SPOT': 2.0, 'AREA': 3.0}
FIELDS = ("type", "px", "py", "pz", "dx", "dy", "dz", "r", "g", "b", "size", "c0", "c1")


def scene_lamps(scene, to_local=None, slots=SLOTS):
    """[{field: value}] for the scene's visible lamps (brightest first), in `to_local`'s space (world
    when None). Colours carry the energy: suns in W/m2, the rest in W."""
    import mathutils
    lamps = []
    view_layer = bpy.context.view_layer
    for obj in scene.objects:
        if obj.type != 'LIGHT' or obj.data.type not in TYPES:
            continue
        try:
            if not obj.visible_get(view_layer=view_layer):
                continue
        except (RuntimeError, TypeError):
            if obj.hide_viewport:
                continue
        lamp = obj.data
        m = obj.matrix_world
        pos = m.translation.copy()
        d = (m.to_3x3() @ mathutils.Vector((0.0, 0.0, -1.0))).normalized()    # lamps shine down their -Z
        if to_local is not None:
            pos = to_local @ pos
            d = (to_local.to_3x3() @ d).normalized()
        energy = float(lamp.energy)
        col = [c * energy for c in lamp.color]
        size = 0.0
        if lamp.type in ('POINT', 'SPOT'):
            size = float(getattr(lamp, "shadow_soft_size", 0.0)) * 2.0
        elif lamp.type == 'AREA':
            size = float(max(lamp.size, getattr(lamp, "size_y", lamp.size)))
        c0 = c1 = 0.0
        if lamp.type == 'SPOT':
            half = float(lamp.spot_size) * 0.5
            c0 = math.cos(half)                                   # outside the cone
            c1 = math.cos(half * (1.0 - float(lamp.spot_blend)))  # fully inside
            if c1 <= c0:
                c1 = c0 + 1e-3
        lamps.append({"type": TYPES[lamp.type], "px": pos.x, "py": pos.y, "pz": pos.z,
                      "dx": d.x, "dy": d.y, "dz": d.z, "r": col[0], "g": col[1], "b": col[2],
                      "size": size, "c0": c0, "c1": c1, "_rank": energy if lamp.type != 'SUN' else 1e9})
    lamps.sort(key=lambda L: -L["_rank"])
    return lamps[:slots]


def world_colour(scene):
    """The world's background colour × strength (a uniform environment), or a dim default."""
    w = scene.world
    if w is None:
        return (0.05, 0.05, 0.05)
    nt = getattr(w, "node_tree", None)
    if nt is not None:
        bg = next((n for n in nt.nodes if n.type == 'BACKGROUND'), None)
        if bg is not None:
            c = bg.inputs["Color"].default_value
            s = float(bg.inputs["Strength"].default_value)
            return (c[0] * s, c[1] * s, c[2] * s)
    return tuple(w.color)


def material_values(mat):
    """{part: value} from a material's Principled BSDF (its unlinked input values), or defaults."""
    out = {"base_r": 0.8, "base_g": 0.8, "base_b": 0.8, "roughness": 0.5, "metallic": 0.0,
           "emit_r": 0.0, "emit_g": 0.0, "emit_b": 0.0, "alpha": 1.0}
    if mat is None:
        return out
    bsdf = None
    nt = getattr(mat, "node_tree", None)
    if nt is not None:
        bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        c = mat.diffuse_color
        out.update(base_r=c[0], base_g=c[1], base_b=c[2], roughness=float(mat.roughness),
                   metallic=float(mat.metallic), alpha=c[3])
        return out

    def val(*names):
        for n in names:
            s = bsdf.inputs.get(n)
            if s is not None:
                return s.default_value
        return None

    base = val("Base Color")
    if base is not None:
        out.update(base_r=base[0], base_g=base[1], base_b=base[2])
    for key, names in (("roughness", ("Roughness",)), ("metallic", ("Metallic",)), ("alpha", ("Alpha",))):
        v = val(*names)
        if v is not None:
            out[key] = float(v)
    ec = val("Emission Color", "Emission")
    es = val("Emission Strength")
    if ec is not None:
        s = float(es) if es is not None else 1.0
        out.update(emit_r=ec[0] * s, emit_g=ec[1] * s, emit_b=ec[2] * s)
    return out


def _set_params(obj, values):
    """Write hidden slider values (no update callbacks). True if anything changed."""
    changed = False
    params = {p.name: p for p in obj.codenodes.params}
    for name, v in values.items():
        p = params.get(name)
        if p is None:
            continue
        if abs(p.value - float(v)) > 1e-6:
            p["value"] = float(v)
            changed = True
    return changed


def lights_values(scene, to_local=None, slots=SLOTS):
    lamps = scene_lamps(scene, to_local, slots)
    vals = {}
    for i in range(slots):
        L = lamps[i] if i < len(lamps) else None
        for f in FIELDS:
            vals[f"l{i}_{f}"] = (L[f] if L is not None else (-1.0 if f == "type" else 0.0))
    w = world_colour(scene)
    vals.update(w_r=w[0], w_g=w[1], w_b=w[2])
    return vals


def is_lights_source(obj):
    s = getattr(obj, "codenodes", None)
    return s is not None and s.enabled and s.kind == 'STAGE' and s.template_key == LIGHTS_TEMPLATE


def sync(scene=None):
    """Refresh every Scene Lights node's hidden light data, every Material input's values, and which
    Scene Lights / Material Look feed each GPU Surface. Returns True if anything visible changed."""
    from . import gn_link, gn_sockets
    scene = scene or bpy.context.scene
    changed = False
    for group in bpy.data.node_groups:
        if not gn_link.is_code_group(group):
            continue
        obj = gn_link.source_of(group)
        if obj is None or not obj.codenodes.enabled:
            continue
        tree, node = gn_link.find_user(group)
        if is_lights_source(obj):
            host = None
            if tree is not None:
                hosts = gn_link._hosts_of(tree)
                host = hosts[0] if hosts else None
            to_local = host.matrix_world.inverted_safe() if host is not None else None
            changed |= _set_params(obj, lights_values(scene, to_local))
        try:
            d = gn_sockets.decls_of(obj)
        except Exception:
            continue
        if d.materials and node is not None:
            vals = {}
            for m in d.materials:
                sock = node.inputs.get(m)
                mat = sock.default_value if sock is not None and hasattr(sock, "default_value") else None
                for part, v in material_values(mat).items():
                    vals[f"{m}_{part}"] = v
            changed |= _set_params(obj, vals)
        if obj.codenodes.kind == 'MESH' and node is not None and tree is not None:
            for sock_name, key in (("Lights", "cn_lights_src"), ("Material", "cn_material_src")):
                sock = node.inputs.get(sock_name)
                src_name = ""
                if sock is not None and sock.is_linked:
                    l = gn_link._feeding(tree, sock)
                    while l is not None and l.from_node.type == 'REROUTE':
                        l = gn_link._feeding(tree, l.from_node.inputs[0])
                    if l is not None and l.from_node.type == 'GROUP':
                        up = gn_link.source_of(l.from_node.node_tree)
                        if up is not None:
                            src_name = up.name
                if obj.get(key, "") != src_name:
                    obj[key] = src_name
                    changed = True
    return changed


def surface_uniforms(src, scene):
    """For raymarch.draw: (lights vec4 list, material vec4 list) or (None, None) when the surface uses
    its built-in sun and sky. Lights are in world space (the raymarcher shades in world space)."""
    lname = src.get("cn_lights_src", "") if src is not None else ""
    mname = src.get("cn_material_src", "") if src is not None else ""
    if not lname and not mname:
        return None, None
    lights = None
    if lname:
        lsrc = bpy.data.objects.get(lname)
        vals = {p.name: p.value for p in lsrc.codenodes.params} if lsrc is not None else {}
        intensity = float(vals.get("intensity", 1.0))
        world = float(vals.get("world", 1.0))
        lamps = scene_lamps(scene, None)
        lights = []
        for i in range(SLOTS):
            L = lamps[i] if i < len(lamps) else None
            if L is None:
                lights += [-1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                continue
            lights += [L["px"], L["py"], L["pz"], L["type"], L["dx"], L["dy"], L["dz"], L["size"],
                       L["r"] * intensity, L["g"] * intensity, L["b"] * intensity, L["c0"], L["c1"], 0.0, 0.0,
                       0.0]
        w = world_colour(scene)
        lights += [w[0] * world, w[1] * world, w[2] * world, 1.0]
    material = None
    if mname:
        msrc = bpy.data.objects.get(mname)
        if msrc is not None:
            from . import gn_sockets
            d = gn_sockets.decls_of(msrc)
            vals = {p.name: p.value for p in msrc.codenodes.params}
            if d.materials:
                m = d.materials[0]
                g = lambda k, dflt: float(vals.get(f"{m}_{k}", dflt))   # noqa: E731
                material = [g("base_r", 0.8), g("base_g", 0.8), g("base_b", 0.8), g("roughness", 0.5),
                            g("emit_r", 0.0), g("emit_g", 0.0), g("emit_b", 0.0), g("metallic", 0.0)]
    return lights, material


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    """A lamp (or the world) changed: refresh the light data now, so the light moves live."""
    try:
        hit = False
        for u in depsgraph.updates:
            idd = u.id
            if isinstance(idd, bpy.types.Object) and idd.type == 'LIGHT':
                hit = True
                break
            if isinstance(idd, (bpy.types.Light, bpy.types.World, bpy.types.Material)):
                hit = True
                break
        if hit:
            from . import gpu_live
            sync(scene)
            gpu_live.redraw()
    except Exception:
        import traceback
        _report_exc()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)


def _report_exc():
    """Print the current error without letting Python touch freed Blender structs (see safe_errors)."""
    from .safe_errors import report
    report()
