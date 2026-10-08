# CodeNodes roadmap

What's done, what's next, and how to help. For the full design see `docs/GPU_NODES.md`; for every
node see `docs/NODE_REFERENCE.md`; for history see `CHANGELOG.md`.

## Where things stand (0.6.0)

**Works:**
- **GPU code nodes inside Geometry Nodes** (Shift A › CodeNodes): particles, raymarched surfaces
  (SDF), GPU Mesh (code run on every vertex) and Code Shape.
- **Code declares the node.** `@in` / `@out` lines become sockets, including functions, per-particle
  attributes, lists and tooltips.
- **Chains that split and merge,** compiled into as few GPU programs as possible.
- **The modular workflow:**
  - **Use lines:** each node decides what a shared input (such as a wind field) means to it.
  - **Lists:** a List node (a table you edit), plus list inputs on code nodes. On Blender 5.2 these
    are real Geometry Nodes lists.
  - **Explode / Collapse:** pull a script's top-level pieces out onto the graph and back.
  - **Code nodes inside node groups** (Ctrl+G).
  - **Values computed by other nodes feed code nodes.**
- **Real geometry and rendering:**
  - **To Geometry** (it labels itself To Points or To Mesh) is the one node that turns GPU results
    into real geometry.
  - **GPU Cache** records a simulation's frames.
  - **Scene Lights** and **Material Look** bring Blender's lights and materials into GPU looks.
  - F12 / Ctrl+F12 include GPU nodes. On 5.2+, command-line and farm renders do too.
- **Live web pages:** particle chains, surfaces and mesh chains run in a browser (WebGL2), via
  Export Live Web Page.
- **Demos:** Galaxy (shared orbits, reused planets, native and GPU nodes mixed, interacting
  systems), Windy Meadow, Fireflies.
- **Tested** on Blender 5.0, 5.1 and 5.2: Windows with OpenGL, and Linux with OpenGL and Vulkan,
  including headless.

**Experimental:**
- **Render form:** GPU Mesh code and warps compiled to native Geometry Nodes (via ExpressNode), so
  they render with no GPU code at all.
- **The factory / building planner** and the `geonodes/` asset library.
- **Bake to Nodes** (needs ExpressNode).

## Next

### The modular workflow
- **Native lists of records:** a bundle of lists into a struct input. Single-value native lists
  already work.
- **Big or animated lists:** lists that change every frame currently recompile every frame, because
  they're constants in the program. A texture path would suit them better.
- **Mesh stages inside node groups:** a mesh stage heading its own chain inside a node group, which
  needs the geometry from outside the group.
- **Use lines in the render form.**

### Native backends ("render form")
The goal: compile each code node once into several forms.
- **Geometry Nodes and shader node groups** for rendering: F12, Cycles, EEVEE, real lighting and
  shadows, no Python at render time.
- **GLSL** for the live viewport.
- **OSL** later, for heavy loops in Cycles.

Steps:
- Extend the render form beyond GPU Mesh and warps:
  - **Implicit surfaces:** Field to Grid → Grid to Mesh.
  - **Volumes:** density into a Volume shader, for lit nebulae, clouds and smoke without meshing.
  - **Looks:** shader nodes.
- Keep To Geometry and GPU Cache for what nodes can't express: loops, state, millions of particles.
- Watch Blender's own planned expression node, and keep the syntax compatible if it lands.

### Rendering limits to lift
- **Live glow isn't real lighting.**
- **No shadows between GPU nodes and Blender objects.**
- **Live surfaces ignore Blender materials,** except through Material Look.
- **Scripts:** `bpy.ops.render.render()` from a script in a window doesn't step the GPU; use
  `bpy.ops.codenodes.render()` instead.
- **Untested hardware:** AMD, Intel and macOS (Metal).

### Grease Pencil in 2.5D and 3D
Grease Pencil is native geometry in Geometry Nodes. Ideas:
- **Layers as animated cards in depth,** moved by code nodes while each layer keeps its hand-drawn
  frames.
- **Strokes to solids:** Grease Pencil to Curves → Curve to Mesh → Extrude.

First check whether layer and frame information survives Grease Pencil to Curves.

### Node trees on the web
- Compile ordinary Geometry Nodes trees to GPU code for the browser, alongside code nodes. A spike
  compiled a 63-node terrain tree to GLSL that matched Blender exactly; see `docs/FEASIBILITY.md`.

### Starter library
Proposals for particle and mesh starter nodes are listed in `docs/GPU_NODES.md`. Pick the first set.

### Known rough edges
- **Galaxy demo:**
  - The galaxy has a crisp outer rim where the summed glow saturates.
  - Asteroids are drawn twice in the viewport (dots plus rocks).
  - The ring renders as dots.
- **Simulation copies:** a branch converted by To Geometry runs its own copy of the simulation.
- **Particles only:** Join Particles and GPU Cache don't take surfaces or meshes yet.
- **Outliner clutter:** helper collections ("CodeNodes Sources" and others) still show greyed out.
- **✎ Edit Code** is a toggle acting as a button, because Blender allows no real buttons on group
  nodes.
- **A first-open review** of the whole add-on, judged as a new Blender user would see it.

## Contributing and testing

- **Rule of thumb:** judge changes by a Blender user's first five minutes. What you see is what you
  edit, and things live in normal Blender places.
- **GPU first:** code runs on the GPU by default, and To Geometry is the one node that makes it real.
- **Settings belong on nodes,** not in the sidebar.
- **Tests:** run them on Blender 5.0, 5.1 and 5.2 (see README › Tests).
  - **Windows:** `tools/testing/run_all_cn.ps1` runs windowed suites on a hidden desktop, so no test
    window appears.
  - **Linux:** `tools/testing/run_all_linux.sh` runs everything headless. With
    `tools/testing/xvfb_linux.sh` and `GPU_BACKEND=vulkan` it runs the window suites too.
- **Node reference:** regenerate `docs/NODE_REFERENCE.md` with `tools/gen_node_reference.py`;
  `tests/test_node_reference.py` checks it matches the templates.
- **Licence:** GPL-3.0-or-later.
