"""The factory builder's core, without Blender: kit, generators, spec, planner, layout,
verifier, repair, edits and drawing.

    python tests/test_factory.py
"""
import copy
import importlib.util
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = importlib.util.module_from_spec(importlib.util.spec_from_loader("codenodes", loader=None, is_package=True))
pkg.__path__ = [os.path.join(ROOT, "codenodes")]
sys.modules["codenodes"] = pkg
from codenodes.factory import (drawing, edits, equipment as eq, kit, layout,  # noqa: E402
                               planner, spec, verify)

_checks = 0


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        raise SystemExit(1)
    print(f"  ok: {msg}")


def closed(m):
    edges = {}
    for f in m.faces:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            edges[(min(a, b), max(a, b))] = edges.get((min(a, b), max(a, b)), 0) + 1
    return all(n == 2 for n in edges.values())


def errors(res):
    return [v for v in res["violations"] if v["severity"] == "error"]


PLANS = {}


def planned(name, seed=1):
    if (name, seed) not in PLANS:
        PLANS[(name, seed)] = planner.plan(spec.load_example(name), seed=seed)
    return PLANS[(name, seed)]


def test_kit():
    p = kit.Params(w=(2.0, 0.5, 4.0), n=(3, 1, 10))
    part = kit.Group([kit.Box((p.w, 1.0, 0.5), at=(0, 0, 0.25), mat="a"),
                      kit.Array(kit.Cyl(0.1, 0.4, mat="b", segments=8), count=p.n, step=(0.5, 0, 0),
                                at=(-p.w / 2, 0, 1.0))])
    m = kit.mesh(part, p.resolve({}))
    check(len(m.verts) == 8 + 3 * 16 and len(m.faces) == 6 + 3 * 10, "a box and three cylinders: 56 verts, 36 faces")
    (x0, y0, z0), (x1, y1, z1) = m.bounds()
    check(abs(x1 - x0 - 2.1) < 1e-9 and abs(z0) < 1e-9 and abs(z1 - 1.2) < 1e-9, "sizes follow the formulas")
    m2 = kit.mesh(part, p.resolve({"w": 3.0, "n": 5.7}))
    check(len(m2.verts) == 8 + 5 * 16, "counts are floored (5.7 -> 5 copies)")
    check(p.resolve({"w": 99})["w"] == 4.0, "values are clamped to the slider range")
    try:
        p.resolve({"nope": 1})
        check(False, "unknown parameters are refused")
    except KeyError as exc:
        check("nope" in str(exc), "unknown parameters are refused by name")
    check(closed(m) and m.materials() == ["a", "b"], "every face belongs to a closed part, materials kept in order")
    turned = kit.mesh(kit.Box((2.0, 1.0, 1.0), rot=(0, 0, 90)), {})
    (a, b, _), (c, d, _) = turned.bounds()
    check(abs((c - a) - 1.0) < 1e-9 and abs((d - b) - 2.0) < 1e-9, "rot turns about Z in degrees")
    cone = kit.mesh(kit.Cone(1.0, 0.0, 2.0, segments=12), {})
    check(len(cone.verts) == 13 and closed(cone), "a cone with a plain 0 top ends in one point")


