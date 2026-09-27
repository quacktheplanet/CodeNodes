# Research notes, 2026-09-26

Background reading for the bigger idea: **a Geometry Nodes agent** that can build anything with
nodes, standing on a growing library of capabilities — and where CodeNodes, Solid Suzanne and
earlier prototypes fit around it.

Anything marked **checked here** was run on this machine. The rest is research, with sources.

---

## 1. Virtualized geometry (the NVIDIA announcement, and solidsuzanne)

### What NVIDIA actually announced

**RTX Mega Geometry**, first shown around GDC/GTC 2025, with a 2.0 in 2025–26. It is **not** a
Nanite replacement for rasterising — it attacks the other bottleneck: getting Nanite-density meshes
into a **ray tracing** acceleration structure.

- It adds **Cluster Acceleration Structures (CLAS)**: pre-built clusters of ~128 triangles that many
  BLASes can reference, built and managed on the GPU so the CPU stops being the limit. 2.0 streams
  clusters from a continuous-LOD hierarchy on demand and drops them under memory pressure.
- Reached through **`VK_NV_cluster_acceleration_structure`** (Vulkan) or NVAPI (DX12). A **vendor
  extension**, NVIDIA only, Ada and Blackwell cards.
- The SDK is **source-available, not open source** — its licence forbids redistribution in a way
  that would create open-source obligations.
- AMD's parallel effort, **DGF (Dense Geometry Format)**, *is* open, and is a compressed meshlet
  format rather than an API.

**Can Blender reach it? No.** Blender's `gpu` module is a small cross-platform wrapper with no ray
tracing pipeline, no acceleration structures and no vendor extensions. Using RTX MG would need a
native C/C++ plugin talking to Vulkan directly, around Blender's GPU abstraction entirely.

