# CodeNodes roadmap

Last updated 2026-09-26. Anything marked **checked** was run on this machine in Blender 5.0.1 and
5.1.2 (NVIDIA RTX A4500). Everything else is research or plan.

## The idea

You or Claude write GPU code. CodeNodes runs it inside Blender and turns the result into something
Blender renders natively and Geometry Nodes can keep working on: meshes, particles, volumes, curves
and fluid. Claude can drive the whole loop and check its own work by looking at renders.

## The three phases of animation

The question this answers: if the viewport animates, why doesn't the final render?

**Phase 1 — Live (done).** A Python frame handler runs the GPU and rebuilds the mesh. Instant
feedback while you work, viewport only.

> **Checked: the live path cannot render.** With the "skip while rendering" guard removed on purpose,
> an F12 animation render crashed Blender with an access violation after the first frame. Background
> Blender has no GPU at all. This is why baking exists, and the guard stays.

**Phase 2 — Baked (done).** A frame loop we control writes one `.ply` per frame next to the .blend.
A generated node group made of stock nodes plays it back:

```
Scene Time.Frame → clamp to the baked range → Float to Int
    → Format String "<dir>/f_{}.ply" → Import PLY → Set Shade Smooth → Output
```

> **Checked end to end:** baked 8 frames, then opened the .blend in **background Blender with no GPU
> and CodeNodes never imported** — the animation loads, differs per frame, and renders. Another
> modifier stacked on top worked on the cached geometry (8,924 → 53,528 verts through Wireframe).

Frames outside the baked range hold the first or last frame, so there are no missing-file errors.
The Format String node needs `format_items.new('INT', ...)` before it has a slot to fill — without
that it silently produces an empty path.

**Phase 3 — Baked to nodes (planned).** Convert the code itself into a real Geometry Nodes network,
so there are no files at all and the maths is evaluated natively on every frame.

- The **script-to-nodes** repo (currently `coding-nodes`, to be renamed) already compiles maths into
  GN node trees, and Blender 5 has **Field to Grid → Grid to Mesh**, which is the same shape as what
  we do on the GPU.
- Two routes: a **"Bake to Nodes"** button that emits a standalone GN group, and a **direct
  conversion** inside the node group.
- Slower than the GPU, so the workflow stays: GPU while you design, nodes for the final output.
- Not everything will convert (loops and long iteration counts especially), so it needs to say
  clearly when it can't and fall back to Phase 2.

## Checked: what Blender 5 gives us

| what | result |
|---|---|
| `openvdb` Python module | bundled (VDB 12 in 5.0.1, 13 in 5.1.2), writes grids |
| numpy field → `.vdb` → GN **Import VDB** → Volume to Mesh | sphere returns at radius 0.999, in background |
| numpy points + custom attribute → `.ply` → GN **Import PLY** | 5,000 points, `temperature` kept |
| PLY with quad faces → Import PLY | 8 verts, 6 quads, from a linked path too |
| GN grid nodes | Field to Grid, Sample Grid, Grid Advect/Curl/Gradient/Divergence/Laplacian, SDF Grid Boolean, Grid to Mesh, Points/Mesh to SDF Grid |
| GN zones | Simulation, Repeat, For Each Element, Bake |
| Point clouds from Python | `pointclouds.new()` works but **cannot add points**; use a vertex-only mesh + Mesh to Points, or Import PLY |
| Curves (hair) from Python | `hair_curves.new()` + `add_curves()` |
| Alembic export of a Python-driven mesh | **doesn't work**: the exporter's own frame stepping doesn't run our Python rebuild, so every frame gets frame 1. We drive the frame loop ourselves instead |
| Volume objects from a written `.vdb` | works, placed exactly where the code puts it (a ball offset to x=1 measured at 1.016); `volume.grids` is lazy, so call `grids.load()`. OpenVDB stores only non-empty voxels, so the object's box hugs the filled part rather than the sample bounds |
| Volumes in a render | Cycles shows them out of the box; EEVEE needs its volumetric settings turned up, so the test renders with Cycles |
| Mesh datablock identity | rebuilding must fill the **same** datablock; making a new one per frame broke cache identity (`Blob_001`, `Blob_002`…) and doubled file size. Fixed |
| GPU uniform buffers | a `GPUUniformBuf` passed straight into `uniform_block()` as a temporary is freed before the dispatch runs, so every parameter silently reads zero. Keep a reference |
| Enum settings from the API | `settings["kind"] = 'PARTICLES'` writes an ID property that the enum never sees; assign `settings.kind = 'PARTICLES'` instead. ID-property writes are still the way to skip an update callback on ordinary properties |
| `@param` detection | only treat a line as a broken declaration when it *starts* with `// @param`, or prose mentioning @param fails to compile |
| Mesh to Points | **drops the object's material**, so the points render unshaded and vanish in a dark scene. Both the live points group and the baked cache group add a Set Material node fed from the modifier |
| The name `velocity` | reserved by Blender for motion blur: a shader's Attribute node reads nothing from it. A `speed` scalar is written alongside so people can shade by it |
| Particle speed | 300,000 particles step in about 12 ms per frame (RTX A4500) |

