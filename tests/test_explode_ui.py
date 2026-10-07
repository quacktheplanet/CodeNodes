"""Explode in the node editor, with a window: the operators from the editor, double-click to go into an
exploded node, and Collapse from inside it.

    blender --factory-startup --enable-event-simulate --python tests/test_explode_ui.py

(On Linux with no screen: eval "$(tools/testing/xvfb_linux.sh)" and add --gpu-backend vulkan.)
Prints each check, ends with "ALL n CHECKS PASSED" or "FAIL: ...", then quits.
"""
import os
import sys
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codenodes  # noqa: E402
from codenodes import explode_ops, gn_link, live  # noqa: E402

_checks = 0
STATE = {}
SWIRL = """// Swirl Push
// @in float speed 1.0 0 4
const float CALM = 0.5;
vec3 curl(vec3 p) { return vec3(-p.y, p.x, 0.0) * speed; }
void behave(inout Particle p, float dt) { p.velocity = curl(p.position) * CALM; }
"""


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


def editor():
    win = bpy.context.window_manager.windows[0]
    area = max(win.screen.areas, key=lambda a: a.width * a.height)
    if area.type != 'NODE_EDITOR' or area.ui_type != 'GeometryNodeTree':
        area.ui_type = 'GeometryNodeTree'
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def setup():
    codenodes.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    obj = bpy.data.objects.new("UI", bpy.data.meshes.new("UI"))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    tree = bpy.data.node_groups.new("UI Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.nodes.new("NodeGroupOutput").location = (600, 0)
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    g, _err = gn_link.create('PARTICLES', "Swirl")
    src = gn_link.insert(tree, g, (-300, 0))
    g, _err = gn_link.create('STAGE', None, "Swirl Push", SWIRL)
    node = gn_link.insert(tree, g, (0, 0))
    tree.links.new(src.outputs["Particles"], node.inputs["Particles"])
    settle()
    win, area, region = editor()
    area.spaces.active.node_tree = tree
    STATE.update(tree=tree.name, node=node.name)


def ctx():
    win, area, region = editor()
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active)


def phase_explode():
    tree = bpy.data.node_groups[STATE["tree"]]
    node = tree.nodes[STATE["node"]]
    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node
    with ctx():
        check(bpy.ops.codenodes.explode.poll(), "Explode is offered for the selected code node in the editor")
        check(not bpy.ops.codenodes.collapse.poll(), "Collapse isn't (nothing exploded yet)")
        bpy.ops.codenodes.explode()
    settle()
    w = tree.nodes.active
    check(w is not None and explode_ops.is_exploded(w.node_tree), f"the node is now its pieces' group ({w.name})")
    STATE["wrapper"] = w.name
    return True


def phase_double_click():
    """Double-click is Blender's own event (simulated events don't make one), so check the key map item and
    run what it runs: CodeNodes' Open Group on the selected exploded node."""
    tree = bpy.data.node_groups[STATE["tree"]]
    w = tree.nodes[STATE["wrapper"]]
    kc = bpy.context.window_manager.keyconfigs.addon
    items = [k for km in kc.keymaps if km.name == "Node Editor" for k in km.keymap_items
             if k.idname == "codenodes.enter_group" and k.type == 'LEFTMOUSE' and k.value == 'DOUBLE_CLICK']
    check(items and items[0].active, "double-click in the node editor is mapped to Open Group")
    for n in tree.nodes:
        n.select = False
    w.select = True
    tree.nodes.active = w
    with ctx():
        check(not bpy.ops.codenodes.edit_code_popup.poll(),
              "on an exploded node, double-click doesn't open a code pop-up (that's for single code nodes)")
        check(bpy.ops.codenodes.enter_group.poll(), "Open Group applies to it")
        bpy.ops.codenodes.enter_group()
    return True


def phase_inside():
    win, area, region = editor()
    space = area.spaces.active
    if os.environ.get("CN_DEBUG"):
        tree = bpy.data.node_groups[STATE["tree"]]
        w = tree.nodes[STATE["wrapper"]]
        print("DEBUG click", STATE["click"], "area", area.x, area.y, area.width, area.height, "region", region.x,
              region.y, "node", tuple(w.location), w.dimensions[:], "active", tree.nodes.active.name, w.select,
              flush=True)
        with bpy.context.temp_override(window=win, area=area):
            bpy.ops.screen.screenshot_area(filepath="/tmp/cn_ui.png")
    inside = space.edit_tree
    check(inside is not None and explode_ops.is_exploded(inside),
          f"it goes into the exploded node, like a node group ({inside.name if inside else None})")
    labels = {(n.label or n.name).split(" · ")[0] for n in inside.nodes}
    check({"CALM", "curl"} <= labels, f"inside: its pieces ({sorted(labels)})")
    with ctx():
        check(bpy.ops.codenodes.collapse.poll(), "Collapse is offered from inside the group")
        bpy.ops.codenodes.collapse()
    settle()
    tree = bpy.data.node_groups[STATE["tree"]]
    code_nodes = [n for n in tree.nodes if n.type == 'GROUP' and gn_link.is_code_group(n.node_tree)]
    src = next((gn_link.source_of(n.node_tree) for n in code_nodes
                if gn_link.source_of(n.node_tree).codenodes.text.as_string() == SWIRL), None)
    check(src is not None and space.edit_tree == tree and not any(
        n.type == 'GROUP' and explode_ops.is_exploded(n.node_tree) for n in tree.nodes),
          "Collapse goes back up and leaves one code node with the original code")
    return True


PLAN = [phase_explode, phase_double_click, phase_inside]


def driver():
    try:
        if PLAN[0]():
            PLAN.pop(0)
        if not PLAN:
            print(f"\nALL {_checks} CHECKS PASSED", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        return 0.5
    except Fail as exc:
        print(f"FAIL: {exc}", flush=True)
    except Exception:
        traceback.print_exc()
        print("FAIL: exception", flush=True)
    bpy.ops.wm.quit_blender()
    return None


try:
    setup()
    bpy.app.timers.register(driver, first_interval=1.0)
except Exception:
    traceback.print_exc()
    print("FAIL: exception", flush=True)
