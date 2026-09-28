# GPU nodes

Code that runs on the graphics card, sitting in your Geometry Nodes tree like any other node.
This page covers what you see, how it works, and where it stops.

## What you see

**Shift A › CodeNodes** in the Geometry Nodes editor offers:

| Node | What it does | Templates |
|---|---|---|
| **Make Real** | Turns the GPU node before it into real geometry | — |
| **GPU Particles** | Particles moved by code you write (`spawn` / `update`) | Galaxy, Flow, Attractor, Swirl, Fountain |
| **GPU Surface (SDF)** | A shape from a distance function (`float sdf(vec3 p)`), raymarched | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob |
| **GPU Mesh** | Code run on every vertex of the mesh wired into it (`void deform(inout Vertex v)`): deform, displace, recolour | Wave, Noise Displace, Twist |
| **Code Shape** | A model built from a parametric description; real geometry straight away | Desk Lamp, Vase |

3D View › Add › Mesh makes the same nodes on a new object.

**Everything is on the node:**
- **The first input, ✎ Edit Code,** works as a button. Switch it on and the code opens in a pop-up
  Text Editor window, and the toggle switches itself back off. Double-clicking a code node, or
  pressing Ctrl+E in the Node Editor, does the same. The node recompiles as you type.
- **Template** is a dropdown. Picking another loads that template. If you had edited the code, your
  version is kept in a backup text named "… (before *template*)".
- **The code's own sliders:** every `@param` line becomes an input, which you can type into or wire
  up.
- **Settings** are inputs too, in closed panels:
  - particles: Count and Emit From at the top; Look (colour mode, colours, Glow, point size,
    brightness); Simulation (substeps, pre-warm, stagger)
  - surfaces: Look (colour, shadows, ambient occlusion, fog, sky, live resolution) and Bounds
- **The header** shows the status: `Galaxy · live · 1.0M`, `Castle · real`, or `⚠` and the
  error with its line.
- **Tab into the node** and one frame shows the code, another the full status or error.

**Make Real's inputs:**
- **When**, a dropdown:
  - Automatic: every frame for particles and code that reads the time; when something changes
    otherwise
  - Every Frame
  - When Changed
  - Only for Render
- **Resolution** for surfaces, or **Max Points** for particles.
- **Keep Velocity** and **Keep Age**, which pick the particle attributes to write.

Its header shows what making it real costs, for example `Make Real · 5 ms`.

**The sidebar** (N) only keeps what Blender can't put on a node: an Edit Code button, an Add Make Real
button, and the full error text. Blender doesn't let add-ons draw buttons or text boxes on Geometry
Nodes nodes, so the ✎ Edit Code toggle is the closest thing to a button on the node.

## Live, and made real

| | A GPU node on its own | With Make Real after it |
|---|---|---|
| Shown by | Drawn straight from GPU memory into the 3D viewport, depth-tested against your objects | Real points (`velocity`, `speed`, `age`, `life`) or a real mesh (with `color` and `value` from GPU Mesh) |
| Speed | 1M particles at about 190 fps and 4.2M at about 160; the Castle raymarched with a galaxy at about 190 (RTX A4500) | What copying into Blender costs: 20,000 points in 5 ms; the Castle at resolution 192 in about 210 ms |
| Selectable, visible to later nodes, Blender's F12 | No: a viewport picture | Yes, like after Realize Instances |

A GPU node's own Geometry output is empty. Put **Make Real** after it, then Instance on Points, Set
Material, Join and so on work as usual.

**Where GPU Mesh gets its mesh:** Blender can't hand a node's incoming geometry to Python. So CodeNodes
keeps a hidden "tap": an object whose Geometry Nodes are a trimmed copy of your tree, ending at the
GPU Mesh node's Mesh input. It's kept in step with your tree automatically.

## Rendering

The GPU must never run while Blender's renderer works: that crashes Blender. So:

1. **Render › Render Image / Render Animation with CodeNodes.** For each frame, on Blender's main
   thread:
   1. Step every GPU node to that frame.
   2. Make it real. Nodes without Make Real are made real just for the render, then go back to live.
   3. Render that one frame and wait for it.

   The GPU and the renderer take turns, so no bake is needed. Measured at 320×180: an EEVEE image in
   0.4 s, a Cycles image in 0.6 s, three animation frames in 0.9 s. The tests count GPU work during
   rendering, and it stays at 0.
2. **Blender's own F12 / Ctrl+F12** render what is real: nodes with Make Real, and baked caches.
   CodeNodes refuses all GPU work while any render runs. Live-only nodes don't appear there.

Background Blender (command line, render farms) has no GPU access for add-ons, so bake first. The
farm then renders the bake.

## Feeding it from Geometry Nodes

- **Values:** a typed value, a Value or Integer node, a reroute, or the modifier's own input.
  Values computed by other nodes can't be read from Python, so the value typed on the socket is used.
- **Geometry for particles:** the **Emit From** object input. CodeNodes reads that object's evaluated
  geometry (points, or a surface sampled evenly by area), including what its own Geometry Nodes make,
  and re-reads it when the object changes. Use `emitPoint(seed)` and `emitNormal(seed)` in `spawn`.
- **Geometry for GPU Mesh:** anything wired into its Mesh input, through the tap described above.

## Blender versions

Blender 5.0.1 and 5.1.2 both have what this needs: compute shaders, image load/store, Menu sockets
and frames that show text. The same tests run on both.

## Limits, honestly

- Live results are an overlay until made real: not selectable, not in Blender's own F12, not visible
  to later nodes.
- A live surface lights itself: a sun from the scene's first Sun lamp, sky, soft shadows, ambient
  occlusion and fog. Blender's lights and materials don't touch it, and it casts no shadows on your
  objects.
- GPU Mesh follows your tree through its tap, so a modifier stacked before the Geometry Nodes
  modifier isn't seen.
- Tested on NVIDIA with OpenGL. AMD, Intel, macOS and the Vulkan backend are untested (the shaders
  keep push constants within the 128 bytes Vulkan guarantees).
