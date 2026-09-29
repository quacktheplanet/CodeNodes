"""The Galaxy demo: builds examples/galaxy_scene.py, saves it, takes screenshots and renders it.

    blender --factory-startup --python examples/galaxy_demo.py

Needs a window (the GPU isn't available with -b). Writes into $CODENODES_DEMO_OUT (default: the
CodeNodes-install demo folder next to the repo): codenodes_galaxy.blend, galaxy_*.png screenshots,
galaxy_render.png (1080p) and galaxy_flythrough.mp4 (a short animation), then quits. Renders go through
Render with CodeNodes, the path F12 and Ctrl+F12 take when a scene has GPU nodes.
"""
import json
import math
import os
import sys
import time
import traceback

import bpy
from mathutils import Euler, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))
import codenodes  # noqa: E402
from codenodes import gn_link, gpu_live, live  # noqa: E402

OUT = os.path.abspath(os.environ.get("CODENODES_DEMO_OUT")
                      or os.path.join(os.path.dirname(ROOT), "CodeNodes-install", "demo"))
os.makedirs(OUT, exist_ok=True)
BLEND = os.path.join(OUT, "codenodes_galaxy.blend")
ANIM_FRAMES = int(os.environ.get("CODENODES_ANIM_FRAMES", "72"))
NUMBERS = {}
print("CodeNodes Galaxy demo: building (this window closes by itself)", flush=True)


def use_workspace(win):
    """Switch to Geometry Nodes (its areas only exist after the next redraw, so arrange in a later tick)."""
    ws = bpy.data.workspaces.get("Geometry Nodes")
    if ws is not None and win.workspace != ws:
        win.workspace = ws


def arrange(win, focus, camera_view=True):
    for area in list(win.screen.areas):
        if area.type == 'SPREADSHEET':
            try:
                with bpy.context.temp_override(window=win, screen=win.screen, area=area):
                    bpy.ops.screen.area_close()
            except Exception:
                traceback.print_exc()
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            sp = area.spaces.active
            sp.shading.type = 'SOLID'
            sp.shading.color_type = 'VERTEX'
            sp.shading.light = 'STUDIO'
            sp.shading.background_type = 'VIEWPORT'
            sp.shading.background_color = (0.004, 0.005, 0.012)
            sp.clip_end = 5000.0
            sp.overlay.show_floor = False
            sp.overlay.show_axis_x = sp.overlay.show_axis_y = False
            sp.overlay.show_extras = False
            sp.overlay.show_outline_selected = False
            sp.region_3d.view_perspective = 'CAMERA' if camera_view else 'PERSP'
        if area.type == 'NODE_EDITOR':
            area.spaces.active.tree_type = 'GeometryNodeTree'
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = focus      # active, not selected: no highlight over it


def fit_nodes(win):
    for area in win.screen.areas:
        if area.type == 'NODE_EDITOR':
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.node.view_all()


def screenshot(win, name, area_type=None):
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=3)
    path = os.path.join(OUT, name)
    if area_type is None:
        with bpy.context.temp_override(window=win):
            bpy.ops.screen.screenshot(filepath=path)
    else:
        for area in win.screen.areas:
            if area.type == area_type:
                with bpy.context.temp_override(window=win, area=area):
                    bpy.ops.screen.screenshot_area(filepath=path)
                break
    print("  screenshot:", path, flush=True)


def measure_fps(seconds=3.0):
    t0 = time.perf_counter()
    start = gpu_live._fps.get("total", 0)
    while time.perf_counter() - t0 < seconds:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    return (gpu_live._fps.get("total", 0) - start) / (time.perf_counter() - t0)


def orbits_node():
    tree = bpy.data.node_groups["Solar System Nodes"]
    return tree.nodes[STEP["orbits"]]


STEP = {"n": 0}


