"""Plan + layout -> Blender: the building from the library's capabilities, the equipment as
real meshes from the generators. Needs bpy.

    building(plan, name)      floor slabs and zone markings, the shell and interior walls
                              (wall_network, openings cut exactly where the plan has them),
                              a roof (roof), stairs (stairs), columns, doors, windows, dock
                              doors, lights and the ground
    equipment(plan, layout, name)
                              every placed item as an object; items with the same generator
                              and values share one mesh
    clear(name, part)         remove what an earlier build of this factory made — only
                              objects tagged with it, never anything else

Everything goes in a collection named after the factory; every object carries
cn_factory / cn_kind / cn_id / cn_space / cn_values so it can be found and edited.
"""

from __future__ import annotations

import hashlib
import json
import math

import bpy

from . import equipment as eq
from . import kit

TAG = "cn_factory"
WALL_COLORS = {"cladding": ((0.52, 0.57, 0.62), 0.3, 0.5), "drywall": ((0.86, 0.86, 0.83), 0.0, 0.8),
               "roof": ((0.25, 0.26, 0.28), 0.2, 0.6), "concrete": ((0.55, 0.55, 0.53), 0.0, 0.85),
               "asphalt": ((0.09, 0.09, 0.1), 0.0, 0.9), "aisle": ((0.72, 0.72, 0.7), 0.0, 0.7),
               "stripe": ((0.95, 0.72, 0.05), 0.0, 0.5), "stair": ((0.6, 0.6, 0.58), 0.0, 0.8)}


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def material(name, color, metallic=0.0, roughness=0.5):
    full = f"CN {name}"
    mat = bpy.data.materials.get(full)
    if mat is None:
        mat = bpy.data.materials.new(full)
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = (*color, 1.0)
    return mat


def palette(name):
    if name in eq.MATERIALS:
        return material(name, *eq.MATERIALS[name])
    if name in WALL_COLORS:
        return material(name, *WALL_COLORS[name])
    if name.startswith("zone "):
        from .drawing import ZONE
        rgb = ZONE.get(name[5:], (230, 230, 230))
        # zone paint is a gentle tint over concrete
        col = tuple(_srgb_to_linear(c / 255.0) * 0.8 for c in rgb)
        return material(name, col, 0.0, 0.8)
    return material(name, (0.6, 0.6, 0.6))


# ---- collections and objects --------------------------------------------------------------------

def collection(name, parent=None):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(col)
    return col


def _tag(obj, factory, kind, ident=None, space=None, values=None, part="building"):
    obj[TAG] = factory
    obj["cn_part"] = part
    obj["cn_kind"] = kind
    if ident:
        obj["cn_id"] = ident
    if space:
        obj["cn_space"] = space
    if values is not None:
        obj["cn_values"] = json.dumps(values, sort_keys=True)


def clear(factory, part=None):
    """Remove objects an earlier build of this factory made (optionally only one part)."""
    gone = 0
    for obj in list(bpy.data.objects):
        if obj.get(TAG) == factory and (part is None or obj.get("cn_part") == part):
            bpy.data.objects.remove(obj, do_unlink=True)
            gone += 1
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0 and mesh.get(TAG):
            bpy.data.meshes.remove(mesh)
    for curve in list(bpy.data.curves):
        if curve.users == 0 and curve.get(TAG):
            bpy.data.curves.remove(curve)
    return gone


def kit_mesh(kind, values, factory):
    """One mesh per generator and values, shared by every object that uses them."""
    key = json.dumps({"k": kind, "v": values}, sort_keys=True)
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    name = f"CN {kind} {digest}"
    me = bpy.data.meshes.get(name)
    if me is not None:
        return me
    m, info = eq.build(kind, values)
    me = bpy.data.meshes.new(name)
    me.from_pydata(m.verts, [], m.faces)
    mats = m.materials()
    for mname in mats:
        me.materials.append(palette(mname))
    index = {n: i for i, n in enumerate(mats)}
    me.polygons.foreach_set("material_index", [index[n] for n in m.mats])
    me.update()
    me[TAG] = factory
    me["cn_kind"] = kind
    return me


def place(kind, values, location, rotation_deg, col, factory, ident, space=None, part="equipment",
          offset=None):
    me = kit_mesh(kind, values, factory)
    obj = bpy.data.objects.new(ident, me)
    obj.location = location
    obj.rotation_euler = (0.0, 0.0, math.radians(rotation_deg))
    col.objects.link(obj)
    _tag(obj, factory, kind, ident, space, values, part)
    return obj


