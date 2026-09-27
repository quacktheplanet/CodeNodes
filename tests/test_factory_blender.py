"""The factory builder in Blender: plan -> building -> equipment -> verify, the checks that
need the built scene, the generators as Geometry Nodes groups, and a two-storey house.

    blender -b --factory-startup --python tests/test_factory_blender.py
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402,F401
from codenodes import agent  # noqa: E402
from codenodes.factory import build, equipment as eq, kit, tools  # noqa: E402
from codenodes.gn import library, serialize  # noqa: E402

_checks = 0
_failed = False


def check(cond, msg):
    global _checks, _failed
    _checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        _failed = True


def world_tree(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    geometry = obj.evaluated_get(dg).evaluated_geometry()      # keep: it owns the mesh
    me = geometry.mesh
    mw = obj.matrix_world
    return BVHTree.FromPolygons([mw @ v.co for v in me.vertices], [tuple(p.vertices) for p in me.polygons])


def test_wall_openings():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    agent.curve("Wall", splines=[[[0, 0, 0], [10, 0, 0]]], smooth=False)
    me = bpy.data.meshes.new("Holes")
    me.from_pydata([(3.0, 0.0, 0.0), (7.0, 0.0, 0.9)], [], [])
    me.attributes.new("size", 'FLOAT_VECTOR', 'POINT')
    me.attributes.new("angle", 'FLOAT', 'POINT')
    me.attributes["size"].data.foreach_set("vector", [1.2, 0, 2.1, 1.5, 0, 1.2])
    holes = bpy.data.objects.new("Holes", me)
    bpy.context.scene.collection.objects.link(holes)
    r = agent.nodes_use("wall_network", "Wall", {"Height": 3.0, "Thickness": 0.3, "Doorways per Wall": 0,
                                                 "Openings": "Holes"})
    check(r["ok"], f"wall_network takes an Openings points object ({r.get('error', '')})")
    t = world_tree(bpy.data.objects["Wall"])

    def through(x, z):
        return t.ray_cast(Vector((x, -1, z)), Vector((0, 1, 0)), 2.0)[0] is None
    check(through(3.0, 1.0) and not through(4.2, 1.0) and through(7.0, 1.5) and not through(7.0, 0.5),
          "each point cuts its own opening: a 1.2 m door from the floor, a 1.5 m window above a 0.9 m sill")
    check(not through(3.0, 2.3) and not through(7.0, 2.2), "openings stop at their height")


def test_generators_as_nodes():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    worst = (0.0, "")
    mismatched = []
    for kind, gen in sorted(eq.GENERATORS.items()):
        g = kit.to_graph(f"CN Kit {kind}", gen.about, gen.params, gen.part())
        tree = serialize.write(g.data(), name=g.name)
        obj = bpy.data.objects.new(kind, bpy.data.meshes.new(kind))
        bpy.context.scene.collection.objects.link(obj)
        mod = obj.modifiers.new("kit", 'NODES')
        mod.node_group = tree
        variants = [{}]
        spec = gen.params.spec
        tweak = next((n for n in spec if spec[n]["max"] is not None and n not in ("j6",)), None)
        if tweak:
            lo, hi = spec[tweak]["min"], spec[tweak]["max"]
            variants.append({tweak: lo + (hi - lo) * 0.7})
        for vals in variants:
            label_vals = {k.replace("_", " ").title(): v for k, v in vals.items()}
            probs = agent._set_modifier_inputs(obj, mod, dict(gen.params.resolve(vals).items() and
                                                              {k.replace("_", " ").title(): v for k, v in
                                                               gen.params.resolve(vals).items()}))
            m = kit.mesh(gen.part(), gen.params.resolve(vals))
            got = agent.measure(obj)
            (x0, y0, z0), (x1, y1, z1) = m.bounds()
            want = [x1 - x0, y1 - y0, z1 - z0]
            err = max(abs(a - b) / max(b, 0.05) for a, b in zip(got["size"], want))
            if err > worst[0]:
                worst = (err, f"{kind} {vals}")
            if probs or got["verts"] != len(m.verts) or err > 0.03:
                mismatched.append(f"{kind} {label_vals}: verts {got['verts']} vs {len(m.verts)}, "
                                  f"size {got['size']} vs {[round(w, 3) for w in want]} {probs}")
    check(not mismatched, f"all {len(eq.GENERATORS)} generators make the same mesh as node groups as in "
                          f"Python, at defaults and moved sliders (worst size difference {worst[0]:.1%}, {worst[1]}) "
                          f"{mismatched[:2]}")


def test_factory():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    r = tools.plan_site(example="factory", name="Plant")
    check(r["ok"] and not r["errors"] and os.path.exists(r["images"][0]),
          f"plan_site plans the example factory ({len(r['levels'][0]['spaces'])} spaces) and draws it")
    r = tools.build_plan("Plant")
    check(r["ok"], f"build_plan builds it ({r.get('error', '')})")
    if not r["ok"]:
        return
    sx, sy, sz = r["shell_size"]
    check(abs(sx - 60.3) < 0.05 and abs(sy - 40.3) < 0.05 and abs(sz - 9.3) < 0.05,
          f"the shell measures {sx} x {sy} x {sz} m: 60 x 40 m on centre lines, 0.3 m walls, 9 m tall plus footing")
    parts = r["parts"]
    check(parts.get("column") == 10 and parts.get("dock_door") == 5 and parts.get("window") >= 5,
          f"columns, dock doors and windows are placed ({parts})")
    r = tools.place_equipment("Plant")
    check(r["ok"] and not r["errors"] and not r["unplaced"],
          f"place_equipment lays out and builds {r.get('objects')} things with no errors")
    equip = build.objects("Plant", "equipment")
    meshes = {o.data.name for o in equip}
    check(len(equip) == r["objects"] and len(meshes) < len(equip) / 2,
          f"{len(equip)} objects share {len(meshes)} meshes (same generator and values, one mesh)")
    grounded = []
    for o in equip:
        lo = min((o.matrix_world @ Vector(c)).z for c in o.bound_box)
        if abs(lo) > 0.01:
            grounded.append(f"{o.name} {lo:.3f}")
    check(not grounded, f"everything stands on the floor ({grounded[:3]})")
    r = tools.verify("Plant", renders=True, views=("plan",))
    check(r["passed"], f"verify passes: {r['errors'][:2]}")
    st = r["stats"]
    check(st["opening_rays"] >= 15 and st["opening_rays_blocked"] == 0,
          f"a ray passes through every one of the {st['opening_rays']} openings")
    check(st["mesh_pairs_tested"] > 20, f"{st['mesh_pairs_tested']} close pairs of meshes were tested for "
                                        "intersections, none found")
    check(os.path.exists(r["images"].get("plan", "")) and os.path.getsize(r["images"]["plan"]) > 20000,
          f"a plan render comes back ({r['engine']})")
    # break it on purpose: the checks must notice
    shell = bpy.data.objects["Plant Shell"]
    mod = shell.modifiers[0]
    ident = next(i.identifier for i in mod.node_group.interface.items_tree
                 if i.item_type == 'SOCKET' and i.name == "Openings")
    mod[ident] = None
    shell.update_tag()
    r = tools.verify("Plant", renders=False)
    check(any(v["code"] == "opening" for v in r["errors"]), "with the shell's openings object taken away, the docks "
                                                            "and exits are reported as not cut")
    desk = next(o for o in build.objects("Plant", "equipment") if "desk" in o.name)
    other = next(o for o in build.objects("Plant", "equipment") if "desk" in o.name and o != desk)
    desk.location = other.location.copy()
    r = tools.verify("Plant", renders=False, repair=False)
    check(any(v["code"] == "intersect" for v in r["errors"]), "two desks pushed into each other are found by "
                                                              "their meshes")
    # the edit loop
    r = tools.edit_plan("Plant", [{"op": "swap", "a": "Offices", "b": "Break Room"}])
    check(r["ok"] and r["edit"]["kept_arrangement"] and "build_plan" in r.get("next", ""),
          "edit_plan swaps two spaces and says the building needs rebuilding")
    tools.build_plan("Plant")
    tools.place_equipment("Plant")
    r = tools.verify("Plant", renders=False)
    check(r["passed"], f"after the edit it rebuilds and verifies again ({r['errors'][:1]})")
    before = len(bpy.data.objects)
    tools.build_plan("Plant")
    check(len(bpy.data.objects) == before, "building again replaces the old building instead of adding to it")
    mine = bpy.data.objects.new("My Own Thing", None)
    bpy.context.scene.collection.objects.link(mine)
    tools.build_plan("Plant")
    check("My Own Thing" in bpy.data.objects, "rebuilding never touches objects it did not make")
    a = tools.assets()
    check(a["ok"] and len(a["generators"]) >= 16 and "factory" in a["examples"], "assets lists generators and examples")
    arm = tools.assets("robot_arm")
    check(arm["ok"] and len(arm["joints"]) == 6 and arm["reach"] > 1.5, "assets('robot_arm') gives joints and reach")


def test_house():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    r = tools.plan_site(example="house", name="House")
    check(r["ok"] and len(r["levels"]) == 2, "a two-storey house plans")
    r = tools.build_plan("House")
    check(r["ok"], f"and builds ({r.get('error', '')})")
    stair = next((o for o in bpy.data.objects if o.name.startswith("House Stair")), None)
    check(stair is not None and agent.measure(stair)["size"][2] > 3.0, "with a stair that climbs a storey")
    state = json.loads(bpy.data.texts["CN Factory House"].as_string())
    hole = state["plan"]["levels"][1]["holes"][0]
    x, y = (hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2
    up = world_tree(bpy.data.objects["House Floor L1"])
    check(up.ray_cast(Vector((x, y, 10)), Vector((0, 0, -1)), 20)[0] is None,
          "the upper floor has a hole over the stair")
    check(up.ray_cast(Vector((x + 3.0, y + 0.0, 10)), Vector((0, 0, -1)), 20)[0] is not None
          or up.ray_cast(Vector((x, y + 3.0, 10)), Vector((0, 0, -1)), 20)[0] is not None,
          "and is solid beside it")
    walls = [o for o in bpy.data.objects if o.name.startswith("House Walls L")]
    check(len(walls) == 2, "interior walls on both floors")
    r = tools.verify("House", renders=False)
    check(r["passed"], f"the house verifies ({r['errors'][:2]})")


def main():
    test_wall_openings()
    test_generators_as_nodes()
    test_factory()
    test_house()
    print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)


main()
