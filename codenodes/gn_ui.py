"""Geometry Nodes editor side of CodeNodes: Add › CodeNodes, the To Geometry node, showing a code
node's code, and the small sidebar fallback. The work is done in gn_link.

Everything you set lives on the nodes themselves (see gn_sockets). The sidebar only keeps what
Blender can't put on a node: buttons (Edit Code, Add To Geometry) and the full error text.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import gn_link, live


def _in_geometry_nodes(context):
    space = context.space_data
    return getattr(space, "type", "") == 'NODE_EDITOR' and getattr(space, "tree_type", "") == "GeometryNodeTree"


def _place_at_cursor(context, event):
    space = context.space_data
    if getattr(space, "edit_tree", None) is not None and context.region.type == 'WINDOW':
        area = context.area
        px, py = int(area.width / 10), int(area.height / 10)
        space.cursor_location_from_region(min(max(px, event.mouse_region_x), area.width - px),
                                          min(max(py, event.mouse_region_y), area.height - py))


class CODENODES_OT_gn_add(bpy.types.Operator):
    bl_idname = "codenodes.gn_add"
    bl_label = "Add Code Node"
    bl_description = ("Add a node run by code. Its settings and the code's sliders are inputs on the node; "
                      "its code opens in a Text Editor when you select it")
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Kind", items=[(k, v[0], v[1]) for k, v in gn_link.KINDS.items()])
    template: StringProperty(name="Template", description="Starting code (empty: the default one)")
    use_transform: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _in_geometry_nodes(context)

    def execute(self, context):
        tree = gn_link.ensure_tree(context)
        if tree is None:
            self.report({'ERROR'}, "select a mesh object (or open a Geometry Nodes tree) first")
            return {'CANCELLED'}
        group, err = gn_link.create(self.kind, self.template or None)
        space = context.space_data
        loc = tuple(space.cursor_location) if getattr(space, "edit_tree", None) is not None else (0.0, 0.0)
        node = gn_link.insert(tree, group, loc)
        gn_link.sync()
        if err:
            self.report({'WARNING'}, f"{node.node_tree.name}: {err.splitlines()[0]}")
        return {'FINISHED'}

    def invoke(self, context, event):
        _place_at_cursor(context, event)
        result = self.execute(context)
        space = context.space_data
        if self.use_transform and 'FINISHED' in result and getattr(space, "edit_tree", None) is not None:
            bpy.ops.node.translate_attach_remove_on_cancel('INVOKE_DEFAULT')
        return result


class CODENODES_OT_gn_add_make_real(bpy.types.Operator):
    bl_idname = "codenodes.gn_add_make_real"
    bl_label = "To Geometry"
    bl_description = ("Add a To Geometry node. Code nodes run and draw on the GPU by default; this is the one "
                      "node that turns the GPU node or chain before it into real geometry (To Points after "
                      "particles, To Mesh after a surface or mesh chain) that later nodes and renders can use, "
                      "like Realize Instances. With a GPU node selected, it goes right after it")
    bl_options = {'REGISTER', 'UNDO'}

    use_transform: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    group: StringProperty(options={'HIDDEN', 'SKIP_SAVE'},
                          description="Put it after the node using this code group (empty: the active node)")

    @classmethod
    def poll(cls, context):
        return _in_geometry_nodes(context) or context.area is None or context.area.type == 'VIEW_3D'

    def execute(self, context):
        tree, after = None, None
        if self.group:
            group = bpy.data.node_groups.get(self.group)
            tree, after = gn_link.find_user(group) if group is not None else (None, None)
        else:
            tree = getattr(context.space_data, "edit_tree", None)
            after = tree.nodes.active if tree is not None else None
            if after is not None and not (after.type == 'GROUP' and gn_link.is_code_group(after.node_tree)):
                after = None
        if tree is None:
            self.report({'ERROR'}, "open a Geometry Nodes tree first")
            return {'CANCELLED'}
        if after is not None:
            node = gn_link.insert_make_real(tree, after)
            self.use_transform = False
        else:
            group = gn_link.build_make_real()
            space = context.space_data
            loc = tuple(space.cursor_location) if getattr(space, "edit_tree", None) is not None else (0.0, 0.0)
            node = gn_link.insert(tree, group, loc)
            node.width = 160
        gn_link.sync()
        return {'FINISHED'}

    def invoke(self, context, event):
        if _in_geometry_nodes(context):
            _place_at_cursor(context, event)
        result = self.execute(context)
        space = context.space_data
        if self.use_transform and 'FINISHED' in result and getattr(space, "edit_tree", None) is not None:
            bpy.ops.node.translate_attach_remove_on_cancel('INVOKE_DEFAULT')
        return result


def _active(context, op=None, group_name=""):
    """(node, group, source, tree) for the code node an operator acts on: the named group when
    `group_name` is set (buttons in the 3D view), else the Node Editor's active node."""
    if group_name:
        group = bpy.data.node_groups.get(group_name)
        obj = gn_link.source_of(group)
        tree, node = gn_link.find_user(group) if obj is not None else (None, None)
    else:
        node, group, obj = gn_link.code_node(context)
        tree = getattr(getattr(context, "space_data", None), "edit_tree", None)
    if obj is None and op is not None:
        op.report({'ERROR'}, "select a code node (added from Add › CodeNodes)")
    return node, group, obj, tree


