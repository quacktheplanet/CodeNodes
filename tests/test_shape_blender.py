"""Parametric shapes as real Blender objects.

    blender --factory-startup --python tests/test_shape_blender.py
"""
import os
import sys
import tempfile
import traceback

import bmesh
import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import agent, api, shapes  # noqa: E402

_checks = 0
WORK = os.path.join(tempfile.gettempdir(), "codenodes_shape_test")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def face_sizes(me):
    sizes = {}
    for poly in me.polygons:
        sizes[poly.loop_total] = sizes.get(poly.loop_total, 0) + 1
    return sizes


def run():
    os.makedirs(WORK, exist_ok=True)
    if "Cube" in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects["Cube"])

    r = api.code_to_shape(shapes.TEMPLATE, name="Lamp")
    check(r["ok"], f"the lamp builds ({r.get('error')})")
    lamp = bpy.data.objects["Lamp"]
    me = lamp.data
    check(len(me.polygons) > 1000, f"it has real geometry ({len(me.polygons):,} faces)")
    check(not me.validate(verbose=False), "Blender accepts the mesh without repairs")

    sizes = face_sizes(me)
    check(sizes.get(4, 0) > sizes.get(3, 0), f"mostly quads, with triangles only at the caps ({sizes})")
    check(len(me.uv_layers) == 1, f"it has UVs ({[layer.name for layer in me.uv_layers]})")
    from codenodes.shape_build import get_uvs
    uv = get_uvs(me.uv_layers[0], len(me.loops))
    check(uv.min() >= -1e-6 and uv.max() <= 1.0 + 1e-6, f"the UVs sit in 0..1 ({uv.min():.3f}..{uv.max():.3f})")
    check(uv.max() > 0.9, "and they actually span the surface")

    sharp = sum(1 for e in me.edges if e.use_edge_sharp)
    check(sharp > 0, f"corners are marked sharp, so they stay crisp ({sharp:,} edges)")

    height = max(v.co.z for v in me.vertices)
    check(abs(height - 0.34) < 1e-4, f"it is the height the parameter says ({height:.4f} m)")
    names = [p.name for p in lamp.codenodes.params]
    check(names == ["height", "shade_r", "stem_r", "base_r"], f"its sliders are on the object ({names})")

    r = api.set_params("Lamp", height=0.5)
    check(r["ok"] and abs(max(v.co.z for v in me.vertices) - 0.5) < 1e-4,
          "changing a slider rebuilds it at the new size")

    # the shape is watertight where it should be
    bm = bmesh.new()
    bm.from_mesh(bpy.data.objects["Lamp"].data)
    open_edges = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    bm.free()
    check(open_edges <= 2 * 72, f"only the open shade rim is a boundary ({open_edges} edges)")

    # errors carry the line
    bad = api.code_to_shape("param h 1\nprofile\n  move 0, 0\n  line nope, 1\nrevolve", name="Bad")
    check(not bad["ok"] and "line 4" in bad["error"] and "nope" in bad["error"],
          f"a mistake names its line ({bad['error']})")
    worse = api.code_to_shape("profile\n  move 0,0\n  line 1,0\n", name="Bad2")
    check(not worse["ok"] and "never becomes a solid" in worse["error"], "an unfinished shape is explained")

    # through the assistant surface
    got = agent.help("shape")
    check("revolve" in got["shape_language"]["commands"] and got["templates"]["shape"],
          "help describes the shape language")
    made = agent.make("shape", """
param radius 0.4
param height 0.9
profile
  move 0, 0
  line radius, 0
  curve x = radius * (1 - 0.55 * t * t)  y = height * t  steps 28
  line 0, height
  close
revolve segments 64
""", name="Vase")
    check(made["ok"] and made["faces"] > 500, f"a vase is built through agent.make ({made.get('error')})")
    vase = bpy.data.objects["Vase"]
    top = max(v.co.z for v in vase.data.vertices)
    check(abs(top - 0.9) < 1e-5, f"to the size the maths says ({top:.4f} m)")

    # --- booleans: a part can cut another ------------------------------------------------
    solid_block = api.code_to_shape("""
part block
  profile
    move -0.5, -0.5
    line 0.5, -0.5
    line 0.5, 0.5
    line -0.5, 0.5
    close
  extrude 1.0
""", name="Block")
    check(solid_block["ok"], f"a plain block builds ({solid_block.get('error')})")
    whole = len(bpy.data.objects["Block"].data.polygons)

    drilled = api.code_to_shape("""
part block
  profile
    move -0.5, -0.5
    line 0.5, -0.5
    line 0.5, 0.5
    line -0.5, 0.5
    close
  extrude 1.0
part hole subtract
  profile
    move 0, -0.2
    line 0.25, -0.2
    line 0.25, 1.2
    line 0, 1.2
    close
  revolve segments 32
""", name="Drilled")
    check(drilled["ok"], f"a block with a hole builds ({drilled.get('error')})")
    dm = bpy.data.objects["Drilled"].data
    check(len(dm.polygons) > whole, f"subtract really cut it ({whole} -> {len(dm.polygons)} faces)")
    inner = [v for v in dm.vertices if 0.2 < (v.co.x ** 2 + v.co.y ** 2) ** 0.5 < 0.3]
    check(len(inner) > 20, f"and left a round hole through it ({len(inner)} vertices on its wall)")
    bm = bmesh.new()
    bm.from_mesh(dm)
    open_edges = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    check(open_edges == 0, f"the cut result is still watertight ({open_edges} loose edges)")

    # --- bevel ------------------------------------------------------------------------------
    sharp_cube = """
part cube
  profile
    move -0.5, -0.5
    line 0.5, -0.5
    line 0.5, 0.5
    line -0.5, 0.5
    close
  extrude 1.0
"""
    api.code_to_shape(sharp_cube, name="Sharp")
    before = len(bpy.data.objects["Sharp"].data.polygons)
    bev = api.code_to_shape(sharp_cube + "finish\n  bevel 0.08 segments 3\n", name="Rounded")
    check(bev["ok"], f"a bevelled shape builds ({bev.get('error')})")
    rounded = bpy.data.objects["Rounded"].data
    check(len(rounded.polygons) > before * 2,
          f"a bevel adds the rounded edges ({before} -> {len(rounded.polygons)} faces)")
    widest = max(abs(v.co.x) for v in rounded.vertices)
    check(widest <= 0.5 + 1e-4, f"and stays inside the original size (half-width {widest:.6f})")

    # --- sweep and loft through the object API -------------------------------------------------
    spring = api.code_to_shape("""
param wire 0.015
param coil 0.09
profile
  curve x = wire * cos(t * tau)  y = wire * sin(t * tau)  steps 14
  close
path
  helix radius coil pitch 0.05 turns 4 steps 200
sweep
""", name="Spring")
    check(spring["ok"] and spring["faces"] > 2000, f"a swept spring builds ({spring.get('error')})")
    sv = bpy.data.objects["Spring"].data.vertices
    rise = max(v.co.z for v in sv) - min(v.co.z for v in sv)
    check(abs(rise - (0.05 * 4 + 2 * 0.015)) < 0.01, f"with the right rise ({rise:.3f} m)")

    # --- the profile can be drawn as a curve, to judge by eye ---------------------------------
    from codenodes import shape_build
    curve, found = shape_build.profiles_to_curve(shapes.TEMPLATE, name="LampProfile")
    check(curve.type == 'CURVE' and found == 3, f"all three lamp profiles are drawn ({found} splines)")
    pts = [p.co for spline in curve.data.splines for p in spline.points]
    check(len(pts) > 20 and max(p.z for p in pts) > 0.3,
          f"in the XZ plane at the model's size ({len(pts)} points, top {max(p.z for p in pts):.2f})")
    check(any(s.use_cyclic_u for s in curve.data.splines), "closed profiles come out closed")

    # --- the Shape node in the editor -----------------------------------------------------------
    from codenodes import nodes
    tree = bpy.data.node_groups.new("T_ShapeGraph", nodes.TREE)
    node = tree.nodes.new("CN_NodeShape")
    text = bpy.data.texts.new("node_shape")
    text.from_string("param r 0.3\nprofile\n  move 0, 0\n  line r, 0\n  line r, 0.5\n"
                     "  line 0, 0.5\n  close\nrevolve segments 24")
    node.text = text
    err = nodes.build_output(tree, node)
    check(not err and node.target is not None, f"a Shape node builds its object ({err or node.stats})")
    check([s.name for s in node.inputs] == ["r"], "its param became an input socket")
    node.inputs["r"].default_value = 0.6
    nodes.build_output(tree, node)
    widest = max(abs(v.co.x) for v in node.target.data.vertices)
    check(abs(widest - 0.6) < 1e-4, f"and the socket drives the model ({widest:.3f})")
    text.from_string("param r 0.3\nprofile\n  move 0, 0\n  line nope, 0\nrevolve")
    err = nodes.build_output(tree, node)
    check("line 4" in err and "nope" in err, f"a mistake in the node names its line ({err})")

    # and it renders
    bpy.data.objects["Vase"].hide_render = True
    agent.look_at("Lamp")
    agent.light("studio")
    shot = agent.render(path=os.path.join(WORK, "lamp.png"), samples=24, width=400)
    check(shot["ok"] and shot["exists"], f"it renders ({shot.get('error')})")
    img = bpy.data.images.load(shot["path"])
    px = np.empty(len(img.pixels), np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    check(float(px.reshape(-1, 4)[:, :3].mean()) > 0.02, "and the picture is not black")

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

    bpy.app.timers.register(tick, first_interval=0.5)


main()
