# Changelog

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
