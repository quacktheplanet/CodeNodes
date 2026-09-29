# Examples

Each script runs straight from a clone of this repo: it loads CodeNodes from the repo, so nothing
needs installing. Run them with Blender 5.0 or newer from the repo folder. `-b` means no window,
which is fine for all of these except `gpu_demo.py`: the GPU nodes need a window.

| Script | What it makes | Run |
|---|---|---|
| [`gpu_demo.py`](gpu_demo.py) | the demo, two scenes. Fireflies: a plateau shaped by a GPU Mesh node, the Aerie castle as a live GPU Surface, and fireflies from a six-node chain (Firefly Swarm, Wander, Rise, Push by Field with Wind Field, Blink, Firefly Look), with a firefly model placed on each point for renders. Bend: one Bend node on a galaxy of particles and on a mesh column. Saves `codenodes_demo_v3.blend` (opens in the Geometry Nodes workspace), screenshots and renders | `blender --factory-startup --window-geometry 0 0 1600 960 --python examples/gpu_demo.py -- out` (with a window) |
| [`village_demo.py`](village_demo.py) | a house from a floor plan with a hip roof, walls meeting at a T, stairs, a river carved into terrain; renders two views | `blender -b --factory-startup --python examples/village_demo.py -- out` |
| [`level_demo.py`](level_demo.py) | terrain with a canyon, a trail that becomes a bridge, a walled courtyard, a forest and lamps, all through the assistant tools | `blender -b --factory-startup --python examples/level_demo.py -- level.png --save level.blend` |
| [`web_demo.py`](web_demo.py) | the level above as an interactive web page with four sliders (about 5.8 MB) | `blender -b --factory-startup --python examples/web_demo.py -- level.blend level.html` |
| [`web_simple.py`](web_simple.py) | the smallest useful page: one walled courtyard with sliders | `blender -b --factory-startup --python examples/web_simple.py -- courtyard.html` |

Output goes where you point it. Without an argument, files land next to the scripts, and git
ignores them.

The [`geonodes/`](../geonodes/) folder has ready-made scenes (a factory, a warehouse, a house) that
open in plain Blender with no add-on.