def mesh_object(name, verts, faces, mats, col, factory, kind, part="building"):
    """A plain mesh object from verts/faces and one material name per face."""
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    names = []
    for m in mats:
        if m not in names:
            names.append(m)
    for n in names:
        me.materials.append(palette(n))
    index = {n: i for i, n in enumerate(names)}
    me.polygons.foreach_set("material_index", [index[m] for m in mats])
    me.update()
    me[TAG] = factory
    obj = bpy.data.objects.new(name, me)
    col.objects.link(obj)
    _tag(obj, factory, kind, part=part)
    return obj


def _slab(out, x0, y0, x1, y1, z0, z1, mat):
    base = len(out[0])
    out[0].extend([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                   (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)])
    for f in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        out[1].append(tuple(base + i for i in f))
        out[2].append(mat)


def _quad(out, r, z, mat):
    base = len(out[0])
    x0, y0, x1, y1 = r
    out[0].extend([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)])
    out[1].append((base, base + 1, base + 2, base + 3))
    out[2].append(mat)


def _around_holes(rect, holes):
    """A rectangle split into up to four pieces round one rectangular hole each time."""
    pieces = [rect]
    for h in holes:
        nxt = []
        for r in pieces:
            if h[0] >= r[2] or h[2] <= r[0] or h[1] >= r[3] or h[3] <= r[1]:
                nxt.append(r)
                continue
            if h[1] > r[1]:
                nxt.append([r[0], r[1], r[2], h[1]])
            if h[3] < r[3]:
                nxt.append([r[0], h[3], r[2], r[3]])
            if h[0] > r[0]:
                nxt.append([r[0], max(h[1], r[1]), h[0], min(h[3], r[3])])
            if h[2] < r[2]:
                nxt.append([h[2], max(h[1], r[1]), r[2], min(h[3], r[3])])
        pieces = nxt
    return pieces


