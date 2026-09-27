"""A small village from the newer capabilities: carved river with water, a house from a
floor plan, a walled yard from a wall network, and stairs up a bank. Renders two views.

    blender -b --factory-startup --python examples/village_demo.py -- <output folder>
"""
import math
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import agent  # noqa: E402

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.path.join(ROOT, "examples")
os.makedirs(OUT, exist_ok=True)
codenodes.register()
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

agent.material("Grass", [0.16, 0.3, 0.09], roughness=0.9, variation=0.5, variation_scale=0.3)
agent.material("Rock", [0.35, 0.33, 0.3], roughness=0.8, variation=0.4)
agent.material("Riverbed", [0.28, 0.24, 0.17], roughness=0.9, variation=0.4)
agent.material("Water", [0.05, 0.16, 0.2], roughness=0.05)
agent.material("Plaster", [0.82, 0.78, 0.7], roughness=0.7, variation=0.15)
agent.material("Planks", [0.42, 0.28, 0.16], roughness=0.6, variation=0.35, variation_scale=4.0)
agent.material("Stone", [0.5, 0.48, 0.44], roughness=0.8, variation=0.35)

# the river, and the ground carved along it
agent.curve("River", [[-40, -14, 0.2], [-15, -9, 0.1], [5, -13, 0.0], [40, -8, -0.2]])
land = agent.nodes_use("terrain", values={"Size": 80, "Resolution": 200, "Height": 2.0,
                                          "Feature Size": 30, "Seed": 4, "Material": "Grass",
                                          "Cliff Material": "Rock", "Carve Along": "River",
                                          "Carve Width": 4.0, "Carve Depth": 1.4, "Bank Width": 4.0,
                                          "Carve Material": "Riverbed"}, name="CN Terrain")["object"]
agent.nodes_use("water", "River", {"Width": 8.0, "Level": -0.8, "Material": "Water"})

# a house: three rooms from a floor plan, set on the ground at z = 0.4
z = 0.4
agent.curve("House", splines=[
    [[0, 0, z], [7, 0, z], [7, 5, z], [0, 5, z]],
    [[7, 0, z], [11, 0, z], [11, 5, z], [7, 5, z]],
    [[0, 5, z], [11, 5, z], [11, 9, z], [0, 9, z]]], cyclic=True, smooth=False)
agent.nodes_use("rooms", "House", {"Wall Material": "Plaster", "Floor Material": "Planks",
                                   "Entrance": 0})
agent.material("Tiles", [0.36, 0.13, 0.08], roughness=0.6, variation=0.3, variation_scale=6.0)
agent.nodes_use("roof", "House", {"Style": 2, "Material": "Tiles"})

# a yard wall: a network of walls meeting at corners and a T
agent.curve("Yard", splines=[[[-12, -3, z], [-12, 12, z]], [[-12, 12, z], [-2, 12, z]],
                             [[-12, 4, z], [-5, 4, z]]], smooth=False)
agent.nodes_use("wall_network", "Yard", {"Height": 1.8, "Thickness": 0.5, "Doorway Width": 1.2,
                                         "Doorway Height": 1.6, "Wall Material": "Stone",
                                         "Ground": land})

# stairs from the river bank up to the house
agent.curve("Steps", [[3.5, -7.5, -0.6], [3.5, -1.2, z]], smooth=False)
agent.nodes_use("stairs", "Steps", {"Width": 1.6, "Side Walls": True, "Material": "Stone"})

agent.light("outdoor")
agent.look_at("Steps", azimuth=-30, elevation=36, distance=42, lens=35)
print("rendered", agent.render(os.path.join(OUT, "village_wide.png"), samples=64, width=1280))
agent.look_at("House", azimuth=-20, elevation=22, distance=19, lens=35)
print("rendered", agent.render(os.path.join(OUT, "village_close.png"), samples=64, width=1280))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "village_demo.blend"))
