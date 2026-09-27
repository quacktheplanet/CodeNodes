# Modeling research, 2026-09-27

**The goal (the user):** an all-round modeling plugin where the Opus 5.5 agent gets a command like
*"make me a floor plan for a factory floor, given some specs"* and actually builds it: the plan,
the building, the rooms, the equipment and robots inside. The house builder is one part of that
system, not a separate toy. Pieces that can ship on their own go in a Geometry Nodes project
folder.

This note is research plus a plan. Nothing here is implemented yet. It builds on
`docs/RESEARCH.md` §2 (capability libraries, representation) and the P2.5 section of `ROADMAP.md`.

---

## The ten findings that matter most

1. **Everyone who succeeds splits the job into stages, and solves the geometry with code, not the
   model.** Holodeck, Infinigen Indoors, SceneCraft, HomeWorld and SimWorlds all go
   plan → coarse layout → detail → verify, and in every one the LLM writes *constraints or a
   plan* while a deterministic solver or compiler produces coordinates. "Thinking in Blender"
   (2026) measured it directly: staged reconstruction beats one-shot by a wide margin with the
   same model.
2. **A compact spatial language beats raw coordinates by a lot.** SpatialGrammar (2026) gave
   Claude Sonnet 4 a bird's-eye grid language with a compiler that returns structured
   violations: instruction following rose from 0.62 to 0.83 and collisions fell from 66.7% to
   13.6% versus the same model writing JSON coordinates. Layouts that take hundreds of JSON tokens
   become a small grid "where spatial relationships are immediately visible".
3. **The verifier is as important as the generator.** BlenderGym (CVPR 2025) found that splitting
   compute between generating and *verifying* matters more than generating more; 3DCodeBench
   (2026) found the top failures are API mismatches and **disconnected or floating geometry**,
   fixed by multi-turn refinement against "high-fidelity feedback". Output-only feedback is not
   enough: one Blender study got 0% full-scene success when the agent only saw renders.