def test_generators():
    kinds = sorted(eq.GENERATORS)
    check(len(kinds) >= 16, f"{len(kinds)} generators ({', '.join(kinds)})")
    for k in kinds:
        m, info = eq.build(k)
        check(len(m.faces) > 0 and closed(m) and info["bottom"] >= -0.02,
              f"{k}: closed parts, stands on the floor ({info['footprint'][0]:.2f} x {info['footprint'][1]:.2f} m)")
    m, info = eq.build("conveyor", {"length": 6.0})
    check(abs(info["footprint"][0] - 6.0) < 0.01 and abs(info["height"] - 0.87) < 0.01,
          "a 6 m conveyor measures 6 m long, rollers at 0.85 m")
    m, info = eq.build("rack", {"bays": 4, "levels": 5, "level_height": 1.5})
    check(abs(info["footprint"][0] - (4 * 2.7 + 0.08)) < 0.01 and abs(info["height"] - (5 * 1.5 + 0.3)) < 0.01,
          "a 4-bay, 5-level rack is 10.88 m long and 7.8 m tall")
    unloaded = eq.build("rack", {"loaded": 0})[0]
    check(len(unloaded.faces) < len(eq.build("rack")[0].faces), "an unloaded rack has no pallets")
    _, arm = eq.build("robot_arm")
    check(arm["reach"] == round(0.85 + 0.8 + 0.25, 3) and len(arm["joints"]) == 6
          and all(j["limits"][0] < j["limits"][1] for j in arm["joints"]),
          "the robot arm reports its reach and six revolute joints with limits")
    a, _ = eq.build("robot_arm", {"j1": 0})
    b, _ = eq.build("robot_arm", {"j1": 90})
    (ax0, ay0, _), (ax1, ay1, _) = a.bounds()
    (bx0, by0, _), (bx1, by1, _) = b.bounds()
    check(ax1 - ax0 > ay1 - ay0 and by1 - by0 > bx1 - bx0, "turning j1 by 90 degrees swings the arm round")
    check(eq.build("robot_arm", {"j2": 500})[1]["values"]["j2"] == 90, "joint angles stay within their limits")
    gen = eq.get("cnc").summary()
    check(gen["clearance"]["front"] == 0.9 and "width" in gen["params"], "a generator's summary lists params and clearance")
    g = kit.to_graph("CN Rack", "test", eq.get("rack").params, eq.get("rack").part())
    data = g.data()
    names = [i["socket"] for i in data["interface"] if i.get("in_out") == "INPUT"]
    check("Bays" in names and "Level Height" in names and "Rack Blue" in names,
          "a generator becomes a node group with a slider per parameter and a material per colour")
    check(any(n["type"] == "GeometryNodeMeshLine" for n in data["nodes"]),
          "arrays become Mesh Line + Instance on Points")


def test_spec():
    for name in spec.examples():
        s, probs = spec.normalize(spec.load_example(name))
        check(not [p for p in probs if p["severity"] == "error"], f"example {name} has no errors")
    bad = {"site": {"size": [30, 20]},
           "spaces": [{"name": "A", "area": 100, "type": "factory"}, {"name": "A", "area": -3},
                      {"name": "B", "area": 50, "type": "office"}],
           "relations": [["A", "Nope", "A"], ["A", "B", "Q"]],
           "equipment": [{"kind": "spaceship", "in": "A"}, {"kind": "cnc", "in": "B", "params": {"wings": 2}},
                         {"kind": "cnc"}]}
    _, probs = spec.normalize(bad)
    text = " | ".join(p["message"] for p in probs)
    for bit in ("unknown type 'factory'", "two spaces are called 'A'", "area must be a positive",
                "no space called 'Nope'", "closeness must be one of", "unknown equipment 'spaceship'",
                "no parameter called 'wings'", "needs \"in\""):
        check(bit in text, f"a broken spec says: {bit}")
    s, probs = spec.normalize({"site": {"footprint": [[0, 0], [10, 0], [10, 8], [0, 8]]},
                               "spaces": [{"name": "Hall", "area": 60, "type": "production"}]})
    check(s["site"]["size"] == [10.0, 8.0], "a rectangular footprint becomes a size")
    _, probs = spec.normalize({"site": {"footprint": [[0, 0], [10, 0], [10, 8], [5, 8], [5, 4], [0, 4]]},
                               "spaces": [{"name": "Hall", "area": 60}]})
    check(any("only rectangular" in p["message"] for p in probs), "other footprints are refused plainly")


