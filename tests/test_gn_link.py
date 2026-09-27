"""Code nodes inside Geometry Nodes, in real Blender. Needs a window (the GPU isn't
available with -b) and, for Make Native, ExpressNode as a sibling checkout (../ExpressNode)
or at CODENODES_EXPRESSION_NODES:

    blender --factory-startup --python tests/test_gn_link.py

Runs from a timer once the window is up, prints each check, ends with
"ALL n CHECKS PASSED" or "FAIL: ...", then quits. Set CODENODES_SHOT to a .png path to
also save a screenshot of the Geometry Nodes editor.
"""
import os
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.environ.get("CODENODES_EXPRESSION_NODES",
                                  os.path.join(os.path.dirname(ROOT), "ExpressNode")))
import codenodes  # noqa: E402
from codenodes import gn_link, gn_ui, live, sampler  # noqa: E402

_checks = 0
SAVE = os.path.join(tempfile.gettempdir(), "codenodes_gn_link_test.blend")


class Fail(Exception):
    pass


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


def evaluated(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    return obj.evaluated_get(dg).data


def radius_xy(obj):
    me = evaluated(obj)
    if not len(me.vertices):
        return 0.0
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return float(np.linalg.norm(co[:, :2], axis=1).max())


def source_radius(group):
    obj = gn_link.source_of(group)
    co = np.empty(len(obj.data.vertices) * 3, np.float32)
    obj.data.vertices.foreach_get("co", co)
    return float(np.linalg.norm(co.reshape(-1, 3)[:, :2], axis=1).max())


def node_editor():
    """An area switched to the Geometry Nodes editor, pinned to the host tree."""
    screen = bpy.context.window.screen
    area = next((a for a in screen.areas if a.type == 'NODE_EDITOR'), None)
    if area is None:
        area = next(a for a in screen.areas if a.type == 'DOPESHEET_EDITOR')
        area.type = 'NODE_EDITOR'
    space = area.spaces.active
    space.tree_type = 'GeometryNodeTree'
    space.pin = True
    space.node_tree = bpy.data.node_groups["Host Tree"]
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return area, region


def ctx(area, region):
    return bpy.context.temp_override(window=bpy.context.window, area=area, region=region)


def setup():
    host = bpy.data.objects.new("Host", bpy.data.meshes.new("Host"))
    bpy.context.scene.collection.objects.link(host)
    bpy.context.view_layer.objects.active = host
    tree = bpy.data.node_groups.new("Host Tree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gout = tree.nodes.new("NodeGroupOutput")
    gout.location = (600, 0)
    host.modifiers.new("GeometryNodes", 'NODES').node_group = tree
    return host, tree


def phase0(state):
    host, tree = setup()
    area, region = node_editor()
    state["area"] = area
    check("CODENODES_MT_gn_add" in dir(bpy.types) and gn_ui._add_menu in bpy.types.NODE_MT_add._dyn_ui_initialize(),
          "Add › CodeNodes is in the Geometry Nodes Add menu")
    with ctx(area, region):
        res = bpy.ops.codenodes.gn_add('EXEC_DEFAULT', kind='MESH', template="Donut", use_transform=False)
    node = tree.nodes.active
    check(res == {'FINISHED'} and node is not None and node.type == 'GROUP', "the Add operator inserts a group node")
    group = node.node_tree
    check(group.name.startswith("Code · Donut") and gn_link.is_code_group(group),
          f"it is a code group named after its code ('{group.name}')")
    src = gn_link.source_of(group)
    col = bpy.data.collections.get(gn_link.SOURCES)
    check(src is not None and col is not None and src.name in col.objects and col.hide_viewport and col.hide_render,
          "its source object lives in the hidden 'CodeNodes Sources' collection")
    names = [s.name for s in node.inputs]
    check(names == ["Resolution", "major", "minor"], f"the node's inputs are the code's sliders ({names})")
    item = next(i for i in group.interface.items_tree if i.item_type == 'SOCKET' and i.name == "major")
    check(abs(item.default_value - 0.8) < 1e-6 and abs(item.min_value - 0.2) < 1e-6
          and abs(item.max_value - 2.0) < 1e-6, "inputs carry the @param default, min and max")
    xform = tree.nodes.new("GeometryNodeTransform")
    xform.location = (300, 0)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    tree.links.new(node.outputs["Geometry"], xform.inputs["Geometry"])
    tree.links.new(xform.outputs["Geometry"], gout.inputs["Geometry"])
    r = radius_xy(host)
    check(abs(r - 1.1) < 0.05, f"the code's geometry flows through the tree (outer radius {r:.3f}, expect 1.1)")
    node.inputs["major"].default_value = 1.2
    state.update(node=node.name, group=group.name, t=time.perf_counter())


def phase1(state):
    """A socket value change rebuilds by itself (depsgraph handler + debounce)."""
    host = bpy.data.objects["Host"]
    r = radius_xy(host)
    if r < 1.45 and time.perf_counter() - state["t"] < 6:
        return False
    check(abs(r - 1.5) < 0.05, f"changing an input on the node rebuilds live (outer radius {r:.3f}, expect 1.5)")
    group = bpy.data.node_groups[state["group"]]
    src = gn_link.source_of(group)
    src.codenodes.text.from_string(src.codenodes.text.as_string()
                                   .replace("// @param minor", "// @param squash 1.0 0.2 3.0\n// @param minor")
                                   .replace("return sdTorus(p, major, minor);",
                                            "return sdTorus(p / vec3(squash, squash, 1.0), major, minor) * squash;"))
    state["t"] = time.perf_counter()
    return True


def phase2(state):
    """Editing the code adds the new slider to the node and rebuilds."""
    tree = bpy.data.node_groups["Host Tree"]
    node = tree.nodes[state["node"]]
    names = [s.name for s in node.inputs]
    if "squash" not in names and time.perf_counter() - state["t"] < 8:
        return False
    check(names == ["Resolution", "major", "squash", "minor"], f"a code edit adds its new slider to the node ({names})")
    check(abs(node.inputs["major"].default_value - 1.2) < 1e-6, "values typed on the node survive the code edit")
    node.inputs["squash"].default_value = 1.25
    gn_link.sync()
    live._flush()
    r = radius_xy(bpy.data.objects["Host"])
    check(abs(r - 1.5 * 1.25) < 0.07, f"the new slider works (outer radius {r:.3f}, expect {1.5 * 1.25:.3f})")
    return True


def phase3(state):
    host = bpy.data.objects["Host"]
    tree = bpy.data.node_groups["Host Tree"]
    area = state["area"]
    region = next(r for r in area.regions if r.type == 'WINDOW')
    node = tree.nodes[state["node"]]
    group = node.node_tree
    src = gn_link.source_of(group)

    # resolution input
    node.inputs["Resolution"].default_value = 48
    gn_link.sync()
    live._flush()
    check(src.codenodes.resolution == 48, "the Resolution input drives the sampling resolution")
    node.inputs["Resolution"].default_value = 96

    # duplicate with the editor's own operator
    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node
    with ctx(area, region):
        bpy.ops.node.duplicate()
    dup = tree.nodes.active
    check(dup != node and dup.node_tree == group, "Shift+D first makes a node sharing the group")
    gn_link.sync()
    check(dup.node_tree != group and gn_link.source_of(dup.node_tree) not in (None, src),
          f"the next sync gives the copy its own group and source ('{dup.node_tree.name}')")
    dup.inputs["major"].default_value = 0.5
    gn_link.sync()
    live._flush()
    ra, rb = source_radius(group), source_radius(dup.node_tree)
    check(abs(ra - 1.875) < 0.07 and abs(rb - 1.0) < 0.07,
          f"the two nodes are independent (original {ra:.3f}, copy {rb:.3f})")
    tree.nodes.remove(dup)

    # a value linked from a Value node
    val = tree.nodes.new("ShaderNodeValue")
    val.outputs[0].default_value = 1.0
    tree.links.new(val.outputs[0], node.inputs["major"])
    gn_link.sync()
    live._flush()
    check(abs(src.codenodes.params["major"].value - 1.0) < 1e-6, "a value linked in from a Value node is followed")
    tree.links.remove(next(l for l in tree.links if l.to_socket == node.inputs["major"]))
    tree.nodes.remove(val)

    # the other kinds
    with ctx(area, region):
        bpy.ops.codenodes.gn_add('EXEC_DEFAULT', kind='SHAPE', template="Desk Lamp", use_transform=False)
    lamp = tree.nodes.active
    lsrc = gn_link.source_of(lamp.node_tree)
    check(lamp.node_tree.name.startswith("Code · Desk Lamp") and len(lsrc.data.polygons) > 100
          and "height" in [s.name for s in lamp.inputs], f"Code Shape inserts too ({len(lsrc.data.polygons)} faces)")
    with ctx(area, region):
        bpy.ops.codenodes.gn_add('EXEC_DEFAULT', kind='PARTICLES', template="Fountain", use_transform=False)
    fountain = tree.nodes.active
    psrc = gn_link.source_of(fountain.node_tree)
    bpy.context.scene.frame_set(12)
    check(len(psrc.data.vertices) > 1000 and "power" in [s.name for s in fountain.inputs],
          f"Code Particles inserts and simulates ({len(psrc.data.vertices):,} points at frame 12)")
    bpy.context.scene.frame_set(1)
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    join.location = (450, -150)
    gout = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    xform = next(n for n in tree.nodes if n.type == 'TRANSFORM_GEOMETRY')
    tree.links.new(xform.outputs["Geometry"], join.inputs["Geometry"])
    lamp_move = tree.nodes.new("GeometryNodeTransform")
    lamp_move.location = (200, -250)
    lamp_move.inputs["Translation"].default_value = (2.2, 0.0, 0.0)
    lamp_move.inputs["Scale"].default_value = (3.0, 3.0, 3.0)
    tree.links.new(lamp.outputs["Geometry"], lamp_move.inputs["Geometry"])
    tree.links.new(lamp_move.outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(join.outputs["Geometry"], gout.inputs["Geometry"])
    lamp.location, fountain.location = (0, -250), (0, -500)

    # editing panel operators
    tree.nodes.active = node
    with ctx(area, region):
        ok = bpy.ops.codenodes.gn_rebuild('EXEC_DEFAULT')
    check(ok == {'FINISHED'} and not src.codenodes.last_error, "Rebuild from the sidebar runs the code")

    # renders read the stored result and never run GPU code
    calls = {"during": 0}
    real = sampler.sample

    def counting(*a, **k):
        if live._rendering():
            calls["during"] += 1
        return real(*a, **k)

    sampler.sample = counting

    def poke(*_a):
        node.inputs["major"].default_value = 1.4
        gn_link.sync()
        live.request(src)
        live._flush()

    bpy.app.handlers.render_pre.append(poke)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 96, 64, 100
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    scene.collection.objects.link(cam)
    cam.location, cam.rotation_euler = (0, -8, 4), (1.1, 0, 0)
    scene.camera = cam
    shot = os.path.join(tempfile.gettempdir(), "codenodes_gn_link_render.png")
    scene.render.filepath = shot
    bpy.ops.render.render(write_still=True)
    bpy.app.handlers.render_pre.remove(poke)
    sampler.sample = real
    check(calls["during"] == 0 and os.path.exists(shot) and not live._rendering(),
          "a render with a pending change reads the stored result: no GPU code runs during it")
    node.inputs["major"].default_value = 1.2
    gn_link.sync()
    live._flush()

    # Make Native
    with ctx(area, region):
        bpy.ops.codenodes.gn_add('EXEC_DEFAULT', kind='MESH', template="Donut", use_transform=False)
    nat = tree.nodes.active
    nat.location = (0, 250)
    nat.inputs["major"].default_value = 0.6
    gn_link.sync()
    live._flush()
    nat_src = gn_link.source_of(nat.node_tree).name
    with ctx(area, region):
        res = bpy.ops.codenodes.gn_make_native('EXEC_DEFAULT')
    if res != {'FINISHED'}:
        try:
            import coding_nodes  # noqa: F401
        except ImportError:
            raise Fail("Make Native needs ExpressNode: put it at ../ExpressNode or set CODENODES_EXPRESSION_NODES")
    ng = nat.node_tree
    check(res == {'FINISHED'} and not gn_link.is_code_group(ng) and nat_src not in bpy.data.objects
          and ng.name.startswith("Nodes · Donut"), f"Make Native swaps in plain Geometry Nodes ('{ng.name}')")
    check(abs(nat.inputs["major"].default_value - 0.6) < 1e-6, "Make Native keeps the values typed on the node")
    probe = bpy.data.objects.new("Probe", bpy.data.meshes.new("Probe"))
    bpy.context.scene.collection.objects.link(probe)
    probe.modifiers.new("N", 'NODES').node_group = ng
    probe.modifiers["N"][next(i.identifier for i in ng.interface.items_tree
                              if i.item_type == 'SOCKET' and i.name == "major")] = 0.6
    r = radius_xy(probe)
    check(abs(r - 0.9) < 0.06, f"the native nodes make the same donut (outer radius {r:.3f}, expect 0.9)")
    bpy.data.objects.remove(probe)
    tree.nodes.remove(nat)

    # screenshot of the editor with the code node wired in
    shot_path = os.environ.get("CODENODES_SHOT")
    if shot_path:
        big = next(a for a in bpy.context.window.screen.areas if a.type == 'VIEW_3D')
        big.type = 'NODE_EDITOR'
        sp = big.spaces.active
        sp.tree_type = 'GeometryNodeTree'
        sp.pin = True
        sp.node_tree = tree
        state["shot_area"] = big
        state["shot_path"] = shot_path

    bpy.ops.wm.save_as_mainfile(filepath=SAVE)
    check(os.path.exists(SAVE), "the file saves")
    return True


def take_shot(state):
    area = state.get("shot_area")
    if area is None:
        return
    region = next(r for r in area.regions if r.type == 'WINDOW')
    tree = area.spaces.active.node_tree
    donut = next((n for n in tree.nodes if n.type == 'GROUP' and n.node_tree
                  and n.node_tree.name.startswith("Code · Donut")), None)
    for n in tree.nodes:
        n.select = False
    if donut is not None:
        donut.select = True
        tree.nodes.active = donut
    area.spaces.active.show_region_ui = True
    ui = next(r for r in area.regions if r.type == 'UI')
    try:
        ui.active_panel_category = "CodeNodes"
    except (AttributeError, TypeError):
        pass
    with ctx(area, region):
        bpy.ops.node.view_all()
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    with ctx(area, region):
        bpy.ops.screen.screenshot_area(filepath=state["shot_path"])
    print(f"  screenshot: {state['shot_path']}", flush=True)


def phase4(state):
    bpy.ops.wm.open_mainfile(filepath=SAVE)
    state["t"] = time.perf_counter()
    return True


def phase5(state):
    """After reopening: the node still drives its code."""
    tree = bpy.data.node_groups.get("Host Tree")
    node = tree.nodes.get(state["node"]) if tree else None
    if node is None:
        raise Fail("the code node is missing after reopening")
    if not state.get("poked"):
        check(gn_link.is_code_group(node.node_tree) and gn_link.source_of(node.node_tree) is not None,
              "after reopening, the node still points at its code")
        node.inputs["major"].default_value = 0.9
        state["poked"] = True
        state["t"] = time.perf_counter()
        return False
    r = source_radius(node.node_tree)
    if abs(r - 1.5) > 0.07 and time.perf_counter() - state["t"] < 6:
        return False
    check(abs(r - 1.5) < 0.07, f"after reopening, changing an input still rebuilds live (outer radius {r:.3f}, expect 1.5)")

    # the 3D view's first-use buttons: a starter graph that looks like something, and Open Node Graph
    from codenodes import nodes
    view = next(a for a in bpy.context.window.screen.areas if a.type in {'VIEW_3D', 'OUTLINER'})
    vreg = next(r for r in view.regions if r.type == 'WINDOW')
    with ctx(view, vreg):
        res = bpy.ops.codenodes.new_graph()
    graph = next(t for t in bpy.data.node_groups if t.bl_idname == nodes.TREE)
    out = next(n for n in graph.nodes if n.bl_idname == "CN_NodeMeshOutput")
    target = out.target
    check(res == {'FINISHED'} and "Saturn" in graph.name and target is not None and len(target.data.polygons) > 1000
          and not out.error, f"New Node Graph makes the Saturn starter ({len(target.data.polygons):,} faces)")
    check(gn_ui.graph_for(target) == graph, "the sidebar knows which graph made that object")
    bpy.context.view_layer.objects.active = target
    with ctx(view, vreg):
        res = bpy.ops.codenodes.open_graph()
    shown = [a.spaces.active.node_tree for a in bpy.context.window.screen.areas if a.type == 'NODE_EDITOR']
    check(res == {'FINISHED'} and graph in shown, "Open Node Graph shows that graph in a Node Editor")
    return True


def main():
    codenodes.register()
    state = {"phase": 0}
    steps = [lambda s: (phase0(s), True)[1], phase1, phase2, phase3, None, phase4, phase5]

    def tick():
        try:
            step = steps[state["phase"]]
            if step is None:
                take_shot(state)
                done = True
            else:
                done = step(state)
            if done:
                state["phase"] += 1
                if state["phase"] == len(steps):
                    print(f"\nALL {_checks} CHECKS PASSED", flush=True)
                    bpy.ops.wm.quit_blender()
                    return None
                return 0.4
            return 0.3
        except Fail as exc:
            print(f"FAIL: {exc}", flush=True)
        except Exception:
            traceback.print_exc()
            print("FAIL: exception", flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=1.0, persistent=True)


main()