4. **Constraint solving with simulated annealing is the proven way to place things in rooms.**
   Infinigen Indoors (BSD-3) uses a Python DSL of relations over *classes* ("storage against a
   wall", "glassware only on shelves") and a hierarchical annealer: floor plan first, then large
   furniture, then small objects. Holodeck uses ten relation types (edge/middle, near/far,
   in front of/side of/above/on top of, center-aligned, face-to) with DFS or MILP. Both enforce
   hard constraints (no collisions, inside the room) and treat the rest as soft.
5. **Factory layout already has a mature, simple method we can encode.** Systematic Layout
   Planning (Muther): an activity relationship chart rating every pair of departments
   A/E/I/O/U/X (absolutely necessary … undesirable), plus flow volumes between them. CORELAP
   builds a layout from an empty floor by total closeness; CRAFT improves one by swapping
   departments to cut flow cost. That is exactly the "spec → floor plan" step for a factory, and
   it gives the agent a vocabulary a plant engineer already uses.
6. **Real clearances are available and should be built in, not guessed.** One-way forklift aisle
   = vehicle + load width + 3 ft (0.9 m); two-way = twice that + 3 ft (OSHA 29 CFR 1910.176(a)
   requires marked, unobstructed aisles but no fixed number). IBC: corridors 44 in (1118 mm),
   36 in under 50 occupants, 0.2 in per occupant, travel-distance limits by occupancy. Rack
   storage above 12 ft brings fire-code aisle rules. These make verification concrete:
   "is there a 3.3 m path from every dock to every cell?"
7. **Articulated, simulation-ready equipment is solved as "code against a small SDK".**
   Infinigen-Sim (BSD-3) adds hinge/slide joint node groups to Geometry Nodes with export to
   URDF/USD/MJCF; Articraft (2026) has the LLM write against a restricted SDK (parts, joints,
   validation tests it writes itself) and never touch URDF directly — 10k assets, 245
   categories. Phobos (BSD-3) already exports Blender robots to URDF/SDF. Robots and machines
   should be parametric generators with joint metadata, not meshes.
8. **Tool granularity is a first-class design decision.** MCP-GRANITE (2026): task-class tools
   (one tool per *kind* of job) beat one-tool-per-operation by 16.4% and a single mega-tool by
   33.6%, nearly doubled argument accuracy, and "a 3.2B model at the optimal granularity
   outperforms a 20.9B model at a mismatched one". Our `nodes_*` tools are the fine end; the
   factory work needs a few mid-level tools (plan, build, place, verify), not more primitives.
9. **Modular kits beat freeform geometry for buildings.** CityEngine's CGA split grammar, Houdini
   Labs Building Generator (slice blockout into floors, classify walls/corners/ledges, swap in
   modules from a library, per-floor overrides, seed), Buildify (GPL, footprint faces → kit
   parts) and Townscaper (WFC on an irregular grid with hand-made modules) all separate *massing*
   from *detail*. Our `rooms`/`wall_network`/`roof` are the massing; a module-swap stage is the
   missing detail layer.
10. **Blender 5.x gives us the pieces to ship this as plain node groups.** Bundles and closures
    (non-experimental in 5.0) let a node group take *behaviour* as an input — e.g. a building
    generator that accepts a "facade closure" or a scatter that accepts a distribution closure.
    Lists (5.2 LTS: Field to List, Filter/Sort List) remove old workarounds. Node group assets
    work with no add-on (the Essentials hair library proves it), and Blender 5.3 (Nov 2026) adds
    **asset libraries as extensions** (CC0 only, hosted remotely). The solver still needs Python,
    so the split is: planning in the add-on, geometry in shippable node groups.

---

## 1. LLM → 3D scene systems (what they do, what to borrow)

| System | Year | Core idea | Borrow |
|---|---|---|---|
| Holodeck | CVPR 2024 | GPT-4 writes relational constraints; DFS/MILP solver places Objaverse assets; floor plan and doors from the LLM | the ten relation types; hard vs soft constraints |
| Holodeck 2.0 | 2025 | adds VLM guidance and editing | edit loop over a persistent scene |
| Infinigen Indoors | CVPR 2024 | Python constraint DSL over object classes; simulated annealing (moves: add, delete, resample, translate, rotate, re-plane; temperature 0.25 → 0.001); hierarchical: plan → large → small; procedural generators, not retrieval | the solver design and staging; class-level constraints (BSD-3, so code can be read and adapted) |
| SceneCraft | ICML 2024 | scene graph blueprint → Blender Python; GPT-V critiques renders; **library learning** turns repeated code into reusable functions | library learning = our capability library growing from use |
| LayoutGPT / LLplace / I-Design | 2023–24 | CSS-like numeric layouts; retrieval of example layouts as few-shot; JSON dialogue protocol | retrieved exemplars for new spec types |
| SpatialGrammar | 2026 | BEV grid DSL for furniture (LLMSLI) and architecture (LLMSLB); sub-layouts on surfaces; deterministic compiler returns violations | **the representation for our spec**; nested grids for "things on a workbench" |
| Agentic scene gen w/ spatial context | 2025 | scene portrait + labelled point cloud + **hypergraph** of unary/binary/higher-order constraints (clearance, contact, alignment, symmetry) | constraints beyond pairs (e.g. "these five machines equally spaced") |
| HomeWorld | 2026 | floor plans from an LM trained on 300k real plans (K-D tree encoding) → furniture via image models → small objects on support surfaces | three-stage split; support surfaces as a first-class idea |
| 3D-GPT | 2023 | dispatch / conceptualise / model agents calling Infinigen generators by documented parameters | parameter docs = our capability manifest |
| LL3M | 2025 | multi-agent Python writers with a Blender-API RAG; ~59% first-try edit success | a warning: freeform code is fragile |
| BlenderAlchemy | ECCV 2024 | visual program refinement: tweak vs leap edits, edit reversion, imagined reference images | revert on regressions; "tweak" (numbers) vs "leap" (structure) |
| BlenderGym | CVPR 2025 | 245 scenes; verifier scaling | budget verification explicitly |
| 3DCodeBench | 2026 | 12 VLMs writing procedural code; failures are API mismatch + floating parts | automatic connectivity / grounding checks |
| ShapeCraft | 2025 | graph of sub-parts per shape, refined per node | our shape language already is a part graph |
| SimWorlds | 2026 | planner / coder / reviewer; layered scene protocol; **runtime-state inspection** to catch mechanisms that look right on video but aren't | for animated robots and conveyors later |
| Articraft | 2026 | restricted SDK for articulated assets, LLM-written tests | the robot/machine generator interface |
| Industrial workcell digital twins | 2026 | NL → layout grounded in an equipment library, an industrial layout knowledge graph and multi-tier constraints | the factory domain pack |
| EZBlender | 2026 | plan-and-ReAct: plan globally, act locally | our agent loop shape |

## 2. Procedural architecture

- **Split grammars / CGA** (Wonka 2003, Müller 2006): a shape is split into floors, floors into
  tiles, tiles into windows/doors, with conversion rules. Deterministic and compact; great for
  facades. For us: a *facade stage* after `rooms` that splits each outside wall face into bays.
- **Houdini Labs Building Generator 4.0**: blockout → floor slices → region classes (wall, convex
  corner, concave corner, top/bottom ledge) → modules from a library, per-floor overrides, seed.
  This is the cleanest statement of "massing then modules".
- **Buildify** (GPL, Geometry Nodes): footprint faces → kit parts from collections; ships a proxy
  kit and expects the user's own. Blender-OSM integration gives real city footprints.
- **Building Tools** (ranjian0, GPL): floorplan → walls, floors, windows, doors, balconies,
  stairs, roofs as edit-mode operators. Good reference for the operation list; destructive, so
  not our model.
- **Townscaper**: WFC on an irregular quad grid + marching-cubes-style module placement, with
  some modules spanning two cells to hide the grid. WFC is worth it for *kits with adjacency
  rules* (pipes, catwalks, cable trays in a factory), less for the plan itself.

## 3. Floor-plan generation methods

| Method | How | Good for | Weak at |
|---|---|---|---|
| Squarified treemap (Marson & Musse 2010) | recursively split a rectangle into rooms of given areas, keeping aspect near 1 | fast, fills the footprint, predictable | adjacency only by ordering; needs corridor pass |
| Constrained growth (Lopes et al. 2010) | seed rooms on a grid, grow them by area ratio, zones first then sub-zones | irregular shapes, adjacency via seed placement | can leave gaps; needs cleanup |
| Graph → plan (Graph2Plan, HouseDiffusion) | bubble diagram (rooms + adjacency) → learned layout | realistic housing | needs trained models; residential-biased |
| LLM → JSON polygons + verifiable rewards (2026) | fine-tuned LLM, rewards for area, adjacency, overlap | shows the checks that matter | training |
| CORELAP / CRAFT (industrial) | construct by closeness ratings / improve by swapping to cut flow cost | factories, labs, warehouses | blocky; ignores aesthetics (fine for factories) |
| Simulated annealing over a grid (Infinigen style) | moves on room cells, score = constraint violations | mixes all of the above as score terms | tuning; speed |

**Recommendation:** a grid-based plan (cells of e.g. 0.5 m or 1 m), zones first then rooms
(constrained growth / treemap to initialise), then annealing with score terms for area, aspect,
adjacency (AEIOUX weights), flow cost (CRAFT), exterior access (docks, windows) and corridor
connectivity. Output: closed outlines per room → exactly what `rooms` already consumes.

## 4. Factory / warehouse layout knowledge (encode as defaults, show as warnings)

- **SLP relationship chart**: A absolutely necessary, E especially important, I important,
  O ordinary, U unimportant, X undesirable (e.g. paint shop X welding). Flow-from-to matrix for
  material handling.
- **Process vs product layout**: job-shop (machines grouped by type) vs line (by sequence);
  U-shaped cells for lean manufacturing (in and out at the same end).
- **Aisles**: forklift one-way = vehicle + load + 0.9 m; two-way = 2 × (vehicle + load) + 0.9 m;
  pedestrian walkways separately marked; OSHA 1910.176(a) requires clear, marked aisles.
  Typical counterbalance forklift aisles 3.3–4 m, reach trucks 2.4–3 m, very-narrow-aisle 1.5–2 m
  (industry guides; treat as defaults the user can override).
- **Egress (IBC 2024)**: corridors ≥ 1118 mm (914 mm if < 50 occupants); 0.2 in/occupant capacity;
  exit-access travel distance limits by occupancy (larger with sprinklers); dead-end limits.
- **Machine envelopes**: footprint + service clearance (commonly ~0.9 m on service sides) + robot
  reach envelope + safety fencing offset. These are generator outputs, so the verifier can check
  them.
- **Structure**: column grid (commonly 6–12 m bays; wider for warehouses), clear height, dock
  doors on one side, mezzanines. Column positions constrain everything else, so they are part of
  the plan, not decoration.

*These are planning references, not code compliance. The verifier should phrase them as
warnings with the rule's name.*

## 5. Equipment, props and robots

- **Parametric generators over retrieval.** Infinigen's lesson: procedural generators give
  unlimited variety, exact dimensions and clean topology; retrieval (Objaverse) gives realism but
  wrong sizes, bad topology and licence questions. For a factory the dimensions *are* the spec, so
  generators win: conveyor (length, width, height, rollers/belt, legs), pallet rack (bays, levels,
  depth), workbench, CNC/lathe box with doors, press, tote, pallet, forklift, AMR, fence panels,
  cable tray, pipe run.
- **Robot arms as kinematic chains.** Base + links + joints (axis, limits) + tool flange; a
  6-axis arm is a list of link lengths and joint types. Generate with the shape language (links
  are sweeps/extrusions) and keep joint metadata so it can pose, animate, and export URDF (via
  Phobos, BSD-3, or our own writer, following Infinigen-Sim's node-group joint convention).
- **Kitbash as fallback.** For things we can't generate yet, a CC0 kit (Poly Haven, Blender
  Studio assets) instanced by the same placement tools. Never ship non-CC0 kit parts.
- **Articulation metadata** in one format across everything: joint type, axis, limits, parent,
  child. Doors on machines and the robot arm use the same code path.

## 6. Agent-side techniques

- **Plan → build → verify → repair**, with the plan persisted as data (JSON spec) so edits are
  diffs, not rebuilds ("move the paint booth next to the dock" = change one relation, re-solve).
- **Structured violations, not pictures, as the first feedback.** Return lists like
  `{"rule": "aisle_width", "where": "between Press-2 and Rack-A", "have": 2.1, "need": 3.3}`.
  Renders come second, for style and "does it look like a factory".
- **VLM critique with reference views.** Fixed cameras (top-down plan, isometric, eye-level walk)
  rendered in EEVEE; BlenderAlchemy's "imagined reference" trick is optional.
- **Revert on regression.** Keep the last good plan; if a repair increases violations, roll back
  (BlenderAlchemy's edit reversion).
- **Library learning.** When the agent composes the same thing twice (e.g. a robot cell = arm +
  fence + conveyor + table), promote it into a named capability (SceneCraft).
- **Mid-level tools** (MCP-GRANITE): plan, build, place, verify, edit — each taking the spec — on
  top of the fine `nodes_*` tools that stay for experts.
- **Exemplars.** Keep a small set of example specs (bakery, machine shop, warehouse, lab,
  2-bed house) and retrieve the nearest one as a few-shot example (LayoutGPT).

## 7. Blender / Geometry Nodes hacks and workarounds

- **Bundles + closures** (5.0): pass a "module closure" into a building node group so facades,
  railings or equipment variants are pluggable without new inputs. Bundles keep a spec-shaped
  record (room name, area, type) flowing through one link.
- **Lists** (experimental 5.0, core in 5.2 LTS): per-room data as lists instead of attribute
  tricks; Filter/Sort List for "the three largest rooms get windows".
- **Repeat zones** for growth and subdivision inside nodes (treemap splits, stair flights,
  multi-storey stacking); **bake nodes** to freeze expensive results (the solved plan, booleans).
- **Instancing, realize late.** Racks, rollers, bolts, fence posts as instances; realize only for
  booleans/export. Keeps a 10,000-part factory interactive.
- **Stable seeds from `id`, never index** (already a library rule).
- **Exact boolean only where needed.** `wall_network` taught that only EXACT truly unions pieces of
  one mesh; everything else should avoid booleans (openings as separate geometry where possible).
- **Object Info sees evaluated geometry**: chained capabilities must output what the next one
  reads (already a library lesson — `path_bridge`).
- **Node tools** (operators made from node groups) give "Add Factory Rack" menu entries with no
  Python — good for the shippable library.
- **Blender 5.2 additions** worth using: Mesh Bevel node (chamfered machine edges), Merge Points /
  Cluster by Distance (clean welds), inputs defaulting to the self object.

## 8. Licence-safe sources

| Source | Licence | Use |
|---|---|---|
| Infinigen / Infinigen Indoors / Infinigen-Sim | BSD-3 (some CC0 snippets, marked) | read and adapt solver, constraint DSL, joint node groups, simulator export |
| Phobos | BSD-3 | URDF/SDF export reference or optional dependency |
| Poly Haven, Blender Studio assets | CC0 | textures, kit parts |
| Buildify, Building Tools | GPL | ideas only unless CodeNodes stays GPL (manifest says GPL-3.0-or-later today) |
| Houdini Labs, CityEngine | proprietary | concepts only |
| Research papers | — | methods; no code copied unless its repo licence allows |

The licence decision (ROADMAP "Decisions waiting") matters here: staying GPL lets us borrow GPL
Blender tools; BSD/MIT would keep only BSD/CC0 borrowing.

---

## 9. What gives us an edge

1. **We already own both halves**: a node-group capability library the agent can build, read and
   edit (P2.5), and a shape language + GPU code for parts. Most research systems have only
   retrieval or only freeform Python.
2. **Everything we generate is editable procedural Blender** — not a mesh soup. The user keeps
   sliders; the agent keeps a spec it can diff.
3. **A verifier that measures, not just looks** — we already verify capabilities by rays and
   measurement; extend that to aisles, egress, reach and collisions.
4. **Domain rules as data** (SLP ratings, clearances) — cheap to write, rare in the research,
   and exactly what makes "a factory floor" plausible.
5. **Shippable output**: plain node-group assets and baked files that open without the add-on,
   plus the web export for sharing.

## 10. Proposed architecture

```
spec (JSON / grid DSL)          ← the agent writes and edits this
   │   validate → violations
   ▼
planner (Python, no Blender)    ← zones → rooms (treemap/growth init + annealing), columns, docks,
   │                               corridors, stairs; factory: SLP/CRAFT flow scoring
   ▼
plan (room outlines, openings,  ← persisted beside the .blend; drives everything below
      levels, grid, equipment slots)
   │
   ├─ building builders (GN)    ← rooms (from plan JSON), wall_network, roof (straight skeleton),
   │                               stairs, slabs per storey, facade modules via closures
   ├─ equipment generators      ← shape-language / GN generators with dimensions + clearances +
   │                               joints (conveyor, rack, bench, machine, arm, AMR, fence)
   ├─ layout solver             ← Infinigen-style annealing: hard (inside, no overlap, clearances,
   │                               aisles) + soft (flow, relations, alignment, symmetry)
   ▼
verifier                        ← measurements (aisle widths, egress path lengths via grid BFS,
   │                               collisions, floating parts, robot reach), then renders
   │                               (plan, iso, walk) for VLM critique
   ▼
repair loop / user edits        ← edit the spec, re-solve only what changed, revert on regression
```

**The spec** (first version, deliberately small):

```json
{
  "site": {"footprint": [[0,0],[60,0],[60,40],[0,40]], "grid": 1.0, "levels": 1,
           "clear_height": 8, "column_grid": [10, 10]},
  "spaces": [
    {"name": "Receiving", "area": 300, "type": "dock", "exterior": "south"},
    {"name": "Machining", "area": 800, "type": "production"},
    {"name": "Assembly",  "area": 600, "type": "production"},
    {"name": "Paint",     "area": 200, "type": "production", "enclosed": true},
    {"name": "Storage",   "area": 500, "type": "storage", "racks": {"levels": 4}},
    {"name": "Office",    "area": 120, "type": "office", "windows": true}
  ],
  "relations": [["Receiving","Storage","A"], ["Storage","Machining","E"],
                ["Machining","Assembly","A"], ["Assembly","Paint","I"],
                ["Paint","Office","X"]],
  "flow": [["Receiving","Storage",40], ["Storage","Machining",30], ["Machining","Assembly",30]],
  "equipment": [
    {"kind": "cnc", "count": 6, "in": "Machining", "arrange": "row", "service": 0.9},
    {"kind": "robot_arm", "count": 2, "in": "Assembly", "reach": 1.8, "fenced": true},
    {"kind": "conveyor", "from": "Machining", "to": "Assembly"},
    {"kind": "rack", "in": "Storage", "aisle": "forklift_two_way"}
  ],
  "rules": {"aisle_forklift": 3.6, "corridor": 1.12, "service_clearance": 0.9}
}
```

The grid DSL (SpatialGrammar-style) is an optional *view* of the same plan the planner returns,
so the agent can read "where things are" at a glance and propose moves in cells.

**MCP tools** (task-class granularity, on top of today's tools):

| Tool | Does |
|---|---|
| `plan_site(spec)` | validate + solve; returns plan id, grid view, violations |
| `edit_plan(plan, ops)` | move/resize/swap spaces, change relations; re-solve locally |
| `build_plan(plan)` | building from plan via existing capabilities (rooms, walls, roof, stairs, slabs) |
| `place_equipment(plan, items)` | generators + layout solver inside spaces |
| `verify(plan, checks?)` | measured violations + optional renders from standard cameras |
| `assets(kind?)` | list generators with their parameters and clearances (like `nodes_library`) |

`nodes_*`, `make` (shape language), `render`, `viewport`, `web_page` stay as they are.

**Mapping onto what exists today**

| Need | Existing piece | Change |
|---|---|---|
| rooms from outlines | `rooms` capability (closed splines → floors, walls, doorways, windows, entrance) | read plan JSON; doorway placement from plan openings |
| walls with junctions | `wall_network` (EXACT union) | reuse for partitions and fences |
| roofs | `roof` (bounding box) | straight skeleton for L/T plans (already on ROADMAP) |
| stairs, levels | `stairs` | multi-storey slabs + stair placement from plan |
| scattering, along-curve | `scatter`, `along_curve` | racks along aisles, fence posts, lights on a grid |
| parts | shape language (profiles, revolve, sweep) + GPU code | equipment generators, robot links |
| freezing / sharing | Bake to Disk, Bake to Nodes, `web_page` / `web_shape` | factory walkthrough pages |
| agent access | MCP server (fixed tools, no exec) | add the six tools above |
| verification | capability tests by rays/measurement | generalise into `verify` |

## 11. Phased build plan

| Phase | Deliverable | Test |
|---|---|---|
| **0. Spec + validator** | JSON schema, validator returning structured violations, 5 example specs (house, bakery, machine shop, warehouse, lab) | pure Python; every example validates; broken specs give readable violations |
| **1. Planner** | grid planner: zones → rooms (treemap/growth init, annealing on area/aspect/adjacency/flow/exterior/corridors), column grid, docks | pure Python, seeded; measurable scores; plots of plans as images |
| **2. Building from plan** | `rooms` reads plan JSON; openings from plan; multi-storey slabs + stairs; L/T roofs; doors and windows as objects | both Blender versions; rays through doors; walk path exists |
| **3. Equipment generators** | 10 parametric generators with clearance boxes and joint metadata (conveyor, rack, bench, CNC, press, arm, AMR, forklift, fence, pallet) | dimensions measured; joints pose within limits; renders |
| **4. Equipment layout** | annealing solver inside spaces (hard: inside, no overlap, clearances, aisles; soft: flow order, rows, alignment, symmetry) | zero hard violations on the examples; flow cost lower than random |
| **5. Verifier + repair loop** | aisle widths, egress BFS distances, collisions, floating parts, robot reach; standard renders; agent repair with revert | seeded bad layouts get fixed; regressions roll back |
| **6. MCP tools + agent prompts** | `plan_site`, `edit_plan`, `build_plan`, `place_equipment`, `verify`, `assets`; exemplar retrieval | end-to-end through a real MCP client: "factory floor for …" → built, verified scene |
| **7. Shippable library** | `geonodes/` folder: a script-built `.blend` of node-group assets (capabilities + generators) with catalogs and node tools; examples; later a Blender 5.3 asset-library extension | opens in plain Blender, drag-and-drop works with no add-on |
| **8. Motion** | conveyors moving, arms running pick-and-place loops, AMRs on paths; runtime-state checks (SimWorlds) | frame checks that parts reach their targets |

Phases 0–1 need no Blender and can be tested fast; 2–4 extend existing capabilities; 5–6 are
where the agent gets good; 7 is packaging; 8 is the showcase.

## 12. The standalone, shippable piece

- **`geonodes/`** (new top-level folder in this repo, or its own repo later): a build script that
  writes `CodeNodes Library.blend` from `codenodes/gn/library.py` (it already builds every
  capability as a node group), marks each as an asset with catalogs (Terrain, Architecture,
  Factory, Props), adds node tools, and a folder of example `.blend` scenes. This works in plain
  Blender with no add-on — exactly how the Essentials hair assets ship.
- **What can't ship that way**: the planner and solver (Python). They stay in CodeNodes, exposed
  through MCP and a panel. A user without the add-on still gets the building blocks; the agent
  (and add-on users) get "spec → factory".
- **The house builder** therefore lives as *Architecture* assets in `geonodes/`, driven by the
  planner in CodeNodes — one modeling system, not a separate add-on.
- **Later**: Blender 5.3 remote asset libraries (CC0 only) could host the library on the
  extensions platform; that forces a CC0 licence for those assets, a separate choice from the
  add-on's licence.

## 13. Risks and open questions

- **Solver speed**: annealing over large factories could take seconds to minutes; keep it in pure
  Python + numpy first, measure, then consider a compiled core.
- **Aesthetics vs rules**: factories tolerate blocky plans; houses don't. Houses may need the
  learned-plan route (HomeWorld / Graph2Plan data) later; start with factories and simple houses.
- **Licence**: GPL vs BSD decides whether Buildify/Building Tools code can be reused.
- **Scope of "robots"**: static posed models first, animated second, simulation export (URDF)
  third — confirm with the user which matters for his uses (Unreal game, showcases, sim).
- **Verification thresholds**: code numbers vary by jurisdiction; present them as editable
  defaults with sources, never as compliance.

---

## Sources

- Holodeck — [arXiv 2312.09067](https://arxiv.org/abs/2312.09067); Holodeck 2.0 — [arXiv 2508.05899](https://arxiv.org/pdf/2508.05899)
- Infinigen Indoors — [arXiv 2406.11824](https://arxiv.org/pdf/2406.11824); Infinigen repo (BSD-3) — [github.com/princeton-vl/infinigen](https://github.com/princeton-vl/infinigen)
- Infinigen-Sim — [arXiv 2505.10755](https://arxiv.org/abs/2505.10755), [simulator export docs](https://github.com/princeton-vl/infinigen/blob/main/docs/simulation/ExportingToSimulators.md)
- SceneCraft — [arXiv 2403.01248](https://arxiv.org/abs/2403.01248)
- SpatialGrammar — [arXiv 2604.27555](https://arxiv.org/html/2604.27555v1)
- Agentic 3D scene generation with spatially contextualized VLMs — [arXiv 2505.20129](https://arxiv.org/html/2505.20129v2)
- HomeWorld — [arXiv 2606.06390](https://arxiv.org/abs/2606.06390)
- Thinking in Blender (staged executable inverse graphics) — [arXiv 2606.02580](https://arxiv.org/abs/2606.02580)
- LayoutGPT — [NeurIPS 2023](https://proceedings.neurips.cc/paper_files/paper/2023/file/3a7f9e485845dac27423375c934cb4db-Paper-Conference.pdf); LLplace — [arXiv 2406.03866](https://arxiv.org/html/2406.03866v1); I-Design — [atcelen.github.io/I-Design](https://atcelen.github.io/I-Design/)
- 3D-GPT — [arXiv 2310.12945](https://arxiv.org/abs/2310.12945); LL3M — [arXiv 2508.08228](https://arxiv.org/abs/2508.08228)
- BlenderAlchemy — [arXiv 2404.17672](https://arxiv.org/html/2404.17672v1); BlenderGym — [arXiv 2504.01786](https://arxiv.org/abs/2504.01786)
- 3DCodeBench — [arXiv 2606.01057](https://arxiv.org/abs/2606.01057); ShapeCraft — [arXiv 2510.17603](https://arxiv.org/abs/2510.17603); SimWorlds — [arXiv 2607.01766](https://arxiv.org/abs/2607.01766); EZBlender — [arXiv 2601.07143](https://arxiv.org/abs/2601.07143)
- Articraft — [arXiv 2605.15187](https://arxiv.org/abs/2605.15187)
- MCP-GRANITE (tool granularity) — [arXiv 2609.24161](https://arxiv.org/html/2609.24161)
- Floor plans with LLMs + verifiable rewards — [arXiv 2605.14117](https://arxiv.org/pdf/2605.14117); HouseLLM — [arXiv 2411.12279](https://arxiv.org/html/2411.12279v1)
- Constrained growth floor plans — [Lopes et al. 2010](https://graphics.tudelft.nl/~rafa/myPapers/bidarra.GAMEON10.pdf); squarified treemap floor plans — [Marson & Musse 2010](https://onlinelibrary.wiley.com/doi/10.1155/2010/624817)
- CGA shape grammar — [Müller et al. 2006](https://dl.acm.org/doi/10.1145/1141911.1141931)
- Houdini Labs Building Generator — [SideFX docs](https://www.sidefx.com/docs/houdini/nodes/sop/labs--building_generator-4.0.html)
- Buildify — [CG Channel](https://www.cgchannel.com/2022/07/download-free-blender-3d-building-generator-buildify/); Building Tools — [github.com/ranjian0/building_tools](https://github.com/ranjian0/building_tools)
- Townscaper / WFC — [Game Developer](https://www.gamedeveloper.com/game-platforms/how-townscaper-works-a-story-four-games-in-the-making), [mxgmn/WaveFunctionCollapse](https://github.com/mxgmn/WaveFunctionCollapse)
- SLP / CORELAP / CRAFT — [IEOM 2021](http://ieomsociety.org/proceedings/2021indonesia/85.pdf), [CORELAP overview](https://slm.mba/mmpo-003/corelap-efficient-layout-planning-relationship-analysis/)
- Forklift aisles / OSHA 1910.176(a) — [BigRentz guide](https://www.bigrentz.com/blog/forklift-aisle-width-requirements-osha-standards-and-planning-guide), [FLC guide](https://www.forkliftcertification.com/determining-warehouse-aisle-width/)
- IBC egress — [corridor widths](https://datadrivenaec.com/insights/ibc-egress-corridor-requirements), [exit requirements](https://datadrivenaec.com/insights/ibc-exit-requirements)
- Industrial workcell digital twins from language — [J. Intelligent Manufacturing 2026](https://link.springer.com/article/10.1007/s10845-026-02970-9)
- Phobos (BSD-3) — [github.com/dfki-ric/phobos](https://github.com/dfki-ric/phobos)
- Bundles and closures — [Blender developers blog](https://code.blender.org/2025/08/bundles-and-closures/); Blender 5.2 Geometry Nodes — [release notes](https://developer.blender.org/docs/release_notes/5.2/geometry_nodes/)
- Essentials node-group assets — [Blender 3.5 notes](https://www.blender.org/download/releases/3-5/); asset libraries on the extensions platform (5.3) — [devtalk announcement](https://devtalk.blender.org/t/asset-libraries-extensions-platform/45779)
