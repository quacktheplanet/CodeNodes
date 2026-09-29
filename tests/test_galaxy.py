"""The Galaxy scene (examples/galaxy_scene.py) end to end: it builds, every chain compiles, the planets
are real coloured meshes from one reused node, they sit exactly where the shared Orbits function says
(at the timeline's time), asteroids never end up inside a planet or the star, switching Orbits to
Figure Eights moves the planets and changes the asteroids, the ring follows its planet, and a render
includes the GPU nodes without running GPU code during it. Needs a window (run it on a hidden desktop):

    blender --factory-startup --python tests/test_galaxy.py

Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...", then quits.
"""
import json
import math
import os
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))
import codenodes  # noqa: E402
from codenodes import gn_link, gpu_guard, gpu_live, live  # noqa: E402

print("CodeNodes test: the Galaxy scene (this window closes by itself)", flush=True)
_checks = 0
NUMBERS = {}
TMP = tempfile.mkdtemp(prefix="cn_galaxy_")
FRAME = 72


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def settle(n=4):
    for _ in range(n):
        gn_link.sync()
        live._flush()


def src(node):
    return gn_link.source_of(node.node_tree)


def points(obj):
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def attr(obj, name):
    a = obj.data.attributes.get(name)
    if a is None:
        return None
    n = len(a.data)
    if a.data_type in ('FLOAT_COLOR', 'BYTE_COLOR'):
        v = np.empty(n * 4, np.float32)
        a.data.foreach_get("color", v)
        return v.reshape(-1, 4)
    v = np.empty(n, np.float32)
    a.data.foreach_get("value", v)
    return v


def orbit_pos(i, t, o, figure_eights=False):
    """Orbits' / Figure Eights' planetPos(i, t), in Python."""
    r = o["first"] + o["gap"] * i
    a = o["speed"] * (o["first"] / r) ** 1.5 * t + i * 2.39996
    if not figure_eights:
        tilt = o["incline"] * math.sin(i * 1.7 + 0.4)
        return np.array([math.cos(a) * r, math.sin(a) * r * math.cos(tilt), math.sin(a) * r * math.sin(tilt)])
    s, c = math.sin(a), math.cos(a)
    q = np.array([c, s * c]) / (1.0 + s * s) * r * 1.35
    v = np.array([q[0], q[1], math.sin(a * 2.0) * r * o["incline"]])
    ang = i * 1.9
    return np.array([math.cos(ang) * v[0] - math.sin(ang) * v[1], math.sin(ang) * v[0] + math.cos(ang) * v[1], v[2]])


def at_frame(f):
    scene = bpy.context.scene
    scene.frame_set(f)
    settle(2)
    scene.frame_set(f)


# ---- phases ----------------------------------------------------------------------------------------------