def test_planner():
    for name in spec.examples():
        p = planned(name)
        W, D = p["site"]["size"]
        for lvl in p["levels"]:
            rects = [sp["rect"] for sp in lvl["spaces"]] + [a["rect"] for a in lvl["aisles"]]
            ok_inside = all(r[0] >= -1e-6 and r[1] >= -1e-6 and r[2] <= W + 1e-6 and r[3] <= D + 1e-6 for r in rects)
            no_overlap = not any(layout.overlap(rects[i], rects[j]) for i in range(len(rects))
                                 for j in range(i + 1, len(rects)))
            covered = sum(layout.area(r) for r in rects)
            check(ok_inside and no_overlap and abs(covered - W * D) < 0.5 or (lvl["level"] > 0 and no_overlap),
                  f"{name} level {lvl['level']}: spaces and aisles tile the site without overlaps")
            fronts = all(any(abs(sp["rect"][1] - a["rect"][3]) < 1e-3 or abs(sp["rect"][3] - a["rect"][1]) < 1e-3
                             or abs(sp["rect"][0] - a["rect"][2]) < 1e-3 or abs(sp["rect"][2] - a["rect"][0]) < 1e-3
                             for a in lvl["aisles"]) for sp in lvl["spaces"])
            check(fronts, f"{name} level {lvl['level']}: every space fronts an aisle")
        res = verify.check(p)
        check(not errors(res), f"{name}: the plan's own checks pass ({len(res['violations'])} warnings)")
    p = planned("factory")
    again = planner.plan(spec.load_example("factory"), seed=1)
    check(again["levels"] == p["levels"], "the same spec and seed give the same plan")
    for lvl in p["levels"]:
        rules = p["spec"]["rules"]
        for a in lvl["aisles"]:
            need = rules["aisle_forklift"] if a["kind"] == "forklift" else rules["corridor"]
            check(a["clear_width"] + 1e-6 >= need, f"aisle {a['index']} is {a['clear_width']} m clear (>= {need})")
    rec = next(sp for sp in p["levels"][0]["spaces"] if sp["name"] == "Receiving")
    shp = next(sp for sp in p["levels"][0]["spaces"] if sp["name"] == "Shipping")
    check("south" in rec["exterior"] and "north" in shp["exterior"], "receiving is on the south wall, shipping on the north")
    docks = [o for o in p["levels"][0]["openings"] if o["kind"] == "dock"]
    check(len(docks) == 4 and all(o["wall"] == "exterior" for o in docks), "four dock doors, all in outside walls")
    exits = [o for o in p["levels"][0]["openings"] if o["kind"] == "exit"]
    check(len(exits) >= 3, f"{len(exits)} personnel exits (aisle ends, and beside the drive-in door)")
    check(all(not any(layout.overlap([c[0] - 0.2, c[1] - 0.2, c[0] + 0.2, c[1] + 0.2], a["rect"])
                      for a in p["levels"][0]["aisles"]) for c in p["columns"]) and p["columns"],
          f"{len(p['columns'])} columns, none in an aisle")
    # the search beats random arrangements on its own score
    lv = planner.Level(p["spec"], 0, planner.Frame(*p["site"]["size"]))
    rng = random.Random(3)
    best = planner.score(lv, p["assignment"]["0"], planner.layout(lv, p["assignment"]["0"])[0])[0]
    names = [sp["name"] for sp in lv.spaces]
    worse = 0
    for _ in range(30):
        rng.shuffle(names)
        k = len(p["assignment"]["0"])
        st = [names[i::k] for i in range(k)]
        if planner.score(lv, st, planner.layout(lv, st)[0])[0] > best:
            worse += 1
    check(worse >= 29, f"the plan scores better than {worse}/30 random arrangements")
    h = planned("house")
    check(len(h["levels"]) == 2 and h["levels"][0]["stairs"] and h["levels"][1]["holes"],
          "a two-storey house gets a stair on the ground floor and a hole for it upstairs")
    st = h["levels"][0]["stairs"][0]
    rise = st["to"][2] - st["from"][2]
    run = math.dist(st["from"][:2], st["to"][:2])
    steps = math.ceil(rise / planner.STEP_RISE)
    check(abs(run / steps - planner.STEP_RUN) < 0.01, f"stairs climb {rise} m in {steps} steps of {run / steps:.2f} m")
    check("level 0" in p["grid_view"] and "= Receiving" in p["grid_view"] and "D" in p["grid_view"],
          "the grid view shows the spaces with a legend and the docks")


