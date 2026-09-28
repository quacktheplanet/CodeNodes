"""Render › Render Image / Render Animation with CodeNodes.

The GPU must never run while Blender's renderer works (that crashes Blender), so this takes turns.
For each frame, on the main thread: step every GPU node to the frame, make its result real, render
that one frame and wait for it, then go on. No bake is needed first. GPU nodes that are only drawn
live (no Make Real after them) are made real just for the render, then go back to being live.

Blender's own F12 / Ctrl+F12 still work: they render whatever is real or baked, and CodeNodes
refuses all GPU work while they run.
"""

from __future__ import annotations

import os
import time

import bpy
from bpy.props import BoolProperty

from . import gn_link, gpu_guard, live

RENDER_LINK = "CodeNodes render link"


def gpu_sources():
    col = bpy.data.collections.get(gn_link.SOURCES)
    out = []
    for obj in (list(col.objects) if col is not None else []):
        s = getattr(obj, "codenodes", None)
        if s is not None and s.enabled and s.kind in gn_link.GPU_KINDS and s.real_mode != "STANDALONE":
            out.append(obj)
    return out


def _group_of(src):
    return next((g for g in bpy.data.node_groups if gn_link.is_code_group(g) and g[gn_link.TAG] == src.name), None)


def _link_for_render(src, on):
    """A live-only GPU node's output is empty; for the render its group hands the made-real result
    straight to its output (and stops again afterwards)."""
    group = _group_of(src)
    if group is None:
        return
    info = group.nodes.get(RENDER_LINK)
    gout = next((n for n in group.nodes if n.type == 'GROUP_OUTPUT'), None)
    if on:
        if info is None:
            info = group.nodes.new("GeometryNodeObjectInfo")
            info.name = info.label = RENDER_LINK
            info.transform_space = 'ORIGINAL'
            info.location = (-150, -200)
        info.inputs["Object"].default_value = src
        target = group.nodes.get("Menus")           # the join in front of the output, if there is one
        if target is not None:
            group.links.new(info.outputs["Geometry"], target.inputs[0])
        elif gout is not None:
            group.links.new(info.outputs["Geometry"], gout.inputs[0])
    elif info is not None:
        group.nodes.remove(info)


def prepare_frame(scene, frame, sources):
    """Step to `frame` and make every GPU node real. Returns the ones made real just for the render."""
    scene.frame_set(frame)                       # frame handlers step Every Frame nodes (no render running)
    temporary = []
    for src in sources:
        s = src.codenodes
        if s.real_mode in ("NONE", "RENDER_ONLY"):
            err = live.make_real_now(src)
            if s.real_mode == "NONE":
                _link_for_render(src, True)
                temporary.append(src)
            if err:
                print(f"CodeNodes render: {src.name}: {err}")
        elif s.real_mode == "ON_CHANGE" and s.kind == 'PARTICLES':
            live.rebuild(src)                    # particles move with time even when "frozen" otherwise
    bpy.context.view_layer.update()
    return temporary


def finish(sources, temporary):
    for src in temporary:
        _link_for_render(src, False)
    for src in sources:
        if live.is_live_only(src):
            live.clear_live(src)
    bpy.context.view_layer.update()


def _is_movie(scene):
    return scene.render.image_settings.file_format in ('FFMPEG', 'AVI_JPEG', 'AVI_RAW')


class CODENODES_OT_render(bpy.types.Operator):
    bl_idname = "codenodes.render"
    bl_label = "Render with CodeNodes"
    bl_description = ("Render with GPU nodes included: for each frame CodeNodes runs the GPU code, makes the "
                      "result real, then renders that frame (the GPU and the renderer take turns, so nothing "
                      "crashes and no bake is needed)")

    animation: BoolProperty(name="Animation", default=False)

    _timer = None

    def invoke(self, context, event):
        scene = context.scene
        self.sources = gpu_sources()
        self.frames = (list(range(scene.frame_start, scene.frame_end + 1, max(1, scene.frame_step)))
                       if self.animation else [scene.frame_current])
        self.orig = scene.frame_current
        self.done = 0
        self.t0 = time.perf_counter()
        self.movie = self.animation and _is_movie(scene)
        self.guard_before = gpu_guard.stats()["during_render"]
        if self.animation:
            context.window_manager.progress_begin(0, len(self.frames))
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        """Blocking version (scripts and tests): render every frame, then return."""
        scene = context.scene
        self.sources = gpu_sources()
        self.frames = (list(range(scene.frame_start, scene.frame_end + 1, max(1, scene.frame_step)))
                       if self.animation else [scene.frame_current])
        self.orig = scene.frame_current
        self.movie = self.animation and _is_movie(scene)
        for i, frame in enumerate(self.frames):
            self._render_one(context, frame)
        if self.animation:
            scene.frame_set(self.orig)
        self.report({'INFO'}, self._summary())
        return {'FINISHED'}

    def _summary(self):
        n = len(self.frames)
        where = bpy.path.abspath(bpy.context.scene.render.filepath) if self.animation else "Render Result"
        return f"CodeNodes rendered {n} frame{'s' if n != 1 else ''} to {where}"

    def _render_one(self, context, frame):
        scene = context.scene
        temporary = prepare_frame(scene, frame, self.sources)
        try:
            bpy.ops.render.render(write_still=False)
        finally:
            finish(self.sources, temporary)
        if self.animation:
            path = scene.render.frame_path(frame=frame)
            if self.movie:
                root, _ext = os.path.splitext(path)
                path = root + ".png"
            img = bpy.data.images.get("Render Result")
            if img is not None:
                if self.movie:
                    fmt = scene.render.image_settings.file_format
                    scene.render.image_settings.file_format = 'PNG'
                    try:
                        img.save_render(path, scene=scene)
                    finally:
                        scene.render.image_settings.file_format = fmt
                else:
                    img.save_render(path, scene=scene)

    def modal(self, context, event):
        wm = context.window_manager
        if event.type == 'ESC':
            return self._end(context, cancelled=True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if self.done >= len(self.frames):
            return self._end(context)
        frame = self.frames[self.done]
        context.workspace.status_text_set(f"CodeNodes: rendering frame {frame} "
                                          f"({self.done + 1}/{len(self.frames)}) · Esc to stop")
        self._render_one(context, frame)
        self.done += 1
        if self.animation:
            wm.progress_update(self.done)
        return {'RUNNING_MODAL'}

    def _end(self, context, cancelled=False):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
        if self.animation:
            wm.progress_end()
            context.scene.frame_set(self.orig)
        context.workspace.status_text_set(None)
        if cancelled:
            self.report({'WARNING'}, f"CodeNodes render stopped after {self.done} frame(s)")
            return {'CANCELLED'}
        if not self.animation:
            try:
                bpy.ops.render.view_show('INVOKE_DEFAULT')
            except RuntimeError:
                pass
        msg = self._summary()
        if self.movie:
            msg += " (as PNG frames: a movie can't be written frame by frame; join them in the Video Editor)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def _render_menu(self, context):
    layout = self.layout
    layout.separator()
    op = layout.operator(CODENODES_OT_render.bl_idname, text="Render Image with CodeNodes", icon='RENDER_STILL')
    op.animation = False
    op = layout.operator(CODENODES_OT_render.bl_idname, text="Render Animation with CodeNodes",
                         icon='RENDER_ANIMATION')
    op.animation = True


classes = (CODENODES_OT_render,)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_render.append(_render_menu)


def unregister():
    bpy.types.TOPBAR_MT_render.remove(_render_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