_GROUP = StringProperty(name="Code Node", description="The code node group (empty: the active node)",
                        options={'HIDDEN', 'SKIP_SAVE'})


class CODENODES_OT_add_object(bpy.types.Operator):
    bl_idname = "codenodes.add_object"
    bl_label = "Add Code Object"
    bl_description = ("Add an object whose geometry comes from a code node in its own Geometry Nodes "
                      "tree. The node's settings and the code's sliders are inputs on that node")
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Kind", items=[(k, v[0], v[1]) for k, v in gn_link.KINDS.items()])
    template: StringProperty(name="Template", description="Starting code (empty: the default one)")
    make_real: BoolProperty(name="To Geometry", default=False,
                            description="Put a To Geometry node after it, so the object gets real geometry")

    def execute(self, context):
        if context.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                pass
        obj, tree, node, err = gn_link.add_object(self.kind, self.template or None,
                                                  location=tuple(context.scene.cursor.location),
                                                  make_real=self.make_real)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        for area in context.screen.areas if context.screen else ():
            space = area.spaces.active
            if (area.type == 'NODE_EDITOR' and getattr(space, "tree_type", "") == "GeometryNodeTree"
                    and not getattr(space, "pin", False)):
                area.tag_redraw()          # it follows the active object's modifier by itself
        if err:
            self.report({'WARNING'}, f"{node.node_tree.name}: {err.splitlines()[0]}")
        elif self.kind in gn_link.GPU_KINDS and not self.make_real:
            self.report({'INFO'}, f"Added '{obj.name}': drawn live on the GPU. Its node is in the Geometry "
                                  "Nodes editor; add To Geometry after it to use it in nodes or renders")
        else:
            self.report({'INFO'}, f"Added '{obj.name}': its code node is in the Geometry Nodes editor")
        return {'FINISHED'}


class CODENODES_OT_show_in_gn(bpy.types.Operator):
    bl_idname = "codenodes.show_in_gn"
    bl_label = "Open in Geometry Nodes"
    bl_description = ("Show this object's Geometry Nodes, with its code node selected (turns another "
                      "editor into a Node Editor if needed)")

    group: _GROUP

    def execute(self, context):
        obj = context.active_object
        found = gn_link.host_code_nodes(obj)
        if self.group:
            found = [f for f in found if f[1].node_tree.name == self.group] or found
        if not found:
            self.report({'ERROR'}, "the active object has no code nodes")
            return {'CANCELLED'}
        tree, node = found[0]
        area = show_node_editor(context)
        if area is None:
            self.report({'WARNING'}, "open a Node Editor and set it to Geometry Nodes")
            return {'CANCELLED'}
        space = area.spaces.active
        space.tree_type = "GeometryNodeTree"
        try:
            space.pin = False
        except (AttributeError, TypeError):
            pass
        for n in tree.nodes:
            n.select = False
        node.select = True
        tree.nodes.active = node
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is not None:
            def _frame():
                try:
                    with context.temp_override(area=area, region=region):
                        bpy.ops.node.view_all()
                except Exception:
                    pass
                return None
            bpy.app.timers.register(_frame, first_interval=0.05)
        return {'FINISHED'}


