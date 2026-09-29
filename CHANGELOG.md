# Changelog

## 0.4.0 (unreleased, draft): code-node graphs, caching and lighting

### Graphs, not just chains
- A stream wired into several stages splits into branches, each going its own way (glowing heads and
  trails from the same particles). Branches that differ only in looks and warps share one GPU
  simulation; a branch with its own behaviour after the split runs its own copy.
- **Join Particles** merges two streams: the stages after it run on both, and a To Geometry after it
  outputs both.
- One function output feeds any number of nodes in any chains (a Wind Field pushing particles and
  swaying a mesh).
- To Geometry partway along a chain outputs the stream at that point.
- Every path from a source to where its stream ends is compiled into one GPU program.

### Baking
- **GPU Cache** node: Mode (Live / Cached), Start / End, ⟳ Bake Now and ✕ Clear toggles, and a header
  that says what's cached. Frames are 16-bit floats, zlib-compressed, next to the .blend
  (`codenodes_cache/`); 20,000 particles × 48 frames = 13 MB. Cached frames play back when scrubbing and
  feed To Geometry and Render with CodeNodes.
- **Blender's own Bake node** now works after To Geometry: code nodes convert each frame before
  Blender evaluates it (frame_change_pre), so playback shows the current frame and the Bake node
  captures every frame.

### Lighting
- **Scene Lights**: the scene's lamps (up to 8) and world as a `light()` function, kept in step with
  the scene without recompiling; lamp size widens highlights as in EEVEE.
- **Material Look**: a Blender material's Principled BSDF values on particles, lit by Scene Lights.
- GPU Surfaces take **Lights** and **Material** inputs; lit that way they look close to the same
  surface made real and rendered by EEVEE.
- New declarations: `// @in material name` (a Material socket) and `// @in hidden name` (a value the
  add-on fills in, no socket).

### Looks and starters
- **Streak Look** (`@shape streak`): each particle drawn as a glowing streak along its motion.
- **Sway by Field**: a mesh stage bent by a function (grass in the wind).
- Firefly wings read as small translucent wings (a teardrop outline, bright rims, veins, a clear
  beat with a pause) and sit over the glow; the default wing size is larger.
- Galaxy: a dimmer bulge and inner disk, so the core glows instead of clipping.

### Documentation
- **docs/NODE_REFERENCE.md**: the full declaration syntax, and every node's complete code and sockets
  (type, default, range), generated from the add-on by `tools/gen_node_reference.py`;
  `tests/test_node_reference.py` fails when they disagree.

### Fixes
- Function-provider names inside a program no longer contain `__` (GLSL reserves it; NVIDIA refused
  some programs).
- Mesh stages get the random and noise helpers (`curlNoise`, `gnoise`, `rand1`...) that particles
  have, so a Wind Field can feed a mesh chain.

## 0.3.0 (unreleased, draft): code nodes you write, wired into chains

### A node's sockets come from its code
- `// @in float|int name default min max`, `// @in color name r g b` and
  `// @in func vec3 name(vec3 p)` become inputs; `// @out func name` and `// @out attr name` become
  outputs. `@param` still works. Sockets change as soon as the code does.
- Streams follow from the functions a node defines: `spawn` (a source, Particles out), `born` /
  `behave` / `look` (particle stages), `warp` (particles and meshes alike), `deform` (meshes).
- Socket types show what connects to what: Particles travel on Bundle sockets, functions on Closure
  sockets, meshes on Geometry sockets, per-particle attributes come out as float fields.

### Chains compile into one GPU program
- A particle source and the GPU Stage nodes wired after it (or a mesh and its stages) are stitched
  together. Each node's names get a per-node prefix, so nodes never clash, and modularity costs no
  speed (the six-node firefly chain draws at about 150 fps).
