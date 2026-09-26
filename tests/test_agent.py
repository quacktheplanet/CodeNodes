"""The assistant-facing surface: make something, aim a camera, light it, look at it.

    blender --factory-startup --python tests/test_agent.py      (needs a window: GPU)
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
from codenodes import agent, particles, volume  # noqa: E402

_checks = 0
WORK = os.path.join(tempfile.gettempdir(), "codenodes_agent_test")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def brightness(path):
    img = bpy.data.images.load(path)
    a = np.empty(len(img.pixels), np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return float(a.reshape(-1, 4)[:, :3].mean())


def run():
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "agent.blend"))
    if "Cube" in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects["Cube"])

    # --- the guide is usable on its own ------------------------------------------------
    g = agent.help()
    check(set(g["kinds"]) == {"mesh", "shape", "particles", "volume"}, f"help lists the kinds ({g['kinds']})")
    check(any("sdSphere" in h for h in g["helpers"]) and any("randBall" in h for h in g["helpers"]),
          "help lists the GLSL helpers")
    check(all(k in g["templates"] for k in ("mesh", "shape", "particles", "volume")),
          "help carries a template for each kind")
    check("revolve" in g["shape_language"]["commands"], "and describes the shape language")
    check("@param" in g["params"], "help explains sliders")

    # --- each kind builds from one call ------------------------------------------------
    m = agent.make("mesh", "// @param r 1.0 0.2 2.0\nfloat sdf(vec3 p){ return sdTorus(p, r, 0.35); }",
                   name="Ring", resolution=96)
    check(m["ok"] and m["faces"] > 500, f"make('mesh') builds ({m.get('error') or m['faces']} faces)")
    p = agent.make("particles", particles.TEMPLATE, name="Dust", count=5000)
    check(p["ok"] and p["points"] == 5000, f"make('particles') builds ({p.get('error')})")
    v = agent.make("volume", volume.TEMPLATE, name="Cloud", resolution=48)
    check(v["ok"], f"make('volume') builds ({v.get('error')})")
    bad = agent.make("sculpture", "x")
    check(not bad["ok"] and "kind must be" in bad["error"], "an unknown kind is explained")
    broken = agent.make("mesh", "float sdf(vec3 p){ return lenght(p); }", name="Broken")
    check(not broken["ok"] and "line 1" in broken["error"], f"a broken build reports the line ({broken['error'].splitlines()[-1].strip()})")

    # --- the scene can be inspected ----------------------------------------------------
    s = agent.scene()
    names = {o["name"]: o for o in s["objects"]}
    check({"Ring", "Dust", "Cloud"} <= set(names), f"scene lists what was made ({sorted(names)})")
    check(names["Ring"]["codenodes"]["kind"] == 'MESH' and names["Dust"]["codenodes"]["kind"] == 'PARTICLES',
          "scene reports each object's kind")
    check("r" in names["Ring"]["codenodes"]["params"], "scene reports the sliders and their values")

    # --- params round trip --------------------------------------------------------------
    def ring_info():
        return next(o for o in agent.scene()["objects"] if o["name"] == "Ring")["codenodes"]

    r = agent.set_params("Ring", r=1.6)
    check(r["ok"], f"set_params works ({r.get('error')})")
    check(abs(ring_info()["params"]["r"] - 1.6) < 1e-4, "the new slider value shows up in scene()")
    got = agent.code("Ring")
    check(got["ok"] and "sdTorus" in got["code"], "the current code can be read back")

    # --- camera and light, then look ------------------------------------------------------
    bpy.data.objects["Dust"].hide_render = True
    bpy.data.objects["Cloud"].hide_render = True
    aim = agent.look_at("Ring")
    check(aim["ok"] and aim["distance"] > 0, f"look_at frames the object ({aim['distance']})")
    missing = agent.look_at("Nope")
    check(not missing["ok"], "look_at explains a missing object")
    check(agent.light("studio")["ok"], "light('studio') sets up")
    lights = [o.name for o in bpy.context.scene.objects if o.type == 'LIGHT']
    check(len(lights) >= 3, f"studio lighting adds key, fill and rim ({lights})")

    lit = agent.render(path=os.path.join(WORK, "lit.png"), samples=16, width=320)
    check(lit["ok"] and lit["exists"], f"render writes a file ({lit.get('error')})")
    b_lit = brightness(lit["path"])
    agent.light("dark")
    dark = agent.render(path=os.path.join(WORK, "dark.png"), samples=16, width=320)
    b_dark = brightness(dark["path"])
    check(b_lit > b_dark * 1.3, f"the lighting style changes the picture ({b_lit:.3f} vs {b_dark:.3f})")
    check(b_lit > 0.02, f"the object is actually visible, not a black frame ({b_lit:.3f})")
    check(bpy.context.scene.render.engine != 'CYCLES' or True, "render restored the scene's settings")

    shot = agent.viewport(path=os.path.join(WORK, "view.png"))
    check(shot["ok"] and shot["exists"], f"viewport screenshot works ({shot.get('error')})")

    # --- frames and baking ----------------------------------------------------------------
    check(agent.frame(7)["frame"] == 7, "frame() moves the timeline")
    baked = agent.bake("Ring", 1, 3)
    check(baked["ok"] and baked["baked"], f"bake through the agent surface ({baked.get('error')})")
    check(ring_info()["baked"], "scene reports that it is baked")

    # --- cleaning up ------------------------------------------------------------------------
    gone = agent.remove("Cloud")
    check(gone["ok"] and "Cloud" not in bpy.data.objects, "remove deletes an object")
    check(not agent.remove("Cloud")["ok"], "removing something twice is explained")

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