class CODENODES_OT_gn_edit_code(bpy.types.Operator):
    bl_idname = "codenodes.gn_edit_code"
    bl_label = "Edit Code"
    bl_description = "Show this node's code in a Text Editor (turns another editor into one if needed)"

    group: _GROUP

    def execute(self, context):
        from .ops import show_text
        _node, _group, obj, _tree = _active(context, self, self.group)
        if obj is None or obj.codenodes.text is None or not show_text(context, obj.codenodes.text, self):
            return {'CANCELLED'}
        return {'FINISHED'}


class CODENODES_OT_gn_template(bpy.types.Operator):
    bl_idname = "codenodes.gn_template"
    bl_label = "Use Template"
    bl_description = "Replace this node's code with a starting template (the old code is kept in a backup text)"
    bl_options = {'REGISTER', 'UNDO'}

    template: StringProperty()
    group: _GROUP

    def execute(self, context):
        from . import gn_sockets
        node, group, obj, tree = _active(context, self, self.group)
        if obj is None:
            return {'CANCELLED'}
        if self.template not in gn_link.TEMPLATES.get(obj.codenodes.kind, {}):
            self.report({'ERROR'}, f"no template called '{self.template}'")
            return {'CANCELLED'}
        user = ('node', tree, node) if node is not None and tree is not None else None
        gn_sockets.switch_template(obj, self.template, user)
        if node is not None and node.inputs.get("Template") is not None:
            try:
                node.inputs["Template"].default_value = self.template
            except (TypeError, ValueError):
                pass
        err = live.rebuild(obj)
        if err:
            self.report({'WARNING'}, err.splitlines()[0])
        return {'FINISHED'}


class CODENODES_OT_gn_rebuild(bpy.types.Operator):
    bl_idname = "codenodes.gn_rebuild"
    bl_label = "Rebuild"
    bl_description = "Run the code again now"
    bl_options = {'REGISTER', 'UNDO'}

    group: _GROUP

    def execute(self, context):
        node, group, obj, tree = _active(context, self, self.group)
        if obj is None:
            return {'CANCELLED'}
        if node is not None and tree is not None:
            gn_link.apply_values(obj, gn_link.read_values(('node', tree, node)), ('node', tree, node))
        err = live.rebuild(obj)
        gn_link.sync_interface(group, obj)
        if err:
            self.report({'ERROR'}, err.splitlines()[0])
            return {'CANCELLED'}
        self.report({'INFO'}, obj.codenodes.stats)
        return {'FINISHED'}


class CODENODES_OT_gn_make_native(bpy.types.Operator):
    bl_idname = "codenodes.gn_make_native"
    bl_label = "Make Native"
    bl_description = ("Replace this code node with real Geometry Nodes that do the same maths: no GPU, "
                      "no add-on needed to open it. GPU Surface only; needs the ExpressNode add-on")
    bl_options = {'REGISTER', 'UNDO'}

    group: _GROUP

    def execute(self, context):
        from .bake_nodes import CannotConvert
        node, group, obj, _tree = _active(context, self, self.group)
        if obj is None:
            return {'CANCELLED'}
        try:
            native = gn_link.make_native(group)
        except CannotConvert as exc:
            self.report({'ERROR'}, str(exc).splitlines()[0])
            return {'CANCELLED'}
        self.report({'INFO'}, f"'{native.name}' is plain Geometry Nodes now")
        return {'FINISHED'}


