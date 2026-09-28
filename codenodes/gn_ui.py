"""Geometry Nodes editor side of CodeNodes: Add › CodeNodes, the sidebar panel, and the
operators behind them. The work is done in gn_link."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import gn_link, live


def _in_geometry_nodes(context):
    space = context.space_data
    return getattr(space, "type", "") == 'NODE_EDITOR' and getattr(space, "tree_type", "") == "GeometryNodeTree"


class CODENODES_OT_gn_add(bpy.types.Operator):
    bl_idname = "codenodes.gn_add"
    bl_label = "Add Code Node"
    bl_description = ("Add a node whose geometry is made by code. Its inputs are the code's sliders; "
                      "edit the code from the sidebar (N) › CodeNodes")
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
        if err:
            self.report({'WARNING'}, f"{node.node_tree.name}: {err.splitlines()[0]}")
        return {'FINISHED'}

    def invoke(self, context, event):
        space = context.space_data
        if getattr(space, "edit_tree", None) is not None and context.region.type == 'WINDOW':
            area = context.area
            px, py = int(area.width / 10), int(area.height / 10)
            space.cursor_location_from_region(min(max(px, event.mouse_region_x), area.width - px),
                                              min(max(py, event.mouse_region_y), area.height - py))
        result = self.execute(context)
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
                      "tree. The code's sliders are inputs on that node")
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Kind", items=[(k, v[0], v[1]) for k, v in gn_link.KINDS.items()])
    template: StringProperty(name="Template", description="Starting code (empty: the default one)")

    def execute(self, context):
        if context.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                pass
        obj, tree, node, err = gn_link.add_object(self.kind, self.template or None,
                                                  location=tuple(context.scene.cursor.location))
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
    bl_description = "Replace this node's code with a starting template"
    bl_options = {'REGISTER', 'UNDO'}

    template: StringProperty()
    group: _GROUP

    def execute(self, context):
        node, group, obj, _tree = _active(context, self, self.group)
        if obj is None:
            return {'CANCELLED'}
        kind = obj.codenodes.kind
        try:
            source = gn_link.template(kind, self.template)
        except KeyError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        obj.codenodes.text.from_string(source)
        if group.name.startswith(gn_link.PREFIX):
            group.name = gn_link._unique(gn_link.PREFIX + self.template, bpy.data.node_groups)
        err = live.rebuild(obj)
        gn_link.sync_interface(group, obj)
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
            gn_link.apply_values(obj, gn_link.read_values(('node', tree, node)))
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
                      "no add-on needed to open it. Code Mesh only; needs the ExpressNode add-on")
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


class CODENODES_MT_gn_add(bpy.types.Menu):
    bl_idname = "CODENODES_MT_gn_add"
    bl_label = "CodeNodes"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'INVOKE_REGION_WIN'
        for kind, (label, _desc, icon) in gn_link.KINDS.items():
            layout.label(text=label, icon=icon)
            for key in gn_link.TEMPLATES[kind]:
                op = layout.operator(CODENODES_OT_gn_add.bl_idname, text=f"    {key}")
                op.kind, op.template = kind, key
            layout.separator()


class CODENODES_MT_gn_templates(bpy.types.Menu):
    bl_idname = "CODENODES_MT_gn_templates"
    bl_label = "Template"

    def draw(self, context):
        _node, _group, obj = gn_link.code_node(context)
        if obj is None:
            return
        for key in gn_link.TEMPLATES[obj.codenodes.kind]:
            self.layout.operator(CODENODES_OT_gn_template.bl_idname, text=key).template = key


class CODENODES_PT_gn(bpy.types.Panel):
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
        if obj is None:
            layout.label(text="Geometry made by code:")
            col = layout.column(align=True)
            col.operator_context = 'EXEC_DEFAULT'
            for kind, (label, _d, icon) in gn_link.KINDS.items():
                op = col.operator(CODENODES_OT_gn_add.bl_idname, text=label, icon=icon)
                op.kind, op.use_transform = kind, False
            layout.label(text="Or Add (Shift A) › CodeNodes.", icon='INFO')
            layout.label(text="Select a code node to edit it here.")
            return
        s = obj.codenodes
        box = layout.box()
        box.label(text=group.name, icon=gn_link.KINDS[s.kind][2])
        box.label(text=gn_link.KINDS[s.kind][0])
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator(CODENODES_OT_gn_edit_code.bl_idname, icon='TEXT')
        layout.menu(CODENODES_MT_gn_templates.bl_idname, text="Replace with Template", icon='FILE_NEW')
        col = layout.column(align=True)
        if s.kind == 'MESH':
            col.label(text="Resolution is an input on the node.")
            col.prop(s, "bounds_min", text="Bounds Min")
            col.prop(s, "bounds_max", text="Bounds Max")
        elif s.kind == 'PARTICLES':
            col.prop(s, "count")
            col.prop(s, "substeps")
        row = layout.row(align=True)
        row.prop(s, "live", toggle=True)
        row.prop(s, "animate", toggle=True)
        row.prop(s, "smooth", toggle=True)
        layout.operator(CODENODES_OT_gn_rebuild.bl_idname, icon='FILE_REFRESH')
        if s.kind == 'MESH':
            layout.operator(CODENODES_OT_gn_make_native.bl_idname, icon='NODETREE')
        if s.last_error:
            box = layout.box()
            box.alert = True
            for line in s.last_error.splitlines()[:12]:
                box.label(text=line, icon='ERROR' if not line.startswith(" ") else 'BLANK1')
        elif s.stats:
            for part in s.stats.split(" · ")[:3]:
                layout.label(text=part)


def _add_menu(self, context):
    if _in_geometry_nodes(context):
        self.layout.separator()
        self.layout.menu(CODENODES_MT_gn_add.bl_idname, icon='SCRIPT')


classes = (CODENODES_OT_gn_add, CODENODES_OT_add_object, CODENODES_OT_show_in_gn, CODENODES_OT_gn_edit_code, CODENODES_OT_gn_template,
           CODENODES_OT_gn_rebuild, CODENODES_OT_gn_make_native, CODENODES_OT_open_graph,
           CODENODES_MT_gn_add, CODENODES_MT_gn_templates, CODENODES_PT_gn)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.NODE_MT_add.append(_add_menu)
    gn_link.register()


def unregister():
    gn_link.unregister()
    bpy.types.NODE_MT_add.remove(_add_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
