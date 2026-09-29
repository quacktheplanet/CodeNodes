"""F12, Ctrl+F12, the Render menu and the Rendered viewport with GPU code nodes in the scene.

Presses the real keys (simulated events), so it goes through Blender's key maps exactly as a person
would: F12 renders a still with the live GPU nodes in the picture (they're turned into geometry just
for the render), Ctrl+F12 writes every frame of an animation in the scene's output format (also a
movie), the Render menu's first two items do the same, a scene without GPU nodes gets Blender's own
render, no GPU code runs while the renderer works, and the Rendered viewport still draws the live GPU
nodes. Needs a window and event simulation (run it on a hidden desktop):

    blender --factory-startup --enable-event-simulate --python tests/test_render_f12.py

With an installed CodeNodes (a copy of a real profile, no --factory-startup) it uses the installed
add-on instead of the repo, so it tests what a user has. Prints each check, ends with
"ALL n CHECKS PASSED" or "FAIL: ...", then quits.
"""
import json
import os
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLED = next((k for k in bpy.context.preferences.addons.keys() if k.endswith(".codenodes")), None)
if INSTALLED is None:
    sys.path.insert(0, ROOT)
    import codenodes  # noqa: E402
    codenodes.register()
print(f"CodeNodes test: F12 / Ctrl+F12 with GPU nodes ({'installed ' + INSTALLED if INSTALLED else 'repo'}); "
      f"this window closes by itself", flush=True)


def cn(name):
    mod = sys.modules.get((INSTALLED or "codenodes") + "." + name)
    if mod is None:
        raise RuntimeError(f"CodeNodes module {name} isn't loaded")
    return mod


_checks = 0
NUMBERS = {}
TMP = tempfile.mkdtemp(prefix="cn_f12_")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    win = window()
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def press(key, ctrl=False):
    win, area, region = view3d()
    x, y = region.x + region.width // 2, region.y + region.height // 2
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    win.event_simulate(type=key, value='PRESS', ctrl=ctrl)
    win.event_simulate(type=key, value='RELEASE', ctrl=ctrl)


def settle():
    gl = cn("gn_link")
    lv = cn("live")
    for _ in range(4):
        gl.sync()
        lv._flush()


