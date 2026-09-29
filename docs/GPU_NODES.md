# GPU nodes

Code that runs on the graphics card, sitting in your Geometry Nodes tree like any other node.

Each code node is a node you write. The code says which inputs and outputs the node has. Wire code
nodes together (split streams, merge them, feed one function to many nodes) and CodeNodes compiles
every path into one GPU program, so splitting an effect into small nodes costs no speed.

Every node, with its complete code and every socket, is in [NODE_REFERENCE.md](NODE_REFERENCE.md).

**The principle: code runs on the GPU by default, and one node makes it geometry.** Every code node
runs and displays on the GPU, live in the viewport: particles, raymarched surfaces, mesh code. No
code node has a "display as mesh" option. The only way to get real Blender geometry is the
**To Geometry** node. Its header says what it outputs: **To Points** after particles, **To Mesh**
after a surface or mesh chain. Nothing adds one for you, not the Add menu and not the templates.
You put it where you want geometry.

## What you see

**Shift A › CodeNodes** in the Geometry Nodes editor offers:

| Node | What it does | Starters |
|---|---|---|
| **To Geometry** | Turns the GPU node or chain before it into real geometry | — |
| **Join Particles** | Merges two particle streams: the stages after it apply to both | — |
| **GPU Cache** | Bakes the GPU simulation passing through it and plays it back | — |
| **GPU Particles** | A particle source you write: `spawn(p)`, and optionally `update(p, dt)` | Galaxy, Flow, Attractor, Swirl, Fountain, Firefly Swarm, Spark Ball |
| **GPU Surface (SDF)** | A shape from a distance function (`float sdf(vec3 p)`), raymarched | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob |
| **GPU Mesh** | Code run on every vertex of the mesh wired into it (`void deform(inout Vertex v)`) | Wave, Noise Displace, Mesa, Twist |
| **GPU Stage** | One step in a chain: what particles do, how they look, a warp, a mesh deform, or functions for other nodes | see below |
| **Code Shape** | A model built from a parametric description; real geometry straight away | Desk Lamp, Vase |

The GPU Stage starters, by section:
- **Particle stages** (what particles do): Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field,
  Collide with Shape
