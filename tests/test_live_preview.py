"""Safety and feel in real Blender: switching a code node's Template over and over (with inputs changed
and undo / redo in between) never crashes or breaks the chain, errors are printed without touching freed
Blender data, and while the timeline is paused the viewport keeps simulating, so a behaviour slider
(Wander's strength) visibly changes the motion within a second. Needs a window (run it on a hidden
desktop in CI):

    blender --factory-startup --python tests/test_live_preview.py

Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...", then quits. Set CODENODES_NUMBERS to
a .json path to save numbers.
"""
import json
import os
import sys
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import gn_link, gpu_live, links, live, particles, safe_errors  # noqa: E402

print("CodeNodes test: template switching and the paused preview (this window closes by itself)", flush=True)
_checks = 0
NUMBERS = {}


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
    gin.location, gout.location = (-600, 0), (1800, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return obj, tree


def add(tree, kind, key, x):
    group, err = gn_link.create(kind, key, None, None)
    if err:
        raise Fail(f"{key}: {err}")
    return gn_link.insert(tree, group, (x, 0))


def settle(n=3):
    for _ in range(n):
        gn_link.sync()
        live._flush()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def ctx():
    win = bpy.context.window_manager.windows[0]
    area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), win.screen.areas[0])
    return bpy.context.temp_override(window=win, area=area)


def node(st, i):
    tree = bpy.data.node_groups[st["tree"]]
    return tree, tree.nodes[st["nodes"][i]]


# ---- phases --------------------------------------------------------------------------------------

def phase_build(st):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=12, size=4)
    bpy.context.object.name = "Ground"
    obj, tree = host("Fireflies")
    names = [('PARTICLES', "Firefly Swarm"), ('STAGE', "Wander"), ('STAGE', "Rise"), ('STAGE', "Firefly Look")]
    nodes = [add(tree, k, key, i * 300) for i, (k, key) in enumerate(names)]
    for a, b in zip(nodes, nodes[1:]):
        tree.links.new(a.outputs["Particles"], b.inputs["Particles"])
    nodes[0].inputs["Emit From"].default_value = bpy.data.objects["Ground"]
    bpy.context.scene.frame_set(40)
    settle(4)
    st["tree"], st["nodes"] = tree.name, [n.name for n in nodes]
    st["head"] = gn_link.source_of(nodes[0].node_tree).name
    check(links.CHAINS.get(st["head"], {}).get("stages", []) != [], "the firefly chain is built and live")
    return True


def phase_safe_errors(st):
    # the exact failure: an AttributeError raised on a Blender struct gets a "Did you mean" suggestion,
    # which calls dir() on the struct. The hook and report() must take the struct off the exception.
    scene = bpy.context.scene
    try:
        scene.frame_curren  # noqa: B018 (deliberately misspelled)
    except AttributeError as exc:
        safe_errors.scrub(exc)
        check(getattr(exc, "obj", None) is None, "errors on Blender data are scrubbed before printing")
    check(sys.excepthook is safe_errors._hook, "Blender's own error printing goes through the safe hook")
    return True


TEMPLATES = ["Rise", "Wander", "Blink", "Gravity", "Vortex", "Drag", "Push by Field", "Glow Look", "Wander"]


def phase_template_stress(st):
    """Switch the second node's Template many times; change inputs; undo / redo in between."""
    rounds = 0
    t0 = time.perf_counter()
    for rnd in range(3):
        for key in TEMPLATES:
            tree, nd = node(st, 1)
            nd.inputs["Template"].default_value = key
            settle(2)
            tree, nd = node(st, 1)                        # never trust the old objects after a rebuild
            obj = gn_link.source_of(nd.node_tree)
            if obj is None or obj.codenodes.template_key != key:
                raise Fail(f"switching to {key} didn't take (got {obj.codenodes.template_key if obj else None})")
            floats = [s for s in nd.inputs if s.bl_idname == "NodeSocketFloat" and not s.is_linked]
            for s in floats[:2]:
                s.default_value = s.default_value * 1.5 + 0.1
            settle(1)
            # also switch a neighbour, so two groups rebuild in the same pass
            _t, nb = node(st, 2)
            nb.inputs["Template"].default_value = "Rise" if key != "Rise" else "Drag"
            settle(1)
            rounds += 1
        with ctx():
            bpy.ops.ed.undo_push(message="stress")
            bpy.ops.ed.undo()
        settle(2)
        with ctx():
            bpy.ops.ed.redo()
        settle(2)
    NUMBERS["template_switches"] = rounds
    NUMBERS["template_stress_s"] = round(time.perf_counter() - t0, 1)
    tree, _ = node(st, 0)
    chain = [tree.nodes[n] for n in st["nodes"]]
    linked = all(any(l.from_node == a and l.to_node == b for l in tree.links) for a, b in zip(chain, chain[1:]))
    check(linked, f"after {rounds} template switches (with undo / redo) the chain is still wired end to end")
    _t, nd = node(st, 1)
    nd.inputs["Template"].default_value = "Wander"
    _t, nb = node(st, 2)
    nb.inputs["Template"].default_value = "Rise"
    settle(3)
    obj = gn_link.source_of(node(st, 1)[1].node_tree)
    check(obj.codenodes.template_key == "Wander" and not obj.codenodes.last_error,
          "and it ends back on Wander with no errors")
    return True


