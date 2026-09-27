# CodeNodes roadmap

Last updated 2026-09-27. Anything marked **checked** was run on this machine in Blender 5.0.1 and
5.1.2 (NVIDIA RTX A4500). Everything else is research or plan.

## Where the work is (2026-09-27)

`main` has everything up to the four graphics showcases. Newer work sits on branches, each
tested on both Blender versions and waiting for the user to try and merge:

| branch | what it adds |
|---|---|
| `install-check` | the extension installed as a user installs it and driven through the real MCP process (27 checks); fixes: MCP SDK 2.x, a shadowed operator, a viewport grab covered by the splash; exact install steps in README |
| `gn-capabilities` | `wall_network`, `rooms`, `roof`, `stairs`, `water`, terrain **Carve**; `curve(splines=...)`; the village demo |
| `shape-sliders` | Code Shapes as live web pages: the shape language ported to JavaScript, held to the Python vertex for vertex |
| `bake-to-nodes` | animation phase 3: the GLSL becomes a Geometry Nodes network through Expression Nodes |
| `roadmap-refresh` | this file |
| `all-changes` | all of the above merged, for trying in one go |
| `modeling-research` | `docs/MODELING_RESEARCH.md`: how others do language-to-buildings, and the plan |
| `factory-builder` | **the plan built (phases 0–7)**: spec → floor plan → building → equipment → verify, six MCP tools, the `geonodes/` library (on top of `modeling-research`, so it includes `all-changes`) |

They were cut from `main` separately, so README.md and the MCP tool list will need a small hand
merge where two branches both added lines.

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

**Phase 3 — Baked to nodes (done, branch `bake-to-nodes`).** The code itself becomes a real
Geometry Nodes network, so there are no files at all and the maths is evaluated natively on every
frame.

> **Checked:** `codenodes/bake_nodes.py` translates the GLSL (local variables, maths, `?:`, user
> helper functions, the distance helpers, `uTime`/`uFrame`) into the language of **Expression
> Nodes** (quacktheplanet/expression-nodes, the renamed script-to-nodes repo), which builds the node
> group; Volume Cube (density = −sdf, background far outside) → Volume to Mesh makes the surface.
> Against the GPU mesh from the same code it gives *the same vertices* (both meshers average the
> edge crossings of the same grid), after a slider change and on an animated frame, and the saved
> file builds the same mesh in a Blender with no add-ons. About 8 ms per rebuild at resolution 96.
> Loops, `if` statements and `noise3`/`fbm3` are refused with the line, pointing at Bake to Disk.
> Next: unroll constant-count loops, and a stock-node noise so noisy shapes convert (they would no
> longer match the GPU exactly, so it has to be opt-in).

Original plan, kept for reference:

- The **script-to-nodes** repo (now Expression Nodes) already compiles maths into
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
| **Geometry backends** | **Organic**: SDF code on the GPU — *done*. **Hard surface**: profiles, revolve, sweep, extrude, bevel, booleans, giving clean topology and UVs — *done* (the shape language). **Precision CAD**: a B-rep kernel (build123d/OpenCascade) for exact dimensions, threads, assemblies, STEP export — later | 2 of 3 |
| **Domain packs** | Architecture (floor plan → house: rooms, walls, doorways, windows, stairs and roofs are built as Geometry Nodes capabilities), Products and lighting (a lamp from scratch, IES profiles), Engineering, Environments | started |
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

   ~~Still to build: our own small MCP server.~~ **Built 2026-09-26** (`codenodes/rpc.py`,
   `server.py`, `mcp/codenodes_mcp/`), and **checked end to end on 2026-09-27**: the extension zip
   installed into a throwaway profile, the server started as the Start button does, and a real MCP
   client driving the MCP process through 27 steps (`tests/install_check.ps1`), with MCP SDK 2.2
   and 1.30. The reasoning, kept for reference:

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
5. ~~**Bake to Nodes (Phase 3).**~~ **Done** (branch `bake-to-nodes`); see above.

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
- ~~**Capability library**~~ **ten.** `terrain`, `scatter`, `wall`, `path_bridge`,
  `along_curve`, and (branch `gn-capabilities`, 2026-09-27) `wall_network`, `rooms`, `roof`,
  `stairs`, `water` plus terrain's **Carve** inputs. Each is a Python function over a small builder
  (`gn/builder.py`) that emits the same plain data `nodes_write` takes; `nodes_library` is the
  generated manifest. Tested for building cleanly, seed determinism, controls that do something,
  geometry checked by measurement (volumes against hand-worked values, rays through doorways and
  windows, tread, ridge and river-bed heights) and by rendering, and the round trip.
  `examples/level_demo.py` composes the first five, `examples/village_demo.py` the rest.

  Done from the old list: ~~wall junctions~~ (`wall_network`: unioned with the EXACT solver, the
  only one that really unions pieces of one mesh; doorways keep off junctions), ~~rooms from a
  floor plan~~ (`rooms`: one doorway per pair of rooms, windows, an entrance; plus `roof`),
  ~~stairs and ramps~~ (`stairs`), ~~river / road~~ (terrain **Carve** reads a curve object;
  `water` outputs its curve too, so a terrain carving along the same object still finds it).

  Next, in the order a level designer would reach for them:
  - **fence** (posts plus rails, following the ground) — mostly `along_curve` plus `path_bridge`'s
    rails;
  - **bridge height** over a gap from the two banks, not the curve;
  - **roofs for L- and T-shaped plans** (a straight skeleton; `roof` covers the bounding box today);
  - **multi-storey rooms** (a floor slab and a stair between levels) and **doors and windows as
    objects** in the openings;
  - a **floor-plan JSON** input for `rooms`, so a plan can come from outside Blender.

  Lessons that shaped these, worth keeping: Object Info sees another object's *evaluated*
  geometry, so something that follows another object's curve needs that object to output its
  curve (path_bridge does, with zero radius); a swept profile's Y points *down* along an
  upright curve; profiles swept round sharp corners pinch unless the corners are filleted first;
  the exact boolean solver collapsed a flat wall with doorways where Manifold did not — but
  only EXACT unions overlapping pieces of one mesh (Manifold and Float hand them back untouched);
  Fill Curve fills all splines as one shape unless each gets its own Group ID; merging points
  averages their rotations, so two opposite facings become none.

