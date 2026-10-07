"""CodeNodes on the web: particle chains packaged to run live in a browser (codenodes/webgl.py).

    blender -b --factory-startup --python tests/test_webgl.py      (Blender 5.2+: GPU in background mode)

Packages an object showing two chains (a Galaxy, and a Swirl pushed by a Wind Field with a use line and a
List), checks the bundles and writes a page. That the page runs in a browser is checked by WebBlend's
tests/browser_codenodes.mjs. Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""
import json
import os
import re
import sys
import tempfile
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gn_sockets, gpu_guard, live, webgl  # noqa: E402

_checks = 0


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def main():
    if bpy.app.background and not gpu_guard.available():
        print(f"SKIP: Blender {bpy.app.version_string} has no GPU in background mode (needs 5.2)", flush=True)
        return
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    host = bpy.data.objects.new("Sky", bpy.data.meshes.new("Sky"))
    bpy.context.scene.collection.objects.link(host)
    host.location = (1.0, 2.0, 3.0)
    tree = bpy.data.node_groups.new("Sky Nodes", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.nodes.new("NodeGroupOutput")
    host.modifiers.new("GeometryNodes", 'NODES').node_group = tree

    def add(kind, key, x, y=0):
        g, err = gn_link.create(kind, key)
        if err:
            raise Fail(err)
        return gn_link.insert(tree, g, (x, y))

    add('PARTICLES', "Galaxy", 0)
    add('MESH', "Castle", 0, 400)
    swirl = add('PARTICLES', "Swirl", 0, -400)
    push = add('STAGE', "Push by Field", 300, -400)
    pull = add('STAGE', "Attract to List", 600, -400)
    table = add('STAGE', "Attractors", 0, -1100)
    wind = add('STAGE', "Wind Field", 0, -800)
    tree.links.new(swirl.outputs["Particles"], push.inputs["Particles"])
    tree.links.new(push.outputs["Particles"], pull.inputs["Particles"])
    tree.links.new(wind.outputs["wind"], push.inputs["field"])
    tree.links.new(table.outputs[gn_sockets.TABLE_OUT], pull.inputs["targets"])
    for _ in range(4):
        gn_link.sync()
        live._flush()
    push.inputs[gn_sockets.use_socket("field")].default_value = "field(p) * 0.5"
    push.inputs["amount"].default_value = 2.5
    for _ in range(4):
        gn_link.sync()
        live._flush()

    bundles = webgl.host_bundles(host)
    names = sorted(b["name"] for b in bundles)
    check(names == ["Castle", "Galaxy", "Swirl"], f"every particle chain and surface on the object is packaged ({names})")
    castle = bundles[0]
    check(castle["kind"] == "surface" and "void cnMain() {" in castle["main"] and "float sdf(vec3 p)" in castle["source"]
          and "CN_HAS_COLOR" in castle["head"], "surfaces come first (particles draw over them): the raymarcher and the SDF")
    check(len(castle["lights"]) == 140 and castle["bounds"][0] != castle["bounds"][1] and castle["sky"] in (True, False),
          "with their bounds, sun and light block")
    sw = next(b for b in bundles if b["name"] == "Swirl")
    check("_field_in(p) * 0.5" in sw["source"] and "_targets_count() { return 3; }" in sw["source"]
          and re.search(r"#define n1_field_in f0_\w*_wind", sw["source"]),
          "a chain's code is packaged whole: the wired-in wind, its use line and the List")
    check(sw["prelude"].count("float rand1(") == 1 and "void cnSpawn(" in sw["source"],
          "with the helpers it calls, once")
    amount = sw["params"][sw["param_names"].index("n1_amount")]
    check(abs(amount - 2.5) < 1e-6, f"the sliders' values come along ({amount})")
    nodes = {s["node"] for s in sw["sliders"]}
    check({"Swirl", "Push by Field", "Wind Field", "Attract to List"} <= nodes,
          f"and every node's sliders are offered as page controls ({sorted(nodes)})")
    check(sw["object_matrix"][12:15] == [1.0, 2.0, 3.0], "the object's place in the world comes along")
    check(sw["row"] * sw["rows"] >= sw["count"] and sw["fps"] > 0, "state texture layout and frame rate")
    check(sw["look"]["color_mode"] in (0, 1, 2) and len(sw["background"]) == 4, "look and background")
    out = os.path.join(tempfile.mkdtemp(prefix="cn_web_"), "index.html")
    webgl.export_bundles(bundles, out)
    html = open(out, encoding="utf-8").read()
    runtime = os.path.join(os.path.dirname(out), webgl.RUNTIME_NAME)
    check(os.path.exists(runtime) and 'from "./codenodes_webgl.js"' in html and '"Galaxy"' in html,
          "a page is written: the runtime next to it, the chains inside")
    js = open(runtime, encoding="utf-8").read()
    check("export async function runParticles" in js and "STEP_MAIN" in js, "the runtime is the WebGL2 one")
    try:
        webgl.host_bundles(bpy.data.objects.new("Empty", None))
        check(False, "an object with no particles is refused")
    except webgl.NotPackable as exc:
        check("shows no CodeNodes particles" in str(exc), "an object with no code nodes is refused, saying why")
    # a mesh chain: a grid swayed by the same wind (a stage heading its own chain from plain geometry)
    import bmesh
    me = bpy.data.meshes.new("Grid")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=20, y_segments=20, size=2.0)
    bm.to_mesh(me)
    bm.free()
    field = bpy.data.objects.new("Field", me)
    bpy.context.scene.collection.objects.link(field)
    ftree = bpy.data.node_groups.new("Field Nodes", "GeometryNodeTree")
    ftree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ftree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = ftree.nodes.new("NodeGroupInput"), ftree.nodes.new("NodeGroupOutput")
    field.modifiers.new("GeometryNodes", 'NODES').node_group = ftree
    g, err = gn_link.create('STAGE', "Sway by Field")
    sway = gn_link.insert(ftree, g, (0, 0))
    ftree.links.new(gin.outputs[0], sway.inputs["Mesh"])
    for _ in range(6):
        gn_link.sync()
        live._flush()
    mb = webgl.host_bundles(field)
    check([b["kind"] for b in mb] == ["mesh"] and mb[0]["vertices"] == 441 and mb[0]["triangles"] == 800
          and "void deform(inout Vertex v)" in mb[0]["source"],
          f"a mesh chain is packaged with the mesh it receives ({mb[0]['vertices']} vertices, {mb[0]['triangles']} triangles)")
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


try:
    main()
except Fail as exc:
    print(f"FAIL: {exc}", flush=True)
except Exception:
    traceback.print_exc()
    print("FAIL: exception", flush=True)
sys.stdout.flush()
os._exit(0)
