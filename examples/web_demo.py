"""The level from level_demo.py as an interactive web page, built headless.

    blender -b --factory-startup --python examples/level_demo.py -- --save level.blend
    blender -b --factory-startup --python examples/web_demo.py -- level.blend level.html
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
blend = next(a for a in args if a.endswith(".blend"))
page = next((a for a in args if a.endswith(".html")), os.path.join(ROOT, "examples", "level.html"))
bpy.ops.wm.open_mainfile(filepath=os.path.abspath(blend))

import codenodes  # noqa: E402
from codenodes import agent  # noqa: E402
codenodes.register()

result = agent.web_page(
    page, title="Canyon Crossing",
    subtitle="A level built with Geometry Nodes by an assistant. Every slider position was "
             "built in Blender beforehand.",
    sliders=[
        {"object": "Courtyard", "input": "Doorways", "values": [0, 1, 2, 3, 4], "label": "Doorways"},
        {"object": "Courtyard", "input": "Height", "values": [2.4, 3.2, 4.4], "unit": "m",
         "label": "Wall height"},
        {"object": "Trail", "input": "Gap Depth", "values": [1.2, 4.0, 20.0], "unit": "m",
         "label": "Bridge where the drop exceeds"},
        {"object": "Trail Lamps", "input": "Spacing", "values": [6.0, 9.0, 14.0], "unit": "m",
         "label": "Lamp spacing"},
    ],
    static=["Forest", "Ground"],
    overrides={"Ground": {"Resolution": 170}})
print(result)
