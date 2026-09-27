"""The shipped Geometry Nodes library and example scenes work in plain Blender: no add-on,
nothing of CodeNodes imported.

    blender -b --factory-startup --python tests/test_geonodes_library.py [-- <folder>]

<folder> defaults to geonodes/ (build it first with geonodes/build_library.py).
"""
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
FOLDER = os.path.abspath(argv[0]) if argv else os.path.join(os.path.dirname(HERE), "geonodes")

_checks = 0
_failed = False


def check(cond, msg):
    global _checks, _failed
    _checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        _failed = True


def evaluated(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    geometry = obj.evaluated_get(dg).evaluated_geometry()
    me = geometry.mesh
    if me is None:
        return 0, None
    mw = obj.matrix_world
    tree = BVHTree.FromPolygons([mw @ v.co for v in me.vertices], [tuple(p.vertices) for p in me.polygons])
    return len(me.polygons), tree


def main():
    check("codenodes" not in sys.modules and not any(a.module == "codenodes" for a in bpy.context.preferences.addons),
          "CodeNodes is neither imported nor enabled")
    lib = os.path.join(FOLDER, "CodeNodes Library.blend")
    cats = open(os.path.join(FOLDER, "blender_assets.cats.txt"), encoding="utf-8").read()
    bpy.ops.wm.open_mainfile(filepath=lib)
    groups = [g for g in bpy.data.node_groups if g.asset_data is not None]
    check(len(groups) >= 27, f"{len(groups)} node groups are assets")
    check(all(str(g.asset_data.catalog_id) in cats for g in groups), "every asset sits in a catalog the "
                                                                     "catalog file defines")
    names = {g.name for g in groups}
    check({"CN Rack", "CN Robot Arm", "CN Rooms", "CN Wall Network", "CN Terrain"} <= names,
          "capabilities and generators are both in it")
    # use one in a fresh file, the way a user drags it in
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(lib, link=False) as (src, dst):
        dst.node_groups = ["CN Rack", "CN Robot Arm"]
    rack = bpy.data.node_groups["CN Rack"]
    obj = bpy.data.objects.new("Rack", bpy.data.meshes.new("Rack"))
    bpy.context.scene.collection.objects.link(obj)
    mod = obj.modifiers.new("Rack", 'NODES')
    mod.node_group = rack
    faces, _ = evaluated(obj)
    check(faces > 100, f"an appended rack makes geometry in a plain file ({faces} faces)")
    bays = next(i.identifier for i in rack.interface.items_tree if i.item_type == 'SOCKET' and i.name == "Bays")
    before = faces
    mod[bays] = 6.0
    obj.update_tag()
    faces, _ = evaluated(obj)
    check(faces > before, "and its Bays slider adds sections")
    mat_inputs = [i for i in rack.interface.items_tree if i.item_type == 'SOCKET'
                  and i.socket_type == "NodeSocketMaterial"]
    check(mat_inputs and all(getattr(i, "default_value", None) is not None for i in mat_inputs),
          "its colours come along as the material inputs' defaults")
    for name in ("factory", "warehouse", "house"):
        path = os.path.join(FOLDER, "examples", f"{name}.blend")
        if not os.path.exists(path):
            check(False, f"example {name}.blend exists")
            continue
        bpy.ops.wm.open_mainfile(filepath=path)
        shell = next((o for o in bpy.data.objects if o.name.endswith(" Shell")), None)
        faces, tree = evaluated(shell)
        check(faces > 20, f"{name}: the shell's walls evaluate without the add-on ({faces} faces)")
        docks = [o for o in bpy.data.objects if o.get("cn_kind") in ("dock_door", "door")
                 and "Shell" not in o.name and abs(o.location.z) < 0.01]
        clear = 0
        for d in docks:
            p = d.location
            for axis in (Vector((0, 1, 0)), Vector((1, 0, 0))):
                if tree.ray_cast(p + Vector((0, 0, 1.0)) - axis * 0.6, axis, 1.2)[0] is None:
                    clear += 1
                    break
        check(docks and clear == len(docks), f"{name}: every door and dock is open through its wall "
                                             f"({clear}/{len(docks)})")
        equip = [o for o in bpy.data.objects if o.get("cn_part") == "equipment"]
        if name != "house":
            check(len(equip) > 30, f"{name}: {len(equip)} pieces of equipment")
    print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)


main()