Sources: [NVIDIA-RTX/RTXMG](https://github.com/NVIDIA-RTX/RTXMG) ·
[vk_lod_clusters sample](https://github.com/nvpro-samples/vk_lod_clusters) ·
[AMD DGF-SDK](https://github.com/GPUOpen-LibrariesAndSDKs/DGF-SDK/)

### What Nanite actually does, briefly

Meshes are split into ~128-triangle clusters arranged in a DAG, where each parent is a simplified
merge of its children. Each frame a *cut* through that DAG is chosen per cluster by screen-space
error, which is what makes the LOD continuous instead of popping. Triangles smaller than about a
pixel go through a **software rasteriser** in compute shaders (Epic says >90% of them), because
hardware rasterisers are inefficient at that size; both paths write a **visibility buffer** that
materials are resolved from afterwards. Known weak spots: skinned/deforming meshes (experimental
since UE 5.5), transparency, and hair-thin geometry.

### Open implementations worth reading

| project | licence | use to us |
|---|---|---|
| **Bevy** virtual geometry (Rust/WGSL) | MIT/Apache-2.0 | the most complete open Nanite-like; read for the algorithms |
| **meshoptimizer** (`buildMeshlets`, `clusterlod.h`) | MIT | **the reusable piece**: clusterisation and simplification as a small C library with Python bindings |
| **nanite-webgpu** | open | a readable teaching implementation of the whole pipeline |

### Blender's own direction

There is a live core issue, [#163926 "Draw: Meshlet culling"](https://projects.blender.org/blender/blender/issues/163926),
proposing sub-object culling granularity for very dense single meshes. That is **culling, not LOD** —
a plausible precursor, not virtualized geometry, and it is C++ core work with no Python surface.
Separately, Blender is mid-migration to a **Vulkan backend** (default on Windows/Linux around 5.3).

### Checked here: what Blender's Python GPU API can actually do

Run on Blender 5.1.2, RTX A4500, OpenGL backend:

| | |
|---|---|
| compute shaders | **yes** (`gpu.compute.dispatch`, work group size up to 1024) |
| image load/store | **yes** — this is how CodeNodes moves data in and out |
| **storage buffers (SSBO)** | **absent.** `gpu.types` has no `GPUStorageBuf`, and `GPUShaderCreateInfo` has no `storage_buf` — only `image`, `uniform_buf`, `push_constant` |
| **indirect draw** | **absent.** `GPUBatch` offers only `draw`, `draw_instanced`, `draw_range` |
| access to Blender's draw manager / depth prepass | none; an add-on draws in its own handler alongside Blender's rendering |

`gpu.types`: GPUBatch, GPUFrameBuffer, GPUIndexBuf, GPUOffScreen, GPUShader, GPUShaderCreateInfo,
GPUStageInterfaceInfo, GPUTexture, GPUUniformBuf, GPUVertBuf, GPUVertFormat.

**What that means.** All compute data must travel through **textures** — which is exactly what
CodeNodes already does (the SDF grid as a tiled 2D atlas, particle state as two RGBA32F textures).
That was the right call, and it is now confirmed as the *only* call.

It also puts a hard ceiling on a pure-Python Nanite: **the GPU cannot drive the draw**. You can cull
and select LOD in a compute shader, but the visible-cluster list has to come back to the CPU before
anything is drawn, which is the very round trip the technique exists to avoid. A Python add-on can
therefore reach:

- a **preview-only viewport overlay** for one very dense mesh, with cluster LOD chosen on the GPU,
  a readback, then `draw_range` per visible run — interactive, but not integrated with scene depth,
  and invisible to Cycles, EEVEE and exporters;
- or **offline** cluster/LOD work (build the hierarchy with meshoptimizer, bake decimated levels as
  real meshes), which *is* visible everywhere and needs no GPU tricks at all.

The second is far less glamorous and far more useful. Worth deciding which solidsuzanne is trying
to be before touching it.

---

## 2. Geometry Nodes capability libraries and environment generation

### What already exists

Two shapes of thing, and the difference matters for an agent:

- **Python add-ons that build node groups**: BB Nodes (paid, ~106 node-group operators),
  Sverchok (GPL-3, its own parametric node system, ten years old), Geo-Scatter ($99+, the dominant
  scatter system), Gscatter/Graswald (free, GN-based, layered effects).
- **Plain `.blend` asset libraries** of drag-and-drop node groups: BlenderKit's geometry-nodes
  category, Superhive's "Geo Nodes Library" (1200+ groups, depth unverified).

A node group in a `.blend` with declared sockets is far easier for an agent to read and compose than
a Python add-on's output, which doesn't exist until you run it.

(One correction: "Physical Starlight and Atmosphere" is a sky shader, not a GN tool.)

### Spline and shape driven environments

The pattern is real and partly solved:

- **Buildify** (free, Pavel Oliva) grows buildings from a footprint, and pairs with Blender-OSM so
  real city footprints generate buildings in one step. The clearest "shape → building" pipeline.
- **Road generators** work the way you'd guess: Resample Curve → Curve to Mesh for the surface,
  Instance on Points along a second curve for props, rotation from Align Euler to Vector fed by
  Curve Tangent.
- **A bridge generator** ([80.lv](https://80.lv)) uses Raycast and Geometry Proximity to tell a gap
  from solid ground and split the path into ramp and span automatically. That is genuine
  connection-resolution, and it is the technique "bridges appear where needed" needs.
- Blender core is adding a low-level **Bridge Curves** node (PR #114014).

### Where it stops

- **Junctions have no answer.** There is no node for "a door where two walls meet"; people do it
  with proximity maths by hand. There is an open request for Curve Intersections (#T102050). This
  is the gap worth owning.
- **Procedural interiors** barely exist — the common trick is a fake-interior *texture*, not
  geometry.
- **Wave function collapse** in GN is experimental only (Sverchok has a node; pure-GN attempts are
  forum posts).

### Techniques a library must standardise on

Instancing on points and curves; Align Euler to Vector from Curve Tangent; Simulation and Repeat
zones for growth; and — the one to make a rule — **randomness seeded from the stable `id`
attribute, never the index**, with every capability exposing a `seed` input. Otherwise editing
anything upstream reshuffles the whole scene.

### Prior art on LLMs writing node graphs

Thin, and the failures are instructive. Rogue Node Communicator exports a tree as JSON for you to
paste into a chat by hand. Tree Gen LLM only sets parameters on a fixed generator. **LL3M**
(arXiv 2508.08228) is the most serious attempt and it writes *Python*, not graphs — with only
**59% of edits succeeding first time**, and documented failures in spatial reasoning and
disconnected geometry. The blender-mcp family exposes add-node / set-socket / link-socket as
separate tool calls rather than having the model emit a whole tree.

**Read that as a warning.** Free-form graph authoring is where this consistently falls over.

### Representation — the actual hard problem

Blender has **no built-in text or JSON form of a node tree**. What exists:

- the live API: `bpy.data.node_groups`, `nodes.new()`, `links.new()`, and since 4.0 the
  `NodeTreeInterface` (`interface.items_tree`) carrying socket type, subtype, min/max, description
  and panels — enough to describe a contract;
- third-party exporters — NodeToPython, NodeKit, GNToolkit, BNDL Pro — none official, none proven
  diff-stable. Git-diffing `.blend` remains unsolved.

---

## 3. Earlier prototypes this builds on

| repo | what it really is | verdict |
|---|---|---|
| **solidsuzanne** | A working C++/Vulkan Nanite-lite: mesh → `.vgeo` via meshoptimizer, headless Vulkan renderer with frustum-cull and HZB compute shaders, pybind11 module, and a Blender `RenderEngine` that pipes pixels back as a texture. One commit, mid-flight. Tests cover the C++ conversion, not the renderer. | Read for the pybind11 + pixel-readback trick. It exists because "Blender's Vulkan wasn't stable until 5.0" — **that reason may have expired**, so decide what it's for before touching it |
| **a scene → web page exporter** | A day-one skeleton: mostly markdown spec, a props module, a DOM/CSS exporter, validators. No tests. | Leave alone |
| **a procedural racetrack kit** | **Finished, with real CI.** A `GraphBuilder` class wraps node-tree authoring; four modules each build one capability (track spline, canyon, infra scatter, vehicle proxy); parameters are typed GN sockets, enums via Menu Switch. CI pip-installs `bpy` and runs a smoke test, a geometric validator and golden-file regression on every push. The `.blend` is a **build artifact** | **Build on it.** This is the capability library, already |
| **a DSL → Geometry Nodes compiler** (now [ExpressNode](https://github.com/quacktheplanet/ExpressNode)) | A three-tier compiler: DSL → symbol graph → a typed `EvalGraph` DAG with socket types → CSE/DCE → a `gn_backend` emitter registry of ~35 ops keyed by dotted names, each calling real `nodes.new()`. 45 tests, bpy-free via mocking | **Build on it.** This is the answer to "how does an agent emit nodes" |

---

## 4. Where this leaves the Geometry Nodes agent

### Both halves already exist

- **The racetrack kit** shows what one capability looks like: a Python function that builds a
  parameterised node group, with a smoke test, a validator and a golden file.
- **The DSL compiler** shows how intent becomes nodes: an IR, then a registry of emitters.

What is missing between them is small: a **manifest** so an agent can discover what capabilities
exist and what each one promises, and a **loop** to check the result — which CodeNodes already has
in `agent.py` and the MCP server (build → render → look → fix).

### One disagreement with the research, worth recording

The survey recommends `.blend` node groups as the source of truth, with a text export as an audit
trail. **The racetrack kit demonstrates the better answer.** It keeps *Python* as the
source and treats the `.blend` as a build artifact — which is diffable, reviewable, testable in CI,
and dodges the unsolved `.blend` diffing problem entirely. Keep that.

### The shape I would build

1. **A manifest generator.** Walk a node group's `interface.items_tree` and emit JSON: what it
   makes, its inputs with types, units, ranges and descriptions, its outputs, and tags for what it
   composes with. Generated, never hand-written. Cheap, testable headlessly, and immediately useful.
2. **`gnkit`** — the racetrack kit's `GraphBuilder` and CI pattern pulled out as the shared way to
   write a capability: one function, typed sockets, a mandatory `seed`, a smoke test and a golden
   file.
3. **Agent tools**: list capabilities, place one, set its sockets, connect two, render, critique.
   Composing tested capabilities — *not* emitting raw graphs, which is exactly where LL3M's 59%
   came from.
4. **First three capabilities**, from the research: a **curve-to-modular-wall** with an explicit
   junction-module socket (the gap nobody has filled); a **path system with proximity-based gap
   detection** that decides ramp versus bridge; and a **seeded scatter** with stable-ID variation.

### And where CodeNodes fits

It is the other backend, for what nodes can't do: exact parametric solids from the shape language
(lamps, walls, bolts, with real UVs and sharp edges), and GPU fields, particles and volumes. A GN
agent that can also call CodeNodes has a way out whenever nodes are the wrong tool — and everything
CodeNodes bakes comes back as plain GN-friendly geometry, so the two compose.
