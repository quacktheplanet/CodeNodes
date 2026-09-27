"""Reading and writing Geometry Nodes trees as data.

    blender -b --factory-startup --python tests/test_gn.py     (no GPU needed)

The test that matters is the round trip: read a tree, build a new one from the data,
and check the two produce the same geometry. If that holds, an assistant can edit a
setup without quietly damaging it.
"""
import json
import os
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from codenodes.gn import catalog, serialize  # noqa: E402

_checks = 0
_failed = False


def check(cond, msg):
    global _checks, _failed
    _checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        _failed = True


def evaluate(tree, base=None):
    """The geometry a tree produces, as (vertex count, face count, rounded bounds)."""
    me = base or bpy.data.meshes.new("probe")
    obj = bpy.data.objects.new("probe", me)
    bpy.context.scene.collection.objects.link(obj)
    mod = obj.modifiers.new("GN", 'NODES')
    mod.node_group = tree
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    out = ev.to_mesh()
    n, f = len(out.vertices), len(out.polygons)
    bounds = [0.0, 0.0, 0.0]
    if n:
        co = np.empty(n * 3, np.float32)
        out.vertices.foreach_get("co", co)
        bounds = [round(float(v), 3) for v in np.ptp(co.reshape(-1, 3), axis=0)]
    ev.to_mesh_clear()
    bpy.data.objects.remove(obj, do_unlink=True)
    return n, f, bounds


def build_reference():
    """A tree using the awkward parts: settings, a field, a multi-input, a frame,
    a simulation zone, a panel, and typed group inputs."""
    tree = bpy.data.node_groups.new("Reference", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    panel = tree.interface.new_panel("Scatter", description="how thickly")
    density = tree.interface.new_socket("Density", in_out='INPUT', socket_type='NodeSocketFloat',
                                        parent=panel)
    density.default_value, density.min_value, density.max_value = 12.0, 0.0, 500.0
    density.description = "points per square metre"
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes, links = tree.nodes, tree.links
    gin = nodes.new("NodeGroupInput")
    gout = nodes.new("NodeGroupOutput")
    grid = nodes.new("GeometryNodeMeshGrid")
    grid.inputs["Size X"].default_value = 4.0
    grid.inputs["Size Y"].default_value = 4.0
    grid.inputs["Vertices X"].default_value = 12
    grid.inputs["Vertices Y"].default_value = 12
    dist = nodes.new("GeometryNodeDistributePointsOnFaces")
    dist.distribute_method = 'POISSON'                 # a setting that is not a socket
    noise = nodes.new("ShaderNodeTexNoise")            # a field feeding a field socket
    noise.noise_dimensions = '3D'
    cube = nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (0.08, 0.08, 0.3)
    inst = nodes.new("GeometryNodeInstanceOnPoints")
    real = nodes.new("GeometryNodeRealizeInstances")   # or to_mesh() sees nothing
    join = nodes.new("GeometryNodeJoinGeometry")       # multi-input
    frame = nodes.new("NodeFrame")
    frame.label = "scattering"
    dist.parent = frame
    noise.parent = frame

    links.new(gin.outputs[0], join.inputs["Geometry"])
    links.new(grid.outputs["Mesh"], dist.inputs["Mesh"])
    # in Poisson mode the node has "Density Max", not "Density" — exactly the sort of
    # thing the catalog is there to tell you
    links.new(gin.outputs[1], dist.inputs["Density Max"])
    links.new(noise.outputs["Fac"], dist.inputs["Density Factor"])
    dist.inputs["Distance Min"].default_value = 0.15
    links.new(dist.outputs["Points"], inst.inputs["Points"])
    links.new(cube.outputs["Mesh"], inst.inputs["Instance"])
    links.new(inst.outputs["Instances"], real.inputs["Geometry"])
    links.new(real.outputs["Geometry"], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], gout.inputs[0])
    return tree


