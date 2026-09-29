"""Nodes first, and no Start button.

    blender --factory-startup --python tests/test_link.py      (needs a window: GPU)

- Add › Mesh › Code Mesh / Shape / Particles make objects whose Geometry Nodes tree holds
  a code node wired to the output, with the code's sliders as inputs on that node.
- The 3D view sidebar draws those nodes' inputs and buttons; its buttons work by group name.
- There is no Assistant panel or Start operator any more.
- The link starts by itself (here forced with CODENODES_LINK=1, since a test imports the
  add-on rather than enabling it in Preferences), registers itself where the MCP server
  looks, answers the shipped MCP client with no token given, and unregisters on stop.
"""
import json
import os
import sys
import tempfile
import threading
import time
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="codenodes_home_")
os.environ["CODENODES_HOME"] = HOME
os.environ["CODENODES_LINK"] = "1"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mcp"))
import codenodes  # noqa: E402
from codenodes import gn_link, server, ui  # noqa: E402
from codenodes_mcp import connection  # noqa: E402

_checks = 0


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


class FakeLayout:
    """Stands in for a UILayout so a panel's draw() can run in a test."""

    def __init__(self, log):
        self.log = log
        self.scale_y = 1.0
        self.alert = False
        self.enabled = True
        self.operator_context = 'INVOKE_DEFAULT'

    def _child(self, *a, **k):
        return FakeLayout(self.log)

    box = row = column = split = _child

    def label(self, text="", **k):
        self.log.append(("label", text))

    def prop(self, data, name, text=None, **k):
        self.log.append(("prop", text or name, getattr(data, name, None)))

    def operator(self, idname, text="", **k):
        self.log.append(("op", idname, text))

        class Props:
            pass
        return Props()

    def menu(self, idname, text="", **k):
        self.log.append(("menu", idname, text))

    def separator(self, **k):
        pass


def draw_sidebar(context):
    log = []

    class Panel:
        layout = FakeLayout(log)
    ui.CODENODES_PT_main.draw(Panel(), context)
    return log


def check_add_objects():
    ctx = bpy.context
    for name in ("Cube", "Camera", "Light"):
        if name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[name])
    check(not hasattr(bpy.types, "CODENODES_PT_server") and "server" not in dir(bpy.ops.codenodes),
          "there is no Assistant panel or Start button")
    labels = []

    class Menu:
        layout = FakeLayout(labels)
    from codenodes import ops
    ops.menu_add(Menu(), ctx)
    texts = [e[2] for e in labels if e[0] == "op"]
    check(texts[:3] == ["GPU Particles", "GPU Surface", "Code Shape"] and
          all(e[1] == "codenodes.add_object" for e in labels if e[0] == "op"),
          f"Add › Mesh lists the node versions first ({texts})")
    check(any(e[0] == "menu" and e[1] == "CODENODES_MT_add_legacy" for e in labels),
          "and keeps the old scripted objects in a submenu")

    made = {}
    for kind in ("MESH", "SHAPE", "PARTICLES"):
        # a GPU Surface is drawn live; with To Geometry after it the object gets the real donut
        r = bpy.ops.codenodes.add_object(kind=kind, make_real=(kind == "MESH"))
        obj = ctx.view_layer.objects.active
        found = gn_link.host_code_nodes(obj)
        check(r == {'FINISHED'} and len(found) == 1,
              f"Add {kind.title()} makes '{obj.name}' with a code node in its Geometry Nodes")
        tree, node = found[0]
        out = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
        real = next((n for n in tree.nodes if n.type == 'GROUP' and gn_link.is_make_real(n.node_tree)), None)
        if kind == "MESH":
            check(real is not None and any(l.from_node == node and l.to_node == real for l in tree.links)
                  and any(l.from_node == real and l.to_node == out for l in tree.links),
                  f"  wired through To Geometry to the output ({tree.name} ← {node.node_tree.name})")
        elif kind == "PARTICLES":
            check(node.outputs[0].bl_idname == "NodeSocketBundle"
                  and not any(l.from_node == node and l.to_node == out for l in tree.links),
                  f"  particles are drawn live; their Particles socket reaches the output only through "
                  f"To Geometry ({node.node_tree.name})")
        else:
            check(any(l.from_node == node and l.to_node == out for l in tree.links),
                  f"  wired to the output ({tree.name} ← {node.node_tree.name})")
        made[kind] = (obj, tree, node)
    obj, tree, node = made['MESH']
    gn_link.sync()
    real = next(n for n in tree.nodes if n.type == 'GROUP' and gn_link.is_make_real(n.node_tree))
    check("major" in node.inputs and "Template" in node.inputs and "Resolution" in real.inputs,
          f"the donut's sliders and template are on its node, Resolution on To Geometry ({[s.name for s in node.inputs][:4]} / {[s.name for s in real.inputs]})")
    from codenodes import live as _live
    _live.rebuild(gn_link.source_of(node.node_tree))
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    size = max(ev.dimensions)
    check(abs(size - 2 * (0.8 + 0.3)) < 0.05, f"and the object shows the donut ({size:.3f} m across)")
    return made