class CODENODES_OT_open_graph(bpy.types.Operator):
    bl_idname = "codenodes.open_graph"
    bl_label = "Open Node Graph"
    bl_description = ("Show a CodeNodes node graph in a Node Editor (turns another editor into one if "
                      "needed); makes a starter graph if there isn't one")

    tree: StringProperty(name="Graph", description="Defaults to the graph that made the active object")

    def execute(self, context):
        from . import nodes
        tree = bpy.data.node_groups.get(self.tree) if self.tree else graph_for(context.active_object)
        if tree is None:
            tree = next((t for t in bpy.data.node_groups if t.bl_idname == nodes.TREE), None)
        if tree is None:
            tree, out = nodes.new_demo_graph()
            nodes.build_output(tree, out)
        area = show_node_editor(context)
        if area is None:
            self.report({'WARNING'}, "open a Node Editor and set its type to CodeNodes")
            return {'CANCELLED'}
        space = area.spaces.active
        space.tree_type = nodes.TREE
        space.node_tree = tree
        return {'FINISHED'}


def graph_for(obj):
    """The CodeNodes graph whose Mesh or Points Output builds `obj`, if any."""
    from . import nodes
    if obj is None:
        return None
    for tree in bpy.data.node_groups:
        if tree.bl_idname != nodes.TREE:
            continue
        for node in tree.nodes:
            if getattr(node, "target", None) == obj:
                return tree
    return None


def code_groups_for(obj):
    """Code node groups used in `obj`'s Geometry Nodes modifiers."""
    out = []
    for mod in getattr(obj, "modifiers", ()):
        if mod.type != 'NODES' or mod.node_group is None:
            continue
        if gn_link.is_code_group(mod.node_group):
            out.append(mod.node_group)
        for node in mod.node_group.nodes:
            if node.type == 'GROUP' and gn_link.is_code_group(node.node_tree):
                out.append(node.node_tree)
    return out


def show_node_editor(context):
    screen = context.screen
    areas = [a for a in screen.areas if a.type == 'NODE_EDITOR']
    if areas:
        return areas[0]
    others = [a for a in screen.areas if a != context.area and a.type not in {'PROPERTIES', 'VIEW_3D'}] or \
             [a for a in screen.areas if a != context.area and a.type != 'PROPERTIES']
    if not others:
        return None
    area = max(others, key=lambda a: a.width * a.height)
    area.type = 'NODE_EDITOR'
    return area


# ---- menus ---------------------------------------------------------------------------------------

class CODENODES_MT_gn_add(bpy.types.Menu):
    bl_idname = "CODENODES_MT_gn_add"
    bl_label = "CodeNodes"

    def draw(self, context):
        from . import stage_templates
        layout = self.layout
        layout.operator_context = 'INVOKE_REGION_WIN'
        layout.operator(CODENODES_OT_gn_add_make_real.bl_idname, icon='MESH_DATA')
        layout.separator()
        for kind, (label, _desc, icon) in gn_link.KINDS.items():
            if kind == 'STAGE':
                continue
            layout.label(text=label, icon=icon)
            for key in gn_link.TEMPLATES[kind]:
                op = layout.operator(CODENODES_OT_gn_add.bl_idname, text=f"    {key}")
                op.kind, op.template = kind, key
            layout.separator()
        for title, icon, keys in stage_templates.SECTIONS:
            layout.label(text=title, icon=icon)
            for key in keys:
                op = layout.operator(CODENODES_OT_gn_add.bl_idname, text=f"    {key}")
                op.kind, op.template = 'STAGE', key
            layout.separator()