def build_dynamic():
    """A tree made of nodes whose sockets are added by hand: Capture Attribute (with an
    item removed, so its identifiers no longer start at zero), a Repeat zone with an extra
    item, a Menu Switch with a third entry, and an Index Switch."""
    tree = bpy.data.node_groups.new("Dynamic", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, links = tree.nodes, tree.links
    grid = n.new("GeometryNodeMeshGrid")
    grid.inputs["Vertices X"].default_value = 20
    grid.inputs["Vertices Y"].default_value = 20
    noise = n.new("ShaderNodeTexNoise")
    cap = n.new("GeometryNodeCaptureAttribute")
    cap.capture_items.new('FLOAT', "Scrap")
    cap.capture_items.new('FLOAT', "Height")
    cap.capture_items.new('VECTOR', "Where")
    cap.capture_items.remove(cap.capture_items[0])        # identifiers now start at Value_1
    links.new(grid.outputs["Mesh"], cap.inputs["Geometry"])
    links.new(noise.outputs["Fac"], cap.inputs[1])
    links.new(n.new("GeometryNodeInputPosition").outputs[0], cap.inputs[2])

    rin, rout = n.new("GeometryNodeRepeatInput"), n.new("GeometryNodeRepeatOutput")
    rin.pair_with_output(rout)
    rout.repeat_items.new('FLOAT', "Lift")
    rin.inputs["Iterations"].default_value = 3
    rin.inputs[2].default_value = 0.2                        # Lift starts at 0.2
    links.new(cap.outputs["Geometry"], rin.inputs[1])
    setpos = n.new("GeometryNodeSetPosition")
    mul = n.new("ShaderNodeMath")
    mul.operation = 'MULTIPLY'
    xyz = n.new("ShaderNodeCombineXYZ")
    links.new(rin.outputs[1], setpos.inputs["Geometry"])
    links.new(rin.outputs[2], mul.inputs[0])
    links.new(cap.outputs[1], mul.inputs[1])
    links.new(mul.outputs[0], xyz.inputs["Z"])
    links.new(xyz.outputs[0], setpos.inputs["Offset"])
    add = n.new("ShaderNodeMath")
    add.operation = 'ADD'
    add.inputs[1].default_value = 0.1
    links.new(rin.outputs[2], add.inputs[0])
    links.new(setpos.outputs[0], rout.inputs[0])
    links.new(add.outputs[0], rout.inputs[1])

    cube = n.new("GeometryNodeMeshCube")
    menu = n.new("GeometryNodeMenuSwitch")
    menu.enum_items.new("Third")
    links.new(cube.outputs[0], menu.inputs[1])
    links.new(rout.outputs[0], menu.inputs[3])
    menu.inputs[0].default_value = "Third"
    index = n.new("GeometryNodeIndexSwitch")
    index.data_type = 'GEOMETRY'                             # it starts out as Float
    index.index_switch_items.new()
    index.inputs["Index"].default_value = 2
    links.new(cube.outputs[0], index.inputs[1])
    links.new(menu.outputs[0], index.inputs[3])
    out = n.new("NodeGroupOutput")
    links.new(index.outputs[0], out.inputs[0])
    return tree


def dynamic_tests():
    original = build_dynamic()
    before = evaluate(original)
    data = serialize.read(original)
    cap = next(e for e in data["nodes"] if e["type"] == "GeometryNodeCaptureAttribute")
    check([i["name"] for i in cap["items"]["capture_items"]] == ["Height", "Where"]
          and "Value_1" in cap["sockets"]["in"],
          f"items are recorded, with the identifiers they had ({cap['sockets']['in']})")
    rebuilt = serialize.write(data, name="DynamicCopy")
    after = evaluate(rebuilt)
    check(before[0] == 400 and before[2][2] > 0 and before == after,
          f"a tree of hand-added sockets rebuilds to identical geometry ({before} vs {after})")
    check(len(rebuilt.links) == len(original.links),
          f"with every link reconnected ({len(rebuilt.links)} of {len(original.links)})")

    # written by hand: plain socket names, no positions
    warnings = []
    tree = serialize.write({
        "interface": [{"socket": "Size", "type": "NodeSocketFloat", "default_value": 2.0},
                      {"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "nodes": [{"name": "in", "type": "NodeGroupInput"},
                  {"name": "cube", "type": "GeometryNodeMeshCube"},
                  {"name": "grow", "type": "ShaderNodeMath", "settings": {"operation": "MULTIPLY"},
                   "values": {"Value_001": 1.5}},
                  {"name": "out", "type": "NodeGroupOutput"}],
        "links": [{"from": ["in", "Size"], "to": ["grow", "Value"]},
                  {"from": ["grow", "Value"], "to": ["cube", "Size"]},
                  {"from": ["cube", "Mesh"], "to": ["out", "Geometry"]}]},
        name="ByHand", warnings=warnings)
    got = evaluate(tree)
    check(got[2] == [3.0, 3.0, 3.0], f"sockets can be named plainly ({got[2]})")
    xs = sorted(n.location.x for n in tree.nodes)
    check(len(set(xs)) == 4 and not warnings, f"and a tree with no positions is laid out ({xs})")

    # a field into a single-value socket is built, but reported
    warnings = []
    serialize.write({"nodes": [
        {"name": "rand", "type": "FunctionNodeRandomValue", "settings": {"data_type": "INT"}},
        {"name": "grid", "type": "GeometryNodeMeshGrid"}],
        "links": [{"from": ["rand", "Value"], "to": ["grid", "Vertices X"]}]},
        name="FieldMistake", warnings=warnings)
    check(any("field" in w and "single value" in w for w in warnings),
          f"a field going into a single-value socket is reported ({warnings[:1]})")

    for broken, wanted in [
        ({"nodes": [{"name": "c", "type": "GeometryNodeMeshCube", "values": {"Sise": 2}}]},
         "has no input 'Sise'. It has: Size"),
        ({"nodes": [{"name": "m", "type": "ShaderNodeMath", "settings": {"operation": "TIMES"}}]},
         "It accepts: ADD"),
        ({"nodes": [{"type": "GeometryNodeMeshCube"}]}, "has no name"),
        ({"nodes": [{"name": "a", "type": "GeometryNodeMeshCube"},
                    {"name": "a", "type": "GeometryNodeMeshCube"}]}, "both called 'a'"),
    ]:
        try:
            serialize.write(broken, name="Broken")
            check(False, f"{wanted!r} should have been reported")
        except serialize.BuildError as exc:
            check(wanted in str(exc), f"and so is: {str(exc)[:80]}")

    # a group inside a group travels with it
    inner = serialize.write({
        "interface": [{"socket": "Geometry", "type": "NodeSocketGeometry"},
                      {"socket": "Lift", "type": "NodeSocketFloat", "default_value": 1.0},
                      {"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "nodes": [{"name": "in", "type": "NodeGroupInput"},
                  {"name": "xyz", "type": "ShaderNodeCombineXYZ"},
                  {"name": "move", "type": "GeometryNodeTransform"},
                  {"name": "out", "type": "NodeGroupOutput"}],
        "links": [{"from": ["in", "Geometry"], "to": ["move", "Geometry"]},
                  {"from": ["in", "Lift"], "to": ["xyz", "Z"]},
                  {"from": ["xyz", "Vector"], "to": ["move", "Translation"]},
                  {"from": ["move", "Geometry"], "to": ["out", "Geometry"]}]}, name="Lifter")
    outer = serialize.write({
        "interface": [{"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "nodes": [{"name": "cube", "type": "GeometryNodeMeshCube"},
                  {"name": "lift", "type": "GeometryNodeGroup", "group": "Lifter",
                   "values": {"Lift": 5.0}},
                  {"name": "out", "type": "NodeGroupOutput"}],
        "links": [{"from": ["cube", "Mesh"], "to": ["lift", "Geometry"]},
                  {"from": ["lift", "Geometry"], "to": ["out", "Geometry"]}]}, name="Outer")
    outer_before = evaluate(outer)
    whole = serialize.read(outer)
    check([g["name"] for g in whole.get("groups", [])] == ["Lifter"],
          "a tree that uses a group brings that group along")
    bpy.data.node_groups.remove(outer)
    bpy.data.node_groups.remove(inner)
    rebuilt_outer = serialize.write(whole)
    check(evaluate(rebuilt_outer) == outer_before and "Lifter" in bpy.data.node_groups,
          "so it can be rebuilt in a file that has neither")

    # an edit to the inner group that adds an input leaves the outer one wired
    inner_data = serialize.read(bpy.data.node_groups["Lifter"])
    inner_data["interface"].insert(2, {"socket": "Spare", "type": "NodeSocketInt"})
    serialize.write(inner_data)
    lift_node = rebuilt_outer.nodes["lift"]
    check(all(s.is_linked for s in lift_node.inputs if s.name == "Geometry")
          and lift_node.outputs["Geometry"].is_linked
          and abs(lift_node.inputs["Lift"].default_value - 5.0) < 1e-6,
          "changing a group's inputs keeps the groups that use it wired, and their values")

    # what someone tuned on the modifier survives a rebuild of the group
    obj = bpy.data.objects.new("Tuned", bpy.data.meshes.new("Tuned"))
    bpy.context.scene.collection.objects.link(obj)
    mod = obj.modifiers.new("GN", 'NODES')
    mod.node_group = bpy.data.node_groups["Reference"]
    key = next(i.identifier for i in mod.node_group.interface.items_tree
               if getattr(i, "name", "") == "Density")
    mod[key] = 40.0
    serialize.write(serialize.read(mod.node_group))
    new_key = next(i.identifier for i in mod.node_group.interface.items_tree
                   if getattr(i, "name", "") == "Density")
    check(abs(mod[new_key] - 40.0) < 1e-6,
          f"a value tuned on the modifier survives rebuilding its group ({key} -> {new_key})")
    bpy.data.objects.remove(obj, do_unlink=True)

    check(serialize.unused(bpy.data.node_groups["FieldMistake"]) == ["rand", "grid"],
          "nodes that do not reach the output are found")


def review_tests():
    """Regressions for what a code review found, each as the scenario it described."""
    from codenodes import agent
    from codenodes.gn import edit

    # a failed rewrite leaves the existing group exactly as it was
    tree = bpy.data.node_groups["Reference"]
    obj = bpy.data.objects.new("Keeper", bpy.data.meshes.new("Keeper"))
    bpy.context.scene.collection.objects.link(obj)
    mod = obj.modifiers.new("GN", 'NODES')
    mod.node_group = tree
    density = next(i.identifier for i in tree.interface.items_tree
                   if i.item_type == 'SOCKET' and i.name == "Density")
    mod[density] = 33.0
    before_nodes, before = len(tree.nodes), evaluate(tree)
    broken = serialize.read(tree)
    broken["links"].append({"from": ["Grid", "Nope"], "to": ["Group Output", "Geometry"]})
    try:
        serialize.write(broken)
        check(False, "a broken rewrite should be refused")
    except serialize.BuildError:
        check(len(tree.nodes) == before_nodes and evaluate(tree) == before
              and abs(mod[density] - 33.0) < 1e-6,
              "a rewrite with a mistake in it leaves the group, and the values tuned on it, alone")
    bpy.data.objects.remove(obj, do_unlink=True)

    # a dependency that shares a name with a different group does not overwrite it
    mine = serialize.write({"nodes": [{"name": "c", "type": "GeometryNodeMeshCube"}]},
                           name="Shared Name")
    theirs = {"name": "Shared Name", "interface": [
        {"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "nodes": [{"name": "s", "type": "GeometryNodeMeshUVSphere"},
                  {"name": "out", "type": "NodeGroupOutput"}],
        "links": [{"from": ["s", "Mesh"], "to": ["out", "Geometry"]}]}
    user = serialize.write({"groups": [theirs], "nodes": [
        {"name": "g", "type": "GeometryNodeGroup", "group": "Shared Name"}]}, name="Uses Shared")
    check(mine.nodes[0].bl_idname == "GeometryNodeMeshCube"
          and user.nodes["g"].node_tree.name != "Shared Name"
          and user.nodes["g"].node_tree.nodes.get("s") is not None,
          f"a group brought along under a taken name is added beside it "
          f"({user.nodes['g'].node_tree.name}), not written over it")

    # changing a node's items keeps its existing wiring
    dyn = bpy.data.node_groups["DynamicCopy"]
    cap = next(n for n in dyn.nodes if n.bl_idname == "GeometryNodeCaptureAttribute")
    wired = sum(1 for l in dyn.links if l.to_node == cap or l.from_node == cap)
    edit.apply(dyn, [{"op": "set", "node": cap.name, "items": {"capture_items": [
        {"name": "Height", "data_type": "FLOAT"}, {"name": "Where", "data_type": "FLOAT_VECTOR"},
        {"name": "Extra", "data_type": "FLOAT"}]}}])
    check(sum(1 for l in dyn.links if l.to_node == cap or l.from_node == cap) == wired,
          f"adding a capture item keeps the existing links ({wired})")

    # removing a node joins up sockets of the same type, not whichever was linked first
    t = serialize.write({"interface": [
        {"socket": "Geometry", "type": "NodeSocketGeometry"},
        {"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "nodes": [{"name": "in", "type": "NodeGroupInput"},
                  {"name": "xyz", "type": "ShaderNodeCombineXYZ"},
                  {"name": "move", "type": "GeometryNodeSetPosition"},
                  {"name": "out", "type": "NodeGroupOutput"}],
        "links": [{"from": ["xyz", "Vector"], "to": ["move", "Offset"]},
                  {"from": ["in", "Geometry"], "to": ["move", "Geometry"]},
                  {"from": ["move", "Geometry"], "to": ["out", "Geometry"]}]}, name="Bridging")
    edit.apply(t, [{"op": "remove", "node": "move", "bridge": True}])
    fed = [l.from_node.name for l in t.links if l.to_node.name == "out"]
    check(fed == ["in"], f"removing a node bridges geometry to geometry ({fed})")

    # two inputs with the same name keep their own tuned values through a rewrite
    twin = serialize.write({"interface": [
        {"panel": "Wall"}, {"socket": "Size", "type": "NodeSocketFloat", "in_panel": "Wall"},
        {"panel": "Posts"}, {"socket": "Size", "type": "NodeSocketFloat", "in_panel": "Posts"},
        {"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "nodes": [{"name": "out", "type": "NodeGroupOutput"}]}, name="Twins")
    obj = bpy.data.objects.new("TwinUser", bpy.data.meshes.new("TwinUser"))
    bpy.context.scene.collection.objects.link(obj)
    mod = obj.modifiers.new("GN", 'NODES')
    mod.node_group = twin
    ids = [i.identifier for i in twin.interface.items_tree if i.item_type == 'SOCKET'
           and i.name == "Size"]
    mod[ids[0]], mod[ids[1]] = 1.5, 7.0
    serialize.write(serialize.read(twin))
    ids = [i.identifier for i in twin.interface.items_tree if i.item_type == 'SOCKET'
           and i.name == "Size"]
    check([round(mod[i], 3) for i in ids] == [1.5, 7.0],
          "two inputs with the same name keep their own values through a rewrite")

    # checking a group on an object that does not have it measures it with the group on
    cube_tree = serialize.write({"interface": [
        {"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "nodes": [{"name": "c", "type": "GeometryNodeMeshCube"},
                  {"name": "out", "type": "NodeGroupOutput"}],
        "links": [{"from": ["c", "Mesh"], "to": ["out", "Geometry"]}]}, name="CheckMe")
    got = agent.nodes_check("CheckMe", on="TwinUser")
    check(got["verts"] == 8 and len(obj.modifiers) == 1,
          "nodes_check on an object tries the group on it, and takes it off again")
    bpy.data.objects.remove(obj, do_unlink=True)

    # a shader node group reads without falling over
    shader = bpy.data.node_groups.new("Shady", "ShaderNodeTree")
    shader.interface.new_socket("Shader", in_out='INPUT', socket_type='NodeSocketShader')
    got = agent.nodes_read("Shady")
    check(got["ok"] and got["interface"][0]["type"] == "NodeSocketShader",
          "a shader group with a shader input can be read")


def main():
    # --- the catalog -------------------------------------------------------------------
    s = catalog.summary()
    check(s["total"] > 250, f"the catalog finds the node types ({s['total']}: "
                            f"{s['geometry']} geometry, {s['function']} function, {s['shader']} shader)")
    check(len(s["zones"]) >= 6, f"and the zones ({len(s['zones'])})")

    found = catalog.search("distribute points")
    check(any(n["name"] == "GeometryNodeDistributePointsOnFaces" for n in found["nodes"]),
          f"search finds a node by plain words ({found['found']} hits)")
    check(catalog.search("this is not a node")["found"] == 0, "and finds nothing when there is nothing")

    d = catalog.describe("GeometryNodeDistributePointsOnFaces")
    by_name = {i["name"]: i for i in d["inputs"]}
    check(by_name["Density"]["field"] and not by_name["Mesh"]["field"],
          "a described node says which inputs take a field and which do not")
    check(d["settings"]["distribute_method"]["options"] == ['RANDOM', 'POISSON'],
          f"and what its dropdowns accept ({d['settings']['distribute_method']['options']})")
    ray = {i["name"]: i for i in catalog.describe("GeometryNodeRaycast")["inputs"]}
    grid_in = {i["name"]: i for i in catalog.describe("GeometryNodeMeshGrid")["inputs"]}
    check(ray["Source Position"]["field"] and not grid_in["Vertices X"]["field"],
          "a circle socket counts as taking a field; a line socket does not")
    resample = {i["name"]: i for i in catalog.describe("GeometryNodeResampleCurve")["inputs"]}
    check(resample["Mode"].get("options") == ["Evaluated", "Count", "Length"],
          f"and a menu socket lists its choices ({resample['Mode'].get('options')})")
    check(catalog.describe("GeometryNodeNope").get("error"), "an unknown node type is reported")

    # --- reading -------------------------------------------------------------------------
    original = build_reference()
    before = evaluate(original)
    data = serialize.read(original)
    check(data["nodes"] and data["links"], f"a tree reads as data ({len(data['nodes'])} nodes, "
                                           f"{len(data['links'])} links)")
    text = json.dumps(data)
    check(json.loads(text) == data, f"and it is plain JSON ({len(text):,} characters)")

    dist = next(n for n in data["nodes"] if n["type"] == "GeometryNodeDistributePointsOnFaces")
    check(dist["settings"].get("distribute_method") == 'POISSON', "settings are recorded")
    check(dist.get("inside") == "Frame", f"so is the frame a node sits in ({dist.get('inside')})")
    grid = next(n for n in data["nodes"] if n["type"] == "GeometryNodeMeshGrid")
    check(any(abs(float(v) - 4.0) < 1e-6 for v in grid["values"].values()),
          "unconnected input values are recorded")
    iface = {i.get("socket"): i for i in data["interface"] if "socket" in i}
    check(iface["Density"]["max_value"] == 500.0 and iface["Density"]["in_panel"] == "Scatter"
          and iface["Density"]["description"] == "points per square metre",
          "the group's own inputs keep their range, panel and description")

    # --- writing it back somewhere new ------------------------------------------------------
    rebuilt = serialize.write(data, name="Rebuilt")
    after = evaluate(rebuilt)
    check(before[0] > 100 and before == after,
          f"a rebuilt tree makes exactly the same geometry ({before} vs {after})")

    # Rebuilding an interface hands out new socket identifiers, so a second reading uses
    # those rather than the originals. What must hold is that it is now stable: read,
    # write, read again gives exactly the same description.
    second = serialize.read(rebuilt)
    third = serialize.read(serialize.write(second, name="Rebuilt3"))
    second["name"] = third["name"] = "x"
    check(second == third, "read → write → read is stable: the description stops changing")
    check(len(second["nodes"]) == len(data["nodes"]) and len(second["links"]) == len(data["links"]),
          "with nothing lost on the way")

    # --- editing it ---------------------------------------------------------------------------
    edited = json.loads(json.dumps(data))
    for entry in edited["nodes"]:
        if entry["type"] == "GeometryNodeMeshGrid":
            for key in list(entry["values"]):
                if entry["values"][key] == 12:
                    entry["values"][key] = 30
    serialize.write(edited, tree=rebuilt)
    denser = evaluate(rebuilt)
    check(denser[0] != after[0], f"changing one value changes the result ({after[0]} -> {denser[0]} verts)")

    # --- mistakes are explained ------------------------------------------------------------------
    for broken, wanted in [
        ({"nodes": [{"name": "a", "type": "GeometryNodeNope"}]}, "not a node type"),
        ({"nodes": [{"name": "a"}]}, "has no type"),
        ({"nodes": [{"name": "a", "type": "GeometryNodeMeshCube"}],
          "links": [{"from": ["a", "Mesh"], "to": ["b", "X"]}]}, "does not exist"),
        ({"nodes": [{"name": "a", "type": "GeometryNodeMeshCube"},
                    {"name": "b", "type": "GeometryNodeJoinGeometry"}],
          "links": [{"from": ["a", "Nope"], "to": ["b", "Geometry"]}]}, "has no output"),
        ({"nodes": [{"name": "a", "type": "GeometryNodeMeshCube",
                     "settings": {"nonsense": 1}}]}, "cannot set"),
        ({"interface": []}, "needs at least a 'nodes' list"),
    ]:
        try:
            serialize.write(broken, name="Broken")
            check(False, f"{wanted!r} should have been reported")
        except serialize.BuildError as exc:
            check(wanted in str(exc), f"a bad description says why: {str(exc)[:72]}")

    # --- a simulation zone survives the trip ---------------------------------------------------
    sim = bpy.data.node_groups.new("Sim", "GeometryNodeTree")
    sim.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    sim.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    sin = sim.nodes.new("GeometryNodeSimulationInput")
    sout = sim.nodes.new("GeometryNodeSimulationOutput")
    sin.pair_with_output(sout)
    gin, gout = sim.nodes.new("NodeGroupInput"), sim.nodes.new("NodeGroupOutput")
    sim.links.new(gin.outputs[0], sin.inputs[-1] if len(sin.inputs) > 1 else sin.inputs[0])
    sim.links.new(sin.outputs[-1], sout.inputs[-1] if len(sout.inputs) > 1 else sout.inputs[0])
    sim.links.new(sout.outputs[0], gout.inputs[0])
    sim_data = serialize.read(sim)
    paired = [n for n in sim_data["nodes"] if n.get("pairs_with")]
    check(len(paired) == 1, f"a simulation zone records its pairing ({paired})")
    sim_copy = serialize.write(sim_data, name="SimCopy")
    copies = [n for n in sim_copy.nodes if n.bl_idname == "GeometryNodeSimulationInput"]
    check(copies and copies[0].paired_output is not None, "and the rebuilt zone is paired again")

    dynamic_tests()
    review_tests()

    # --- through the tools an assistant actually calls ----------------------------------------
    import codenodes
    from codenodes import agent
    codenodes.register()
    check(agent.nodes_help()["total"] > 250, "nodes_help reports the surface")
    hits = agent.nodes_find("instance on points")
    check(any(n["name"] == "GeometryNodeInstanceOnPoints" for n in hits["nodes"]),
          "nodes_find looks up a node by plain words")
    described = agent.nodes_describe("GeometryNodeInstanceOnPoints")
    check(described["ok"] and any(i["name"] == "Instance" for i in described["inputs"]),
          "nodes_describe returns its sockets")
    listed = agent.nodes_list()
    check(any(g["name"] == "Reference" for g in listed["groups"]), "nodes_list finds the groups")
    got = agent.nodes_read("Reference")
    check(got["ok"] and got["nodes"], "nodes_read returns the tree")
    check(not agent.nodes_read("nope")["ok"], "and explains a missing group")

    made = agent.nodes_write({"nodes": [
        {"name": "out", "type": "NodeGroupOutput", "at": [200, 0]},
        {"name": "cube", "type": "GeometryNodeMeshCube", "at": [0, 0],
         "values": {"Size": [2.0, 2.0, 2.0]}}],
        "interface": [{"socket": "Geometry", "in_out": "OUTPUT", "type": "NodeSocketGeometry"}],
        "links": [{"from": ["cube", "Mesh"], "to": ["out", "Socket_0"]}]}, name="ToolBuilt")
    check(made["ok"], f"nodes_write builds a tree from scratch ({made.get('error')})")
    checked = agent.nodes_check("ToolBuilt")
    check(checked["ok"] and checked["verts"] == 8 and checked["size"] == [2.0, 2.0, 2.0],
          f"nodes_check reports what came out ({checked})")
    empty = agent.nodes_write({"nodes": [{"name": "c", "type": "GeometryNodeMeshCube"}]},
                              name="Empty")
    check(agent.nodes_check("Empty")["note"], "and says so when a setup produces nothing")
    bad = agent.nodes_write({"nodes": [{"name": "c", "type": "GeometryNodeWrong"}]}, name="Bad")
    check(not bad["ok"] and "not a node type" in bad["error"], "a bad description is explained")

    obj = bpy.data.objects.new("Target", bpy.data.meshes.new("Target"))
    bpy.context.scene.collection.objects.link(obj)
    applied = agent.nodes_apply("Target", "ToolBuilt")
    check(applied["ok"] and len(obj.modifiers) == 1, "nodes_apply puts it on an object")

    print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)


main()
