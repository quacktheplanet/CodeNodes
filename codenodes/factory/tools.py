"""The six mid-level tools an assistant drives a building with (see docs/MODELING_RESEARCH.md):

    plan_site(spec)          validate + solve; a plan with a text grid view and a picture
    edit_plan(name, ops)     move / resize / add / remove spaces, change rules; re-solve locally
    build_plan(name)         the building in Blender from the library's capabilities
    place_equipment(name)    lay out and build the equipment (repairs what it can)
    verify(name)             measured checks, in Blender too, and renders from set views
    assets(kind)             the generators, their sliders and clearances; example specs

A factory's spec, plan and layout live in a text block ("CN Factory <name>") saved with the
.blend, so it can be edited again tomorrow. Needs bpy.
"""

from __future__ import annotations

import json
import math
import os
import tempfile

import bpy

from . import build, drawing, edits, equipment as eq, kit, layout, planner, spec as specs, verify as checks

STORE = "CN Factory {}"


# ---- state --------------------------------------------------------------------------------------

def _load(name):
    text = bpy.data.texts.get(STORE.format(name))
    if text is None:
        raise KeyError(f"no plan called {name!r}; make one with plan_site"
                       + (f" (there is: {', '.join(names())})" if names() else ""))
    return json.loads(text.as_string())


def _save(name, state):
    key = STORE.format(name)
    text = bpy.data.texts.get(key) or bpy.data.texts.new(key)
    text.clear()
    text.write(json.dumps(state, indent=1))
    text.use_fake_user = True


def names():
    return [t.name[len("CN Factory "):] for t in bpy.data.texts if t.name.startswith("CN Factory ")]


def _out(name, stem):
    folder = os.path.join(tempfile.gettempdir(), "codenodes_views")
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in name)
    return os.path.join(folder, f"{safe}_{stem}.png")


def _summary(p, res, name):
    lv = []
    for lvl in p["levels"]:
        counts = {}
        for o in lvl["openings"]:
            counts[o["kind"]] = counts.get(o["kind"], 0) + 1
        lv.append({"level": lvl["level"],
                   "spaces": [{"name": sp["name"], "type": sp["type"], "rect": sp["rect"], "area": sp["area"],
                               "asked": sp["target_area"], "walled": sp["enclosed"], "outside": sp["exterior"]}
                              for sp in lvl["spaces"]],
                   "aisles": [{"index": a["index"], "rect": a["rect"], "width": a["width"], "kind": a["kind"]}
                              for a in lvl["aisles"]],
                   "openings": counts, "stairs": len(lvl["stairs"])})
    images = []
    for lvl in p["levels"]:
        path = drawing.save(_out(name, f"plan_L{lvl['level']}"), p, None, 10, lvl["level"])
        images.append(path)
    errs = [v for v in res["violations"] if v["severity"] == "error"]
    return {"ok": True, "name": name, "grid": p["grid_view"], "levels": lv, "columns": len(p["columns"]),
            "scores": p["scores"], "spec_notes": [x["message"] for x in p["problems"]],
            "errors": errs, "warnings": [v for v in res["violations"] if v["severity"] == "warning"],
            "images": images}


# ---- the tools ----------------------------------------------------------------------------------

def plan_site(spec=None, name="Factory", seed=1, example=None):
    """Plan a building from a spec (see factory/spec.py), or from one of the examples."""
    if example:
        try:
            base = specs.load_example(example)
        except KeyError as exc:
            return {"ok": False, "error": str(exc.args[0])}
        for key, value in (spec or {}).items():
            base[key] = value
        spec = base
    if not spec:
        return {"ok": False, "error": "give a spec, or example=<name>; examples: " + ", ".join(specs.examples())}
    p = planner.plan(spec, seed=int(seed))
    if not p["ok"]:
        return {"ok": False, "error": "the spec has problems", "problems": p["problems"]}
    res = checks.check(p)
    _save(name, {"spec_in": spec, "plan": p, "layout": None, "built": False, "history": []})
    return _summary(p, res, name)