class CODENODES_PT_gn(bpy.types.Panel):
    """A small fallback: the node holds every setting, but Blender can't put buttons on a node."""
    bl_label = "CodeNodes"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "CodeNodes"

    @classmethod
    def poll(cls, context):
        return _in_geometry_nodes(context)

    def draw(self, context):
        layout = self.layout
        node, group, obj = gn_link.code_node(context)
        rnode, rgroup, _rtree = gn_link.make_real_node(context)
        if rnode is not None:
            layout.label(text="To Geometry: settings are on the node.", icon='MESH_DATA')
            layout.label(text=rnode.label)
            return
        if obj is None:
            layout.label(text="Add (Shift A) › CodeNodes", icon='INFO')
            layout.label(text="Settings live on the nodes;")
            layout.label(text="code opens in a Text Editor.")
            return
        s = obj.codenodes
        layout.label(text=node.label or group.name, icon=gn_link.KINDS.get(s.kind, ("", "", 'SCRIPT'))[2])
        row = layout.row(align=True)
        row.operator(CODENODES_OT_gn_edit_code.bl_idname, icon='TEXT')
        if s.kind in gn_link.GPU_KINDS:
            row.operator(CODENODES_OT_gn_add_make_real.bl_idname, text="Add To Geometry", icon='MESH_DATA')
        if s.last_error:
            box = layout.box()
            box.alert = True
            for line in s.last_error.splitlines()[:12]:
                box.label(text=line, icon='ERROR' if not line.startswith(" ") else 'BLANK1')


def _add_menu(self, context):
    if _in_geometry_nodes(context):
        self.layout.separator()
        self.layout.menu(CODENODES_MT_gn_add.bl_idname, icon='SCRIPT')


# ---- the code in a pop-up window ---------------------------------------------------------------------

def open_code_popup(text):
    """A small separate window with a Text Editor showing `text`: what the node's Edit Code toggle,
    double-clicking a code node and Ctrl+E open. Returns the window, or None."""
    wm = bpy.context.window_manager
    if not wm.windows:
        return None
    before = {w.as_pointer() for w in wm.windows}
    base = bpy.context.window or wm.windows[0]
    try:
        with bpy.context.temp_override(window=base, screen=base.screen):
            bpy.ops.screen.userpref_show('INVOKE_DEFAULT')     # Blender's small one-area window
    except Exception:
        return None
    win = next((w for w in wm.windows if w.as_pointer() not in before), None)
    if win is None:                                            # it was open already: reuse it
        win = next((w for w in wm.windows if getattr(w.screen, "is_temporary", False)), None)
    if win is None or not win.screen.areas:
        return None
    area = win.screen.areas[0]
    area.type = 'TEXT_EDITOR'
    sp = area.spaces.active
    sp.text = text
    try:
        text.cursor_set(0)                 # start at the top, not at the end of the file
    except (AttributeError, TypeError):
        pass
    sp.top = 0
    for attr, value in (("show_line_numbers", True), ("show_syntax_highlight", True),
                        ("show_line_highlight", True)):
        try:
            setattr(sp, attr, value)
        except (AttributeError, TypeError):
            pass
    return win


class CODENODES_OT_edit_code_popup(bpy.types.Operator):
    bl_idname = "codenodes.edit_code_popup"
    bl_label = "Edit Code"
    bl_description = "Open this code node's code in a pop-up Text Editor (it rebuilds live as you type)"

    group: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not _in_geometry_nodes(context):
            return False                  # the double-click / Ctrl+E keys stay free everywhere else
        tree = getattr(context.space_data, "edit_tree", None)
        node = tree.nodes.active if tree is not None else None
        return (node is not None and node.type == 'GROUP' and gn_link.is_code_group(node.node_tree)) or \
            gn_link.is_code_group(tree)

    def execute(self, context):
        if self.group:
            obj = gn_link.source_of(bpy.data.node_groups.get(self.group))
        else:
            tree = getattr(context.space_data, "edit_tree", None)
            node = tree.nodes.active if tree is not None else None
            if gn_link.is_code_group(tree):
                obj = gn_link.source_of(tree)
            else:
                obj = gn_link.source_of(node.node_tree) if node is not None and node.type == 'GROUP' else None
        if obj is None or obj.codenodes.text is None:
            return {'CANCELLED'}
        if open_code_popup(obj.codenodes.text) is None:
            from .ops import show_text
            show_text(context, obj.codenodes.text, self)
        return {'FINISHED'}


