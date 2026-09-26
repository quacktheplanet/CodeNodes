"""Operators: add a Code -> Mesh object, rebuild, and bake the current result."""

from __future__ import annotations

import bpy
from bpy.props import IntProperty, StringProperty

from . import live, sdf_code


class CODENODES_OT_add(bpy.types.Operator):
    bl_idname = "codenodes.add"
    bl_label = "Code Mesh"
    bl_description = "Add a mesh object built from sdf code"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        me = bpy.data.meshes.new("CodeMesh")
        obj = bpy.data.objects.new("CodeMesh", me)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        text = bpy.data.texts.new(f"{obj.name}.sdf")
        text.from_string(sdf_code.TEMPLATE)
        s = obj.codenodes
        s.enabled = True
        s.text = text
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        err = live.rebuild(obj)
        if err:
            self.report({'WARNING'}, err.splitlines()[0])
        return {'FINISHED'}


class CODENODES_OT_add_shape(bpy.types.Operator):
    bl_idname = "codenodes.add_shape"
    bl_label = "Code Shape"
    bl_description = ("Add a model built from a parametric description — profiles, revolve, "
                      "extrude — with exact edges and clean quads")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import api, shapes
        name = "CodeShape"
        n = 1
        while name in bpy.data.objects:
            n += 1
            name = f"CodeShape.{n:03d}"
        r = api.code_to_shape(shapes.TEMPLATE, name=name)
        obj = bpy.data.objects.get(r["object"])
        if obj is not None:
            obj.location = context.scene.cursor.location
            for o in context.selected_objects:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        if not r["ok"]:
            self.report({'WARNING'}, (r["error"] or "").splitlines()[0])
        return {'FINISHED'}


class CODENODES_OT_add_particles(bpy.types.Operator):
    bl_idname = "codenodes.add_particles"
    bl_label = "Code Particles"
    bl_description = "Add a GPU particle system you write yourself"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import api, particles
        name = "CodeParticles"
        n = 1
        while name in bpy.data.objects:
            n += 1
            name = f"CodeParticles.{n:03d}"
        r = api.code_to_particles(particles.TEMPLATE, name=name)
        obj = bpy.data.objects.get(r["object"])
        if obj is not None:
            obj.location = context.scene.cursor.location
            for o in context.selected_objects:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        if not r["ok"]:
            self.report({'WARNING'}, (r["error"] or "").splitlines()[0])
        return {'FINISHED'}


class CODENODES_OT_profile_to_curve(bpy.types.Operator):
    bl_idname = "codenodes.profile_to_curve"
    bl_label = "Profile as Curve"
    bl_description = ("Draw the profiles as a curve object, to look at or reshape by hand "
                      "while working out the maths")
    bl_options = {'REGISTER', 'UNDO'}

    tree: StringProperty(options={'HIDDEN'})
    node: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from . import shape_build
        from .shapes import ShapeError
        source = values = None
        name = "Profile"
        if self.tree:
            tree = bpy.data.node_groups.get(self.tree)
            node = tree.nodes.get(self.node) if tree else None
            if node is None or node.text is None:
                self.report({'ERROR'}, "that Shape node has no description")
                return {'CANCELLED'}
            source = node.text.as_string()
            values = {s.name: s.default_value for s in node.inputs}
            name = f"{node.name} Profile"
        else:
            obj = context.active_object
            s = getattr(obj, "codenodes", None) if obj else None
            if s is None or not s.enabled or s.kind != 'SHAPE' or s.text is None:
                self.report({'ERROR'}, "select a Code Shape object")
                return {'CANCELLED'}
            source = s.text.as_string()
            values = {p.name: p.value for p in s.params}
            name = f"{obj.name} Profile"
        try:
            curve, found = shape_build.profiles_to_curve(source, values, name)
        except ShapeError as exc:
            self.report({'ERROR'}, str(exc).splitlines()[0])
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        curve.select_set(True)
        context.view_layer.objects.active = curve
        self.report({'INFO'}, f"{found} profile{'s' if found != 1 else ''} drawn as '{curve.name}'")
        return {'FINISHED'}


class CODENODES_OT_rebuild(bpy.types.Operator):
    bl_idname = "codenodes.rebuild"
    bl_label = "Rebuild"
    bl_description = "Run the code and rebuild the mesh"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(name="Object", description="Defaults to the active object")

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name) if self.object_name else context.active_object
        if obj is None or not obj.codenodes.enabled:
            self.report({'ERROR'}, "not a Code -> Mesh object")
            return {'CANCELLED'}
        err = live.rebuild(obj)
        if err:
            self.report({'ERROR'}, err.splitlines()[0])
            return {'CANCELLED'}
        self.report({'INFO'}, obj.codenodes.stats)
        return {'FINISHED'}


class CODENODES_OT_bake(bpy.types.Operator):
    bl_idname = "codenodes.bake"
    bl_label = "Make Plain Mesh"
    bl_description = "Keep the current mesh and stop driving it from code (the code text stays)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or not obj.codenodes.enabled:
            return {'CANCELLED'}
        obj.codenodes.enabled = False
        obj.codenodes.animate = False
        return {'FINISHED'}


class CODENODES_OT_edit_code(bpy.types.Operator):
    bl_idname = "codenodes.edit_code"
    bl_label = "Edit Code"
    bl_description = "Show the code in a Text Editor (turns the largest other editor into one if needed)"

    def execute(self, context):
        text = context.active_object.codenodes.text if context.active_object else None
        if text is None or not show_text(context, text, self):
            return {'CANCELLED'}
        return {'FINISHED'}


def show_text(context, text, op=None):
    """Show `text` in a Text Editor, turning the largest other editor into one if there is none."""
    areas = [a for a in context.screen.areas if a.type == 'TEXT_EDITOR']
    if not areas:
        keep = {'PROPERTIES', 'NODE_EDITOR', 'VIEW_3D'} if len(context.screen.areas) > 2 else {'PROPERTIES'}
        others = [a for a in context.screen.areas if a != context.area and a.type not in keep] or \
                 [a for a in context.screen.areas if a != context.area and a.type != 'PROPERTIES']
        if not others:
            if op is not None:
                op.report({'WARNING'}, "open a Text Editor to edit the code")
            return False
        area = max(others, key=lambda a: a.width * a.height)
        area.type = 'TEXT_EDITOR'
        areas = [area]
    areas[0].spaces.active.text = text
    return True


classes = (CODENODES_OT_add, CODENODES_OT_add_shape, CODENODES_OT_add_particles,
           CODENODES_OT_profile_to_curve, CODENODES_OT_rebuild, CODENODES_OT_bake,
           CODENODES_OT_edit_code)


def menu_add(self, context):
    self.layout.operator(CODENODES_OT_add.bl_idname, icon='SCRIPT')
    self.layout.operator(CODENODES_OT_add_shape.bl_idname, icon='MESH_CYLINDER')
    self.layout.operator(CODENODES_OT_add_particles.bl_idname, icon='PARTICLES')


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_add)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_add)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