- **Particle looks** (how they're drawn live): Glow Look, Firefly Look, Streak Look (trails),
  Material Look (a Blender material, lit by the scene)
- **Warps** (particles and meshes alike): Bend, Taper
- **Mesh stages**: Ripple, Sway by Field
- **Functions** for other nodes: Wind Field
- **Lighting**: Scene Lights

3D View › Add › Mesh makes the same nodes on a new object.

## Writing a node: its sockets come from its code

```glsl
// Push by Field: a force from a function wired into 'field'
// @in  func  vec3 field(vec3 p)        a function input: wire a function in, then call field(p)
// @in  float amount 1.0 0.0 10.0       a slider (min and max are optional)
// @in  int   steps 2 1 8               a whole number
// @in  color tint 1.0 0.6 0.2          a colour
// @out attr  heat 0.0                  a per-particle value later nodes can read and write
// @out func  force                     your function `force` becomes an output socket
void behave(inout Particle p, float dt) {
  p.velocity += field(p.position) * amount * dt;
  p.heat = length(p.velocity);
}
vec3 force(vec3 q) { return -q * amount; }
```

That node gets these inputs: a Particles stream, `field` (a function socket), `amount`, `steps`,
`tint`. It gets these outputs: the Particles stream, `heat` (a field), and `force` (a function
socket). The sockets change as soon as you edit the code. `// @param name default min max` still
works: it means `@in float`.

**Which streams a node has** follows from the functions it defines:

| The code defines | The node | What it's for |
|---|---|---|
| `void spawn(inout Particle p)` | Particles out | a source: where particles are born |
| `void update(inout Particle p, float dt)` | (on a source) | the source moves its own particles; optional |
| `void born(inout Particle p)` | Particles in and out | runs once when a particle is (re)born, e.g. to set an attribute |
| `void behave(inout Particle p, float dt)` | Particles in and out | every step: change the velocity; the chain moves the particle |
| `vec4 look(Particle p)` | Particles in and out | how it's drawn live: colour and alpha |
| `vec3 warp(vec3 q)` | Particles **and** Mesh in and out | where things are *shown*: bends particles and meshes alike |
| `void deform(inout Vertex v)` | Mesh in and out | change each vertex of a mesh |
| `float sdf(vec3 p)` | an `sdf` function output | a GPU Surface; wire it into Collide with Shape |

**Declarations:**
- `@in float|int name default [min max]`: a slider or whole number.
- `@in color name r g b`: a colour socket (inside the code it's a `vec3`).
- `@in func RET name(ARGS) [= value]`: a function input. Wire another node's function output into
  it, then call it by name. With nothing wired in it returns zero, or the value after `=`.
- `@out func name`: one of your functions becomes an output socket.
- `@out attr name [default]`: a per-particle value. Up to four per chain. Every later node can read
  and write `p.name`. It's also a field output on the node, and To Geometry writes it as an
  attribute, so ordinary nodes can use it after conversion.
- `@shape point|glow|firefly`: how a Look node draws each particle.

**Names don't clash.** Each node's functions, constants and sliders get a per-node prefix, so two
nodes can both have a slider called `strength` or a helper called `hash()`. Attributes are the
exception: they're shared across the chain by name, on purpose. Compile errors name the node and the
line in the code you wrote (`node 'Wander', line 4: undefined variable "gravty"`).

## Chains and graphs

```
                                              ┌─> [Firefly Look]   heads   ┐  one GPU simulation,
[Firefly Swarm] -> [Wander] -> [Push by Field] -> [Blink]                   │  two looks
                                   ^ field    └─> [Streak Look]    trails  ┘
                              [Wind Field] ──> field of [Sway by Field] <- grass mesh -> To Mesh
[Spark Ball] ─┐
              ├─> [Join Particles] -> [Vortex] -> [Glow Look]
[Swirl] ──────┘
```

- **Particles** travel on Particles sockets (a Bundle socket, shown in its own colour). Per step, the
  source's `update` runs, then every stage's `behave` in the order they're wired; if the source has no
  `update`, the chain then moves each particle by its velocity. `born` runs after `spawn`. The last
  `look` decides how particles are drawn. Every `warp` runs in order where particles are shown and
  made real, never where they're simulated: straighten a Bend and the swirl is exactly as before.
- **Meshes** travel on ordinary Geometry sockets. A GPU Mesh node (or a stage with `deform` or `warp`
  wired straight to ordinary geometry) heads a mesh chain; each later stage's `deform` and `warp` run
  in order.
- **Functions** travel on Closure sockets. One function output can feed any number of nodes, in any
  chains of the same tree; each program includes it once, with its own prefix.

**How a graph is evaluated.** CodeNodes turns the graph into *pipelines*: every path from a source to
where a stream ends (a node nothing continues from, drawn live, or a node wired into To Geometry, made
real there). Each pipeline is one GPU program.

- **Branches.** An output wired into several stages splits the stream: each branch goes its own way.
  Branches that differ only in looks and warps **share one simulation** (the same source and born /
  behave stages, functions and attributes): the particles are stepped once and drawn once per
  branch. A branch with its own behaviour after the split runs its own copy of the simulation, which
  starts identical and then diverges. The first path keeps the source node's own object; each extra
  branch gets a hidden object in the CodeNodes Sources collection (named `source › last stage`).
- **Merging.** Join Particles takes two streams. Everything wired after it runs on both (each source
  keeps its own simulation), they're drawn together, and a To Geometry after it outputs both.
- **To Geometry partway along.** It outputs the stream as it is at that point; the rest of the chain
  still draws live.
- **Colours on the sockets:** Particles (bundle), Mesh or Geometry (geometry), functions (closure),
  attributes (float field). Blender won't wire a Particles output into a geometry input, so particles
  reach the rest of your tree only through To Geometry.

## Everything on the node

- **✎ Edit Code** (the first input) works as a button. Switch it on and the code opens in a pop-up
  Text Editor window; the toggle switches itself back off. Double-clicking a code node, or pressing
  Ctrl+E in the Node Editor, does the same. The node recompiles as you type.
- **Template** is a dropdown. Picking another loads that starter. If you had edited the code, your
  version is kept in a backup text named "… (before *template*)".
- **The code's own inputs** are sockets you can type into or wire up.
- **Settings** are inputs too, in closed panels: Count and Emit From on sources; Look (colour mode,
  colours, Glow, point size, brightness); Simulation (substeps, pre-warm, stagger); Bounds for surfaces.
- **The header** shows the status: `Galaxy · live · 1.0M`, `Wander · in chain`, `Castle · real`, or
  `⚠` and the error.
- **Tab into the node** and one frame shows the code, another the full status or error.

**To Geometry's inputs:**
- **Particles** (after a particle chain) or **Geometry** (after a surface or mesh chain): the input
  follows what's wired into it.
- **When**, a dropdown:
  - Automatic: every frame for particles and code that reads the time; when something changes
    otherwise
  - Every Frame
  - When Changed
  - Only for Render: stays live in the viewport, and is converted for Render with CodeNodes
- **Resolution** for surfaces, or **Max Points** for particles.
- **Keep Velocity** and **Keep Age** pick which particle attributes to write. The chain's own
  attributes (`brightness`, `phase`, …) are always written.

Its header says what it outputs and what that costs, for example `To Points · 5 ms` or
`To Mesh · 210 ms`. It used to be called Make Real; older files are renamed when they open.

## Live, and converted

| | A GPU chain on its own | With To Geometry after it |
|---|---|---|
| Shown by | Drawn straight from GPU memory into the 3D viewport, depth-tested against your objects: points, glowing sprites, or fireflies with flapping wings | Real points (`velocity`, `speed`, `age`, `life`, and the chain's attributes) or a real mesh (with `color` and `value` from GPU Mesh) |
| Speed | 1M particles at about 190 fps; the five-node firefly chain at about 150; a bent galaxy at about 140 (RTX A4500) | What copying into Blender costs: 2,500 fireflies in about 5 ms; the Castle at resolution 192 in about 210 ms |
| Selectable, visible to later nodes, Blender's F12 | No: a viewport picture | Yes, like after Realize Instances |

**Where a mesh chain gets its mesh:** Blender can't hand a node's incoming geometry to Python. So
CodeNodes keeps a hidden "tap": an object whose Geometry Nodes are a trimmed copy of your tree,
ending at the chain's Mesh input. It's kept in step with your tree automatically.

**The helpers are out of the way:** the objects behind code nodes (and taps) live in the "CodeNodes
Sources" collection, which is excluded from the view layer. Object Info still reads them.

## Rendering

The GPU must never run while Blender's renderer works: that crashes Blender. So:

1. **Render › Render Image / Render Animation with CodeNodes.** For each frame, on Blender's main
   thread:
   1. Step every GPU chain to that frame.
   2. Convert it. Chains without To Geometry are converted just for the render, then go back to live.
   3. Render that one frame and wait for it.

   The GPU and the renderer take turns, so no bake is needed. The tests count GPU work during
   rendering, and it stays at 0.
2. **Blender's own F12 / Ctrl+F12** render what is real: chains with To Geometry, and baked caches.
   CodeNodes refuses all GPU work while any render runs. Live-only chains don't appear there.

The live glow is a viewport drawing, so a render gets what your nodes build from the converted
points. The demo places a small firefly model on each point, flaps its wings from `phase` and the
time, lights its abdomen from `brightness`, and adds bloom in the compositor.

Background Blender (command line, render farms) has no GPU access for add-ons, so bake first. The
farm then renders the bake.

## Baking

- **Blender's Bake node after To Geometry** works like on any geometry: bake a range (Animation
  mode), scrub it, render it with F12, with the code nodes switched off or the add-on gone. CodeNodes
  converts each frame before Blender evaluates it (a frame_change_pre handler), which is what the
  Bake node captures. In a script, refresh the object (`obj.update_tag()`) after baking; Blender's UI
  does that itself.
- **GPU Cache** bakes a live simulation itself, before any To Geometry:
  - **Mode**: Live (simulate on the GPU every frame) or Cached (play the baked frames).
  - **Start / End**: the frames to bake. Outside them, the nearest baked frame shows.
  - **⟳ Bake Now** and **✕ Clear** are toggles that act as buttons.
  - The header says what's there: `Cached 1–48 · 13.0 MB`.
  - What's cached is the simulation of every pipeline passing through the node (its source and
    born/behave stages); looks and warps after it stay live, so you can restyle a cached simulation.
  - Cached frames play back when scrubbing, feed To Geometry and Render with CodeNodes, and are stored
    next to the .blend in `codenodes_cache/<source>/` (until the file is saved, in Blender's temp folder).
  - The format is per frame: position, velocity, age, life and each attribute as 16-bit floats,
    zlib-compressed. Measured: 20,000 particles × 48 frames = 13.0 MB (14.25 bytes per particle per
    frame), baked in under a second; positions within 1 mm of the live simulation.

## Lighting

- **Scene Lights** (Add › CodeNodes › Lighting) turns the scene's lamps (sun, point, spot, area, up to
  8, brightest first) and world colour into a function, `light(p, n, v, albedo, roughness, metallic)`:
  Lambert diffuse and GGX specular, the main lobes of Principled BSDF, with each lamp's size widening
  its highlight as EEVEE's soft lamps do. The light data are hidden inputs CodeNodes keeps in step with
  the scene (move a lamp and the light moves; nothing recompiles), in the space of the object whose
  tree holds the node. Its inputs: intensity and world.
- **Material Look** takes a Blender material (a Material socket) and brings in its Principled BSDF
  values: base colour, roughness, metallic, emission (colour × strength), alpha. Wire Scene Lights'
  `light` into it and particles are shaded by the scene's lamps; wire its `material` output into a GPU
  Surface.
- **GPU Surfaces** have **Lights** and **Material** inputs. With Scene Lights and a Material Look wired
  in, the raymarched surface is lit by the scene's lamps with that material (soft self-shadows for the
  two brightest lamps) instead of its built-in sun and sky. Side by side with the same surface made
  real and lit by EEVEE, the two look close (see `docs/lit_compare.jpg`).