def test_layout_and_verify():
    for name in spec.examples():
        p = planned(name)
        lay = layout.solve(p)
        res = verify.check(p, lay)
        check(not errors(res), f"{name}: {len(lay['items'])} things placed, no errors "
                               f"(longest walk out {res['stats'].get('longest_walk')} m)")
        asked = sum(e["count"] for e in p["spec"]["equipment"] if isinstance(e["count"], int) and "to" not in e)
        got = sum(1 for it in lay["items"] if it["role"] not in ("rack", "conveyor", "fixture", "cell")
                  and it["kind"] != "fence")
        check(got == asked - sum(u.get("count", 1) for u in lay["unplaced"]),
              f"{name}: every asked-for thing is placed or reported ({got}/{asked})")
    p = planned("factory")
    lay = layout.solve(p)
    conv = [it for it in lay["items"] if it["kind"] == "conveyor"]
    check(len(conv) == 1 and set(conv[0]["spans"]) == {"Machining", "Assembly"}, "the conveyor links Machining to Assembly")
    racks = [it for it in lay["items"] if it["kind"] == "rack"]
    check(len(racks) >= 6 and all(it["rotation"] % 180 == 90 for it in racks),
          f"{len(racks)} rack rows, all at right angles to the aisle")
    for it in racks:
        face = it["footprint"]
        fronting = [ra for ra in lay["rack_aisles"] if ra["space"] == it["space"] and
                    (abs(ra["rect"][0] - face[2]) < 0.05 or abs(ra["rect"][2] - face[0]) < 0.05)]
        check(fronting, f"{it['id']} faces a rack aisle")
    arms = [it for it in lay["items"] if it["kind"] == "robot_arm"]
    fences = [it for it in lay["items"] if it["kind"] == "fence"]
    check(len(arms) == 2 and len(fences) == 2, "two robot arms, each in its own fenced cell")
    again = layout.solve(planner.plan(spec.load_example("factory"), seed=1))
    check(again["items"] == lay["items"], "the same plan gives the same layout")
    check(not conv[0]["overhead"] and conv[0]["values"]["height"] < 1.0,
          "neighbours in one strip get a conveyor on the floor across their shared edge")
    # the bakery's ovens and packing face each other across the aisle: an overhead bridge
    b = planned("bakery")
    bl = layout.solve(b)
    bc = [it for it in bl["items"] if it["kind"] == "conveyor"]
    aisle = b["levels"][0]["aisles"][0]["rect"]
    check(bc and bc[0]["overhead"] and bc[0]["values"]["height"] >= 4.5
          and layout.overlap(bc[0]["footprint"], aisle) and not any(layout.overlap(s, aisle) for s in bc[0]["solids"]),
          "across an aisle the conveyor becomes an overhead bridge: it spans the aisle, its legs stay out of it")


def test_breakage_and_repair():
    p = planned("factory")
    lay = layout.solve(p)
    # push a machine into the aisle: an error; repair puts it back
    bad = copy.deepcopy(lay)
    cnc = next(it for it in bad["items"] if it["kind"] == "cnc")
    aisle = next(a for a in p["levels"][0]["aisles"] if a["kind"] == "forklift")
    dx, dy = 0.0, (aisle["rect"][1] + aisle["rect"][3]) / 2 - (cnc["footprint"][1] + cnc["footprint"][3]) / 2
    for key in ("footprint", "clearance"):
        r = cnc[key]
        cnc[key] = [r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy]
    cnc["solids"] = [cnc["footprint"]]
    res = verify.check(p, bad)
    check(any(v["code"] == "aisle" and cnc["id"] in v["message"] for v in errors(res)),
          "a machine in the aisle is an error that names it")
    fixed, fixed_res, report = verify.repair(p, bad)
    check(not errors(fixed_res) and report["before"] > report["after"] == 0,
          f"repair re-places it: {report['before']} errors -> {report['after']}")
    # two things on top of each other
    bad = copy.deepcopy(lay)
    desks = [it for it in bad["items"] if it["kind"] == "desk"]
    desks[1].update(footprint=desks[0]["footprint"], solids=[desks[0]["footprint"]], clearance=desks[0]["clearance"])
    check(any(v["code"] == "collision" for v in errors(verify.check(p, bad))), "overlapping desks are a collision")
    fixed, fixed_res, report = verify.repair(p, bad)
    check(not errors(fixed_res), "repair separates them")
    # a robot moved away from its fixture
    bad = copy.deepcopy(lay)
    arm = next(it for it in bad["items"] if it["kind"] == "robot_arm")
    f = arm["footprint"]
    arm["footprint"] = [f[0] + 2.2, f[1], f[2] + 2.2, f[3]]
    codes = {v["code"] for v in errors(verify.check(p, bad, egress=False))}
    check("reach" in codes or "fence" in codes, f"a robot out of reach of its fixture or too near its fence: {codes}")
    # an office walled in: its door blocked by a cabinet across it
    bad = copy.deepcopy(lay)
    door = next(o for o in p["levels"][0]["openings"] if o["space"] == "Offices" and o["kind"] == "door")
    x, y = door["pos"]
    block = [x - 1.2, y - 0.6, x + 1.2, y + 0.6]
    bad["items"].append({"id": "Offices blocker 1", "kind": "cabinet", "space": "Offices", "level": 0, "role": "item",
                         "values": {}, "location": [x, y, 0], "rotation": 0, "footprint": block, "solids": [block],
                         "clearance": block, "height": 2.0, "overhead": False, "group": None})
    res = verify.check(p, bad)
    check(any(v["code"] == "egress" and "Offices" in v["message"] for v in errors(res)),
          "blocking the only door of the offices leaves them with no way out")
    # a rule the plan cannot meet
    tight = spec.load_example("factory")
    tight["rules"] = {"max_travel": 10.0}
    tp = planner.plan(tight, seed=1)
    res = verify.check(tp, layout.solve(tp))
    check(any(v["code"] == "egress" and "longest walk" in v["message"] for v in errors(res)),
          "a 10 m travel limit is reported as broken, with the measured walk")


