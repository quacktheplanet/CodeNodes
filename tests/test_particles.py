"""Code Particles: a GPU solver you write, stepped per frame, baked for rendering.

    blender --factory-startup --python tests/test_particles.py      (needs a window: GPU)
"""
import os
import shutil
import sys
import tempfile
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import api, cache, particles  # noqa: E402

_checks = 0
WORK = os.path.join(tempfile.gettempdir(), "codenodes_particles_test")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def positions(obj):
    me = obj.data
    a = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def render_mean(path):
    """Average brightness of a small Cycles render of the current frame."""
    scene = bpy.context.scene
    cam = bpy.data.objects.get("Camera")
    if cam is None:
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        scene.collection.objects.link(cam)
        cam.location, cam.rotation_euler = (0, -8, 1), (1.45, 0, 0)
    scene.camera = cam
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 12
    scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = 160
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    a = np.empty(len(img.pixels), np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return float(a.reshape(-1, 4)[:, :3].mean())


def evaluated_points(obj):
    """How many points Blender actually ends up with, after the modifiers."""
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    geo = ev.evaluated_geometry()
    pc = geo.pointcloud
    return len(pc.points) if pc else 0, [a.name for a in pc.attributes] if pc else []


FALL = """\
// @param gravity 9.8 0.0 30.0
void spawn(inout Particle p) {
  p.position = vec3(rand1(p.seed) * 2.0 - 1.0, rand1(p.seed * 3.7) * 2.0 - 1.0, 2.0);
  p.velocity = vec3(0.0);
  p.life = 1000.0;          // never respawn, so the test can predict where they are
}
void update(inout Particle p, float dt) {
  p.velocity.z -= gravity * dt;
  p.position += p.velocity * dt;
}
"""


def run():
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "particles.blend"))
    scene = bpy.context.scene
    scene.frame_start = 1
    if "Cube" in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects["Cube"])

    # --- it builds ------------------------------------------------------------------
    r = api.code_to_particles(particles.TEMPLATE, name="Swirl", count=20000)
    check(r["ok"], f"the curl-flow template builds ({r.get('error')})")
    obj = bpy.data.objects["Swirl"]
    check(len(obj.data.vertices) == 20000, f"20,000 points exist ({len(obj.data.vertices):,})")
    names = {a.name for a in obj.data.attributes}
    check({"velocity", "speed", "age", "life"} <= names,
          f"velocity, speed, age and life are point attributes ({sorted(n for n in names if not n.startswith('.'))})")
    n_points, attrs = evaluated_points(obj)
    check(n_points == 20000, f"Blender evaluates them as a real point cloud ({n_points:,} points)")
    check(r["params"] and "speed" in r["params"], f"@param sliders came through ({list(r['params'])})")

    # --- the simulation actually runs -------------------------------------------------
    scene.frame_set(1)
    at1 = positions(obj).copy()
    scene.frame_set(12)
    at12 = positions(obj).copy()
    moved = np.linalg.norm(at12 - at1, axis=1)
    check(moved.mean() > 0.05, f"particles move over 11 frames (mean {moved.mean():.3f})")
    check(np.isfinite(at12).all(), "no particle went to NaN or infinity")

    # --- physics we can predict -------------------------------------------------------
    p = api.code_to_particles(FALL, name="Fall", count=4096, params={"gravity": 10.0})
    check(p["ok"], f"a falling-particles system builds ({p.get('error')})")
    fall = bpy.data.objects["Fall"]
    scene.frame_set(1)
    z0 = positions(fall)[:, 2].mean()
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    frames = 24
    scene.frame_set(1 + frames)
    z1 = positions(fall)[:, 2].mean()
    t = frames / fps
    # semi-implicit Euler with n steps of dt: drop = g*dt^2 * n(n+1)/2
    dt = 1.0 / fps
    expected = 10.0 * dt * dt * frames * (frames + 1) / 2
    check(abs((z0 - z1) - expected) < 0.02,
          f"gravity matches the maths after {t:.2f}s: fell {z0 - z1:.3f}, expected {expected:.3f}")

    # --- scrubbing --------------------------------------------------------------------
    scene.frame_set(1 + frames)
    forward = positions(fall).copy()
    scene.frame_set(3)                       # jump back: restarts and replays
    scene.frame_set(1 + frames)              # and forward again
    again = positions(fall).copy()
    check(np.abs(forward - again).max() < 1e-4, "scrubbing back and forth lands on the same state")
    scene.frame_set(1)
    check(abs(positions(fall)[:, 2].mean() - z0) < 1e-5, "returning to the start frame resets cleanly")

    # --- errors -----------------------------------------------------------------------
    bad = api.code_to_particles("void spawn(inout Particle p) { p.life = 1.0; }", name="Bad")
    check(not bad["ok"] and "void update(inout Particle p, float dt)" in bad["error"],
          "a missing update() is explained")
    typo = api.code_to_particles(FALL.replace("gravity * dt", "gravty * dt"), name="Bad2")
    check(not typo["ok"] and "line 8" in typo["error"] and "gravty" in typo["error"],
          f"a typo names the line it is on ({typo['error'].splitlines()[-2].strip()})")

    # --- counts -----------------------------------------------------------------------
    small = api.code_to_particles(FALL, name="Few", count=500)
    check(small["ok"] and len(bpy.data.objects["Few"].data.vertices) == 500, "a different count works")
    huge = api.code_to_particles(FALL, name="Huge", count=5_000_000)
    check(not huge["ok"] and "between 1 and" in huge["error"], "an absurd count is refused")

    # --- the material reaches the points (Mesh to Points drops it on its own) -----------
    for other in ("Fall", "Few"):
        bpy.data.objects[other].hide_render = True
    scene.frame_set(20)
    before = render_mean(os.path.join(WORK, "no_mat.png"))
    mat = bpy.data.materials.new("Glow")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 15.0
    em.inputs["Color"].default_value = (1.0, 0.4, 0.1, 1.0)
    nt.links.new(em.outputs[0], nt.nodes.new("ShaderNodeOutputMaterial").inputs["Surface"])
    obj.data.materials.append(mat)
    scene.frame_set(21)                                   # a rebuild pushes it into the modifier
    scene.frame_set(20)
    after = render_mean(os.path.join(WORK, "with_mat.png"))
    check(after > before * 1.3, f"the material reaches the points: render brightened {before:.4f} -> {after:.4f}")

    # --- baking -----------------------------------------------------------------------
    scene.frame_set(1)
    res = bpy.ops.codenodes.bake('EXEC_DEFAULT', object_name="Swirl", frame_start=1, frame_end=6)
    check('FINISHED' in res, "the particle bake runs")
    directory = cache.cache_dir("Swirl", create=False)
    files = sorted(f for f in os.listdir(directory) if f.endswith(".ply"))
    check(len(files) == 6, f"six point files on disk ({len(files)})")
    check(cache.is_baked(obj), "the cache modifier is attached")
    kinds = {n.bl_idname for n in obj.modifiers[cache.MODIFIER_NAME].node_group.nodes}
    check("GeometryNodeMeshToPoints" in kinds and "GeometryNodeStoreNamedAttribute" in kinds,
          "the cache group rebuilds points and the velocity vector")
    seen = []
    for f in (1, 4, 6):
        scene.frame_set(f)
        n, attrs = evaluated_points(obj)
        seen.append(n)
        if f == 1:
            check("velocity" in attrs, f"velocity survives the bake as a vector attribute ({sorted(attrs)})")
    check(all(n == 20000 for n in seen), f"every baked frame has all the points {seen}")

    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "particles.blend"))
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