## Layers (how this grows past one add-on)

| layer | what it is | state |
|---|---|---|
| **Core** | Claude's tools, scene assembly, materials, lighting, camera, and the check → render → critique → fix loop | planned |
| **Geometry backends** | **Organic**: SDF code on the GPU — *done*. **Hard surface**: profiles, revolve, sweep, extrude, bevel, booleans, giving clean topology and UVs — planned. **Precision CAD**: a B-rep kernel (build123d/OpenCascade) for exact dimensions, threads, assemblies, STEP export — later | 1 of 3 |
| **Domain packs** | Architecture (floor plan → house), Products and lighting (a lamp from scratch, IES profiles), Engineering, Environments | planned |
| **Simulation** | Blender's own (rigid body, cloth, Mantaflow), CodeNodes GPU sims, and imported results from outside solvers (FEM, CFD) | planned |

Blender is excellent for design, visualisation and physically plausible simulation. For engineering
that has to be certified — tolerances, structural FEA, photometric compliance — these tools should
drive real solvers and CAD, not stand in for them.

## Priorities

**P0 — foundations**
1. ~~**Bake to disk + a stock-node reader.**~~ **Done.** `codenodes.cache`, the Bake and Remove Cache
   buttons, `api.bake()`; 18 checks plus a 10-check render-farm test.
2. ~~**Volume output.**~~ **Done.** `float density(vec3 p)` → OpenVDB → a Blender Volume object, one
   frame or a numbered sequence Blender plays natively. `codenodes.volume`, `api.code_to_volume()`,
   16 checks. Still to add: a node and a panel (it is API-only today), plus temperature and colour
   grids for fire.

**P1 — the headline features**
3. ~~**Code Particles.**~~ **Done.** `spawn` / `update` in GLSL, state in two RGBA32F textures stepped
   on the GPU, output as a point cloud with `velocity`, `age` and `life` attributes. Bakes to `.ply`
   (velocity as three scalars, put back together by the cache group, so Cycles gets motion blur).
   `codenodes.particles`, `api.code_to_particles()`, 21 checks — including gravity matching the
   closed-form answer to 3 decimal places.
   Still to do: a Particles node in the editor, forces/colliders as nodes, and sampling a CodeNodes
   SDF as a collider.