def test_edits():
    p = planned("factory")
    names = [sp["name"] for sp in p["levels"][0]["spaces"]]
    a, b = "Offices", "Break Room"
    new, rep = edits.apply(p, [{"op": "swap", "a": a, "b": b}])
    ra = {sp["name"]: sp["rect"] for sp in p["levels"][0]["spaces"]}
    rb = {sp["name"]: sp["rect"] for sp in new["levels"][0]["spaces"]}
    check(rep["kept_arrangement"] and set(rep["moved"]) >= {a, b}, "a swap exchanges two spaces and keeps the rest")
    still = [n for n in names if n not in rep["moved"]]
    check(all(ra[n] == rb[n] for n in still), f"{len(still)} other spaces stay exactly where they were")
    new, rep = edits.apply(p, [{"op": "add_space", "space": {"name": "Paint", "area": 150, "type": "production",
                                                              "enclosed": True}},
                               {"op": "relation", "a": "Paint", "b": "Assembly", "rating": "A"},
                               {"op": "add_equipment", "item": {"kind": "tank", "count": 2, "in": "Paint"}}])
    check(new["ok"] and rep["added"] == ["Paint"] and any(e["in"] == "Paint" for e in new["spec"]["equipment"]),
          "adding a space with a relation and equipment re-solves around it")
    new, rep = edits.apply(p, [{"op": "remove_space", "space": "Break Room"}])
    check(new["ok"] and rep["removed"] == ["Break Room"], "removing a space")
    new, rep = edits.apply(p, [{"op": "rule", "name": "aisle_forklift", "value": 4.2}])
    widths = [a["width"] for a in new["levels"][0]["aisles"] if a["kind"] == "forklift"]
    check(widths and min(widths) >= 4.2 - 1e-6, f"a wider forklift rule widens the aisles ({widths})")
    try:
        edits.apply(p, [{"op": "swap", "a": "Offices", "b": "Nowhere"}])
        check(False, "a bad edit is refused")
    except edits.EditError as exc:
        check("Nowhere" in str(exc) and "ops[0]" in str(exc), f"a bad edit says which op and why: {exc}")


def test_drawing():
    p = planned("factory")
    data = drawing.draw(p, layout.solve(p), px_per_m=8)
    check(data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 2000, f"the plan draws as a PNG ({len(data)} bytes)")


def main():
    test_kit()
    test_generators()
    test_spec()
    test_planner()
    test_layout_and_verify()
    test_breakage_and_repair()
    test_edits()
    test_drawing()
    print(f"\nAll {_checks} checks passed.")


if __name__ == "__main__":
    main()