def _points_object(name, points, col, factory):
    """A vertices-only mesh whose points carry 'size' and 'angle', for wall_network."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([p[0] for p in points], [], [])
    # make both layers first, then look each up again by name to fill it: adding a layer
    # can move the others, and a handle taken before that writes to the wrong place
    # (seen on Blender 5.0.1: the sizes landed in the vertex positions)
    me.attributes.new("size", 'FLOAT_VECTOR', 'POINT')
    me.attributes.new("angle", 'FLOAT', 'POINT')
    if points:
        me.attributes["size"].data.foreach_set("vector", [c for p in points for c in p[1]])
        me.attributes["angle"].data.foreach_set("value", [p[2] for p in points])
    me.update()
    me[TAG] = factory
    obj = bpy.data.objects.new(name, me)
    col.objects.link(obj)
    _tag(obj, factory, "openings")
    obj.hide_render = True
    obj.display_type = 'WIRE'
    return obj


def _use(capability, obj, values):
    from .. import agent
    from ..gn import library
    tree = library.build(capability, refresh=False)
    mod = next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group == tree), None)
    if mod is None:
        mod = obj.modifiers.new(tree.name.replace("CN ", ""), 'NODES')
        mod.node_group = tree
    problems = agent._set_modifier_inputs(obj, mod, values)
    if problems:
        raise RuntimeError("; ".join(problems))
    return mod


def _curve(name, splines, col, factory, cyclic=False):
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    data[TAG] = factory
    for pts in splines:
        sp = data.splines.new('POLY')
        sp.points.add(len(pts) - 1)
        for p, c in zip(sp.points, pts):
            p.co = (*c, 1.0)
        sp.use_cyclic_u = cyclic
    obj = bpy.data.objects.new(name, data)
    col.objects.link(obj)
    return obj


def _merge_collinear(walls):
    """Join wall segments that continue one another, so junctions come out clean."""
    segs = [tuple(w) for w in walls]
    changed = True
    while changed:
        changed = False
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a, b = segs[i], segs[j]
                horiz_a, horiz_b = abs(a[1] - a[3]) < 1e-6, abs(b[1] - b[3]) < 1e-6
                if horiz_a != horiz_b:
                    continue
                if horiz_a and abs(a[1] - b[1]) < 1e-6:
                    lo_a, hi_a = sorted((a[0], a[2]))
                    lo_b, hi_b = sorted((b[0], b[2]))
                    if lo_b <= hi_a + 1e-6 and lo_a <= hi_b + 1e-6:
                        segs[i] = (min(lo_a, lo_b), a[1], max(hi_a, hi_b), a[1])
                        segs.pop(j)
                        changed = True
                        break
                elif not horiz_a and abs(a[0] - b[0]) < 1e-6:
                    lo_a, hi_a = sorted((a[1], a[3]))
                    lo_b, hi_b = sorted((b[1], b[3]))
                    if lo_b <= hi_a + 1e-6 and lo_a <= hi_b + 1e-6:
                        segs[i] = (a[0], min(lo_a, lo_b), a[0], max(hi_a, hi_b))
                        segs.pop(j)
                        changed = True
                        break
            if changed:
                break
    return segs


# ---- the building -------------------------------------------------------------------------------

def building(p, name):
    clear(name, "building")
    root = collection(name)
    col = collection(f"{name} Building", root)
    lights = collection(f"{name} Lights", root)
    site, rules = p["site"], p["spec"]["rules"]
    W, D = site["size"]
    multi = site["levels"] > 1
    fh = p["levels"][0]["height"]
    house = site["kind"] == "house"
    shell_h = site["levels"] * fh if multi else site["clear_height"] + 1.0
    t_ext, t_int = rules["wall_exterior"], rules["wall_interior"]
    made = {}
    for m in ("cladding", "drywall", "roof", "stair"):      # the capabilities take them by name
        palette(m)

    # ground and slabs, with the zones painted on
    g = ([], [], [])
    _slab(g, -W, -D, 2 * W, 2 * D, -0.36, -0.3, "asphalt")
    made["ground"] = mesh_object(f"{name} Ground", *g, col, name, "ground")
    for lvl in p["levels"]:
        z = lvl["z"]
        slab = ([], [], [])
        for piece in _around_holes([-t_ext / 2, -t_ext / 2, W + t_ext / 2, D + t_ext / 2], lvl["holes"]):
            _slab(slab, *piece, z - 0.3, z, "concrete")
        def paint(r, dz, mat):
            for piece in _around_holes(list(r), lvl["holes"]):
                _quad(slab, piece, z + dz, mat)
        for sp in lvl["spaces"]:
            paint(sp["rect"], 0.002, f"zone {sp['type']}")
        for a in lvl["aisles"]:
            r = a["rect"]
            paint(r, 0.002, "aisle")
            horizontal = r[2] - r[0] >= r[3] - r[1]
            if not house:
                if horizontal:
                    paint([r[0], r[1], r[2], r[1] + 0.1], 0.004, "stripe")
                    paint([r[0], r[3] - 0.1, r[2], r[3]], 0.004, "stripe")
                else:
                    paint([r[0], r[1], r[0] + 0.1, r[3]], 0.004, "stripe")
                    paint([r[2] - 0.1, r[1], r[2], r[3]], 0.004, "stripe")
        made[f"floor {lvl['level']}"] = mesh_object(f"{name} Floor L{lvl['level']}", *slab, col, name, "floor")

    # the shell: one cyclic wall round the site, openings from the plan
    shell_pts, inner_pts = [], {}
    for lvl in p["levels"]:
        for o in lvl["openings"]:
            x, y = o["pos"]
            pt = ((x, y, lvl["z"] + o["sill"]), (o["width"], 0.0, o["height"]),
                  0.0 if o["axis"] == "x" else math.pi / 2)
            if o["wall"] == "exterior":
                shell_pts.append(pt)
            elif o["kind"] != "exit":
                inner_pts.setdefault(lvl["level"], []).append(pt)
    shell_open = _points_object(f"{name} Shell Openings", shell_pts, col, name)
    shell = _curve(f"{name} Shell", [[(0, 0, 0), (W, 0, 0), (W, D, 0), (0, D, 0)]], col, name, cyclic=True)
    _tag(shell, name, "shell")
    _use("wall_network", shell, {"Height": shell_h, "Thickness": t_ext, "Footing": 0.3,
                                 "Doorways per Wall": 0, "Openings": shell_open.name,
                                 "Wall Material": "CN cladding"})
    made["shell"] = shell
    for lvl in p["levels"]:
        if not lvl["walls"]:
            continue
        z = lvl["z"]
        segs = _merge_collinear(lvl["walls"])
        wobj = _curve(f"{name} Walls L{lvl['level']}", [[(s[0], s[1], z), (s[2], s[3], z)] for s in segs],
                      col, name)
        _tag(wobj, name, "walls")
        opts = _points_object(f"{name} Wall Openings L{lvl['level']}", inner_pts.get(lvl["level"], []), col, name)
        height = (fh - 0.3) if multi else (site["clear_height"] - 0.3 if house else rules["room_height"])
        _use("wall_network", wobj, {"Height": height, "Thickness": t_int, "Footing": 0.3,
                                    "Doorways per Wall": 0, "Openings": opts.name,
                                    "Wall Material": "CN drywall"})
        made[f"walls {lvl['level']}"] = wobj

    # roof over the shell
    top = shell_h
    rm = bpy.data.meshes.new(f"{name} Roof")
    rm.from_pydata([(0, 0, top), (W, 0, top), (W, D, top), (0, D, top)], [], [(0, 1, 2, 3)])
    rm[TAG] = name
    roof = bpy.data.objects.new(f"{name} Roof", rm)
    col.objects.link(roof)
    _tag(roof, name, "roof")
    _use("roof", roof, {"Style": 2 if house else 0, "Overhang": 0.4 if house else 0.2,
                        "Flat Thickness": 0.35, "Material": "CN roof"})
    made["roof"] = roof

    # stairs
    for lvl in p["levels"]:
        for i, st in enumerate(lvl["stairs"]):
            so = _curve(f"{name} Stair L{lvl['level']} {i + 1}", [[tuple(st["from"]), tuple(st["to"])]], col, name)
            _tag(so, name, "stairs")
            _use("stairs", so, {"Width": st["width"] - 0.1, "Side Walls": True, "Material": "CN stair"})

    # columns, doors, windows, docks
    height = site["clear_height"]
    for i, c in enumerate(p["columns"]):
        place("column", {"height": height}, (c[0], c[1], 0.0), 0.0, col, name, f"{name} column {i + 1}", part="building")
    spaces = {sp["name"]: sp for lvl in p["levels"] for sp in lvl["spaces"]}
    count = {}
    for lvl in p["levels"]:
        z = lvl["z"]
        for o in lvl["openings"]:
            x, y = o["pos"]
            kind = o["kind"]
            count[kind] = count.get(kind, 0) + 1
            ident = f"{name} {kind} {count[kind]}"
            wall = t_ext if o["wall"] == "exterior" else t_int
            if kind == "window":
                rot = 0.0 if o["axis"] == "x" else 90.0
                place("window", {"width": o["width"], "height": o["height"], "wall": wall},
                      (x, y, z + o["sill"]), rot, col, name, ident, o["space"], part="building")
            elif kind in ("door", "double_door", "exit"):
                width = o["width"]
                # swing into the room the door belongs to (exits swing in, off the street)
                sp = spaces.get(o["space"])
                if sp is not None and kind != "exit":
                    cx, cy = (sp["rect"][0] + sp["rect"][2]) / 2, (sp["rect"][1] + sp["rect"][3]) / 2
                else:
                    cx, cy = W / 2, D / 2
                if o["axis"] == "x":
                    rot = 0.0 if cy > y else 180.0
                else:
                    rot = -90.0 if cx > x else 90.0
                values = {"width": min(width, 2.4), "height": o["height"], "wall": wall}
                place("door", values, (x, y, z), rot, col, name, ident, o["space"], part="building")
            elif kind in ("dock", "drive_in"):
                # the leveller goes inside: local -Y points into the building
                if o["axis"] == "x":
                    rot = 180.0 if y < D / 2 else 0.0
                else:
                    rot = 90.0 if x < W / 2 else -90.0
                place("dock_door", {"width": o["width"], "height": o["height"]}, (x, y, z), rot, col, name,
                      ident, o["space"], part="building")

    # lights: a grid of area lights under the roof, a sun outside
    for obj in list(lights.objects):
        if obj.get(TAG) == name:
            bpy.data.objects.remove(obj, do_unlink=True)
    step = 4.0 if house else 12.0
    nx, ny = max(1, round(W / step)), max(1, round(D / step))
    for lvl in p["levels"]:
        zl = lvl["z"] + (fh - 0.4 if multi else site["clear_height"] - 0.3)
        for i in range(nx):
            for j in range(ny):
                ld = bpy.data.lights.new(f"{name} lamp", 'AREA')
                ld.size = 1.2 if house else 2.5
                ld.energy = 120.0 if house else 2200.0
                # dozens of shadowed area lights overflow EEVEE's shadow pool; the sun
                # gives the shadows, the grid only lights
                ld.use_shadow = False
                lo = bpy.data.objects.new(f"{name} lamp L{lvl['level']} {i}.{j}", ld)
                lo.location = ((i + 0.5) * W / nx, (j + 0.5) * D / ny, zl)
                lights.objects.link(lo)
                _tag(lo, name, "light")
                place("light_fixture", {"length": 1.2}, (lo.location.x, lo.location.y, zl + 0.02), 0.0,
                      col, name, f"{name} fixture L{lvl['level']} {i}.{j}", part="building")
    sun = bpy.data.lights.new(f"{name} sun", 'SUN')
    sun.energy = 2.2
    so = bpy.data.objects.new(f"{name} Sun", sun)
    so.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
    lights.objects.link(so)
    _tag(so, name, "light")
    return made


def equipment(p, lay, name):
    clear(name, "equipment")
    root = collection(name)
    col = collection(f"{name} Equipment", root)
    objs = []
    for it in lay["items"]:
        obj = place(it["kind"], it["values"], tuple(it["location"]), it["rotation"], col, name,
                    f"{name} {it['id']}", it["space"])
        obj["cn_role"] = it["role"]
        if it.get("group"):
            obj["cn_group"] = it["group"]
        objs.append(obj)
    return objs


def objects(name, part=None):
    return [o for o in bpy.data.objects if o.get(TAG) == name and (part is None or o.get("cn_part") == part)]