def check_sidebar(made):
    ctx = bpy.context
    obj, tree, node = made['MESH']
    for o in ctx.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    ctx.view_layer.objects.active = obj
    log = draw_sidebar(ctx)
    ops_drawn = {e[1] for e in log if e[0] == "op"}
    props = {e[1]: e[2] for e in log if e[0] == "prop"}
    check({"codenodes.gn_edit_code", "codenodes.show_in_gn"} <= ops_drawn,
          "the 3D sidebar shows Edit Code and Show Nodes for the code node (settings live on the node)")
    node.inputs["major"].default_value = 1.2
    t0 = time.time()
    while time.time() - t0 < 3:
        gn_link.sync()
        from codenodes import live
        src = gn_link.source_of(node.node_tree)
        if abs(src.codenodes.params["major"].value - 1.2) < 1e-6:
            break
    r = bpy.ops.codenodes.gn_rebuild(group=node.node_tree.name)
    ctx.view_layer.update()
    size = max(obj.evaluated_get(ctx.evaluated_depsgraph_get()).dimensions)
    check(r == {'FINISHED'} and abs(size - 2 * (1.2 + 0.3)) < 0.06,
          f"a value typed on the node rebuilds it through the named-group Rebuild ({size:.3f} m)")
    r = bpy.ops.codenodes.gn_edit_code(group=node.node_tree.name)
    text_areas = [a for a in ctx.screen.areas if a.type == 'TEXT_EDITOR']
    src = gn_link.source_of(node.node_tree)
    check(r == {'FINISHED'} and text_areas and text_areas[0].spaces.active.text == src.codenodes.text,
          "Edit Code (by group) opens the node's code in a Text Editor")
    r = bpy.ops.codenodes.show_in_gn(group=node.node_tree.name)
    node_areas = [a for a in ctx.screen.areas if a.type == 'NODE_EDITOR']
    check(r == {'FINISHED'} and node_areas and node_areas[0].spaces.active.tree_type == "GeometryNodeTree"
          and tree.nodes.active == node, "Show Nodes opens Geometry Nodes with the code node selected")
    empty = bpy.data.objects.new("Plain", None)
    ctx.scene.collection.objects.link(empty)
    ctx.view_layer.objects.active = empty
    log = draw_sidebar(ctx)
    check(any(e[0] == "op" and e[1] == "codenodes.add_object" for e in log),
          "with nothing made by code selected, the sidebar offers the three Add buttons")


def check_link(done):
    """Runs on a thread: the shipped MCP client talks to the auto-started link."""
    out = done
    try:
        live = connection.instances()
        out["instances"] = live
        c = connection.Connection(token=None, timeout=120)
        out["status"] = c.call("status")
        out["scene"] = c.call("scene")
        out["made"] = c.call("code_node", kind="mesh", template="Gyroid Ball", name="Lattice")
        out["edit"] = c.call("code_node", object=out["made"]["object"], values={"cells": 4.0})
        c.close()
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["finished"] = True


def run():
    made = check_add_objects()
    check_sidebar(made)
    # the link: started by the add-on's own timer, not by the test
    t0 = time.time()
    state = {"t0": t0}

    def wait_link():
        info = server.status()
        if not info["running"]:
            if time.time() - t0 > 10:
                return finish(Fail("the link did not start by itself"))
            return 0.2
        path = server.instance_path()
        try:
            check(True, f"the link started by itself on port {info['port']}")
            with open(path, encoding="utf-8") as fh:
                reg = json.load(fh)
            check(reg["port"] == info["port"] and reg.get("token") and reg["pid"] == os.getpid(),
                  "and registered its port and token where the MCP server looks")
        except Exception as exc:
            return finish(exc)
        threading.Thread(target=check_link, args=(state,), daemon=True).start()
        bpy.app.timers.register(wait_client, first_interval=0.2)
        return None

    def wait_client():
        if not state.get("finished"):
            if time.time() - t0 > 180:
                return finish(Fail("the MCP client did not finish"))
            return 0.2
        try:
            check("error" not in state, f"the shipped MCP client found Blender with no token given ({state.get('error', 'ok')})")
            check(len(state["instances"]) == 1 and state["status"]["running"], "status through the link")
            made = state["made"]
            check(made.get("ok") and made.get("tree") and "cells" in made["inputs"],
                  f"code_node over the link: {made.get('object')} ({made.get('error', 'ok')})")
            check(state["edit"].get("ok") and abs(state["edit"]["inputs"]["cells"] - 4.0) < 1e-6,
                  "and its values change over the link")
            path = server.instance_path()
            server.stop()
            check(not os.path.exists(path), "stopping removes the registration")
            os.environ["CODENODES_LINK"] = "0"
            check(not server.wanted(), "CODENODES_LINK=0 keeps it off")
            os.environ.pop("CODENODES_LINK")
            check(server._pref_enabled() is None and not server.wanted(),
                  "and imported by a script (not enabled in Preferences) it stays off")
        except Exception as exc:
            return finish(exc)
        return finish(None)

    bpy.app.timers.register(wait_link, first_interval=0.2)


def finish(exc):
    if exc is None:
        print(f"\nALL {_checks} CHECKS PASSED", flush=True)
    elif isinstance(exc, Fail):
        print(f"FAIL: {exc}", flush=True)
    else:
        traceback.print_exception(exc)
        print("FAIL: exception", flush=True)
    bpy.ops.wm.quit_blender()
    return None


def main():
    codenodes.register()

    def tick():
        try:
            run()
        except Exception as exc:
            finish(exc)
        return None

    bpy.app.timers.register(tick, first_interval=0.5)


main()
