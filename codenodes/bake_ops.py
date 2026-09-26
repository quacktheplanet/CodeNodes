"""Bake operators: write a frame range to disk and play it back with plain Geometry Nodes."""

from __future__ import annotations

import traceback

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from . import cache, live
from .sdf_code import SdfCodeError


def _target(context, object_name, tree_name, node_name):
    """(object, settings-owner, compute(frame) -> MeshResult, name) for whatever is being baked."""
    if tree_name:
        from . import nodes
        tree = bpy.data.node_groups.get(tree_name)
        out = tree.nodes.get(node_name) if tree else None
        if out is None:
            raise SdfCodeError("that Mesh Output node is gone")
        if out.target is None:
            raise SdfCodeError("build the mesh once before baking, so there is an object to bake to")
        return out.target, out, (lambda f: nodes.compute_output(tree, out)[0]), tree.name
    obj = bpy.data.objects.get(object_name) if object_name else context.active_object
    if obj is None or not obj.codenodes.enabled:
        raise SdfCodeError("select a Code Mesh object")
    return obj, obj.codenodes, (lambda f: live.compute_object(obj)[0]), obj.name


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
            obj, settings, compute, name = _target(context, self.object_name, self.tree, self.node)
        except SdfCodeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        wm = context.window_manager
        total = max(1, self.frame_end - self.frame_start + 1)
        wm.progress_begin(0, total)
        try:
            info = cache.bake(name, compute, self.frame_start, self.frame_end,
                              progress=lambda done, n: wm.progress_update(done))
        except SdfCodeError as exc:
            self.report({'ERROR'}, str(exc).splitlines()[0])
            return {'CANCELLED'}
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, f"{type(exc).__name__}: {exc}")
            return {'CANCELLED'}
        finally:
            wm.progress_end()

        tree = cache.reader_group(name, info["dir"], info["start"], info["end"], settings.smooth)
        cache.attach(obj, tree)
        obj.data.clear_geometry()      # the cache supplies the geometry now; don't store it twice
        if self.stop_live:
            settings.animate = False
            settings.live = False
            if hasattr(settings, "enabled"):
                settings.enabled = True        # keep the panel; the cache modifier now supplies the mesh
        msg = (f"Baked {info['frames']} frames · {cache.human_bytes(info['bytes'])} · "
               f"~{info['avg_faces']:,} faces/frame")
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


classes = (CODENODES_OT_bake, CODENODES_OT_unbake)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
