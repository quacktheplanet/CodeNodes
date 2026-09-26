# CodeNodes

GPU code inside Blender. You (or Claude) write a small piece of GLSL, and CodeNodes runs it on the GPU
and turns the result into something Blender can use.

**First piece: Code → Mesh.** Write a signed distance function (negative inside, positive outside),
and CodeNodes samples it on the GPU and builds a real, watertight quad mesh. You can light it, render
it with EEVEE or Cycles, sculpt it, add modifiers or export it.

```glsl
// @param radius 1.0 0.2 2.0
// @param thickness 0.3 0.05 0.8
float sdf(vec3 p) {
  return smin(sdTorus(p, radius, thickness), sdSphere(p, 0.6), 0.4);
}
```

Each `// @param name default min max` line becomes a slider. Your slider values are kept when the code
changes, so Claude can rewrite the code without resetting what you tuned.

**Code → Volume.** Write a density instead of a distance and get smoke, cloud or nebula geometry that
EEVEE and Cycles render natively:

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

**Code → Particles.** Write the solver yourself. `spawn` places a particle, `update` moves it, and
CodeNodes keeps the state on the GPU and steps it as the frame changes:

```glsl
// @param speed 1.0 0.0 4.0
void spawn(inout Particle p) {
  p.position = randBall(p.seed) * 1.8;
  p.life = 4.0 + rand1(p.seed * 3.3) * 3.0;      // then it respawns
}
void update(inout Particle p, float dt) {
  p.velocity = curl(p.position) * speed;          // your own field
  p.position += p.velocity * dt;
}
```

`Particle` carries `position`, `velocity`, `age`, `life` and a per-particle `seed`. The points come
out as a real point cloud with `velocity`, `age` and `life` attributes, so Geometry Nodes can
instance anything onto them and Cycles gets velocity for motion blur. Add › Mesh › **Code Particles**,
or `api.code_to_particles(source, name="Swirl", count=20000)`.

## Use it

- **In Blender:** Add › Mesh › **Code Mesh**, then View3D › Sidebar (N) › **CodeNodes**. The code
  lives in a Text block; edit it and the mesh rebuilds (Live). **Animate** rebuilds every frame, with
  the time in `uTime`.
- **From a script or Claude** (through any Blender MCP that runs Python):

  ```python
  from codenodes import api          # installed as an extension: from bl_ext.user_default.codenodes import api
  api.code_to_mesh(source, name="Ring", resolution=128)
  # {"ok": True, "faces": 18432, ...}, or {"ok": False, "error": "line 3: undefined variable 'lenght'"}
  api.set_params("Ring", radius=1.4)
  print(api.reference())             # the helper functions, for Claude
  ```

  Errors never raise. They come back with the line number in *your* code, so a caller can fix the
  code and try again.

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

The whole graph compiles into one GPU program (`codenodes/graph.py`, pure Python). Each code node's
functions and parameters get a per-node prefix, so two nodes can both define `bump()`. A compile error
names the node and the line inside it, and that node shows the error too. Reroutes and muted nodes pass
shapes through, and graphs can have up to 256 sliders.

## Animation, and making it render

There are three phases; the first two are built (see `docs/ROADMAP.md`).

1. **Live.** Turn on **Animate** and the mesh rebuilds on every frame change, using `uTime`. This is
   for working, not for rendering: running GPU code from a frame handler during an F12 render crashes
   Blender, and background Blender has no GPU at all.
2. **Baked.** Press **Bake to Disk**, pick a frame range, and CodeNodes writes one file per frame next
   to your .blend and builds a small node group — Scene Time → Format String → Import PLY — that plays
   it back. From then on it is ordinary Geometry Nodes geometry: it renders in F12, it renders on a
   machine with no GPU and without this add-on, and every other Geometry Nodes node can work on it.
   **Remove Cache** goes back to live.
3. **Bake to nodes** (planned): compile the maths into a real Geometry Nodes network, so there are no
   files at all.

```python
api.bake("Blob", 1, 48)     # same thing from a script or from Claude
```

## What keeps it from crashing

- A shader that doesn't compile is a message, not a crash. The driver's log is captured and mapped
  back to your line numbers.
- The GPU only samples the grid; the meshing is pure numpy. One slice is timed first, and code that
  would take too long is refused before it can stall the GPU. Work runs in short slabs.
- Resolution (8–512), memory (1 GiB) and texture size are checked before anything runs.
- A new mesh is built on the side and swapped in only when everything succeeded, so a failed build
  leaves the object as it was. Materials and modifiers carry over, and old mesh data is freed.
- Animate is skipped while a render job runs. Baking frames for final renders is still to come.

## Tests

```bash
python tests/test_mesher.py                                    # mesher, no Blender (17 checks)
python tests/test_graph.py                                     # graph compiler, no Blender (16 checks)
blender --factory-startup --python tests/test_blender.py       # needs a window: the GPU isn't available with -b (27)
blender --factory-startup --python tests/test_nodes.py         # node editor, incl. save/reload (20)
blender --factory-startup --python tests/test_bake.py          # baking, and playback through stock nodes (18)
blender -b --factory-startup --python tests/test_farm.py       # the bake renders with no GPU and no add-on (10)
blender --factory-startup --python tests/test_volume.py        # density code -> OpenVDB -> Volume object (16)
blender --factory-startup --python tests/test_particles.py     # GPU particle solver, incl. baking (21)
```

Run `test_bake.py` before `test_farm.py`: the first saves the .blend the second opens.
All of them pass on Blender 5.0.1 and 5.1.2 (NVIDIA RTX A4500, OpenGL).

## Limits right now

- Needs Blender with a window. Background mode (`-b`) has no GPU, so there's no Code → Mesh in
  headless renders yet.
- The node editor handles shapes only so far. Volumes are available through `api.code_to_volume`
  but have no node or panel yet. Particles, float links between nodes and the live GPU viewport
  preview are next — see `docs/ROADMAP.md`.
- Surface nets rounds off sharp edges and corners slightly.
