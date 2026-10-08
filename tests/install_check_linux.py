"""Inside Blender, for tools/testing/install_check_linux.sh: the extension installed from its zip (not the
repo) is enabled, its operators exist, and on Blender 5.2+ a chain with a use line, a List and an
Explode runs from the installed copy. Prints "ALL n CHECKS PASSED" or "FAIL: ...".
"""
import importlib
import os
import sys
import traceback

import bpy

_checks = 0


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def main():
    mods = sorted(a.module for a in bpy.context.preferences.addons if a.module.endswith("codenodes"))
    check(mods and mods[0].startswith("bl_ext."), f"the installed extension is enabled ({mods})")
    pkg = importlib.import_module(mods[0])
    check("CodeNodes-" not in pkg.__file__ and "/apps/projects" not in pkg.__file__,
          f"it runs from the install, not the repo ({os.path.dirname(pkg.__file__)})")
    for op in ("explode", "collapse", "enter_group", "render", "edit_code_popup"):
        check(hasattr(bpy.ops.codenodes, op) and getattr(bpy.ops.codenodes, op).idname() == f"CODENODES_OT_{op}",
              f"operator codenodes.{op}")
    gpu_guard = importlib.import_module(mods[0] + ".gpu_guard")
    if not gpu_guard.available():
        print(f"(no GPU in background mode on {bpy.app.version_string}: the chain check needs 5.2+)")
        print(f"\nALL {_checks} CHECKS PASSED", flush=True)
        return
    gn_link = importlib.import_module(mods[0] + ".gn_link")
    gn_sockets = importlib.import_module(mods[0] + ".gn_sockets")
    links = importlib.import_module(mods[0] + ".links")
    live = importlib.import_module(mods[0] + ".live")
    explode_ops = importlib.import_module(mods[0] + ".explode_ops")
    obj = bpy.data.objects.new("Install", bpy.data.meshes.new("Install"))
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new("Install Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gout = tree.nodes.new("NodeGroupOutput")
    obj.modifiers.new("GeometryNodes", 'NODES').node_group = tree

    def add(kind, key, x):
        g, err = gn_link.create(kind, key)
        if err:
            raise Fail(err)
        return gn_link.insert(tree, g, (x, 0))

    swirl = add('PARTICLES', "Swirl", 0)
    push = add('STAGE', "Push by Field", 300)
    wind = add('STAGE', "Wind Field", 0)
    table = add('STAGE', "Attractors", 0)
    pull = add('STAGE', "Attract to List", 600)
    tree.links.new(swirl.outputs["Particles"], push.inputs["Particles"])
    tree.links.new(push.outputs["Particles"], pull.inputs["Particles"])
    tree.links.new(wind.outputs["wind"], push.inputs["field"])
    tree.links.new(table.outputs[gn_sockets.TABLE_OUT], pull.inputs["targets"])
    tg = gn_link.insert_make_real(tree, pull)
    tree.links.new(tg.outputs["Geometry"], gout.inputs[0])
    push.inputs[gn_sockets.use_socket("field")].default_value = "field(p) * 0.5"
    for _ in range(6):
        gn_link.sync()
        live._flush()
    head = gn_link.source_of(swirl.node_tree)
    comp, _v = links.composite(head)
    check("wired_field(p) * 0.5" in comp.source and "_targets_count() { return 3; }" in comp.source,
          "a chain with a use line and a List compiles from the install")
    check(not head.codenodes.last_error, f"and runs on the GPU ({head.codenodes.last_error[:80]})")
    w = explode_ops.explode_node(tree, swirl)
    for _ in range(4):
        gn_link.sync()
        live._flush()
    check(explode_ops.is_exploded(w.node_tree), "Explode works from the install")
    explode_ops.collapse_node(tree, w)
    check(gn_link.source_of(next(n for n in tree.nodes if n.type == 'GROUP' and n.node_tree is not None
                                 and gn_link.is_code_group(n.node_tree)
                                 and gn_link.source_of(n.node_tree).name == head.name).node_tree).codenodes
          .text.as_string() == gn_link.template('PARTICLES', "Swirl"), "and Collapse gives the starter back")
    print(f"\nALL {_checks} CHECKS PASSED", flush=True)


try:
    main()
except Fail as exc:
    print(f"FAIL: {exc}", flush=True)
except Exception:
    traceback.print_exc()
    print("FAIL: exception", flush=True)
sys.stdout.flush()
os._exit(0)
