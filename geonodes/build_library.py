"""Write the CodeNodes Geometry Nodes library: every capability and every generator as a
node-group asset, sorted into catalogs, in one .blend that works in plain Blender with no
add-on — plus example scenes built by the factory planner.

    blender -b --factory-startup --python geonodes/build_library.py [-- --out DIR] [--no-examples]

Writes, in geonodes/ (or DIR):
    CodeNodes Library.blend        node groups marked as assets, with catalogs and previews off
    blender_assets.cats.txt        the catalog tree Blender reads next to the .blend
    examples/*.blend               a factory, a warehouse and a house, built and verified

Point Blender at the folder (Preferences > File Paths > Asset Libraries) and the groups show
in the Asset Browser and the Add > Node Group menus, ready to drag onto objects.
"""

import os
import sys
import uuid

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402,F401
from codenodes.factory import build, equipment as eq, kit, tools  # noqa: E402
from codenodes.gn import library, serialize  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(argv[argv.index("--out") + 1]) if "--out" in argv else HERE
EXAMPLES = "--no-examples" not in argv

# stable ids so re-building keeps assets in their catalogs
NS = uuid.UUID("6f0c1c3e-9a53-4c55-9b2e-4e2a6c1b7d11")
CATALOGS = {
    "Terrain": ["terrain", "water", "scatter"],
    "Architecture/Walls": ["wall", "wall_network", "rooms"],
    "Architecture/Roofs and Stairs": ["roof", "stairs"],
    "Architecture/Paths": ["path_bridge", "along_curve"],
}


def catalog_id(path):
    return str(uuid.uuid5(NS, path))


def generator_catalog(g):
    return {"Factory/Handling": "Factory/Handling", "Factory/Storage": "Factory/Storage",
            "Factory/Vehicles": "Factory/Vehicles", "Factory/Robots": "Factory/Robots",
            "Factory/Machines": "Factory/Machines", "Factory/Workstations": "Factory/Workstations",
            "Factory/Process": "Factory/Process", "Factory/Electrical": "Factory/Electrical",
            "Factory/Safety": "Factory/Safety", "Office": "Furniture/Office"}.get(g.category, g.category)


def mark(tree, catalog, about, tags):
    tree.asset_mark()
    ad = tree.asset_data
    ad.catalog_id = catalog_id(catalog)
    ad.description = about
    for t in tags:
        ad.tags.new(t, skip_if_exists=True)
    tree.use_fake_user = True
    try:
        tree.is_modifier = True
    except AttributeError:
        pass


def write_catalogs(paths):
    lines = ["# This is an Asset Catalog Definition file for Blender.", "#",
             "# Written by geonodes/build_library.py; edits here are overwritten.", "", "VERSION 1", ""]
    done = set()
    for path in sorted(paths):
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in done:
                done.add(sub)
                lines.append(f"{catalog_id(sub)}:{sub}:{sub.replace('/', '-')}")
    with open(os.path.join(OUT, "blender_assets.cats.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def library_file():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    used = set()
    count = 0
    for cat, keys in CATALOGS.items():
        for key in keys:
            tree = library.build(key)
            mark(tree, cat, tree.description if hasattr(tree, "description") else key, ["CodeNodes", key])
            used.add(cat)
            count += 1
    for key in sorted(library.CAPABILITIES):
        if not any(key in keys for keys in CATALOGS.values()):
            tree = library.build(key)
            mark(tree, "Other", key, ["CodeNodes"])
            used.add("Other")
            count += 1
    for kind, g in sorted(eq.GENERATORS.items()):
        graph = kit.to_graph(f"CN {kind.replace('_', ' ').title()}", g.about, g.params, g.part())
        tree = serialize.write(graph.data(), name=graph.name)
        # the generator's colours as the material inputs' defaults, so a dropped-in asset
        # already looks right
        for item in tree.interface.items_tree:
            if item.item_type == 'SOCKET' and item.socket_type == "NodeSocketMaterial":
                key = item.name.lower().replace(" ", "_")
                if key in eq.MATERIALS:
                    try:
                        item.default_value = build.palette(key)
                    except (AttributeError, TypeError):
                        pass
        cat = generator_catalog(g)
        mark(tree, cat, g.about, ["CodeNodes", "generator", kind])
        used.add(cat)
        count += 1
    for mat in bpy.data.materials:
        mat.use_fake_user = True
    write_catalogs(used)
    path = os.path.join(OUT, "CodeNodes Library.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print(f"LIBRARY {count} node groups -> {path} ({os.path.getsize(path) / 1e6:.2f} MB)")
    return path


def example(name, spec_name):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    r = tools.plan_site(example=spec_name, name=name)
    assert r["ok"], r
    assert tools.build_plan(name)["ok"]
    eqp = tools.place_equipment(name)
    res = tools.verify(name, renders=False)
    folder = os.path.join(OUT, "examples")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{spec_name}.blend")
    cam = bpy.data.objects.get(f"{name} Camera")
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print(f"EXAMPLE {spec_name}: {eqp.get('objects', 0)} things, verify passed={res['passed']} -> {path} "
          f"({os.path.getsize(path) / 1e6:.2f} MB)")
    return res["passed"]


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    library_file()
    if EXAMPLES:
        ok = all([example("Factory", "factory"), example("Warehouse", "warehouse"), example("House", "house")])
        print("EXAMPLES", "OK" if ok else "FAILED")