def edit_plan(name, ops, seed=None):
    try:
        state = _load(name)
        new, report = edits.apply(state["plan"], ops, seed)
    except (KeyError, edits.EditError) as exc:
        return {"ok": False, "error": str(exc.args[0] if exc.args else exc)}
    if not new.get("ok"):
        return {"ok": False, "error": "the edited spec has problems", "problems": new["problems"]}
    state["history"].append(ops)
    state["plan"] = new
    stale = []
    if state.get("layout"):
        state["layout"] = None
        stale.append("place_equipment")
    if state.get("built"):
        stale.insert(0, "build_plan")
    _save(name, state)
    out = _summary(new, checks.check(new), name)
    out["edit"] = report
    if stale:
        out["next"] = "the building in Blender is from before this edit: run " + " then ".join(stale)
    return out


def build_plan(name):
    try:
        state = _load(name)
    except KeyError as exc:
        return {"ok": False, "error": str(exc.args[0])}
    p = state["plan"]
    try:
        made = build.building(p, name)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    state["built"] = True
    _save(name, state)
    from .. import agent
    shell = agent.measure(made["shell"])
    parts = {}
    for obj in build.objects(name, "building"):
        parts[obj.get("cn_kind")] = parts.get(obj.get("cn_kind"), 0) + 1
    return {"ok": True, "name": name, "shell_size": shell["size"], "parts": parts,
            "collection": name, "next": "place_equipment, then verify"}


def place_equipment(name, items=None, seed=None, repair=True):
    try:
        state = _load(name)
    except KeyError as exc:
        return {"ok": False, "error": str(exc.args[0])}
    p = state["plan"]
    if items:
        try:
            p, _ = edits.apply(p, [{"op": "add_equipment", "item": it} for it in items], seed)
        except edits.EditError as exc:
            return {"ok": False, "error": str(exc)}
        if not p.get("ok"):
            return {"ok": False, "error": "the equipment has problems", "problems": p["problems"]}
        state["plan"] = p
    lay = layout.solve(p, seed=int(seed or p.get("seed", 1)))
    report = None
    if repair:
        lay, res, report = checks.repair(p, lay)
    else:
        res = checks.check(p, lay)
    state["layout"] = lay
    _save(name, state)
    try:
        objs = build.equipment(p, lay, name)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    counts = {}
    for it in lay["items"]:
        counts.setdefault(it["space"], {}).setdefault(it["kind"], 0)
        counts[it["space"]][it["kind"]] += 1
    path = drawing.save(_out(name, "layout_L0"), p, lay, 10, 0)
    return {"ok": True, "name": name, "objects": len(objs), "placed": counts, "unplaced": lay["unplaced"],
            "notes": lay["notes"], "repair": report,
            "errors": [v for v in res["violations"] if v["severity"] == "error"],
            "warnings": [v for v in res["violations"] if v["severity"] == "warning"],
            "stats": res["stats"], "image": path}


def assets(kind=None):
    if not kind:
        return {"ok": True, "generators": [{"kind": g["kind"], "category": g["category"], "about": g["about"]}
                                           for g in eq.generators()],
                "examples": {n: specs.load_example(n).get("about", "") for n in specs.examples()},
                "space_types": list(specs.SPACE_TYPES), "rules": specs.RULES,
                "tip": "assets('<kind>') for a generator's sliders and clearances; "
                       "assets('example:<name>') for a whole example spec to start from"}
    if kind.startswith("example:"):
        try:
            return {"ok": True, "spec": specs.load_example(kind.split(":", 1)[1])}
        except KeyError as exc:
            return {"ok": False, "error": str(exc.args[0])}
    try:
        g = eq.get(kind)
    except KeyError as exc:
        found = eq.generators(kind)
        if found:
            return {"ok": True, "generators": found}
        return {"ok": False, "error": str(exc.args[0])}
    m, info = eq.build(kind)
    return {"ok": True, **g.summary(), "footprint": info["footprint"], "height": info["height"]}


# ---- verification in Blender --------------------------------------------------------------------