4. **Claude connection (MCP).** ~~The tool surface~~ **done**: `codenodes.agent` (help, make, scene,
   code, set_params, bake, frame, look_at, light, render, viewport, remove), 29 checks. Everything
   returns a dict and never raises for a fixable mistake.

   **Still to build: our own small MCP server.** Decided 2026-09-26 after reviewing both options.

   *Why not the third-party blender-mcp:* [issue #339](https://github.com/ahujasid/blender-mcp/issues/339)
   reports it hanging on Blender 5.1.1 / Windows 11 — this exact setup; telemetry to a hosted backend
   with disputed defaults ([#232](https://github.com/ahujasid/blender-mcp/issues/232)); and CVEs
   including arbitrary code execution (CVE-2026-10688, closed "not planned"). No socket auth.

   *Why not Blender's Lab MCP yet:* official and GPL-3.0-or-later like us, but experimental, and its
   premise is arbitrary `exec` behind a self-described "weak sandbox".

   *What to copy from Lab MCP (its architecture is good):*
   - **No worker thread at all.** The listening socket is non-blocking and everything — accept, read,
     run, reply — happens inside a `bpy.app.timers` callback, which is already the main thread. That
     removes the whole thread/queue/lock problem rather than solving it.
   - Timer backoff: poll at 0.05 s while busy, 1 s when idle.
   - Long jobs use a deferred "is it finished yet?" check polled on later ticks, never a thread.
   - Background mode needs a separate blocking `select` loop, because timers don't fire without a UI.
   - Tools as pairs: the MCP-side declaration and the code that runs inside Blender.
   - Images come back as base64 PNG in the result.
   - Errors become real MCP tool errors so the model sees them.

   *What we do differently:* expose only `codenodes.agent`'s fixed functions — no arbitrary `exec` —
   bind to localhost with a shared secret, and no telemetry.
5. **Bake to Nodes (Phase 3).** See above.

**P2 — beyond organic shapes**
6. **Maths into models (the construct backend).** ~~Profiles, revolve, extrude~~ **done** — the
   `shapes` package: a small language we parse ourselves (so it is safe to accept from anywhere),
   a safe expression evaluator, and a numpy kernel that builds the mesh directly. Exact edges,
   sharp corners kept, quads that follow the form, real UVs. `api.code_to_shape`,
   `agent.make("shape", …)`, Add › Mesh › Code Shape. 63 checks without Blender, 19 with.

   ~~Still to add: sweep, loft, shell, bevel, booleans, helix, a Shape node, profile as curve.~~
   **All done.** `sweep` carries a profile along a `path` (with parallel-transport frames, so it
   doesn't twist); `helix` makes springs and screw threads; `loft` blends profile to profile;
   `shell` gives an open profile thickness; `bevel` rounds edges (bmesh) per part or in a `finish`
   block; `part … subtract|intersect` cuts one part with another; there is a Shape node in the
   editor; and **Profile as Curve** draws the outlines as a real curve object to judge by eye.

   Found and fixed while building these:
   - **A lathe profile touching the axis** produced one vertex per segment at each pole, leaving a
     non-manifold solid that booleans refused. The poles are welded now, and those quads become
     triangles.
   - **Winding**: the cap fans were wound against the sides, so the solid was inside out in places.
     A bevel then grew outward instead of cutting in. Caps now match the sides, the whole solid is
     flipped once if its volume is negative, and Blender recalculates normals as a backstop.
   - **Blender 5's Manifold boolean solver** refuses these solids ("non-manifold inputs") even when
     every edge has exactly two faces — the pole fans seem to be enough to put it off. EXACT works,
     so that is what is used.
   - A hidden cutter object is left out of the depsgraph, so the boolean silently does nothing.

   Next for shapes: text as a profile; fillets between parts rather than on edges; a proper offset
   that handles self-intersection; reading an edited curve back into a description.
7. **Look-good pass.** CC0 materials and HDRIs (Poly Haven, ambientCG) fetched and cached; lighting
   rigs per scene type (exterior: HDRI + sun; interior: area lights + portals); AgX; a camera tool
   with framing rules (24–35 mm interiors, eye level or three-quarter).
8. **Check-and-fix loop.** Programmatic checks first (overlaps, floating objects, camera inside a
   wall, opening clearances), then render → critique → fix → repeat.
9. **Architecture pack.** Floor-plan JSON → walls, doors, windows, stairs, roof.
10. **Products and lighting pack.** Real light physics: blackbody colour temperature, IES profiles,
    emissive geometry, and a light-meter reading Cycles passes (design guidance, not certified
    photometry).

**P2.5 — the Geometry Nodes agent** (started 2026-09-26; see `docs/RESEARCH.md`)

The goal in the user's words: *"talk to Claude and have it build me a geometry node setup,
understand it entirely, and edit it on the fly."*

- ~~**Catalog**~~ **done.** `codenodes/gn/catalog.py` reads every node type out of Blender itself
  (320 here: 245 geometry, 49 function, 26 shader), with each socket's type, **whether it takes a
  field** (`display_shape` is a diamond) and what each dropdown accepts. Nothing hand-maintained.
- ~~**Round trip**~~ **done.** `gn/serialize.py` reads a tree to plain JSON-able data and writes it
  back: settings, unconnected values, links by socket identifier, frames, the group interface with
  panels and ranges, and zone pairing. Verified by rebuilding a tree and getting *identical*
  geometry, and by read → write → read being stable.
- ~~**Tools**~~ **done.** `nodes_help`, `nodes_find`, `nodes_describe`, `nodes_list`, `nodes_read`,
  `nodes_write`, `nodes_apply`, `nodes_check` — in `agent.py`, the socket server and the MCP server.
  `nodes_check` reports what geometry actually came out, and says so when nothing did.

- ~~**Round trip, the hard parts**~~ **done.** Nodes whose sockets are added by hand (Capture
  Attribute, Repeat/Simulation zones, Menu and Index Switch, Bake, bundles, closures) record their
  items, and because their socket identifiers come from a counter that survives deletions, each
  such node records the identifiers it had and they are mapped onto the rebuilt ones by position.
  Groups inside groups travel with the tree. A rewrite keeps values tuned on modifiers, and the
  wiring of other groups that use the rewritten one, by socket name.
- ~~**Validation**~~ **done.** `validate()` reads Blender's own link validity and says what is
  wrong in terms of the node: a field into a single-value socket, a type that cannot convert, a
  link to a socket the node is not using, an unconnected output. `unused()` lists nodes that do
  not reach the output.
- ~~**Editing and explaining**~~ **done.** `nodes_edit` applies small operations — set, add, link,
  unlink, insert, remove (optionally joining the flow up around it), rename, group inputs — all or
  nothing, tried on a copy first. `nodes_explain` describes a tree in words, in flow order,
  showing only what differs from a fresh node.
- ~~**Capability library**~~ **started: five.** `terrain`, `scatter`, `wall`, `path_bridge`,
  `along_curve`, each a Python function over a small builder (`gn/builder.py`) that emits the same
  plain data `nodes_write` takes; `nodes_library` is the generated manifest. Tested for building
  cleanly, seed determinism, controls that do something, geometry checked by ray (a doorway is a
  hole a ray passes through), and the round trip. `examples/level_demo.py` composes all five.

  Next capabilities, in the order a level designer would reach for them:
  - **wall junctions** — walls that meet at a T or a corner join instead of overlapping;
  - **rooms from a floor plan** — closed curves become rooms with walls, floors, doorways where
    two rooms share a wall;
  - **stairs and ramps** between two heights along a curve;
  - **river / road** along a curve that carves or flattens the terrain under it — this needs the
    terrain to read other objects (a "deform by curve" pass), which is a design question first;
  - **fence** (posts plus rails, following the ground) — mostly `along_curve` plus `path_bridge`'s
    rails;
  - **bridge height** over a gap from the two banks, not the curve.

  Lessons that shaped these, worth keeping: Object Info sees another object's *evaluated*
  geometry, so something that follows another object's curve needs that object to output its
  curve (path_bridge does, with zero radius); a swept profile's Y points *down* along an
  upright curve; profiles swept round sharp corners pinch unless the corners are filleted first;
  the exact boolean solver collapsed a flat wall with doorways where Manifold did not.

**P2.6 — headless Blender to the web** (the user's idea, 2026-09-26)

Run Blender with no window, build the geometry, and publish it as an interactive page — three.js,
with sliders for the parameters. His **webblend** repo is the start of this (a scene → HTML/CSS
exporter, currently a skeleton).

Worth noting how close it already is: a shape description and a node tree are both **plain data
with declared parameters**, so the page can expose exactly those as sliders. Two honest routes:
- **bake** each parameter combination to glTF and switch between them — simple, limited;
- **re-evaluate** in the browser — needs the generator ported to JS, which is realistic for the
  shape language (it is our own small language) and not for Geometry Nodes.

So: shapes first, with real sliders; node setups exported as glTF with a turntable and a few baked
variants. The pieces we need — an evaluator that is already data-driven, and a way to publish a
page — both exist.

**P3 — bigger simulations**
11. **Field output** (vector grids that GN simulations and hair can sample).
12. **GPU smoke solver** (advect, pressure solve, project; sources, forces and SDF obstacles as nodes).
13. **Curves output**, **live viewport raymarch preview**, **liquids**.
14. **Engineering bridges**: CAD kernel, and importing FEM/CFD results as colours and volumes.

## Mesh quality (asked about after seeing the blob)

Surface nets gives even, all-quad, watertight geometry that follows a grid — the same character as
Blender's Voxel Remesh. Good for rendering, animation, sculpt bases and printing; not for rigging or
UVs without a QuadriFlow pass. Worth doing later: snap vertices onto the true surface, keep sharp
edges (dual contouring), and use fewer faces where the surface is flat.

## Decisions waiting

- **MCP server**: blender-mcp (MIT, most used, Poly Haven built in) or Blender's own Lab MCP
  (official, experimental, 5.1+). Both run arbitrary code inside Blender, so read before installing.
- **Where the house builder lives**: its own add-on using CodeNodes, or inside CodeNodes.
- **License**: the manifest says GPL-3.0-or-later (placeholder). Infinigen is BSD-3 and Poly Haven
  assets are CC0, so both fit.
- **Merging `node-editor` and `bake` into `main`.**

## Known risks

- GPU work during a render crashes Blender (verified). Final renders always read a bake.
- Disk use: dense volume grids are big (256³ floats ≈ 64 MB per frame before compression); level sets
  and points are small. The bake reports its size; a size estimate before baking is still to add.
- Baked paths are absolute today, so moving the .blend breaks them. A relative-path mode is planned.
- Point clouds can't be sized from Python (above).
- Particles and fluids carry state between frames, so scrubbing backwards needs a cache or a replay
  from the start frame.

## Research sources (summaries, not verified here)

Volume grids in GN: code.blender.org/2025/10/volume-grids-in-geometry-nodes · Bundles and closures:
code.blender.org/2025/08/bundles-and-closures · GN import nodes: docs.blender.org manual › Geometry
Nodes › Input › Import · Fluid in GN request: projects.blender.org/blender/blender/issues/163996 ·
blender-mcp: github.com/ahujasid/blender-mcp · Blender Lab MCP: projects.blender.org/lab/blender_mcp ·
Infinigen (BSD-3, procedural interiors + placement solver): github.com/princeton-vl/infinigen ·
LL3M (plan→code→critique agents): github.com/threedle/ll3m · BlenderGym (evaluation scenes):
blendergym.github.io · Archimesh (GPL, walls/doors/windows): extensions.blender.org/add-ons/archimesh ·
Bonsai/IFC (LGPL): github.com/IfcOpenShell/IfcOpenShell · Poly Haven license: polyhaven.com/license ·
ambientCG (CC0): ambientcg.com
