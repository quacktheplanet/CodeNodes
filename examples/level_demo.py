"""A small level, built entirely through the tools an assistant calls over MCP.

    blender -b --factory-startup --python examples/level_demo.py -- [output.png] [--save file.blend]

Terrain with a canyon, a trail that becomes a bridge where it crosses, a walled courtyard
with doorways that follows the ground, a forest that keeps clear of both, lamps along the
trail — and trees and lamps modelled with the shape language. Every step below is one
tool call; nothing reaches into Blender directly except clearing the startup scene.
"""
import os
import sys
import time

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import agent  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = next((a for a in args if a.endswith(".png")), os.path.join(ROOT, "examples", "level_demo.png"))
SAVE = args[args.index("--save") + 1] if "--save" in args else None


def call(tool, *a, **k):
    started = time.perf_counter()
    result = getattr(agent, tool)(*a, **k)
    took = time.perf_counter() - started
    brief = {key: result[key] for key in ("object", "group", "material", "collection", "made",
                                          "error", "warnings") if result.get(key)}
    print(f"{tool:<18}{took:6.2f}s  {brief}", flush=True)
    if not result.get("ok"):
        raise SystemExit(f"{tool} failed: {result.get('error')}")
    return result


codenodes.register()
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

# --- materials ------------------------------------------------------------------------------
call("material", "Grass", [0.19, 0.30, 0.11], roughness=0.95, variation=0.5, variation_scale=0.08)
call("material", "Rock", [0.20, 0.155, 0.11], roughness=0.9, variation=0.7, variation_scale=0.5)
call("material", "Stone", [0.52, 0.49, 0.44], roughness=0.85, variation=0.45, variation_scale=0.9)
call("material", "Capstone", [0.36, 0.34, 0.31], roughness=0.8)
call("material", "Gravel", [0.50, 0.45, 0.37], roughness=1.0, variation=0.35, variation_scale=3.0)
call("material", "Timber", [0.33, 0.20, 0.11], roughness=0.75, variation=0.4, variation_scale=2.0)

# --- assets, modelled in the shape language ----------------------------------------------------
call("make", "shape", """
param h 6.0 2 14
part low
  profile
    move 0, h * 0.2
    line h * 0.24, h * 0.2
    line 0, h * 0.62
    close
  revolve segments 9
part mid
  profile
    move 0, h * 0.42
    line h * 0.18, h * 0.42
    line 0, h * 0.84
    close
  revolve segments 9
part top
  profile
    move 0, h * 0.62
    line h * 0.12, h * 0.62
    line 0, h
    close
  revolve segments 9
""", name="Pine Crown")
call("make", "shape", """
part trunk
  profile
    move 0, 0
    line 0.2, 0
    line 0.12, 1.6
    line 0, 1.6
    close
  revolve segments 8
""", name="Pine Trunk")
call("make", "shape", """
part crown
  profile
    move 0, 1.8
    curve x = 1.7 * pow(sin(t * pi), 0.8)   y = 1.8 + 3.0 * t   steps 12
    close
  revolve segments 12
""", name="Round Crown")
call("make", "shape", """
part trunk
  profile
    move 0, 0
    line 0.16, 0
    line 0.1, 2.2
    line 0, 2.2
    close
  revolve segments 8
""", name="Round Trunk")
call("make", "shape", """
part post
  profile
    move 0, 0
    line 0.1, 0
    line 0.06, 3.0
    line 0, 3.0
    close
  revolve segments 10
part cap
  profile
    move 0, 3.38
    line 0.28, 3.38
    line 0, 3.6
    close
  revolve segments 10
""", name="Lamp Post")
call("make", "shape", """
part lantern
  profile
    move 0, 2.96
    curve x = 0.17 * sin(t * pi)   y = 2.96 + 0.42 * t   steps 10
    close
  revolve segments 12
""", name="Lantern")

call("material", "Needles", [0.06, 0.17, 0.07], roughness=0.9, variation=0.4, variation_scale=1.5,
     objects=["Pine Crown"])
call("material", "Leaves", [0.20, 0.30, 0.08], roughness=0.9, variation=0.5, variation_scale=1.2,
     objects=["Round Crown"])
