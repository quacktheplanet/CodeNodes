"""The smallest useful web page: one walled courtyard with three sliders, built headless.

    blender -b --factory-startup --python examples/web_simple.py -- courtyard.html
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import agent  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
page = next((a for a in args if a.endswith(".html")), os.path.join(ROOT, "examples", "courtyard.html"))

codenodes.register()
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

agent.material("Grass", [0.19, 0.30, 0.11], roughness=0.95)
agent.material("Stone", [0.55, 0.52, 0.47], roughness=0.85)
agent.material("Capstone", [0.33, 0.31, 0.29], roughness=0.8)
agent.nodes_use("terrain", values={"Size": 36, "Resolution": 48, "Height": 1.2,
                                   "Feature Size": 14, "Seed": 2, "Material": "Grass"})
agent.curve("Courtyard", [[-8, -6, 0], [8, -6, 0], [8, 6, 0], [-8, 6, 0]], cyclic=True,
            smooth=False)
agent.nodes_use("wall", "Courtyard", {"Ground": "Terrain", "Height": 3.0, "Thickness": 0.5,
                                      "Doorways": 2, "Post Spacing": 4.0,
                                      "Wall Material": "Stone", "Post Material": "Capstone"})
agent.light("outdoor")
agent.look_at("Courtyard", azimuth=-35, elevation=32, lens=35)

print(agent.web_page(
    page, title="Courtyard Wall",
    subtitle="One Geometry Nodes wall that follows the ground. Every slider position was "
             "built in Blender beforehand.",
    sliders=[
        {"object": "Courtyard", "input": "Doorways", "values": [0, 1, 2, 3, 4], "label": "Doorways"},
        {"object": "Courtyard", "input": "Height", "values": [2.0, 3.0, 4.5], "unit": "m",
         "label": "Wall height"},
    ]))