**P2.6 — headless Blender to the web** (the user's idea, 2026-09-26)

Run Blender with no window, build the geometry, and publish it as an interactive page — three.js,
with sliders for the parameters. His **webblend** repo is the start of this (a scene → HTML/CSS
exporter, currently a skeleton).

Worth noting how close it already is: a shape description and a node tree are both **plain data
with declared parameters**, so the page can expose exactly those as sliders. Two honest routes:
- **bake** each parameter combination to glTF and switch between them — simple, limited;
- **re-evaluate** in the browser — needs the generator ported to JS, which is realistic for the
  shape language (it is our own small language) and not for Geometry Nodes.

- ~~**Baked sliders for node setups**~~ **done (v1).** `codenodes/web.py`, `agent.web_page`. Every
  slider value is built and exported as glTF (instances stay instances: a 450-tree forest is
  0.12 MB). Each slider is tried at every value to find which objects it really changes, and
  sliders that overlap are baked as combinations (capped at 64). One self-contained HTML file,
  three.js 0.147 as classic scripts from jsdelivr, materials rebuilt by name from Blender's
  values, sun and camera carried over, errors shown on the page. Runs headless.
  `tests/test_web.py`; pages checked in headless Edge, including moving sliders.

  The first published version used ES modules and an import map and did not work on the
  artifact host, though it worked in a local browser; the cause was not pinned down (an import
  map after a host module script was ruled out). Not yet: procedural material variation is lost
  (flat colours on the web); no Draco compression (the sandbox blocks fetching the decoder, so
  it would need inlining); a wall that follows the ground carries many points, so variants are
  ~250 KB each.
- ~~**Live sliders for shapes**~~ **done** (branch `shape-sliders`). `codenodes/shapes/shapes.js`
  is a line-for-line port of the shape language; `tests/test_shape_js.py` holds it to the Python
  (same vertices to float32 precision, same faces in the same order, errors on the same line), and
  `agent.web_shape` writes a ~60 KB page that rebuilds as sliders move (5-10 ms for the lamp),
  checked in headless Edge. Shapes whose parts cut others still need Blender. The port found a
  bug in the Python: a path's `line … steps N` could never work.
- **webblend** can take the exporter as its core.

**P2.7 — graphics showcases** (the user, 2026-09-26: "put all of those on a list and knock them
out one by one")

Rampart showed what a mesh editor with good lighting looks like; these push further, each a
separate page. **All four built the same day** (`web/aerie.html`, `tellus.html`, `myriad.html`,
`sanctum.html`), each checked on the GPU in headless Edge; frame rates on real machines are still
to be measured (headless Edge fast-forwards its clock, so its numbers mean nothing). Lessons: a
JavaScript copy of shader noise does not match the GPU (float precision in the hash), so ask the
GPU (Tellus' probe); ground lit at sunlight x albedo / pi against scattered light, or the air
swamps it; simulated galaxy discs wind up, density waves do not; pre-warm simulations so they
open in shape.
1. **Raymarched SDF world** — a citadel on a sea-stack above clouds, traced per pixel: no polygon
   budget, soft shadows, AO, volumetric clouds, reflective ocean. The castle is live GLSL in the
   same helper vocabulary as CodeNodes (Z up, `sdBox`, `smin`, `// @param`), so it copies
   straight into a Code Mesh.
2. **Procedural planet** — atmospheric scattering, oceans, clouds, terrain; orbit to surface.
3. **Million-particle GPU simulation** — galaxy, fluid or murmuration; bloom and trails.
4. **Cinematic raster flythrough** — triangles pushed hard; the bridge to the game and to
   Solid Suzanne.

**P2.8 — buildings from a spec: the factory builder** (the user, 2026-09-27: "make me a floor plan
for a factory floor, given some specs, and actually go to work creating the building, the room,
the robots inside it"; branch `factory-builder`, plan in `docs/MODELING_RESEARCH.md`)

![the example factory](factory_demo.jpg)

Done, phases 0–7, in `codenodes/factory/`, all tested on 5.0.1 and 5.1.2:
- **Spec** (`spec.py`): site, spaces (16 types) with areas, SLP closeness A E I O U X, material
  flow, equipment, rules with sourced defaults (forklift aisle 3.6 m, corridor 1.2 m, 76 m travel).
  Problems come back as readable sentences with where they are. Six example specs: factory,
  machine shop, warehouse, bakery, lab, two-storey house.
- **Planner** (`planner.py`, no Blender): strips of spaces along the long side with an aisle
  between every two, annealed on shape, outside-wall needs (docks, windows, a named side),
  closeness, flow and (upstairs) areas; then walls, doors onto aisles, dock doors, windows,
  exits at both aisle ends with a personnel door beside the drive-in door, a column grid kept
  out of aisles, stairs and slab holes. Beats 30/30 random arrangements on its own score.
  A text grid view and a PNG drawing (pure Python) for looking before building.
- **Generators** (`kit.py`, `equipment.py`): 17 parametric parts written once as formulas of
  their sliders, turned into a plain mesh (tests, the build) or a Geometry Nodes group (the
  library) that match vertex for vertex. Robot arm with six joints (limits, parent/child links)
  recorded for animation and URDF later. A floating-parts check (every part rests on the floor or
  on another part) runs at every slider's ends and 324 arm poses; it found nine real defects.
- **Layout** (`layout.py`): conveyors between neighbours on the floor and across aisles as
  overhead bridges, fenced robot cells sized from reach + fence clearance with a fixture in
  reach, rack rows at right angles to the aisle with forklift aisles on every face, rows / grids /
  perimeter items with service clearances; door and dock approaches kept free; what does not fit
  is reported. A constraint search, not annealing: fast (under 0.5 s) and exact.
- **Verifier** (`verify.py`, `tools.scene_checks`): areas, shapes, outside walls, docks,
  collisions, bounds, aisle clear widths, service clearance, the walk from every free spot to an
  exit (8-connected on 0.25 m), robot reach and fences; in Blender also floating parts, real mesh
  intersections (BVH) and a ray through every opening. Repair re-places offenders and rolls back
  when it gets worse. Renders: plan, cut-away, aisle walk (EEVEE).
- **Building** (`build.py`): slabs with painted zones and aisle lines, `wall_network` for the shell
  and partitions with a new Openings input (a points object: size and angle per point), `roof`,
  `stairs`, columns, doors swung into their rooms, windows, dock doors, lights. Only objects tagged
  with the factory are ever replaced.
- **MCP tools**: `plan_site`, `edit_plan`, `build_plan`, `place_equipment`, `verify`, `assets`
  (pictures come back as images). A real MCP client took the plain request above to a verified
  building, then added a paint booth and verified again (13/13 on both Blender versions).
- **`geonodes/`**: the library as node-group assets with catalogs (27 groups, 0.5 MB) and three
  example scenes; opens in plain Blender with no add-on (checked on both versions).

Next (phase 8 and after): motion (conveyors running, arms on pick-and-place loops, AMRs on paths,
checked frame by frame); URDF/USD export from the recorded joints; L-shaped footprints and
straight-skeleton roofs; a module-swap layer for façades (bundles and closures); more generators
(press, paint booth, mezzanine, racking with pallets on the floor); aisles that turn corners
instead of only running end to end; a learned or rule-based route for house plans that read
better than strips.

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

- ~~**MCP server**~~ decided and built: our own, with a fixed tool table and no exec (see P1.4).
- ~~**Where the house builder lives**~~ decided (the user, 2026-09-27): part of the all-round modeling
  system, inside CodeNodes (`codenodes/factory/`), with the standalone pieces shipped as node-group
  assets in `geonodes/`. Built on branch `factory-builder` (P2.8).
- **Robots first as what?** Static posed models are built; the joints are recorded. Animated
  (showcases, the Unreal game) or simulation export (URDF/MJCF) next: the user to choose.
- **License**: the manifest says GPL-3.0-or-later (placeholder). Infinigen is BSD-3 and Poly Haven
  assets are CC0, so both fit.
- ~~**Merging `node-editor` and `bake` into `main`.**~~ Done. Still waiting: the five branches in
  "Where the work is" at the top.

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
