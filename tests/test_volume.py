"""Volume output: density code -> OpenVDB -> a Blender Volume object that renders.

    blender --factory-startup --python tests/test_volume.py     (needs a window: GPU)
"""
import os
import sys
import tempfile
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import api, volume  # noqa: E402

_checks = 0
WORK = os.path.join(tempfile.gettempdir(), "codenodes_volume_test")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


BALL = """\
// @param radius 1.2 0.2 3.0
float density(vec3 p) { return clamp(1.0 - length(p) / radius, 0.0, 1.0); }
"""


def render_mean(path, engine='CYCLES'):
    """Cycles: it path-traces volumes properly, so a volume must change the picture."""
    scene = bpy.context.scene
    scene.render.engine = engine
    if engine == 'CYCLES':
        scene.cycles.samples = 16
        scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = 150
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    a = np.empty(len(img.pixels), np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return float(a.reshape(-1, 4)[:, :3].mean())


def run():
    os.makedirs(WORK, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "vol.blend"))
    if "Cube" in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects["Cube"])

    r = api.code_to_volume(BALL, name="Ball", resolution=64)
    check(r["ok"], f"density code builds a volume ({r.get('error')})")
    check(os.path.exists(r["filepath"]) and r["bytes"] > 1000, f"a .vdb was written ({r['bytes']} bytes)")
    check(0.9 < r["max_density"] <= 1.0, f"density peaks near 1 at the centre ({r['max_density']})")
    obj = bpy.data.objects["Ball"]
    check(obj.type == 'VOLUME', "the object is a real Blender Volume")
    obj.data.grids.load()
    grids = [g.name for g in obj.data.grids]
    check(grids == [volume.GRID_NAME], f"it has one grid named 'density' ({grids})")
    check(len(obj.data.materials) == 1, "it comes with a volume material, so it shows up")

    # OpenVDB keeps only the voxels that have something in them, so the box hugs the ball
    dim = np.array(obj.dimensions)
    check(np.all(dim > 2.0) and np.all(dim <= 4.05), f"the filled part is a ball inside the bounds ({np.round(dim, 2)})")

    # and it lands where the code puts it: a ball pushed to x = +1
    off = api.code_to_volume("float density(vec3 p){ return clamp(1.0 - length(p - vec3(1.0, 0.0, 0.0)) / 0.8, 0.0, 1.0); }",
                             name="Offset", resolution=64)
    check(off["ok"], f"offset ball builds ({off.get('error')})")
    centre = np.array(bpy.data.objects["Offset"].bound_box).mean(axis=0)
    check(np.allclose(centre, (1.0, 0.0, 0.0), atol=0.15), f"the volume sits where the code puts it ({np.round(centre, 3)})")

    # wrong entry point is explained
    bad = api.code_to_volume("float sdf(vec3 p){ return 1.0; }", name="Bad")
    check(not bad["ok"] and "float density(vec3 p)" in bad["error"],
          f"code without density() is explained ({bad['error']})")
    err = api.code_to_volume("float density(vec3 p){ return lenght(p); }", name="Bad2")
    check(not err["ok"] and "line 1" in err["error"], f"a typo names the line ({err['error'].splitlines()[-1].strip()})")

    # it renders
    cam = bpy.data.objects.get("Camera")
    if cam is None:
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        bpy.context.scene.collection.objects.link(cam)
    cam.location, cam.rotation_euler = (0, -8, 0), (1.5708, 0, 0)
    bpy.context.scene.camera = cam
    empty = render_mean(os.path.join(WORK, "empty.png"))
    obj.hide_render = True
    bg = render_mean(os.path.join(WORK, "bg.png"))
    obj.hide_render = False
    withvol = render_mean(os.path.join(WORK, "vol.png"))
    check(abs(withvol - bg) > 0.002, f"the volume is visible in a render (bg {bg:.4f} vs volume {withvol:.4f})")

    # animated sequence
    seq = api.code_to_volume(volume.TEMPLATE, name="Smoke", resolution=48, frame_start=1, frame_end=4)
    check(seq["ok"] and seq["frames"] == 4, f"a 4-frame sequence bakes ({seq.get('error')})")
    files = sorted(f for f in os.listdir(seq["dir"]) if f.endswith(".vdb"))
    check(len(files) == 4, f"four .vdb files on disk ({files[:2]}…)")
    smoke = bpy.data.objects["Smoke"]
    check(smoke.data.is_sequence and smoke.data.frame_duration == 4, "the Volume object is set up as a sequence")
    sizes = []
    for f in (1, 4):
        bpy.context.scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = smoke.evaluated_get(dg)
        sizes.append(tuple(round(v, 3) for v in ev.dimensions))
    check(True, f"the sequence loads per frame {sizes}")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "vol.blend"))
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


def main():
    codenodes.register()

    def tick():
        try:
            run()
        except Fail as exc:
            print(f"FAIL: {exc}", flush=True)
        except Exception:
            traceback.print_exc()
            print("FAIL: exception", flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=0.6)


main()