def host(name):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(f"{name} Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.nodes.new("NodeGroupInput").location = (-600, 0)
    tree.nodes.new("NodeGroupOutput").location = (600, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree


def add(tree, kind, key, x):
    gl = cn("gn_link")
    group, err = gl.create(kind, key)
    if err:
        raise Fail(f"{key}: {err}")
    return gl.insert(tree, group, (x, 0))


def saved_pixels(name):
    img = bpy.data.images.get("Render Result")
    path = os.path.join(TMP, name)
    img.save_render(path, scene=bpy.context.scene)
    im = bpy.data.images.load(path)
    px = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)
    bpy.data.images.remove(im)
    return px


def centre_brightness(px):
    h, w = px.shape[:2]
    return float(px[h // 3: 2 * h // 3, w // 3: 2 * w // 3, :3].mean())


def done_since(count):
    last = cn("render_ops").LAST
    return last["count"] > count


# ---- phases --------------------------------------------------------------------------------------------------

def phase_blank(st):
    path = os.path.join(TMP, "start.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    return True


def phase_scene(st):
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    # a live-only GPU surface (a glowing sun) and live-only glowing particles, nothing made real
    sun, stree = host("Sun Host")
    surf = add(stree, 'MESH', "Sun", 0)
    mlook = add(stree, 'STAGE', "Material Look", -300)
    stree.links.new(mlook.outputs["material"], surf.inputs["Material"])
    glow = bpy.data.materials.new("Glow")
    if glow.node_tree is None:
        glow.use_nodes = True
    b = glow.node_tree.nodes["Principled BSDF"]
    b.inputs["Emission Color"].default_value = (1.0, 0.7, 0.3, 1.0)
    b.inputs["Emission Strength"].default_value = 3.0
    sparks, ptree = host("Sparks Host")
    src = add(ptree, 'PARTICLES', "Spark Ball", 0)
    look = add(ptree, 'STAGE', "Glow Look", 300)
    ptree.links.new(src.outputs["Particles"], look.inputs["Particles"])
    settle()
    mlook.inputs["mat"].default_value = glow
    src.inputs["Count"].default_value = 3000
    src.inputs["radius"].default_value = 2.5
    settle()
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    cam.location, cam.rotation_euler = (0.0, -7.0, 0.0), (1.5708, 0.0, 0.0)
    scene.camera = cam
    light = bpy.data.objects.new("Light", bpy.data.lights.new("Light", 'SUN'))
    scene.collection.objects.link(light)
    world = bpy.data.worlds.new("Dark")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 180, 100
    scene.frame_start, scene.frame_end = 1, 3
    ro = cn("render_ops")
    check(ro.scene_has_gpu_nodes(scene), "the scene's GPU nodes are found (a live-only surface and live-only particles)")
    km = bpy.context.window_manager.keyconfigs.addon.keymaps.get("Screen")
    ours = [k for k in (km.keymap_items if km else []) if k.idname == "codenodes.render_auto"]
    check(len(ours) == 2 and {k.ctrl for k in ours} == {False, True},
          "F12 and Ctrl+F12 are CodeNodes key map entries in the Screen key map")
    cn("gpu_guard").reset_stats()
    st["count"] = ro.LAST["count"]
    st["t"] = time.perf_counter()
    press('F12')
    return True


def phase_f12_wait(st):
    if not done_since(st["count"]):
        if time.perf_counter() - st["t"] > 60:
            raise Fail("F12 didn't start a CodeNodes render within 60 s (the key went to Blender's own render?)")
        return False
    NUMBERS["f12_s"] = round(time.perf_counter() - st["t"], 2)
    px = saved_pixels("f12.png")
    st["with"] = centre_brightness(px)
    ro = cn("render_ops")
    check(ro.LAST["finished"] and not ro.LAST["animation"] and st["with"] > 0.05,
          f"F12 renders a still with the live GPU nodes in the picture (centre brightness {st['with']:.3f}, "
          f"{NUMBERS['f12_s']} s)")
    check(cn("gpu_guard").stats()["during_render"] == 0, "no GPU code ran while the renderer worked")
    # the same scene through Blender's own render: the GPU nodes are live-only, so it has nothing there
    bpy.ops.render.render(write_still=False)
    without = centre_brightness(saved_pixels("native.png"))
    check(st["with"] > without + 0.03, f"(Blender's own render of the same scene shows none of it: {without:.3f})")
    scene = bpy.context.scene
    scene.render.filepath = os.path.join(TMP, "anim_")
    scene.render.image_settings.file_format = 'PNG'
    st["count"] = ro.LAST["count"]
    st["t"] = time.perf_counter()
    press('F12', ctrl=True)
    return True


def phase_ctrl_f12_wait(st):
    if not done_since(st["count"]):
        if time.perf_counter() - st["t"] > 90:
            raise Fail("Ctrl+F12 didn't finish a CodeNodes animation render within 90 s")
        return False
    files = sorted(f for f in os.listdir(TMP) if f.startswith("anim_") and f.endswith(".png"))
    frames = []
    for f in files:
        im = bpy.data.images.load(os.path.join(TMP, f))
        frames.append(np.array(im.pixels[:], np.float32))
        bpy.data.images.remove(im)
    moved = float(np.abs(frames[0] - frames[-1]).mean()) if len(frames) >= 2 else 0.0
    check(len(files) == 3 and moved > 1e-4 and cn("render_ops").LAST["animation"],
          f"Ctrl+F12 writes every frame to the output path ({files}); the particles move between them")
    # a movie format: frames rendered, then written with Blender's movie writer
    scene = bpy.context.scene
    s = scene.render.image_settings
    if hasattr(s, "media_type"):
        s.media_type = 'VIDEO'
    s.file_format = 'FFMPEG'
    scene.render.ffmpeg.format = 'MPEG4'
    scene.render.ffmpeg.codec = 'H264'
    scene.render.filepath = os.path.join(TMP, "movie_")
    st["count"] = cn("render_ops").LAST["count"]
    st["t"] = time.perf_counter()
    press('F12', ctrl=True)
    return True


def phase_movie_wait(st):
    if not done_since(st["count"]):
        if time.perf_counter() - st["t"] > 90:
            raise Fail("Ctrl+F12 (movie) didn't finish within 90 s")
        return False
    movies = [f for f in os.listdir(TMP) if f.startswith("movie_") and f.endswith(".mp4")]
    size = os.path.getsize(os.path.join(TMP, movies[0])) if movies else 0
    check(len(movies) == 1 and size > 1000, f"with a movie format it writes the movie ({movies}, {size:,} bytes)")
    s = bpy.context.scene.render.image_settings
    if hasattr(s, "media_type"):
        s.media_type = 'IMAGE'
    s.file_format = 'PNG'
    check(cn("gpu_guard").stats()["during_render"] == 0, "and still no GPU code ran while the renderer worked")
    return True


def phase_menu(st):
    ro = cn("render_ops")
    seen = []

    class Rec:
        operator_context = ''

        def operator(self, idname, text="", icon='', **kw):
            seen.append((idname, text))
            return type("P", (), {})()

        def separator(self, *a, **k):
            pass

        def menu(self, *a, **k):
            pass

        def label(self, *a, **k):
            pass

        def prop(self, *a, **k):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: self

    funcs = bpy.types.TOPBAR_MT_render._dyn_ui_initialize()
    funcs[0](type("M", (), {"layout": Rec()})(), bpy.context)
    firsts = [i for i, _t in seen[:2]]
    rest = [i for i, _t in seen[2:]]
    check(firsts == ["codenodes.render_auto"] * 2 and "render.render" not in rest[:1],
          f"the Render menu's Render Image / Render Animation go through CodeNodes when there are GPU nodes "
          f"({seen[:3]})")
    return True


def phase_native(st):
    """A scene without GPU nodes: F12 is Blender's own render."""
    scene = bpy.data.scenes.new("Plain")
    window().scene = scene
    cube = bpy.data.objects.new("Cube", bpy.data.meshes.new("Cube"))
    scene.collection.objects.link(cube)
    cam = bpy.data.objects.new("Cam2", bpy.data.cameras.new("Cam2"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = 160, 90
    ro = cn("render_ops")
    check(not ro.scene_has_gpu_nodes(scene), "a scene without GPU nodes isn't claimed by CodeNodes")
    st["count"] = ro.LAST["count"]
    st["native_seen"] = False

    def seen(*_a):
        st["native_seen"] = True
    st["handler"] = seen
    bpy.app.handlers.render_init.append(seen)
    st["t"] = time.perf_counter()
    press('F12')
    return True


def phase_native_wait(st):
    running = bpy.app.is_job_running('RENDER')
    if not st["native_seen"] or running:
        if time.perf_counter() - st["t"] > 60:
            raise Fail("F12 in a scene without GPU nodes didn't run Blender's own render")
        return False
    bpy.app.handlers.render_init.remove(st["handler"])
    check(cn("render_ops").LAST["count"] == st["count"],
          "F12 there is Blender's own render (CodeNodes stays out of it)")
    window().scene = bpy.data.scenes["Scene"]
    return True


def phase_rendered_view(st):
    win, area, region = view3d()
    sp = area.spaces.active
    gpu_live = cn("gpu_live")
    results = {}
    for engine in ('BLENDER_EEVEE', 'CYCLES'):
        bpy.context.scene.render.engine = engine
        if engine == 'CYCLES':
            bpy.context.scene.cycles.samples = 2
            bpy.context.scene.cycles.preview_samples = 2
        sp.shading.type = 'RENDERED'
        start = gpu_live._fps.get("total", 0)
        for _ in range(20):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        results[engine] = gpu_live._fps.get("total", 0) - start
    sp.shading.type = 'SOLID'
    bpy.context.scene.render.engine = 'BLENDER_EEVEE'
    check(all(v > 0 for v in results.values()),
          f"the Rendered viewport still draws the live GPU nodes, in EEVEE and Cycles (draws: {results})")
    return True


PLAN = [phase_blank, phase_scene, phase_f12_wait, phase_ctrl_f12_wait, phase_movie_wait, phase_menu,
        phase_native, phase_native_wait, phase_rendered_view]
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
        return 0.4
    except Exception as exc:
        finish(exc)
        return None


bpy.app.timers.register(driver, first_interval=1.5, persistent=True)
