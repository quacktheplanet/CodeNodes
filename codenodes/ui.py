"""3D Viewport sidebar panel: View3D > Sidebar (N) > CodeNodes."""

from __future__ import annotations

import bpy


class CODENODES_PT_main(bpy.types.Panel):
    bl_label = "Code → Mesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CodeNodes"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        from .gn_ui import code_groups_for, graph_for
        if obj is None or not getattr(obj, "codenodes", None) or not obj.codenodes.enabled:
            graph = graph_for(obj)
            if graph is not None:
                box = layout.box()
                box.label(text=f"Made by the node graph '{graph.name}'", icon='NODETREE')
                box.operator("codenodes.open_graph", icon='WINDOW').tree = graph.name
                box.label(text="Edit the code from the Code nodes there.")
            groups = code_groups_for(obj)
            if groups:
                box = layout.box()
                box.label(text="Code nodes in its Geometry Nodes:", icon='GEOMETRY_NODES')
                for g in groups:
                    box.label(text=g.name, icon='SCRIPT')
                box.label(text="Node Editor › Sidebar (N) › CodeNodes")
            layout.label(text="Add an object made by code:")
            col = layout.column(align=True)
            col.operator("codenodes.add", icon='ADD')
            col.operator("codenodes.add_shape", icon='MESH_CYLINDER')
            col.operator("codenodes.add_particles", icon='PARTICLES')
            layout.label(text="Or build it from nodes:")
            col = layout.column(align=True)
            col.operator("codenodes.new_graph", icon='NODETREE')
            col.operator("codenodes.open_graph", icon='WINDOW')
            layout.label(text="In Geometry Nodes: Add › CodeNodes", icon='INFO')
            return
        s = obj.codenodes
        from . import gn_link
        if gn_link.SOURCES in {c.name for c in obj.users_collection}:
            layout.label(text="Feeds a code node in Geometry Nodes", icon='GEOMETRY_NODES')
        row = layout.row(align=True)
        row.prop(s, "text", text="")
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("codenodes.edit_code", icon='TEXT')
        col = layout.column(align=True)
        if s.kind == 'SHAPE':
            col.prop(s, "smooth", toggle=True)
            col.operator("codenodes.profile_to_curve", icon='OUTLINER_OB_CURVE')
        elif s.kind == 'PARTICLES':
            col.prop(s, "count")
            col.prop(s, "substeps")
            col.prop(s, "point_radius")
            col.prop(s, "stagger", slider=True)
        else:
            col.prop(s, "resolution")
            col = layout.column(align=True)
            col.prop(s, "bounds_min")
            col.prop(s, "bounds_max")
        row = layout.row(align=True)
        row.prop(s, "live", toggle=True)
        row.prop(s, "animate", toggle=True)
        row.prop(s, "smooth", toggle=True)
        if s.params:
            box = layout.box()
            box.label(text="Parameters (from the code)")
            for p in s.params:
                box.prop(p, "value", text=p.name, slider=False)
        from . import cache
        baked = cache.is_baked(obj)
        row = layout.row(align=True)
        row.enabled = not baked
        row.operator("codenodes.rebuild", icon='FILE_REFRESH')
        if baked:
            box = layout.box()
            box.label(text="Playing the baked cache", icon='FILE_CACHE')
            box.label(text="Renders without a GPU.")
            box.operator("codenodes.unbake", icon='X').object_name = obj.name
        else:
            layout.operator("codenodes.bake", icon='FILE_CACHE').object_name = obj.name
            if s.kind == 'MESH':
                layout.operator("codenodes.bake_nodes", icon='NODETREE').object_name = obj.name
        if s.last_error:
            box = layout.box()
            box.alert = True
            for line in s.last_error.splitlines()[:12]:
                box.label(text=line, icon='ERROR' if not line.startswith(" ") else 'BLANK1')
        elif s.stats:
            layout.label(text=s.stats)
        layout.operator("codenodes.make_plain", icon='MESH_DATA')


classes = (CODENODES_PT_main,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
