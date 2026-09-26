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
3. **Code Particles.** GPU state buffers (position, velocity, age, custom attributes) and an emitter;
   your code updates each particle. The artifact's curl flow is the first demo. Bakes to `.ply` with
   velocity so motion blur works, and Geometry Nodes can instance anything onto the points.
4. **Claude connection (MCP).** A structured tool surface — `code_to_mesh`, `build_graph`, `bake`,
   `render_preview`, `screenshot` — so Claude works in your open Blender and checks its own results.
5. **Bake to Nodes (Phase 3).** See above.

**P2 — beyond organic shapes**
6. **Hard-surface backend.** Profiles, revolve, sweep, extrude, bevel and booleans, with clean
   topology, sharp edges and UVs. This is what makes "a lamp from scratch" and walls possible.
7. **Look-good pass.** CC0 materials and HDRIs (Poly Haven, ambientCG) fetched and cached; lighting
   rigs per scene type (exterior: HDRI + sun; interior: area lights + portals); AgX; a camera tool
   with framing rules (24–35 mm interiors, eye level or three-quarter).
8. **Check-and-fix loop.** Programmatic checks first (overlaps, floating objects, camera inside a
   wall, opening clearances), then render → critique → fix → repeat.
9. **Architecture pack.** Floor-plan JSON → walls, doors, windows, stairs, roof.
10. **Products and lighting pack.** Real light physics: blackbody colour temperature, IES profiles,
    emissive geometry, and a light-meter reading Cycles passes (design guidance, not certified
    photometry).

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