- **Any node can declare** `// @in material mat` (a Material socket; its values arrive as `mat_base`,
  `mat_roughness`, `mat_metallic`, `mat_emit`, `mat_alpha`) and `// @in hidden name` (a value the
  add-on fills in, with no socket).

## Feeding it from Geometry Nodes

- **Values:** a typed value, a Value or Integer node, a reroute, or the modifier's own input.
  Values computed by other nodes can't be read from Python, so the value typed on the socket is used.
- **Geometry for particles:** the source's **Emit From** object input. CodeNodes reads that object's
  evaluated geometry (points, or a surface sampled evenly by area), including what its own Geometry
  Nodes make, and re-reads it when the object changes. Use `emitPoint(seed)` and `emitNormal(seed)`
  in `spawn`.
- **Geometry for mesh chains:** anything wired into the first node's Mesh input, through the tap.

## Starter library: proposals to choose from

Only the starters the demos need are built (since 0.4 also Streak Look, Material Look, Sway by Field
and Scene Lights). Here are proposals for the rest, particles and meshes separately, for you to pick
from:

**Particles**
- Sources: Emit from Curve, Burst (all at once, on a trigger frame), Grid, Inside Volume
  (fill an SDF), From Points (one particle per point of a Geometry Nodes point cloud)
- Behaviours: Attract to Point, Orbit, Flock (align, cohere, separate, via a spatial hash), Turbulence
  (fBm), Collide with Ground, Stick to Surface, Age-based Colour, Size over Life, Kill Outside Bounds,
  Follow Curve, Springs to Rest Position (for jiggly "made of particles" objects)
