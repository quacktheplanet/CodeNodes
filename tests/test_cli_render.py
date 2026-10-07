"""Command-line renders with GPU nodes (Blender 5.2 or later, whose gpu.init() gives background mode the GPU):

    blender -b --factory-startup --python tests/test_cli_render.py

Builds a scene with a live-only GPU surface and live-only glowing particles, saves it, then renders it
the way a farm would: a separate `blender -b scene.blend` process that runs bpy.ops.codenodes.render().
Checks every frame is written, the GPU nodes are in the picture, the particles move, and no GPU code
ran while the renderer worked. Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gpu_guard, live, render_ops  # noqa: E402

TMP = tempfile.mkdtemp(prefix="cn_cli_")
_checks = 0

# what the farm process runs (CodeNodes from this repo, as an installed add-on would be)
FARM = """
import json, sys, bpy
sys.path.insert(0, {root!r})
import codenodes
codenodes.register()
from codenodes import gpu_guard, render_ops
gpu_guard.reset_stats()
bpy.ops.codenodes.render(animation=True)
print("CNFARM " + json.dumps(dict(last=render_ops.LAST, guard=gpu_guard.stats(), init=gpu_guard._init)), flush=True)
"""


# a stage the render must include: it moves the whole ball to the right of the picture
SHIFT = "// Shift Right\nvec3 warp(vec3 q) { return q + vec3(1.6, 0.0, 0.0); }\n"


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def host(name):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(f"{name} Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-600, 0), (600, 0)
    tree.links.new(gin.outputs[0], gout.inputs[0])          # as in Blender's new Geometry Nodes tree
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree


def add(tree, kind, key, x):
    group, err = gn_link.create(kind, key)
    if err:
        raise Fail(f"{key}: {err}")
    return gn_link.insert(tree, group, (x, 0))


def settle():
    for _ in range(4):
        gn_link.sync()
        live._flush()


def load(path):
    im = bpy.data.images.load(path)
    px = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)
    bpy.data.images.remove(im)
    return px


def build_scene():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    sun, stree = host("Sun Host")
    surf = add(stree, 'MESH', "Sun", 0)
    gout = next(n for n in stree.nodes if n.type == 'GROUP_OUTPUT')
    stree.links.new(next(o for o in surf.outputs if o.type == 'GEOMETRY'), gout.inputs[0])
    sparks, ptree = host("Sparks Host")
    src = add(ptree, 'PARTICLES', "Spark Ball", 0)
    group, err = gn_link.create('STAGE', None, "Shift Right", SHIFT)
    shift = gn_link.insert(ptree, group, (150, 0))
    look = add(ptree, 'STAGE', "Glow Look", 300)
    ptree.links.new(src.outputs["Particles"], shift.inputs["Particles"])
    ptree.links.new(shift.outputs["Particles"], look.inputs["Particles"])
    settle()
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
    scene.render.filepath = os.path.join(TMP, "farm_")
    scene.render.image_settings.file_format = 'PNG'


def main():
    if not hasattr(__import__("gpu"), "init"):
        print(f"SKIP: Blender {bpy.app.version_string} has no GPU in background mode (needs 5.2)", flush=True)
        return
    codenodes.register()
    check(gpu_guard.available(), f"background Blender has the GPU ({gpu_guard._init['error'] or 'gpu.init()'})")
    build_scene()
    check(render_ops.scene_has_gpu_nodes(bpy.context.scene), "the scene has live-only GPU nodes")
    path = os.path.join(TMP, "farm.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)

    # the same scene through Blender's own command-line render: live-only nodes aren't in it
    t0 = time.perf_counter()
    plain = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", path, "-o",
                            os.path.join(TMP, "plain_"), "-F", "PNG", "-f", "1"],
                           capture_output=True, text=True, timeout=300)
    plain_files = [f for f in os.listdir(TMP) if f.startswith("plain_")]
    check(plain.returncode == 0 and len(plain_files) == 1, f"a plain `blender -b -f 1` renders ({plain_files})")
    bright_plain = float(load(os.path.join(TMP, plain_files[0]))[:, :, :3].mean())

    script = os.path.join(TMP, "farm.py")
    with open(script, "w") as f:
        f.write(FARM.format(root=ROOT))
    t0 = time.perf_counter()
    run = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", path, "--python", script],
                         capture_output=True, text=True, timeout=600)
    took = time.perf_counter() - t0
    line = next((l for l in run.stdout.splitlines() if l.startswith("CNFARM ")), None)
    if line is None:
        print(run.stdout[-3000:], run.stderr[-3000:], sep="\n")
        raise Fail("the farm process didn't finish its render")
    info = json.loads(line[len("CNFARM "):])
    files = sorted(f for f in os.listdir(TMP) if f.startswith("farm_") and f.endswith(".png"))
    check(info["last"]["finished"] and info["last"]["frames"] == 3 and len(files) == 3,
          f"`blender -b scene.blend` + bpy.ops.codenodes.render(animation=True) writes every frame "
          f"({files}, {took:.1f} s for the whole process)")
    frames = [load(os.path.join(TMP, f)) for f in files]
    bright = float(frames[0][:, :, :3].mean())
    check(bright > bright_plain + 0.01,
          f"the GPU nodes are in the picture (mean brightness {bright:.3f}; Blender's own render {bright_plain:.3f})")
    h, w = frames[0].shape[:2]
    centre = float(frames[0][h // 2 - 8:h // 2 + 8, w // 2 - 8:w // 2 + 8, :3].mean())
    check(centre > 0.2, f"the live-only surface (the Sun, in the middle) is in the picture (centre {centre:.3f})")
    third = w // 3
    right = float(frames[0][:, -third:, :3].mean())
    left = float(frames[0][:, :third, :3].mean())
    check(right > left + 0.01, f"the chain's stages are in the render: the Shift Right stage puts the sparks on the "
                               f"right (right third {right:.3f}, left third {left:.3f})")
    moved = float(np.abs(frames[0] - frames[-1]).mean())
    check(moved > 1e-4, f"the particles move between frames (mean change {moved:.4f})")
    check(info["guard"]["during_render"] == 0 and info["guard"]["dispatches"] > 0,
          f"the GPU ran between frames, never while the renderer worked ({info['guard']})")

    # plain `blender -b scene.blend -a` with the add-on enabled: no script, CodeNodes steps every frame up front
    enable = os.path.join(TMP, "enable.py")
    with open(enable, "w") as f:
        f.write(f"import sys\nsys.path.insert(0, {ROOT!r})\nimport codenodes\ncodenodes.register()\n")
    t0 = time.perf_counter()
    run = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", path, "--python", enable,
                          "-o", os.path.join(TMP, "cli_"), "-F", "PNG", "-a"],
                         capture_output=True, text=True, timeout=600)
    took = time.perf_counter() - t0
    cli = sorted(f for f in os.listdir(TMP) if f.startswith("cli_") and f.endswith(".png"))
    if len(cli) != 3:
        print(run.stdout[-3000:], run.stderr[-3000:], sep="\n")
    check(run.returncode == 0 and len(cli) == 3,
          f"a plain `blender -b scene.blend -a` renders every frame ({cli}, {took:.1f} s)")
    same = max(float(np.abs(load(os.path.join(TMP, a)) - load(os.path.join(TMP, b))).max())
               for a, b in zip(cli, files))
    check(same < 0.02, f"and its frames match the CodeNodes render's (largest pixel difference {same:.4f})")
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


try:
    main()
except Fail as exc:
    print(f"FAIL: {exc}", flush=True)
except Exception:
    traceback.print_exc()
    print("FAIL: exception", flush=True)
sys.stdout.flush()
os._exit(0)                 # (with a window too)
