"""Bake operators: write a frame range to disk and play it back with plain Geometry Nodes."""

from __future__ import annotations

import traceback

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from . import cache, live, particles
from .sdf_code import SdfCodeError


class Job:
    """What to bake: where it goes, how to make a frame, and how to write it."""

    def __init__(self, obj, settings, compute, name, writer=None, as_points=False):
        self.obj, self.settings, self.compute, self.name = obj, settings, compute, name
        self.writer = writer or cache.write_ply
        self.as_points = as_points


def _target(context, object_name, tree_name, node_name):
    if tree_name:
        from . import nodes
        tree = bpy.data.node_groups.get(tree_name)
        out = tree.nodes.get(node_name) if tree else None
        if out is None:
            raise SdfCodeError("that output node is gone")
        if out.target is None:
            raise SdfCodeError("build it once before baking, so there is an object to bake to")
        if out.bl_idname == "CN_NodePointsOutput":
            return Job(out.target, out, lambda f: nodes.simulate_points(tree, out)[0], tree.name,
                       writer=cache.write_points_ply, as_points=True)
        return Job(out.target, out, lambda f: nodes.compute_output(tree, out)[0], tree.name)
    obj = bpy.data.objects.get(object_name) if object_name else context.active_object
    if obj is None or not obj.codenodes.enabled:
        raise SdfCodeError("select a Code Mesh or Code Particles object")
    if obj.codenodes.kind == 'PARTICLES':
        return Job(obj, obj.codenodes, lambda f: live.simulate_object(obj)[0], obj.name,
                   writer=cache.write_points_ply, as_points=True)
    return Job(obj, obj.codenodes, lambda f: live.compute_object(obj)[0], obj.name)


class CODENODES_OT_bake(bpy.types.Operator):
    bl_idname = "codenodes.bake"
    bl_label = "Bake to Disk"
    bl_description = ("Write one file per frame and play it back with plain Geometry Nodes, so the "
                      "animation renders in F12 and on machines without a GPU")
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN'})
    tree: StringProperty(options={'HIDDEN'})
    node: StringProperty(options={'HIDDEN'})
    frame_start: IntProperty(name="Start", default=1, min=0)
    frame_end: IntProperty(name="End", default=24, min=0)
    stop_live: BoolProperty(name="Stop Live Rebuilds", default=True,
                            description="After baking, let the cache drive the object instead of the GPU")

    def invoke(self, context, event):
        scene = context.scene
        self.frame_start, self.frame_end = scene.frame_start, scene.frame_end
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        col = self.layout.column(align=True)
        row = col.row(align=True)
        row.prop(self, "frame_start")
        row.prop(self, "frame_end")
        col.prop(self, "stop_live")
        n = max(0, self.frame_end - self.frame_start + 1)
        col.label(text=f"{n} frame{'s' if n != 1 else ''} to bake", icon='FILE_CACHE')

    def execute(self, context):
        try:
            job = _target(context, self.object_name, self.tree, self.node)
        except SdfCodeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        obj, settings, name = job.obj, job.settings, job.name

        wm = context.window_manager
        total = max(1, self.frame_end - self.frame_start + 1)
        wm.progress_begin(0, total)
        try:
            info = cache.bake(name, job.compute, self.frame_start, self.frame_end,
                              progress=lambda done, n: wm.progress_update(done), writer=job.writer)
        except SdfCodeError as exc:
            self.report({'ERROR'}, str(exc).splitlines()[0])
            return {'CANCELLED'}
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, f"{type(exc).__name__}: {exc}")
            return {'CANCELLED'}
        finally:
            wm.progress_end()

        tree = cache.reader_group(name, info["dir"], info["start"], info["end"],
                                  getattr(settings, "smooth", True), as_points=job.as_points,
                                  radius=getattr(settings, "point_radius", 0.02))
        if job.as_points:
            mod = obj.modifiers.get(particles.POINTS_MODIFIER)
            if mod is not None:
                obj.modifiers.remove(mod)      # the cache group makes the points itself
        mod = cache.attach(obj, tree)
        if job.as_points:
            key = particles.socket_id(tree, "Material")
            if key is not None and obj.data.materials:
                mod[key] = obj.data.materials[0]
        obj.data.clear_geometry()      # the cache supplies the geometry now; don't store it twice
        if self.stop_live:
            settings.animate = False
            settings.live = False
        what = "particles" if job.as_points else f"~{info['avg_faces']:,} faces/frame"
        msg = f"Baked {info['frames']} frames · {cache.human_bytes(info['bytes'])} · {what}"
        settings.stats = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class CODENODES_OT_unbake(bpy.types.Operator):
    bl_idname = "codenodes.unbake"
    bl_label = "Remove Cache"
    bl_description = "Stop playing the baked files and go back to live rebuilding"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN'})
    delete_files: BoolProperty(name="Delete the baked files too", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name) if self.object_name else context.active_object
        if obj is None:
            return {'CANCELLED'}
        cache.detach(obj, remove_files=self.delete_files)
        s = getattr(obj, "codenodes", None)
        if s is not None and s.enabled:
            s.live = True
            s.stats = ""
            live.rebuild(obj)
        self.report({'INFO'}, "Cache removed")
        return {'FINISHED'}


class CODENODES_OT_bake_nodes(bpy.types.Operator):
    bl_idname = "codenodes.bake_nodes"
    bl_label = "Bake to Nodes"
    bl_description = ("Rebuild the code as a Geometry Nodes network: no files, no GPU, evaluated every "
                      "frame, sliders kept (needs the Expression Nodes add-on)")
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from . import api
        name = self.object_name or (context.active_object.name if context.active_object else "")
        result = api.bake_to_nodes(name)
        if not result["ok"]:
            self.report({'ERROR'}, result["error"])
            return {'CANCELLED'}
        self.report({'INFO'}, f"'{name}' is now a node network ({result['group']})")
        return {'FINISHED'}


classes = (CODENODES_OT_bake, CODENODES_OT_unbake, CODENODES_OT_bake_nodes)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