def phase_blank(st):
    path = os.path.join(TMP, "start.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    return True


def phase_build(st):
    import galaxy_scene
    t0 = time.perf_counter()
    g = galaxy_scene.build(bpy.context.scene)
    NUMBERS["build_s"] = round(time.perf_counter() - t0, 2)
    st["g"] = g
    st["names"] = {k: v.name for k, v in g["nodes"].items()}
    st["mod"] = galaxy_scene
    col = bpy.data.collections[gn_link.SOURCES]
    errors = {o.name: o.codenodes.last_error for o in col.objects if o.codenodes.last_error}
    check(not errors, f"the scene builds in {NUMBERS['build_s']} s and every code node compiles ({len(col.objects)} "
                      f"sources, errors: {errors or 'none'})")
    return True


def node(st, key):
    return bpy.data.node_groups["Solar System Nodes"].nodes[st["names"][key]]


def phase_planets(st):
    at_frame(FRAME)
    meshes = []
    for row in range(3):
        ter = node(st, f"terrain{row}")
        s = src(ter)
        col = attr(s, "color")
        co = points(s)
        radius = np.linalg.norm(co, axis=1)
        meshes.append(co)
        check(len(co) > 5000 and col is not None and float(col[:, :3].std()) > 0.03 and float(radius.std()) > 0.003,
              f"planet {row + 1} is a real mesh from Ico Sphere -> Planet Terrain -> Colour by Height -> To Mesh "
              f"({len(co):,} vertices, colour spread {float(col[:, :3].std()):.3f}, "
              f"height spread {float(radius.std()):.4f})")
    groups = {node(st, f"terrain{r}").node_tree.name for r in range(3)}
    templates = {node(st, f"terrain{r}").inputs["Template"].default_value for r in range(3)}
    seeds = {round(node(st, f"terrain{r}").inputs["seed"].default_value, 3) for r in range(3)}
    check(len(groups) == 3 and templates == {"Planet Terrain"} and len(seeds) == 3
          and not np.allclose(meshes[0][:200], meshes[1][:200]),
          f"reuse: one node (Planet Terrain) three times with different inputs gives three different planets "
          f"(seeds {sorted(seeds)})")
    dg = bpy.context.evaluated_depsgraph_get()
    solar = bpy.data.objects["Solar System"]
    n_inst = sum(1 for i in dg.object_instances if i.is_instance and i.parent and i.parent.original == solar)
    check(n_inst >= 3 + 4000, f"native + GPU: Instance on Points places the planets and the rocks ({n_inst:,} instances)")
    return True


def phase_orbits(st):
    g = st["mod"]
    o = dict(g.ORBITS)
    pts = src(node(st, "points"))
    co = points(pts)
    size = attr(pts, "bodySize")
    t = (FRAME - bpy.context.scene.frame_start) / (bpy.context.scene.render.fps / bpy.context.scene.render.fps_base)
    want = np.array([orbit_pos(i, t, o) for i in range(3)])
    err = float(np.abs(co[:3] - want).max()) if len(co) >= 3 else 1e9
    rad = np.array([g.planet_radius(i) for i in range(3)])
    check(len(co) == 3 and err < 2e-3 and np.allclose(size[:3], rad, atol=1e-4),
          f"shared orbit: the planets sit where Orbits' planetPos(i, t) says at the timeline's time, with "
          f"planetRadius as their size (off by {err:.2e} m)")
    st["planets"], st["radii"] = co[:3].copy(), rad
    return True


def phase_asteroids(st):
    g = st["mod"]
    belt = src(node(st, "belt"))
    ast = points(belt)
    planets, radii = st["planets"], st["radii"]
    margin = node(st, "coll").inputs["margin"].default_value
    star = node(st, "coll").inputs["starRadius"].default_value
    worst, near = 1e9, 0
    for c, r in zip(planets, radii):
        d = np.linalg.norm(ast - c, axis=1) / (r * (1.0 + margin))
        worst = min(worst, float(d.min()))
        near += int((d < 1.4).sum())
    star_d = float(np.linalg.norm(ast, axis=1).min()) / star
    check(len(ast) == 5000 and worst >= 0.97 and star_d >= 0.97,
          f"interaction: no asteroid ends up inside a planet or the star (closest at {worst:.2f} and {star_d:.2f} "
          f"of the contact radius)")
    check(near > 0, f"and some are right at a planet's surface, where Collide with Bodies bounced them ({near})")
    r = np.linalg.norm(ast[:, :2], axis=1)
    bound = float(((r > 4.0) & (r < 16.0)).mean())
    check(bound > 0.9, f"Gravity to Bodies keeps the belt in orbit ({bound:.0%} still between 4 m and 16 m out)")
    st["asteroids"] = ast.copy()
    return True


def phase_swap(st):
    n = node(st, "orbits")
    n.inputs["Template"].default_value = "Figure Eights"
    st["t_swap"] = time.perf_counter()
    return True


def phase_swap_check(st):
    settle(4)
    live._flush()
    at_frame(FRAME)
    g = st["mod"]
    pts = src(node(st, "points"))
    co = points(pts)
    t = (FRAME - 1) / 24.0
    want = np.array([orbit_pos(i, t, g.ORBITS, figure_eights=True) for i in range(3)])
    moved = float(np.linalg.norm(co[:3] - st["planets"], axis=1).max())
    err = float(np.abs(co[:3] - want).max())
    check(moved > 0.5 and err < 2e-3 and node(st, "orbits").inputs["Template"].default_value == "Figure Eights",
          f"switching Orbits to Figure Eights moves the planets onto figure-of-eight paths ({moved:.2f} m, "
          f"off by {err:.1e} m), with its links kept")
    ast = points(src(node(st, "belt")))
    diff = float(np.linalg.norm(ast - st["asteroids"], axis=1).mean())
    check(diff > 1e-3, f"and the asteroids feel the new positions: their paths change ({diff:.3f} m on average)")
    node(st, "orbits").inputs["Template"].default_value = "Orbits"
    return True


def phase_back(st):
    settle(4)
    at_frame(FRAME)
    co = points(src(node(st, "points")))
    back = float(np.abs(co[:3] - st["planets"]).max())
    check(back < 2e-3, f"switching back puts every planet back ({back:.1e} m)")
    return True


def phase_ring(st):
    ring = src(node(st, "ring"))
    err = live.make_real_now(ring)
    co = points(ring)
    centre = co.mean(axis=0) if len(co) else np.zeros(3)
    target = st["planets"][1]
    off = float(np.linalg.norm(centre - target))
    check(not err and len(co) > 1000 and off < 0.3,
          f"the ring follows planet 2 (Follow Body with Orbits' planetPos): its centre is {off:.3f} m from the planet")
    live.clear_live(ring) if live.is_live_only(ring) else None
    return True


def phase_render(st):
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 480, 270, 100
    gpu_guard.reset_stats()
    t0 = time.perf_counter()
    res = bpy.ops.codenodes.render('EXEC_DEFAULT', animation=False)
    NUMBERS["render_480_s"] = round(time.perf_counter() - t0, 2)
    img = bpy.data.images.get("Render Result")
    path = os.path.join(TMP, "render.png")
    img.save_render(path, scene=scene)
    im = bpy.data.images.load(path)
    px = np.array(im.pixels[:]).reshape(-1, 4)
    bright = float((px[:, :3].max(axis=1) > 0.6).mean())
    check(res == {'FINISHED'} and bright > 0.002 and gpu_guard.stats()["during_render"] == 0,
          f"Render with CodeNodes includes the GPU nodes ({bright:.1%} bright pixels) and runs no GPU code "
          f"while Blender renders ({NUMBERS['render_480_s']} s)")
    fps_t0 = time.perf_counter()
    start = gpu_live._fps.get("total", 0)
    while time.perf_counter() - fps_t0 < 2.0:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    NUMBERS["live_fps"] = round((gpu_live._fps.get("total", 0) - start) / (time.perf_counter() - fps_t0), 1)
    return True


PLAN = [phase_blank, phase_build, phase_planets, phase_orbits, phase_asteroids, phase_swap, phase_swap_check,
        phase_back, phase_ring, phase_render]
STATE = {}


def finish(exc):
    print("NUMBERS", json.dumps(NUMBERS), flush=True)
    if exc is None:
        print(f"\nALL {_checks} CHECKS PASSED", flush=True)
    elif isinstance(exc, Fail):
        print(f"FAIL: {exc}", flush=True)
    else:
        traceback.print_exception(exc)
        print("FAIL: exception", flush=True)
    bpy.ops.wm.quit_blender()


def driver():
    try:
        if not PLAN:
            finish(None)
            return None
        if PLAN[0](STATE):
            PLAN.pop(0)
        return 0.5
    except Exception as exc:
        finish(exc)
        return None


codenodes.register()
bpy.app.timers.register(driver, first_interval=1.0, persistent=True)