# ---- showing the code automatically (off by default) ---------------------------------------------------

_last_shown = {}      # screen name -> (tree name, node name) last acted on


def _code_for_area(area):
    """The Text block of the code node the Geometry Nodes editor in `area` is looking at, or None:
    the active node when it's a code node, or the code group you've Tabbed into."""
    space = area.spaces.active
    tree = getattr(space, "edit_tree", None)
    if tree is None:
        return None, None
    if gn_link.is_code_group(tree):
        obj = gn_link.source_of(tree)
        return (obj.codenodes.text if obj is not None else None), (tree.name, "(inside)")
    node = tree.nodes.active
    if node is None or not node.select or node.type != 'GROUP' or not gn_link.is_code_group(node.node_tree):
        return None, None
    obj = gn_link.source_of(node.node_tree)
    return (obj.codenodes.text if obj is not None else None), (tree.name, node.name)


def _show_code_tick():
    from . import prefs
    try:
        p = prefs.get()
        if p is None or not p.open_code_editor:            # off unless asked for in Preferences
            return 0.4
        if live._rendering():
            return 0.4
        wm = bpy.context.window_manager
        for win in wm.windows:
            screen = win.screen
            for area in screen.areas:
                if area.type != 'NODE_EDITOR' or getattr(area.spaces.active, "tree_type", "") != "GeometryNodeTree":
                    continue
                text, key = _code_for_area(area)
                if text is None:
                    continue
                if _last_shown.get(screen.name) == key:
                    continue
                _last_shown[screen.name] = key
                _show_text_beside(win, screen, area, text)
                break
    except Exception:
        import traceback
        traceback.print_exc()
    return 0.4


def _show_text_beside(win, screen, node_area, text):
    editors = [a for a in screen.areas if a.type == 'TEXT_EDITOR']
    if editors:
        editors[0].spaces.active.text = text
        return
    before = set(a.as_pointer() for a in screen.areas)
    region = next((r for r in node_area.regions if r.type == 'WINDOW'), None)
    try:
        with bpy.context.temp_override(window=win, screen=screen, area=node_area, region=region):
            bpy.ops.screen.area_split(direction='VERTICAL', factor=0.62)
    except Exception:
        return
    new = next((a for a in screen.areas if a.as_pointer() not in before), None)
    if new is None:
        return
    new.type = 'TEXT_EDITOR'
    sp = new.spaces.active
    sp.text = text
    for attr in ("show_line_numbers", "show_syntax_highlight", "show_word_wrap"):
        try:
            setattr(sp, attr, attr != "show_word_wrap")
        except (AttributeError, TypeError):
            pass


classes = (CODENODES_OT_edit_code_popup, CODENODES_OT_gn_add, CODENODES_OT_gn_add_make_real, CODENODES_OT_add_object, CODENODES_OT_show_in_gn,
           CODENODES_OT_gn_edit_code, CODENODES_OT_gn_template, CODENODES_OT_gn_rebuild,
           CODENODES_OT_gn_make_native, CODENODES_OT_open_graph, CODENODES_MT_gn_add, CODENODES_PT_gn)


_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.NODE_MT_add.append(_add_menu)
    gn_link.register()
    bpy.app.timers.register(_show_code_tick, first_interval=0.6, persistent=True)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Node Editor", space_type='NODE_EDITOR')
        for key, value, ctrl in (('LEFTMOUSE', 'DOUBLE_CLICK', False), ('E', 'PRESS', True)):
            kmi = km.keymap_items.new(CODENODES_OT_edit_code_popup.bl_idname, key, value, ctrl=ctrl)
            _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    if bpy.app.timers.is_registered(_show_code_tick):
        bpy.app.timers.unregister(_show_code_tick)
    gn_link.unregister()
    bpy.types.NODE_MT_add.remove(_add_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