- Looks: Trails (fading history, beyond Streak Look's motion streak), Sparks, Smoke Puff (soft,
  growing, fading), Dust Motes (depth-of-field-like softness), Colour Ramp by Speed
- Effects on the whole system: Bloom Strength, Density Tint, Fade by Distance

**Meshes**
- Deformers: Wave, Noise Displace, Twist, Bend, Taper, Inflate, Melt (gravity sag), Explode (pieces fly
  by face), Shrinkwrap to SDF, Lattice-like Cage
- Colour and data: Curvature, Ambient Occlusion (GPU), Height Gradient, Distance to Point, Vertex Noise
  Colour
- Fields for other nodes: Wind, Vortex Field, SDF Primitives (sphere, box, capsule) as function outputs

## Blender versions

Blender 5.0.1 and 5.1.2 both have what this needs: compute shaders, image load/store, Menu, Bundle
and Closure sockets, and frames that show text. The same tests run on both.

## Limits, honestly

- **Live results are an overlay until converted:** not selectable, not in Blender's own F12, not
  visible to later nodes.
- **Branches made real run their own simulation:** live branches share their head's simulation, but
  a branch that To Geometry converts steps its own copy (the same particles, computed again).
- **Join Particles** is for particles; meshes merge with an ordinary Join Geometry after To Mesh.
- **GPU Cache** caches particle simulations; surfaces and mesh chains bake with Blender's Bake node
  after To Geometry.
- **Additive glow isn't tone-mapped** in the Solid viewport: where very many glowing particles pile
  up (a galaxy's core, a fold in a bent galaxy) it can clip to white. Lower Brightness, or use fewer,
  larger particles.
- **Attributes:** up to four per-particle attributes per chain.
- **Glow isn't lighting:** the live glow and halo are drawn, not lit. They don't light your objects,
  and a render gets the look you build from the converted points.
- **Lighting between GPU nodes and Blender objects:** no shadows either way. A GPU Surface without
  Scene Lights lights itself (a sun from the scene's first Sun lamp, sky, soft shadows, ambient
  occlusion, fog). Scene Lights reads lamps in the space of the first object using the tree; lamp
  textures, IES, light linking and material nodes beyond the Principled BSDF's own values are ignored.
  Particles lit by a Material Look are shaded as small spheres facing the camera.
- **Taps and modifiers:** a mesh chain follows your tree through its tap, so a modifier stacked
  before the Geometry Nodes modifier isn't seen.
- **Tested hardware:** NVIDIA with OpenGL only. AMD, Intel, macOS and the Vulkan backend are untested.
  The shaders keep push constants within the 128 bytes Vulkan guarantees.
