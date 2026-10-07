"""A scene as a web page with sliders, built headless.

    blender -b --factory-startup --python tests/test_web.py
"""
import base64
import json
import os
import re
import sys
import tempfile

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import agent, mod_inputs  # noqa: E402

_checks = 0
_failed = False


def check(cond, msg):
    global _checks, _failed
    _checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        _failed = True


def page_data(path):
    text = open(path, encoding="utf-8").read()
    found = re.search(r'<script type="application/json" id="scene-data">(.*?)</script>', text, re.S)
    return text, json.loads(found.group(1).replace("<\\/", "</"))


def main():
    codenodes.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    agent.material("Stone", [0.5, 0.5, 0.5])
    agent.nodes_use("terrain", values={"Size": 50, "Resolution": 60, "Height": 2,
                                       "Canyon Depth": 8, "Canyon Width": 4})
    agent.curve("Courtyard", [[8, 8, 0], [16, 8, 0], [16, 14, 0], [8, 14, 0]], cyclic=True,
                smooth=False)
    agent.nodes_use("wall", "Courtyard", {"Doorways": 1, "Wall Material": "Stone"})
    agent.curve("Trail", [[0, -22, 1], [0, 0, 1.5], [0, 22, 1]])
    agent.nodes_use("path_bridge", "Trail", {"Ground": "Terrain"})
    lamps = bpy.data.objects.new("Lamps", bpy.data.meshes.new("Lamps"))
    bpy.context.scene.collection.objects.link(lamps)
    agent.nodes_use("along_curve", "Lamps", {"Follow Object": "Trail", "Spacing": 8.0})
    agent.light("outdoor")
    agent.look_at("Terrain", azimuth=-30, elevation=30)

    out = os.path.join(tempfile.mkdtemp(), "level.html")
    height_before = bpy.data.objects["Courtyard"].modifiers[0]
    result = agent.web_page(out, title="Test Level", sliders=[
        {"object": "Courtyard", "input": "Doorways", "values": [0, 1, 2]},
        {"object": "Courtyard", "input": "Height", "values": [2.0, 3.0], "unit": "m"},
        {"object": "Trail", "input": "Gap Depth", "values": [1.5, 30.0], "unit": "m"},
    ], static=["Terrain"])
    check(result["ok"], f"a page is written ({result.get('bytes', 0):,} bytes)")
    groups = {tuple(g["sliders"]): g for g in result["groups"]}
    check(groups.get(("Doorways", "Height"), {}).get("variants") == 6,
          "two sliders on the same object are baked together, every combination (3 x 2)")
    trail = groups.get(("Gap Depth",), {})
    check(set(trail.get("objects", [])) == {"Trail", "Lamps"},
          f"a slider's indirect effects are found: the lamps follow the bridge ({trail.get('objects')})")
    check("Terrain" in result["static"], "and what no slider touches is exported once")

    text, data = page_data(out)
    check("<title>Test Level</title>" in text and "three@0.147.0/build/three.min.js" in text
          and "importmap" not in text and 'type="module"' not in text,
          "the page names itself and loads three.js as plain scripts (an import map breaks "
          "when a host adds its own module script first)")
    check(data["view"].get("sun") and data["view"].get("position"),
          "it carries the camera and the sun")
    check("Stone" in data["palette"], "and the materials, by name")
    glb = base64.b64decode(data["groups"][0]["variants"]["0,0"])
    check(glb[:4] == b"glTF", "each variant is a real glTF binary")
    first = data["groups"][0]["variants"]
    check(len(set(first.values())) == len(first), "and no two variants are the same")

    mod = bpy.data.objects["Courtyard"].modifiers[0]
    doors_id = next(i.identifier for i in mod.node_group.interface.items_tree   # (a panel
                    if i.item_type == 'SOCKET' and i.name == "Doorways")         # shares the name)
    check(mod_inputs.of(mod)[doors_id] == 1, "the scene is left as it was found")

    bad = agent.web_page(out, sliders=[{"object": "Courtyard", "input": "Nope", "values": [1, 2]}])
    check(not bad["ok"] and "no input 'Nope'" in bad["error"], "a wrong slider is explained")
    bad = agent.web_page(out.replace(".html", ".exe"))
    check(not bad["ok"], "and only .html files are written")

    # --- a Code Shape as a live page: built in the browser, nothing baked -------------------
    from codenodes.shapes import TEMPLATE, parse
    made = agent.make("shape", TEMPLATE, name="Lamp")
    agent.set_params("Lamp", height=0.5)
    folder = os.environ.get("CODENODES_SHAPE_PAGE_DIR") or tempfile.mkdtemp()
    page = os.path.join(folder, "lamp.html")
    got = agent.web_shape(page, object="Lamp", colors={"shade": [0.9, 0.55, 0.2]})
    text = open(page, encoding="utf-8").read() if os.path.exists(page) else ""
    found = re.search(r'id="shape-data">(.*?)</script>', text, re.S)
    data = json.loads(found.group(1).replace("<\\/", "</")) if found else {}
    heights = [p for p in data.get("params", []) if p["name"] == "height"]
    check(made["ok"] and got["ok"] and "CodeShapes" in text and len(data.get("params", [])) == 4,
          f"a shape becomes a live page with its four sliders and the builder inside ({got.get('bytes')} bytes)")
    check(bool(heights) and abs(heights[0]["default"] - 0.5) < 1e-6,
          "the page starts from the value tuned in Blender")
    # what the browser should build: the Python kernel at a few slider values, in Y-up
    expect = []
    for values in ({"height": 0.5}, {"height": 0.75, "shade_r": 0.3}, {"height": 0.2, "stem_r": 0.03}):
        solid = parse(TEMPLATE).build(values)
        size = solid.verts.max(axis=0) - solid.verts.min(axis=0)
        expect.append({"values": values, "faces": len(solid.faces) + len(solid.tris),
                       "size": [float(size[0]), float(size[2]), float(size[1])]})
    with open(os.path.join(folder, "lamp_expected.json"), "w", encoding="utf-8") as fh:
        json.dump(expect, fh)
    cut = agent.web_shape(os.path.join(folder, "cut.html"), source=(
        "part block\n  profile\n    move 0, 0\n    line 1, 0\n    line 1, 1\n    line 0, 1\n    close\n"
        "  extrude 1\npart hole subtract\n  profile\n    move 0, 0\n    line 0.2, 0\n    line 0, 0.2\n"
        "    close\n  extrude 1\n"))
    check(not cut["ok"] and "'hole'" in cut["error"], "a shape that cuts is refused, with the reason")
    check(not agent.web_shape(page, object="Nope")["ok"], "and so is something that is not a shape")
    print(f"SHAPE PAGE {page}")

    print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)


main()