- Function sockets wire one node's function into another's input (Wind Field -> Push by Field; a GPU
  Surface's `sdf` -> Collide with Shape).
- Per-particle attributes (up to four per chain) are shared state every later node can read and
  write, and To Geometry writes them out.
- Compile errors name the node and the line in the code you wrote.
- Warps run where particles are shown and converted, never in the simulation: straighten a Bend and
  the swirl is exactly as before.

### New: GPU Stage, and the starters the examples need
- Particle stages: Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape.
- Looks: Glow Look, and Firefly Look (a glowing abdomen with a soft halo, and flapping wings).
- Warps: Bend (with an axis, stretch and twist) and Taper, on particles and meshes.
- Mesh stages: Ripple. Functions: Wind Field.
- Sources: Firefly Swarm and Spark Ball. GPU Mesh: Mesa.
- The rest of the starter library is written up as proposals in docs/GPU_NODES.md.

### Make Real is now To Geometry
- Same node, clearer name; older files are renamed when they open. Its first input follows what comes
  in (Particles or Geometry), and it writes every attribute the chain declares.

### Also
- A Bend (or any warp or deform stage) wired straight to ordinary geometry heads its own mesh chain.
- The CodeNodes Sources collection is excluded from the view layer, so the Outliner stays clean.
- The demo (`examples/gpu_demo.py`) has two scenes: Fireflies (a six-node chain; a firefly model with
  flapping wings on each point for renders, lit by `brightness`, with bloom) and Bend. It is saved at a
  window size that fits common screens, without the Spreadsheet, and every code node shows its
  template's name.
- The assistant can build chains: `code_stage` adds and wires a stage (MCP tool and skill updated).
- `update()` is optional on a particle source: without it, the chain moves particles by their
  velocity.
- tests/test_modular.py (27 checks, Blender 5.0.1 and 5.1.2).

## 0.2.0 (unreleased, draft): GPU nodes

### GPU code as nodes, drawn live
- **GPU Particles** are stepped by a compute shader and drawn straight from GPU memory into the
  viewport, with no copy into Blender. On an RTX A4500: 1M particles at about 190 fps, 4.2M at about
  160. The templates Galaxy, Flow and Attractor come from Myriad; `look(p)` colours them from code.
  **Emit From** spawns them on another object's evaluated surface (`emitPoint`, `emitNormal`).
- **GPU Surface** raymarches `sdf(p)` live, depth-tested against your objects, with sun, sky, soft
  shadows, ambient occlusion and fog. An optional `color(p)` colours it. New templates: Castle (from
  Aerie), Planet (Tellus-style), Saturn.
- **GPU Mesh** is new: `deform(v)` runs on every vertex of the mesh wired into the node (deform,
  displace, recolour), fed through a hidden "tap" copy of your tree. Templates: Wave, Noise Displace,
  Twist.

### Make Real
- A node (Shift A › CodeNodes › Make Real, like Realize Instances) turns the GPU node before it into
  real geometry:
  - particles become points with `velocity`, `speed`, `age` and `life`
  - surfaces become a mesh
  - GPU Mesh gives the same topology with `color` and `value`
- Its inputs: **When** (Automatic / Every Frame / When Changed / Only for Render), **Resolution** or
  **Max Points**, and which particle attributes to keep. Its header shows the cost.

### Everything on the node
- Settings are node inputs: the **Template** dropdown (switching keeps your edited code in a backup
  text), Count, Emit From, and Look / Simulation / Bounds panels.
- **✎ Edit Code**, the first input, opens the code in a pop-up Text Editor window and switches itself
  back off. Double-click or Ctrl+E does the same. Code recompiles as you type.
- The header shows the status (`Galaxy · live · 1.0M`, `⚠ line 12 …`). Tab in to see the code and
  the full status in frames. The sidebar keeps only buttons and the full error.

### Rendering
- **Render › Render Image / Animation with CodeNodes:** for each frame, the GPU steps, the result is
  made real, then that frame renders. The GPU and the renderer never overlap, so no bake is needed.
  Live-only nodes are made real just for the render.
- All GPU work goes through one guard that refuses it during any render; the tests count 0.

### Also
- Particle seeds use an integer hash (PCG). The old `sin`-based hash lined up in streaks at millions
  of particles.
- Particles are placed at reset (`update(p, 0)`), so kinematic motions like Galaxy start in shape.
- Shader push constants stay within 128 bytes, the most Vulkan guarantees.
- **Behaviour change:** a GPU Surface or GPU Particles node's own output is empty until a Make Real
  node follows it. 3D View › Add › Mesh › GPU Surface adds one when asked, and so does the
  assistant's `code_node` (`make_real`, on by default).

## 0.1.2 (unreleased, draft)

### Nodes first
- **3D Viewport › Add › Mesh › Code Mesh / Code Shape / Code Particles** now make an object whose
  Geometry Nodes tree holds a code node wired to the output (the code's sliders are inputs on that
  node). **Code Templates** lists every template; the old code-driven objects without nodes moved to
  **Scripted (no nodes)**, and files that use them still work.
- The 3D Viewport sidebar shows the selected object's code nodes: their inputs, **Edit Code**,
  **Show Nodes** (opens Geometry Nodes on that tree with the node selected), Live / Animate, Rebuild
  and Make Native.

### Claude, with nothing to start
- The **Assistant panel and Start button are gone.** The add-on lets assistants on this computer
  connect by itself when it's enabled (not in background Blender); Edit › Preferences › Add-ons ›
  CodeNodes › *Let assistants connect* turns it off.
- Each open Blender registers its port and token in `~/.codenodes/instances/`, so the MCP server finds
  Blender by itself and several Blenders can be open at once (ports 9877, 9878, …). On Windows a
  second Blender can no longer bind the same port.
- **Claude Code plugin** in the repo: `claude plugin marketplace add quacktheplanet/CodeNodes`, then
  `claude plugin install codenodes@codenodes`. It brings the MCP server and a `codenodes` skill.
- The MCP server is now **standard library only** (no MCP SDK to install). New tools: `code_node`
  (make or update a code node inside Geometry Nodes), `use_blender`; `status` lists every open
  Blender.

### Shapes
- `rotate x 90`, `translate z 1`, `scale z 2`, `array 3 x 0.5`: named components leave the others at
  their default. Before, one named value was applied to every axis and two failed.
- A sweep along a path that ends where it starts (a full turn with no pitch) joins into a ring with
  no end caps.
- Booleans are checked one part at a time: an empty or implausible result is an error naming the
  part, instead of a partial mesh reported as fine.

## 0.1.1 (unreleased, draft)

### Code nodes in Geometry Nodes
- **Add › CodeNodes** in the Geometry Nodes editor: Code Mesh (SDF), Code Shape and Code Particles,
  with starting templates (Donut, Rounded Box, Gyroid Ball, Blob; Desk Lamp, Vase; Swirl, Fountain).
  Each is a Group node whose inputs are the code's sliders and whose output is the code's geometry.
- Values typed on the node, or linked from a Value / Integer node, a reroute or the modifier's input,
  rebuild the geometry live. Editing the code adds or removes inputs to match. Shift D gives the copy
  its own code. Everything survives save and reopen.
- **Node Editor › Sidebar › CodeNodes:** Edit Code, Replace with Template, Live / Animate, Rebuild, and
  **Make Native** (a Code Mesh becomes plain Geometry Nodes through ExpressNode).
- Renders never run GPU code, now also for blocking renders (`bpy.ops.render.render`) started from
  scripts.

### First use
- The 3D Viewport sidebar explains where things live, shows which node graph made the selected object,
  and has **Open Node Graph** and a clearly labelled **Edit Code** button.
- The starter node graph makes a small Saturn instead of an unexplained blob.
- README: a "Your first five minutes" section.

## 0.1.0 (unreleased, draft)

The first public version. Tested on Blender 5.0.1 and 5.1.2, Windows 11, NVIDIA RTX A4500.

### Code into geometry
- **Code → Mesh:** a GLSL signed distance function, sampled on the GPU and meshed into a watertight
  quad mesh. `// @param` lines become sliders, and tuned values survive code edits. Live rebuild and
  per-frame Animate.
- **Code → Shape:** a small parametric language (profiles, paths, revolve, extrude, sweep, loft,
  booleans, bevels) that builds exact meshes with sharp edges and meaningful UVs.
- **Code → Volume:** a GLSL density written to OpenVDB, single frames or sequences.
- **Code → Particles:** your own `spawn`/`update` solver on the GPU, output as a point cloud with
  velocity, speed, age and life.
- **Node editor:** SDF Code, Combine, Transform, Offset, Mesh Output, Particles, Points Output and
  Shape nodes, compiled into one GPU program.
- Errors come back with the line number in your code instead of raising. Shader compile failures,
  slow code and oversized grids are caught before they can crash Blender or stall the GPU.

### Animation
- **Bake to Disk:** per-frame files played back by stock Geometry Nodes, so bakes render in F12, on
  machines with no GPU and without the add-on.
- **Bake to Nodes (experimental):** GLSL translated into a native Geometry Nodes network through the
  ExpressNode add-on (not public yet).

### Assistants
- `codenodes.agent`: a dict-returning surface for assistants (make, set sliders, frame, light,
  render, inspect, bake).
- An MCP server (`mcp/`) exposing it to MCP clients such as Claude Code: localhost only, token
  required, a fixed tool table with no arbitrary code execution. Works with MCP SDK 1.x and 2.x.

### Geometry Nodes
- Build, read, explain, edit and check any node tree as plain data, with a catalog read from Blender
  itself (field vs single-value sockets, menu choices).
- A library of tested capabilities: terrain (with canyon and carving), scatter, wall, wall network,
  rooms, roof, stairs, water, path/bridge, along-curve.

### Web
- `web_page`: a scene as one self-contained three.js page with sliders, every slider position baked
  to glTF.
- `web_shape`: a Code Shape as a live page that rebuilds in the browser from a JavaScript port of the
  shape language.

### Experimental
- **Buildings from a spec:** spec → floor plan → building → equipment → checks, with six assistant
  tools and 17 parametric equipment generators.
- **`geonodes/`:** node-group assets and example scenes (factory, warehouse, house) that open in
  plain Blender with no add-on.

### Known limitations
See "Known limitations" in the README.
