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
blender --factory-startup --python tests/test_blender.py       # needs a window: the GPU isn't available with -b (27 checks)
```

Both pass on Blender 5.0.1 and 5.1.2 (NVIDIA RTX A4500, OpenGL).

## Limits right now

- Needs Blender with a window. Background mode (`-b`) has no GPU, so there's no Code → Mesh in
  headless renders yet.
- Only signed distance functions for now. The node editor (code nodes with typed inputs and outputs)
  is next.
- Surface nets rounds off sharp edges and corners slightly.
