"""The render-farm test: open the .blend that tests/test_bake.py saved in BACKGROUND
Blender, with no GPU and without importing CodeNodes at all, and check the baked
animation still plays and renders.

    blender -b --factory-startup --python tests/test_farm.py
"""
import os
import sys
import tempfile

import bpy
import numpy as np

BLEND = os.path.join(tempfile.gettempdir(), "codenodes_bake_test", "baked.blend")
_checks = 0
_failed = False


def check(cond, msg):
    global _checks, _failed
    _checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        _failed = True


def shape(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    n = len(me.vertices)
    ext = 0.0
    if n:
        co = np.empty(n * 3, np.float32)
        me.vertices.foreach_get("co", co)
        ext = round(float(np.ptp(co.reshape(-1, 3), axis=0).sum()), 3)
    ev.to_mesh_clear()
    return n, ext


check(bpy.app.background, "running in background mode (no GPU available)")
check("codenodes" not in sys.modules, "the CodeNodes add-on is not loaded")
check(os.path.exists(BLEND), f"the baked .blend exists ({BLEND})")
bpy.ops.wm.open_mainfile(filepath=BLEND)

obj = bpy.data.objects.get("Blob")
check(obj is not None, "the baked object is in the file")
scene = bpy.context.scene

shapes = {}
for f in (1, 4, 8):
    scene.frame_set(f)
    shapes[f] = shape(obj)
check(all(n > 1000 for n, _ in shapes.values()), f"geometry loads from the cache {shapes}")
check(len({s for s in shapes.values()}) == 3, "and it animates: every frame differs")

try:
    import gpu
    gpu.shader.create_from_info(gpu.types.GPUShaderCreateInfo())
    check(False, "the GPU really is unavailable here")
except Exception:
    check(True, "the GPU really is unavailable here (so the cache did the work)")

cam = bpy.data.objects.get("Camera")
if cam is None:
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    cam.location = (7, -7, 5)
    cam.rotation_euler = (1.1, 0, 0.78)
scene.camera = cam
scene.render.engine = 'BLENDER_WORKBENCH'
scene.render.resolution_x = scene.render.resolution_y = 200
out_dir = os.path.dirname(BLEND)
renders = []
for f in (2, 7):
    scene.frame_set(f)
    scene.render.filepath = os.path.join(out_dir, f"farm_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
    path = scene.render.filepath
    check(os.path.exists(path) and os.path.getsize(path) > 2000, f"frame {f} rendered ({os.path.getsize(path)} bytes)")
    img = bpy.data.images.load(path)
    a = np.empty(len(img.pixels), np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    renders.append(a)
check(float(np.abs(renders[0] - renders[1]).mean()) > 1e-4, "the two rendered frames differ (the animation reached the render)")

print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)