def _world_mesh(obj, dg):
    from mathutils.bvhtree import BVHTree
    ev = obj.evaluated_get(dg)
    geometry = None
    try:
        geometry = ev.evaluated_geometry()      # keep it: it owns the mesh
        me = geometry.mesh
    except Exception:
        me = None
    if me is None:
        me = ev.to_mesh()
    if me is None or not len(me.polygons):
        return None
    mw = obj.matrix_world
    verts = [mw @ v.co for v in me.vertices]
    tree = BVHTree.FromPolygons(verts, [tuple(p.vertices) for p in me.polygons])
    del geometry
    return tree


def _aabb(obj):
    from mathutils import Vector
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return ([min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)])


def scene_checks(name, p):
    """What only the built scene can show: parts floating in the air, meshes that really
    intersect, and openings that were not actually cut through the walls."""
    from mathutils import Vector
    out, stats = [], {}
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    # floating parts, per generator and values in use
    seen = set()
    floating = 0
    for obj in build.objects(name):
        kind = obj.get("cn_kind")
        if kind not in eq.GENERATORS or kind == "light_fixture" or "cn_values" not in obj:
            continue
        key = (kind, obj["cn_values"])
        if key in seen:
            continue
        seen.add(key)
        parts = kit.floating(eq.build(kind, json.loads(obj["cn_values"]))[0])
        if parts:
            floating += 1
            out.append({"severity": "error", "code": "floating",
                        "message": f"{obj.name} ({kind}) has {len(parts)} part(s) in mid air, lowest at "
                                   f"z {parts[0][2][0][2]:.2f} m", "where": obj.get("cn_space")})
    stats["generator_variants"] = len(seen)
    # equipment meshes that intersect each other, the walls or the columns
    equip = build.objects(name, "equipment")
    fixed = [o for o in build.objects(name, "building") if o.get("cn_kind") in ("column", "shell", "walls")]
    trees = {}

    def tree(o):
        if o.name not in trees:
            trees[o.name] = _world_mesh(o, dg)
        return trees[o.name]

    boxes = {o.name: _aabb(o) for o in equip + fixed}
    hits = 0
    pairs = 0
    for i, a in enumerate(equip):
        (alo, ahi) = boxes[a.name]
        for b in equip[i + 1:] + fixed:
            if a.get("cn_group") and a.get("cn_group") == b.get("cn_group"):
                continue
            blo, bhi = boxes[b.name]
            if any(alo[k] > bhi[k] - 0.005 or blo[k] > ahi[k] - 0.005 for k in range(3)):
                continue
            ta, tb = tree(a), tree(b)
            if ta is None or tb is None:
                continue
            pairs += 1
            if ta.overlap(tb):
                hits += 1
                out.append({"severity": "error", "code": "intersect",
                            "message": f"{a.name} and {b.name} intersect", "where": a.get("cn_space")})
    stats["mesh_pairs_tested"] = pairs
    # rays through every door, dock, exit and window: nothing of the walls in the way
    walls = [o for o in fixed if o.get("cn_kind") in ("shell", "walls")]
    wall_trees = [(o, tree(o)) for o in walls]
    blocked = 0
    rays = 0
    for lvl in p["levels"]:
        for o in lvl["openings"]:
            x, y = o["pos"]
            z = lvl["z"] + o["sill"] + min(1.0, o["height"] / 2)
            d = Vector((0, 1, 0)) if o["axis"] == "x" else Vector((1, 0, 0))
            start = Vector((x, y, z)) - d * 0.6
            rays += 1
            for wo, t in wall_trees:
                if t is None:
                    continue
                hit = t.ray_cast(start, d, 1.2)
                if hit[0] is not None:
                    blocked += 1
                    out.append({"severity": "error", "code": "opening",
                                "message": f"the {o['kind']} of {o['space']} at ({x:.1f}, {y:.1f}) is not cut "
                                           f"through {wo.name}", "where": o["space"]})
                    break
    stats["opening_rays"] = rays
    stats["opening_rays_blocked"] = blocked
    return out, stats


def _camera(name):
    cam = bpy.data.objects.get(f"{name} Camera")
    if cam is None:
        cam = bpy.data.objects.new(f"{name} Camera", bpy.data.cameras.new(f"{name} Camera"))
        build.collection(name).objects.link(cam)
        build._tag(cam, name, "camera")
    bpy.context.scene.camera = cam
    return cam


