"""Every ready-made capability: it builds, it makes something, it is seeded, its controls
do something, it survives the round trip — and editing and explaining work on real trees.

    blender -b --factory-startup --python tests/test_gn_library.py     (no GPU needed)
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import agent  # noqa: E402
from codenodes.gn import library, serialize  # noqa: E402

_checks = 0
_failed = False


def check(cond, msg):
    global _checks, _failed
    _checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        _failed = True


def made(obj_name):
    return agent.measure(bpy.data.objects[obj_name])


def ray(obj_name, origin, direction):
    """Distance to the first surface an object's result has along a ray, or None."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    obj = bpy.data.objects[obj_name]
    dg = bpy.context.evaluated_depsgraph_get()
    geometry = obj.evaluated_get(dg).evaluated_geometry()     # keep: it owns the mesh
    mesh = geometry.mesh
    tree = BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices],
                                [tuple(p.vertices) for p in mesh.polygons])
    hit = tree.ray_cast(Vector(origin), Vector(direction))
    return None if hit[0] is None else round(hit[3], 2)


def signature(info):
    return (info["verts"], info["faces"], info["curves"], info["points"], info["instances"],
            tuple(info["size"]))


def main():
    codenodes.register()
    listed = agent.nodes_library()
    keys = [c["key"] for c in listed["capabilities"]]
    check(set(keys) >= {"terrain", "scatter", "wall", "path_bridge", "along_curve"},
          f"the library lists its capabilities ({', '.join(keys)})")
    check(all(c["about"] and c["inputs"] for c in listed["capabilities"]),
          "each one says what it does and what it takes")

    # every capability builds without a single warning
    for key in keys:
        warnings = []
        library.build(key, warnings=warnings)
        check(not warnings, f"{key} builds cleanly{': ' + warnings[0] if warnings else ''}")

    # terrain, with a canyon
    got = agent.nodes_use("terrain", values={"Size": 60, "Resolution": 120, "Height": 3,
                                             "Canyon Depth": 8, "Canyon Width": 5})
    check(got["ok"] and got["made"]["faces"] == 119 * 119,
          f"terrain makes ground ({got['made']['faces']} faces, size {got['made']['size']})")
    check(got["made"]["size"][2] > 8, "and the canyon cuts into it")
    ground = got["object"]
    before = signature(made(ground))
    agent.nodes_set_inputs(ground, {"Seed": 7})
    check(signature(made(ground)) != before, "a different seed gives different land")
    agent.nodes_set_inputs(ground, {"Seed": 0})
    check(signature(made(ground)) == before, "and the same seed gives the same land back")

    # scatter on it
    carpet = bpy.data.objects.new("Carpet", bpy.data.meshes.new("Carpet"))
    bpy.context.scene.collection.objects.link(carpet)
    tmod = carpet.modifiers.new("Ground", 'NODES')
    tmod.node_group = bpy.data.node_groups["CN Terrain"]
    agent._set_modifier_inputs(carpet, tmod, {"Size": 60, "Resolution": 120, "Height": 3,
                                              "Canyon Depth": 8, "Canyon Width": 5})
    got = agent.nodes_use("scatter", "Carpet", {"Density": 0.1, "Spacing": 2.0})
    trees = got["made"]["instances"]
    check(got["ok"] and trees > 50, f"scatter places things ({trees} stand-in trees)")
    agent.nodes_set_inputs("Carpet", {"Max Slope": 1.5}, modifier=got["modifier"])
    steep = made("Carpet")["instances"]
    check(steep > trees, f"and keeps off steep ground until allowed ({trees} -> {steep})")
    agent.nodes_set_inputs("Carpet", {"Max Slope": 0.52, "Keep Surface": False},
                           modifier=got["modifier"])
    check(made("Carpet")["verts"] == 0 and made("Carpet")["instances"] == trees,
          "the surface can be left out")

    # a collection of real objects gets used instead of the stand-in
    rocks = bpy.data.collections.new("Rocks")
    for n in range(3):
        rock = bpy.data.objects.new(f"Rock{n}", bpy.data.meshes.new(f"Rock{n}"))
        rocks.objects.link(rock)
    agent.nodes_set_inputs("Carpet", {"Instances": "Rocks"}, modifier=got["modifier"])
    check(made("Carpet")["instances"] == trees, "a collection is picked from, spot for spot")
    bad = agent.nodes_set_inputs("Carpet", {"Instances": "Nope"}, modifier=got["modifier"])
    check(not bad["ok"] and "no" in bad["error"], "a missing collection is reported")

    # a wall along a closed curve, with doorways
    agent.curve("Courtyard", [[0, 0, 0], [12, 0, 0], [12, 8, 0], [0, 8, 0]], cyclic=True,
                smooth=False)
    got = agent.nodes_use("wall", "Courtyard", {"Doorways": 0, "Posts": False})
    plain = got["made"]
    # 3 m above the curve and a 0.5 m footing below it
    check(got["ok"] and abs(plain["size"][2] - 3.5) < 0.01 and abs(plain["centre"][2] - 1.25) < 0.01,
          f"a wall stands on the curve, 3 m high on a footing ({plain['size']}, "
          f"centre {plain['centre']})")
    check(abs(plain["size"][0] - 12.4) < 0.02 and abs(plain["size"][1] - 8.4) < 0.02,
          f"and keeps its full thickness round the corners ({plain['size'][0]} x {plain['size'][1]})")
    # Three doorways round a 40 m courtyard sit 1/6, 1/2 and 5/6 of the way along: the first
    # on the south wall at x = 6.67. A ray at waist height through it should cross the whole
    # courtyard and stop at the north wall; one through solid wall stops straight away.
    solid_at_door = ray("Courtyard", (6.67, -3, 1.0), (0, 1, 0))
    agent.nodes_set_inputs("Courtyard", {"Doorways": 3})
    through_door = ray("Courtyard", (6.67, -3, 1.0), (0, 1, 0))
    beside_door = ray("Courtyard", (3.0, -3, 1.0), (0, 1, 0))
    over_door = ray("Courtyard", (6.67, -3, 2.7), (0, 1, 0))
    check(solid_at_door == 2.8 and through_door == 10.8 and beside_door == 2.8,
          f"a doorway goes right through the wall (ray stops at {solid_at_door} m before, "
          f"{through_door} m after; {beside_door} m beside it)")
    check(over_door == 2.8, f"and the wall carries on above it ({over_door} m)")
    agent.nodes_set_inputs("Courtyard", {"Posts": True, "Post Spacing": 4.0})
    posted = made("Courtyard")
    check(posted["instances"] >= 10 and abs(posted["size"][2] - 3.8) < 0.01,
          f"posts stand along it, above the wall ({posted['instances']} posts)")

    # on uneven ground the wall follows it, and stays vertical rather than leaning
    hilly = agent.nodes_use("terrain", values={"Size": 60, "Resolution": 90, "Height": 5,
                                               "Seed": 3}, name="CN Terrain")["object"]
    agent.nodes_set_inputs("Courtyard", {"Ground": hilly, "Posts": False})
    on_hill = made("Courtyard")
    check(abs(on_hill["size"][0] - 12.4) < 0.02 and on_hill["size"][2] > 3.6,
          f"on a hillside it rises and falls with the ground but does not lean "
          f"({on_hill['size']})")
    agent.nodes_set_inputs("Courtyard", {"Ground": None, "Posts": True})
    bpy.data.objects.remove(bpy.data.objects[hilly], do_unlink=True)

    # a path over the canyon becomes a bridge
    agent.curve("Trail", [[2, -26, 3], [0, -10, 2], [1, 0, 2], [0, 10, 2], [-2, 26, 3]])
    got = agent.nodes_use("path_bridge", "Trail", {"Ground": ground})
    walk = got["made"]
    check(got["ok"] and walk["faces"] > 100 and walk["curves"] == 1,
          f"a path is laid along the curve, and its guide curve comes out too "
          f"({walk['faces']} faces, {walk['curves']} curve)")
    check(walk["size"][2] > 8,
          f"it reaches from the deck down to the canyon floor in pillars ({walk['size']})")
    trail_tree = bpy.data.node_groups["CN Path and Bridge"]
    problems = serialize.validate(trail_tree)
    check(not problems, f"its tree has no problems{': ' + problems[0] if problems else ''}")
    agent.nodes_set_inputs("Trail", {"Gap Depth": 500.0})
    no_bridge = made("Trail")
    check(no_bridge["faces"] < walk["faces"],
          f"the railings, posts and pillars appear only over the gap ({no_bridge['faces']} "
          f"faces with no gap, {walk['faces']} with one)")
    agent.nodes_set_inputs("Trail", {"Gap Depth": 1.5})

    # lamps along the same trail, following it from a second object
    lamps = bpy.data.objects.new("Lamps", bpy.data.meshes.new("Lamps"))
    bpy.context.scene.collection.objects.link(lamps)
    got = agent.nodes_use("along_curve", "Lamps", {"Follow Object": "Trail", "Spacing": 6.0,
                                                   "Sides": 2})
    both = got["made"]["instances"]
    check(got["ok"] and both >= 16, f"lamps line the trail on both sides ({both})")
    agent.nodes_set_inputs("Lamps", {"Sides": 3})
    alternating = made("Lamps")["instances"]
    agent.nodes_set_inputs("Lamps", {"Sides": 0})
    one_side = made("Lamps")["instances"]
    check(alternating == one_side == both // 2,
          f"alternating or one side gives half as many ({alternating}, {one_side})")

    # every capability's tree survives the round trip, geometry for geometry
    hosts = {"CN Terrain": ground, "CN Scatter": "Carpet", "CN Wall Along Curve": "Courtyard",
             "CN Path and Bridge": "Trail", "CN Props Along Curve": "Lamps"}
    for group, host in hosts.items():
        before = signature(made(host))
        tree = bpy.data.node_groups[group]
        serialize.write(serialize.read(tree))
        after = signature(made(host))
        check(before == after, f"{group} rebuilds from its own data to the same result"
                               + ("" if before == after else f" ({before} vs {after})"))

    # explaining and editing real trees
    words = agent.nodes_explain("CN Wall Along Curve")
    check(words["ok"] and "Doorway Width" in words["text"] and "Mesh Boolean" in words["text"]
          and not words["problems"],
          f"a capability explains itself ({len(words['text'].splitlines())} lines)")
    print("\n".join("      " + line[:150] for line in words["text"].splitlines()[:8]))

    # find the wall's material node and the join it feeds, as an assistant would: by reading
    wall_tree = bpy.data.node_groups["CN Wall Along Curve"]
    last_join = wall_tree.nodes["Group Output"].inputs[0].links[0].from_node
    wall_mat_node = next(l.from_node for l in wall_tree.links
                         if l.to_node == last_join and l.from_node.bl_idname == "GeometryNodeSetMaterial")
    before = made("Courtyard")
    wrong = agent.nodes_edit("CN Wall Along Curve", [
        {"op": "insert", "node": {"name": "Wobble", "type": "GeometryNodeSetPosition"},
         "between": {"from": ["Group Input", "Curve"], "to": [last_join.name, "Geometry"]}}])
    check(not wrong["ok"] and "fed by" in wrong["error"],
          f"inserting on a link that is not there says what is ({wrong['error'][-70:]})")
    edited = agent.nodes_edit("CN Wall Along Curve", [
        {"op": "input", "socket": "Lean", "type": "NodeSocketFloat", "default_value": 0.0},
        {"op": "insert", "node": {"name": "Wobble", "type": "GeometryNodeSetPosition"},
         "between": {"from": [wall_mat_node.name, "Geometry"], "to": [last_join.name, "Geometry"]}},
    ])
    check(edited["ok"] and not edited["warnings"],
          f"a node is inserted into a real tree and an input added ({edited.get('error') or edited['done']})")
    check(made("Courtyard")["faces"] == before["faces"],
          "and the wall is unchanged until the new node does something")
    lean = agent.nodes_edit("CN Wall Along Curve", [
        {"op": "set", "node": "Wobble", "values": {"Offset": [0, 0, 1.0]}}])
    lifted = made("Courtyard")
    check(lean["ok"] and abs(lifted["size"][2] - 4.5) < 0.01,
          f"setting its value lifts the wall above its posts (height {before['size'][2]} -> "
          f"{lifted['size'][2]})")
    broken = agent.nodes_edit("CN Wall Along Curve", [
        {"op": "set", "node": "Wobble", "values": {"Offset": [0, 0, 0]}},
        {"op": "link", "from": ["Nope", "Geometry"], "to": ["Wobble", "Geometry"]}])
    check(not broken["ok"] and "step 2" in broken["error"]
          and abs(made("Courtyard")["size"][2] - 4.5) < 0.01,
          f"a failing edit changes nothing at all, not even its first step ({broken['error'][:48]})")
    tuned = bpy.data.objects["Courtyard"].modifiers[0]
    height_id = next(i.identifier for i in tuned.node_group.interface.items_tree
                     if getattr(i, "name", "") == "Height")
    agent.nodes_set_inputs("Courtyard", {"Height": 5.0})
    agent.nodes_edit("CN Wall Along Curve", [{"op": "remove", "node": "Wobble", "bridge": True}])
    check(abs(tuned[height_id] - 5.0) < 1e-6 and abs(made("Courtyard")["size"][2] - 5.8) < 0.01,
          "editing keeps what was tuned on the modifier, and a removed node's flow is rejoined")

    # using a capability again keeps edits made to its group; a foreign group is left alone
    agent.nodes_edit("CN Wall Along Curve", [{"op": "add", "node": {
        "name": "My Note", "type": "NodeFrame", "label": "edited by hand"}}])
    agent.curve("Second", [[0, 30, 0], [10, 30, 0]], smooth=False)
    again = agent.nodes_use("wall", "Second")
    check(again["ok"] and "My Note" in bpy.data.node_groups["CN Wall Along Curve"].nodes,
          "using a capability again keeps the edits made to its group")
    refreshed = agent.nodes_use("wall", "Second", refresh=True)
    check(refreshed["ok"] and "My Note" not in bpy.data.node_groups["CN Wall Along Curve"].nodes,
          "and refresh=True rebuilds it from the library")
    serialize.write({"nodes": [{"name": "c", "type": "GeometryNodeMeshCube"}]}, name="My Scatter")
    clash = agent.nodes_use("scatter", name="My Scatter")
    check(not clash["ok"] and "already a node group" in clash["error"]
          and bpy.data.node_groups["My Scatter"].nodes[0].bl_idname == "GeometryNodeMeshCube",
          "a group of someone else's with the requested name is never overwritten")

    print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)


main()
