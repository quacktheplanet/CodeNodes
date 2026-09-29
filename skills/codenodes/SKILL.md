---
name: codenodes
description: Build geometry in the user's open Blender with the CodeNodes add-on — shapes, meshes and particles written as code, code nodes inside Geometry Nodes, ready-made Geometry Nodes capabilities (terrain, walls, rooms, roofs, stairs, scatter, water), and renders to look at. Use when the user asks to model, generate or procedurally build something in Blender, or mentions CodeNodes.
---

# CodeNodes

CodeNodes is a Blender add-on that turns code into real geometry, live in the user's open
Blender. You drive it through the `codenodes` MCP tools. There is nothing for the user to
start: when Blender is open with the add-on enabled, the tools find it by themselves.

## First

1. Call `status`. If `connected` is false, ask the user to open Blender with the CodeNodes
   add-on enabled (installing it is in the README). Several open Blenders are listed with
   their ports; `use_blender(port)` switches between them.
2. Call `guide` once before writing code: it has the helper functions, the rules for each
   kind of code, and a template for each.

## Pick the right tool

| The user wants | Use |
|---|---|
| a thing made from code that they can tweak in Geometry Nodes (the normal case) | `code_node` |
| a plain object driven by code, no nodes | `make` |
| terrain, walls, rooms, roofs, stairs, bridges, scatter, water | `nodes_library` → `nodes_use` |
| any other Geometry Nodes setup, or a change to one | `nodes_find` / `nodes_describe` → `nodes_write` or `nodes_edit` |
| a building from a floor-plan spec (experimental) | `plan_site` → `build_plan` → `place_equipment` → `verify` |
| a web page with sliders | `web_page`, `web_shape` |

`code_node` makes what a person gets from Shift A › CodeNodes: an object whose Geometry
Nodes tree holds one code node (plus a To Geometry node after a GPU node, unless
`make_real=false`), with the code's sliders and the node's settings as inputs on that node. The result names the object, its tree
(`tree`) and the node group (`node`), so `nodes_read(tree)` and `nodes_edit(tree, ...)`
can wire it into more nodes (a Transform, a Join, an Instance on Points). Update it with
`code_node(object=..., code=..., values={...})`.

## The kinds of code

- **shape** (the shape language, not GLSL): the best choice for manufactured things —
  lamps, bottles, vases, columns, furniture parts, walls. Exact edges, clean quads, real
  UVs. `param name default min max` lines become sliders; a 2D `profile` (move / line /
  arc / curve / close / shell) is turned into a solid with `revolve`, `extrude`, `sweep`
  along a `path`, or `loft`; `part name subtract` cuts one part from another; `bevel`
  rounds edges. Any number can be maths (`radius * 0.38`, `sin(t * pi)`). See
  [reference/shape-language.md](reference/shape-language.md).
- **mesh** (GLSL): `float sdf(vec3 p)` returns the signed distance to the surface in
  metres (negative inside). Best for organic, blended, carved forms (smin blends, gyroids).
  It rounds sharp corners and has no useful UVs. `// @param name default min max`.
- **particles** (GLSL, GPU Particles): `void spawn(inout Particle p)` and `void update(inout
  Particle p, float dt)`; Particle has position, velocity, age, life, seed. Optional `vec4
  look(Particle p)` colours them live. `emitPoint(seed)` / `emitNormal(seed)` spawn on the
  node's Emit From object. Drawn live on the GPU (millions are fine); To Geometry turns them into
  points with `velocity`, `speed`, `age`, `life`. Shade with `speed`, not `velocity`.
- **deform** (GLSL, GPU Mesh): `void deform(inout Vertex v)` runs on every vertex of the mesh
  wired into the node's Mesh input; Vertex has position, normal, color (vec4), value, index.
  To Geometry gives the same topology with new positions and `color` / `value` attributes.

GPU nodes (mesh, particles, deform) are drawn live in the viewport and are not real
geometry until a To Geometry node follows them; `code_node` adds one by default. `render()`
includes live-only GPU nodes too (they're made real just for that frame).

Mistakes come back as `{"ok": false, "error": "line 3: ..."}` with the line in the code
you wrote. Read it, fix the code, call again. Slider values survive code edits.

## Look at what you made

Always check your work before saying it's done:

1. `light("studio")` for objects or `light("outdoor")` for landscapes. Without light a
   render is black.
2. `look_at(object)` to frame it.
3. `viewport()` for a quick look, `render()` for a real one. Both return an image. Look at
   it and fix what's wrong: proportions, gaps, floating parts, faceting.
4. `scene()` lists what exists, each object's kind, sliders, stats and errors.

## Geometry Nodes

Read `nodes_help()` first. The rules that matter:

- Compose the tested capabilities (`nodes_library`, `nodes_use`) before writing trees from
  scratch. Curve-following ones (wall, wall_network, rooms, stairs, water, path_bridge,
  along_curve) go on a curve object: make it with `curve` (several splines for
  wall_network and rooms). Change their sliders later with `nodes_set_inputs`.
- Never guess a socket name: `nodes_describe(type)` gives exact names, which inputs take a
  field, and what each menu accepts.
- `nodes_edit` for small changes (keeps everything else and the user's tuned values);
  `nodes_write` only for new trees or big rewrites.
- Always `nodes_check(group)` afterwards: it says what geometry actually came out.

## Good habits

- Real-world scale: Blender units are metres. A desk lamp is about 0.4 m tall, a door
  2.1 m, a table 0.75 m.
- Build in steps and look after each one, rather than writing everything in one call.
- Prefer editing what exists (`get_code`, `nodes_read`) over replacing it: the user may
  have tuned values.
- Final renders and animations: `bake(name)` code objects first, so they play and render
  without the GPU code running.
- If a tool says Blender isn't open, don't retry in a loop: tell the user.
