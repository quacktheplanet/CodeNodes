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
