# Code → Mesh, Volume and Particles

Besides code nodes, CodeNodes turns code straight into geometry on an object: from the Add menu,
from a script, or from an assistant. This page was part of the README until November 2026.

## Code → Mesh

Write a signed distance function (negative inside, positive outside). CodeNodes samples it on the
GPU and builds a real, watertight quad mesh.

```glsl
// @param radius 1.0 0.2 2.0
// @param thickness 0.3 0.05 0.8
float sdf(vec3 p) {
  return smin(sdTorus(p, radius, thickness), sdSphere(p, 0.6), 0.4);
}
```

Each `// @param name default min max` line becomes a slider. Your slider values are kept when the
code changes, so an assistant can rewrite the code without resetting what you tuned. **Live**
rebuilds as you edit; **Animate** rebuilds every frame, with the time in `uTime`.

## Code → Volume

Write a density instead of a distance and get smoke, cloud or nebula geometry that EEVEE and Cycles
render natively:

```glsl
// @param scale 1.6 0.2 6.0
float density(vec3 p) {
  return clamp(1.0 - length(p) / 1.6 + 0.5 * (fbm3(p * scale) - 0.55), 0.0, 1.0);
}
```

```python
api.code_to_volume(source, name="Smoke", resolution=96)                    # one frame
api.code_to_volume(source, name="Smoke", frame_start=1, frame_end=48)      # a .vdb sequence
```

Volumes are written as OpenVDB files, so a sequence plays back natively with no add-on and no GPU.

## Code → Particles

Write the solver yourself. `spawn` places a particle, `update` moves it, and CodeNodes keeps the
state on the GPU and steps it as the frame changes:

```glsl
// @param speed 1.0 0.0 4.0
void spawn(inout Particle p) {
  p.position = randBall(p.seed) * 1.8;
  p.life = 4.0 + rand1(p.seed * 3.3) * 3.0;      // then it respawns
}
void update(inout Particle p, float dt) {
  p.velocity = vec3(-p.position.y, p.position.x, 0.2) * speed;  // your own field: a swirl
  p.position += p.velocity * dt;
}
```

`Particle` carries `position`, `velocity`, `age`, `life` and a per-particle `seed`. The points come
out as a real point cloud with `velocity`, `speed`, `age` and `life` attributes, so Geometry Nodes
can instance anything onto them and Cycles uses `velocity` for motion blur. (The Add › Code
Particles template shows a curl-noise field written the same way.) Shade with **`speed`**, not
`velocity`: Blender reserves that name for motion blur and a shader can't read it back.
Add › Mesh › **Code Particles**, or `api.code_to_particles(source, name="Swirl", count=20000)`.

300,000 particles simulate in about 12 ms per frame on an RTX A4500.

## From a script

```python
from bl_ext.user_default.codenodes import api      # the extension's module path
api.code_to_mesh(source, name="Ring", resolution=128)
# {"ok": True, "faces": 18432, ...}, or {"ok": False, "error": "line 3: undefined variable 'lenght'"}
api.set_params("Ring", radius=1.4)
print(api.reference())                             # the helper functions available in GLSL
```

Errors never raise. They come back with the line number in *your* code, so a caller can fix the code
and try again.

## Node editor

Open a Node Editor and switch its type to **CodeNodes** (or View3D › Sidebar › CodeNodes ›
**New Node Graph** for a starter graph).

| node | does |
|---|---|
| **SDF Code** | code in a Text block; each `@param` line becomes an input socket |
| **Combine** | Union, Subtract, Intersect, Smooth Union, Smooth Subtract (blend width K) |
| **Transform** | move, rotate, scale a shape |
| **Offset** | grow or shrink a shape |
| **Mesh Output** | Code → Mesh: target object, resolution, bounds, Live, Animate |
| **Particles** | a solver in code; its `@param` lines become input sockets |
| **Points Output** | simulates and shows the points on an object, with the same Live / Animate / Bake |
| **Shape** | a parametric description; its `param` lines become input sockets |

The whole graph compiles into one GPU program. Each code node's functions and parameters get a
per-node prefix, so two nodes can both define `bump()`. A compile error names the node and the line
inside it, and that node shows the error too. Reroutes and muted nodes pass shapes through, and
graphs can have up to 256 sliders.

## Animation, and making it render

There are three phases.

1. **Live.** Turn on **Animate** and the mesh rebuilds on every frame change, using `uTime`. This is
   for working, not for rendering: running GPU code from a frame handler during an F12 render crashes
   Blender, and background Blender has no GPU at all.
2. **Baked.** Press **Bake to Disk**, pick a frame range, and CodeNodes writes one file per frame next
   to your .blend and builds a small node group (Scene Time → Format String → Import PLY) that plays
   it back. From then on it's ordinary Geometry Nodes geometry: it renders in F12, on a machine with no
   GPU and without this add-on. **Remove Cache** goes back to live.
3. **Bake to Nodes (experimental).** The code itself becomes a Geometry Nodes network: no files, no
   GPU, evaluated natively every frame, sliders on the modifier. CodeNodes translates the GLSL into
   the language of [ExpressNode](https://github.com/quacktheplanet/ExpressNode) (a separate add-on), which builds the node group;
   a Volume Cube (density = −sdf) and Volume to Mesh make the surface. Tested against the GPU mesh from
   the same code: the same vertices, after a slider change and on an animated frame. About 8 ms per
   rebuild at resolution 96.

   What translates: local variables, maths, `?:`, your own helper functions and CodeNodes' distance
   helpers (`sdSphere`, `sdBox`, `smin`, `rotateZ`…), `uTime`/`uFrame`. Loops, `if` statements and
   `noise3`/`fbm3` don't yet: the button says which line and suggests Bake to Disk.

```python
api.bake("Blob", 1, 48)     # to disk
api.bake_to_nodes("Ring")   # to nodes: {"ok": True, "sliders": [...]} or {"ok": False, "error": "line 9: a loop...", "fallback": "bake"}
```

## What keeps it from crashing

- A shader that doesn't compile is a message, not a crash. The driver's log is captured and mapped
  back to your line numbers.
- The GPU only samples the grid; the meshing is pure numpy. One slice is timed first, and code that
  would take too long is refused before it can stall the GPU. Work runs in short slabs.
- Resolution (8–512), memory (1 GiB) and texture size are checked before anything runs.
- A new mesh is built on the side and swapped in only when everything succeeded, so a failed build
  leaves the object as it was. Materials and modifiers carry over, and old mesh data is freed.
- Animate is skipped while a render job runs.
- Rebuilding a node (a new template, a code edit that changes its sockets) never keeps Python
  references to Blender data across the rebuild: users are saved and re-found by name. And before any
  error is printed, Blender structs are taken off it, because Python's "Did you mean …?" suggestion
  calls dir() on them, which crashes Blender when the struct was just freed (this was a real crash on
  switching templates).