def render_views(name, p, views=("plan", "iso", "walk"), width=960):
    """Standard pictures: a plan from above and an isometric cut-away (roof off), and a
    walk down the main aisle at eye height (roof on)."""
    from mathutils import Vector
    s = bpy.context.scene
    W, D = p["site"]["size"]
    top = p["levels"][-1]["z"] + p["levels"][-1]["height"]
    roof = bpy.data.objects.get(f"{name} Roof")
    cam = _camera(name)
    if s.world is None:
        s.world = bpy.data.worlds.new("World")
    s.world.color = (0.55, 0.6, 0.66)
    engine = next((e for e in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT") if _has_engine(e)), "BLENDER_WORKBENCH")
    paths = {}
    previous = (s.render.engine, s.render.resolution_x, s.render.resolution_y, s.render.filepath,
                s.render.resolution_percentage)
    try:
        for view in views:
            cam.data.type = 'PERSP'
            cam.data.lens = 28
            if roof is not None:
                roof.hide_render = view in ("plan", "iso")
            if view == "plan":
                cam.data.type = 'ORTHO'
                cam.data.ortho_scale = max(W, D) * 1.08
                cam.location = (W / 2, D / 2, top + 60)
                cam.rotation_euler = (0, 0, 0)
                aspect = D / W if W >= D else 1.0
            elif view == "iso":
                centre = Vector((W / 2, D / 2, 0))
                off = Vector((-0.75, -1.0, 0.95)).normalized() * (max(W, D) * 1.25)
                cam.location = centre + off
                cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()
                aspect = 0.62
            else:
                a = max(p["levels"][0]["aisles"], key=lambda a: a["width"], default=None)
                if a is None:
                    continue
                r = a["rect"]
                if r[2] - r[0] >= r[3] - r[1]:
                    cam.location = (r[0] + 1.5, (r[1] + r[3]) / 2, 1.7)
                    target = Vector((r[2], (r[1] + r[3]) / 2, 1.6))
                else:
                    cam.location = ((r[0] + r[2]) / 2, r[1] + 1.5, 1.7)
                    target = Vector(((r[0] + r[2]) / 2, r[3], 1.6))
                cam.rotation_euler = (target - Vector(cam.location)).to_track_quat('-Z', 'Y').to_euler()
                aspect = 0.56
            s.render.engine = engine
            s.render.resolution_percentage = 100
            s.render.resolution_x = width
            s.render.resolution_y = max(1, int(width * aspect))
            path = _out(name, view)
            s.render.filepath = path
            bpy.ops.render.render(write_still=True)
            paths[view] = path
    finally:
        if roof is not None:
            roof.hide_render = False
        (s.render.engine, s.render.resolution_x, s.render.resolution_y, s.render.filepath,
         s.render.resolution_percentage) = previous
    return paths, engine


def _has_engine(e):
    try:
        return e in {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    except Exception:
        return False


def verify(name, renders=True, repair=True, views=("plan", "iso", "walk")):
    try:
        state = _load(name)
    except KeyError as exc:
        return {"ok": False, "error": str(exc.args[0])}
    p, lay = state["plan"], state.get("layout")
    report = None
    if lay is not None and repair:
        before = lay
        lay, res, report = checks.repair(p, lay)
        if lay is not before and report["rounds"] and report["rounds"][-1]["kept"]:
            state["layout"] = lay
            _save(name, state)
            build.equipment(p, lay, name)
    res = checks.check(p, lay)
    out = list(res["violations"])
    stats = dict(res["stats"])
    if state.get("built"):
        more, extra = scene_checks(name, p)
        out.extend(more)
        stats.update(extra)
    images = {}
    engine = None
    if renders and state.get("built"):
        images, engine = render_views(name, p, views)
    errors = [v for v in out if v["severity"] == "error"]
    return {"ok": True, "name": name, "passed": not errors, "errors": errors,
            "warnings": [v for v in out if v["severity"] == "warning"], "stats": stats,
            "repair": report, "images": images, "engine": engine,
            "checked": ["areas", "shapes", "outside walls", "docks", "collisions", "bounds", "aisles",
                        "service clearance", "egress walk", "robot reach and fences"]
                       + (["floating parts", "mesh intersections", "openings cut"] if state.get("built") else [])}