def _positions(sim_name):
    sim = particles._sims.get(sim_name)
    if sim is None:
        return None
    state = sim.read(0, None, (), 0.0, 0)
    return np.asarray(state["position"], np.float64)


def _speed(p1, p2, frames, fps):
    if p1 is None or p2 is None or len(p1) != len(p2) or frames <= 0:
        return None
    d = np.linalg.norm(p2 - p1, axis=1)
    d = d[d < np.percentile(d, 95)]            # leave out particles that respawned in between
    return float(np.median(d)) * fps / frames


def phase_preview_starts(st):
    scene = bpy.context.scene
    st.setdefault("wait0", time.perf_counter())
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    if time.perf_counter() - st["wait0"] < 1.5:
        return False
    check(not gpu_live._playing() and gpu_live.preview_active(scene),
          "with the timeline paused, the viewport runs its preview clock")
    check(gpu_live.view_frame(scene) > scene.frame_current and scene.frame_current == 40,
          f"the preview moves on (view frame {gpu_live.view_frame(scene)}) while the scene stays on frame 40")
    return True


def _sample(st, key):
    """Two position samples ~0.5 s apart on the preview sim; returns the speed when both are in."""
    scene = bpy.context.scene
    name = st["head"] + gpu_live.PREVIEW_SUFFIX
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    s = st.setdefault(key, {})
    now = time.perf_counter()
    if "p1" not in s:
        s["p1"], s["f1"], s["t1"] = _positions(name), gpu_live.view_frame(scene), now
        return None
    if now - s["t1"] < 0.5:
        return None
    p2, f2 = _positions(name), gpu_live.view_frame(scene)
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    sp = _speed(s["p1"], p2, f2 - s["f1"], fps)
    s.clear()
    return sp


def phase_speed_before(st):
    sp = _sample(st, "before")
    if sp is None:
        return False
    NUMBERS["wander_speed_before"] = round(sp, 4)
    st["speed_before"] = sp
    return True


def phase_change_slider(st):
    _t, nd = node(st, 1)
    nd.inputs["strength"].default_value = 4.0
    nd.inputs["calm"].default_value = 0.5
    st["changed_at"] = time.perf_counter()
    return True


def phase_speed_after(st):
    sp = _sample(st, "after")
    if sp is None:
        return False
    waited = time.perf_counter() - st["changed_at"]
    if sp < st["speed_before"] * 1.5 and waited < 3.0:
        return False                                   # give it a moment, measured below
    NUMBERS["wander_speed_after"] = round(sp, 4)
    NUMBERS["wander_change_visible_s"] = round(waited, 2)
    check(sp > st["speed_before"] * 1.5,
          f"dragging Wander's strength while paused visibly changes the motion "
          f"(median speed {st['speed_before']:.3f} → {sp:.3f} m/s)")
    check(waited <= 1.5, f"and it shows within about a second ({waited:.2f} s, sampling included)")
    real = particles._sims.get(st["head"])
    check(real is None or real.frame in (None, 40),
          "the real simulation stays on the scene frame (renders and To Geometry are untouched)")
    return True


def phase_playback(st):
    scene = bpy.context.scene
    with ctx():
        bpy.ops.screen.animation_play()
    st.setdefault("play0", time.perf_counter())
    if time.perf_counter() - st["play0"] < 0.8:
        return False
    playing_preview = gpu_live.preview_active(scene)
    with ctx():
        bpy.ops.screen.animation_cancel(restore_frame=True)
    check(not playing_preview, "during playback the preview clock steps aside and the real frame is shown")
    return True


PLAN = [phase_build, phase_safe_errors, phase_template_stress, phase_preview_starts, phase_speed_before,
        phase_change_slider, phase_speed_after, phase_playback]
STATE = {}


def finish(exc):
    if NUMBERS and os.environ.get("CODENODES_NUMBERS"):
        with open(os.environ["CODENODES_NUMBERS"], "w") as fh:
            json.dump(NUMBERS, fh, indent=1)
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
        return 0.1
    except Exception as exc:
        finish(exc)
        return None


def main():
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.app.timers.register(driver, first_interval=1.0, persistent=True)


main()