def tick():
    try:
        STEP["n"] += 1
        n = STEP["n"]
        win = bpy.context.window_manager.windows[0]
        scene = bpy.context.scene
        if n == 1:
            path = os.path.join(OUT, "galaxy_start.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)          # closes the splash screen
            return 0.8
        if n == 2:
            import galaxy_scene
            t0 = time.perf_counter()
            STEP["g"] = galaxy_scene.build(bpy.context.scene)
            STEP["orbits"] = STEP["g"]["nodes"]["orbits"].name
            NUMBERS["build_s"] = round(time.perf_counter() - t0, 2)
            return 1.0
        if n == 3:
            use_workspace(win)
            return 1.0
        if n == 3.5:
            pass
        if n == 4 and not STEP.get("arranged"):
            STEP["arranged"] = True
            STEP["n"] = 3
            arrange(win, bpy.data.objects["Solar System"])
            scene.frame_set(96)
            return 2.0
        if n == 4:
            fit_nodes(win)
            NUMBERS["live_fps"] = round(measure_fps(), 1)
            screenshot(win, "galaxy_live.png", 'VIEW_3D')
            screenshot(win, "galaxy_workspace.png")
            screenshot(win, "galaxy_nodes.png", 'NODE_EDITOR')
            orbits_node().inputs["Template"].default_value = "Figure Eights"
            return 2.5
        if n == 5:
            gn_link.sync()
            live._flush()
            scene.frame_set(96)
            return 1.5
        if n == 6:
            screenshot(win, "galaxy_live_figure_eights.png", 'VIEW_3D')
            orbits_node().inputs["Template"].default_value = "Orbits"
            return 2.5
        if n == 7:
            gn_link.sync()
            live._flush()
            arrange(win, bpy.data.objects["Solar System"], camera_view=False)
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    r3d = area.spaces.active.region_3d
                    r3d.view_location = Vector((0.0, 0.0, 0.0))
                    r3d.view_distance = 24.0
                    r3d.view_rotation = Euler((math.radians(52), 0, math.radians(24))).to_quaternion()
            scene.frame_set(96)
            return 2.0
        if n == 8:
            screenshot(win, "galaxy_live_above.png", 'VIEW_3D')
            arrange(win, bpy.data.objects["Solar System"])
            fit_nodes(win)
            scene.frame_set(1)
            bpy.ops.wm.save_as_mainfile(filepath=BLEND, relative_remap=True)
            return 1.0
        if n == 9:
            for frame, name in ((120, "galaxy_render.png"), (1, "galaxy_render_start.png"),
                                (260, "galaxy_render_end.png")):
                scene.frame_set(frame)
                t0 = time.perf_counter()
                bpy.ops.codenodes.render('EXEC_DEFAULT', animation=False)
                NUMBERS.setdefault("render_still_1080p_s", round(time.perf_counter() - t0, 1))
                img = bpy.data.images.get("Render Result")
                if img is not None:
                    img.save_render(os.path.join(OUT, name), scene=scene)
            return 0.5
        if n == 10:
            # a short fly-through as a movie, through Render with CodeNodes (frames, then Blender's movie writer)
            r = scene.render
            r.resolution_percentage = 50
            if hasattr(r.image_settings, 'media_type'):
                r.image_settings.media_type = 'VIDEO'   # Blender 5: pick video before FFmpeg
            r.image_settings.file_format = 'FFMPEG'
            r.ffmpeg.format = 'MPEG4'
            r.ffmpeg.codec = 'H264'
            r.ffmpeg.constant_rate_factor = 'HIGH'
            r.filepath = os.path.join(OUT, "galaxy_flythrough_")
            start, end = scene.frame_start, scene.frame_end
            scene.frame_start, scene.frame_end, scene.frame_step = 1, FRAMES_FOR_ANIM(), 1
            t0 = time.perf_counter()
            bpy.ops.codenodes.render('EXEC_DEFAULT', animation=True)
            NUMBERS["render_anim_frames"] = scene.frame_end - scene.frame_start + 1
            NUMBERS["render_anim_s"] = round(time.perf_counter() - t0, 1)
            scene.frame_start, scene.frame_end = start, end
            r.resolution_percentage = 100
            if hasattr(r.image_settings, 'media_type'):
                r.image_settings.media_type = 'IMAGE'
            r.image_settings.file_format = 'PNG'
            print("NUMBERS", json.dumps(NUMBERS), flush=True)
            with open(os.path.join(OUT, "galaxy_numbers.json"), "w") as fh:
                json.dump(NUMBERS, fh, indent=1)
            print("DEMO DONE", OUT, flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception:
        traceback.print_exc()
        print("DEMO FAILED", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return None


def FRAMES_FOR_ANIM():
    return ANIM_FRAMES


codenodes.register()
bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