call("material", "Bark", [0.20, 0.12, 0.07], roughness=0.9, objects=["Pine Trunk", "Round Trunk"])
call("material", "Iron", [0.08, 0.08, 0.09], roughness=0.4, metallic=1.0, objects=["Lamp Post"])
call("material", "Glow", [1.0, 0.8, 0.5], emission=[1.0, 0.72, 0.4], emission_strength=12.0,
     objects=["Lantern"])

call("collect", "Pine", ["Pine Crown", "Pine Trunk"], parent="Trees")
call("collect", "Round Tree", ["Round Crown", "Round Trunk"], parent="Trees")
call("collect", "Lamp", ["Lamp Post", "Lantern"], parent="Lamps")

# --- the level ---------------------------------------------------------------------------------
call("nodes_use", "terrain", values={"Size": 90, "Resolution": 280, "Height": 3.5,
                                     "Feature Size": 26, "Canyon Depth": 10, "Canyon Width": 5.5,
                                     "Canyon Wander": 6, "Canyon Steepness": 7, "Seed": 4,
                                     "Material": "Grass", "Cliff Material": "Rock"},
     name="CN Terrain")
bpy.data.objects["Terrain"].name = "Ground"

call("curve", "Trail", [[-9, -44, 1.0], [-4, -24, 1.0], [0, -8, 1.6], [2, 8, 1.6],
                        [7, 24, 1.0], [12, 44, 1.0]])
call("nodes_use", "path_bridge", "Trail", {"Ground": "Ground", "Width": 2.6, "Gap Depth": 1.2,
                                           "Path Material": "Gravel",
                                           "Bridge Material": "Timber"})

call("curve", "Courtyard", [[14, 14, 0], [30, 14, 0], [30, 28, 0], [14, 28, 0]],
     cyclic=True, smooth=False)
call("nodes_use", "wall", "Courtyard", {"Ground": "Ground", "Height": 3.2, "Thickness": 0.6,
                                        "Doorways": 3, "Doorway Shift": 0.12,
                                        "Wall Material": "Stone", "Post Material": "Capstone",
                                        "Post Spacing": 4.0, "Post Size": 0.85})

call("collect", "Built", ["Trail", "Courtyard"], keep_in_scene=True)
forest = bpy.data.objects.new("Forest", bpy.data.meshes.new("Forest"))
bpy.context.scene.collection.objects.link(forest)
call("nodes_use", "scatter", "Forest", {"Surface Object": "Ground", "Density": 0.07,
                                        "Spacing": 3.2, "Max Slope": 0.45, "Seed": 2,
                                        "Keep Clear Of": "Built", "Clear Distance": 3.0,
                                        "Instances": "Trees", "Keep Surface": False,
                                        "Scale Min": 0.6, "Scale Max": 1.35})

lamps = bpy.data.objects.new("Trail Lamps", bpy.data.meshes.new("Trail Lamps"))
bpy.context.scene.collection.objects.link(lamps)
call("nodes_use", "along_curve", "Trail Lamps", {"Follow Object": "Trail", "Spacing": 9.0,
                                                 "Side Offset": 0.95, "Sides": 3,
                                                 "Instances": "Lamps"})

# --- look at it ----------------------------------------------------------------------------------
call("light", "outdoor", strength=1.0)
call("look_at", "Ground", azimuth=-38, elevation=24, distance=62, lens=30)
try:        # the GPU if there is one; Cycles falls back to the CPU otherwise
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            if any(d.type == kind for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == kind
                bpy.context.scene.cycles.device = 'GPU'
                break
        except TypeError:
            continue
except Exception as exc:
    print("GPU not available:", exc)
bpy.context.scene.view_settings.look = 'AgX - Medium High Contrast' \
    if 'AgX - Medium High Contrast' in [i.identifier for i in
                                        bpy.types.ColorManagedViewSettings.bl_rna.properties[
                                            'look'].enum_items] else 'None'
result = call("render", OUT, samples=96, width=1400, aspect=0.6)
print("wrote", result["path"])
if SAVE:
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(SAVE))
    print("saved", SAVE)
