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
from codenodes import agent  # noqa: E402

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
    check(mod[doors_id] == 1, "the scene is left as it was found")

    bad = agent.web_page(out, sliders=[{"object": "Courtyard", "input": "Nope", "values": [1, 2]}])
    check(not bad["ok"] and "no input 'Nope'" in bad["error"], "a wrong slider is explained")
    bad = agent.web_page(out.replace(".html", ".exe"))
    check(not bad["ok"], "and only .html files are written")

    print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)


main()
