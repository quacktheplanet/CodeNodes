"""Rendering GPU nodes: F12, Ctrl+F12 and Render › Render Image / Render Animation.

The GPU must never run while Blender's renderer works (that crashes Blender), so this takes turns.
For each frame, on the main thread: step every GPU node to the frame, make its result real, render
that one frame and wait for it, then go on. No bake is needed first. GPU nodes that are only drawn
live (no To Geometry after them) are made real just for the render, then go back to being live.

When the scene has GPU code nodes, F12, Ctrl+F12 and the Render menu's Render Image / Render Animation
go through this: an add-on key map entry and the Render menu's first two items call
`codenodes.render_auto`, which falls back to Blender's own render when there are no GPU nodes (or when
the preference is off). Blender's render handlers (render_init, and frame_change_pre during an animation
render) run on the render job's thread, where GPU work crashes, so the stepping can't be done from them.

Stills land in Blender's Render window. Animations write every frame to the scene's output path in its
format; a movie format is written by rendering the frames to PNG, then encoding them with Blender's own
movie writer (a temporary scene plays them as an image strip).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import types

import bpy
from bpy.props import BoolProperty

from . import gn_link, gpu_guard, live

RENDER_LINK = "CodeNodes render link"


def gpu_sources():
    from . import links
    col = bpy.data.collections.get(gn_link.SOURCES)
    out = []
    for obj in (list(col.objects) if col is not None else []):
        s = getattr(obj, "codenodes", None)
        if s is not None and s.enabled and links.is_gpu(obj) and s.real_mode != "STANDALONE":
            out.append(obj)
    return out


RENDER_JOIN = "CodeNodes render join"
RENDER_INSTANCE = "CodeNodes render instance"

# The last CodeNodes render: frames rendered, whether it finished, where it went (for tests and reports)
LAST = {"count": 0, "frames": 0, "finished": False, "animation": False, "path": ""}


def _host_trees(src):
    group = _group_of(src)
    if group is None:
        return []
    return [t for t in bpy.data.node_groups if t.bl_idname == "GeometryNodeTree"
            and any(n.type == 'GROUP' and n.node_tree == group for n in t.nodes)]


def _link_in_host(src, on):
    """A live-only particle chain feeds nothing in the tree (its streams are bundles), so for the render
    its made-real points are joined in front of the host tree's output (and taken out again after)."""
    for tree in _host_trees(src):
        gout = next((n for n in tree.nodes if n.type == 'GROUP_OUTPUT'), None)
        geo = next((sk for sk in gout.inputs if sk.bl_idname == "NodeSocketGeometry"), None) if gout else None
        if geo is None:
            continue
        join = tree.nodes.get(RENDER_JOIN)
        info = tree.nodes.get(RENDER_LINK)
        if on:
            if join is None:
                join = tree.nodes.new("GeometryNodeJoinGeometry")
                join.name = join.label = RENDER_JOIN
                join.location = (gout.location.x - 160, gout.location.y - 140)
                prev = next((l for l in tree.links if l.to_socket == geo), None)
                if prev is not None:
                    join["cn_prev"] = [prev.from_node.name, prev.from_socket.identifier]
                    tree.links.new(prev.from_socket, join.inputs[0])
                tree.links.new(join.outputs[0], geo)
            if info is None:
                info = tree.nodes.new("GeometryNodeObjectInfo")
                info.name = info.label = RENDER_LINK
                info.transform_space = 'ORIGINAL'
                info.location = (join.location.x - 400, join.location.y - 120)
            # joined as an instance: Join Geometry merging two point clouds keeps only one of their
            # material lists (Blender 5.2), which would take the materials off the tree's own points
            inst = tree.nodes.get(RENDER_INSTANCE)
            if inst is None:
                inst = tree.nodes.new("GeometryNodeGeometryToInstance")
                inst.name = inst.label = RENDER_INSTANCE
                inst.location = (join.location.x - 200, join.location.y - 120)
            info.inputs["Object"].default_value = src
            tree.links.new(info.outputs["Geometry"], inst.inputs[0])
            tree.links.new(inst.outputs[0], join.inputs[0])
        else:
            prev = join.get("cn_prev") if join is not None else None
            for n in (info, tree.nodes.get(RENDER_INSTANCE), join):
                if n is not None:
                    tree.nodes.remove(n)
            if prev:
                node = tree.nodes.get(prev[0])
                out = next((o for o in node.outputs if o.identifier == prev[1]), None) if node else None
                if out is not None:
                    tree.links.new(out, geo)


def _group_of(src):
    return next((g for g in bpy.data.node_groups if gn_link.is_code_group(g) and g[gn_link.TAG] == src.name), None)


def _trees_containing(group):
    """Every Geometry Nodes tree that uses `group`, directly or through nested groups."""
    found, todo = set(), [group]
    while todo:
        g = todo.pop()
        for t in bpy.data.node_groups:
            if t.bl_idname == "GeometryNodeTree" and t not in found and \
                    any(n.type == 'GROUP' and n.node_tree == g for n in t.nodes):
                found.add(t)
                todo.append(t)
    return found


def scene_has_gpu_nodes(scene):
    """True when an object this scene renders uses a GPU code node (directly or in a nested group)."""
    trees = set()
    for src in gpu_sources():
        group = _group_of(src)
        if group is not None:
            trees |= _trees_containing(group)
    if not trees:
        return False
    for obj in scene.objects:
        if obj.hide_render:
            continue
        if any(m.type == 'NODES' and m.show_render and m.node_group in trees for m in obj.modifiers):
            return True
    return False


def _link_for_render(src, on):
    """A live-only GPU node's output is empty; for the render its group hands the made-real result
    straight to its output (and stops again afterwards)."""
    from . import links
    if links.ekind(src) == 'PARTICLES':
        _link_in_host(src, on)
        return
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
    """Step to `frame` and make every GPU node real. Returns the ones made real just for the render.
    `frame` None: Blender is already changing to the frame (a frame_change_pre handler)."""
    if frame is not None:
        scene.frame_set(frame)                   # frame handlers step Every Frame nodes (no render running)
        if gn_link.has_value_taps():
            sync_graph()                         # values computed by nodes (e.g. from Scene Time) for this frame
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
        elif s.real_mode == "ON_CHANGE" and s.kind == 'PARTICLES':    # (chain heads only)
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

    def _setup(self, context):
        scene = context.scene
        self.sources = gpu_sources()
        self.frames = (list(range(scene.frame_start, scene.frame_end + 1, max(1, scene.frame_step)))
                       if self.animation else [scene.frame_current])
        self.orig = scene.frame_current
        self.done = 0
        self.movie = self.animation and _is_movie(scene)
        self.pngs = []
        self.tmpdir = tempfile.mkdtemp(prefix="codenodes_frames_") if self.movie else None

    def invoke(self, context, event):
        self._setup(context)
        self.t0 = time.perf_counter()
        self.guard_before = gpu_guard.stats()["during_render"]
        try:                                     # the Render window, as with Blender's own F12
            bpy.ops.render.view_show('INVOKE_DEFAULT')
        except RuntimeError:
            pass
        if self.animation:
            context.window_manager.progress_begin(0, len(self.frames))
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        """Blocking version (scripts, tests, and command-line renders on Blender 5.2+): render every
        frame, then return."""
        scene = context.scene
        if not gpu_guard.available():
            self.report({'ERROR'}, "Rendering GPU nodes needs the GPU, and this background Blender has none "
                                   f"({gpu_guard._init['error']}). Bake them first, or use Blender 5.2 or later")
            return {'CANCELLED'}
        if bpy.app.background:
            sync_graph()
        self._setup(context)
        for frame in self.frames:
            self._render_one(context, frame)
        if self.animation:
            scene.frame_set(self.orig)
        self._write_movie(context)
        self._record(True)
        self.report({'INFO'}, self._summary())
        return {'FINISHED'}

    def _record(self, finished):
        LAST.update(count=LAST["count"] + 1, frames=len(self.frames), finished=finished,
                    animation=bool(self.animation), path=bpy.context.scene.render.filepath)

    def _write_movie(self, context):
        if not self.movie:
            return
        try:
            if self.pngs:
                encode_movie(context.scene, self.pngs)
        finally:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _summary(self):
        n = len(self.frames)
        where = bpy.path.abspath(bpy.context.scene.render.filepath) if self.animation else "Render Result"
        return f"CodeNodes rendered {n} frame{'s' if n != 1 else ''} to {where}"

    def _render_one(self, context, frame):
        scene = context.scene
        temporary = prepare_frame(scene, frame, self.sources)
        _OURS[0] = True
        try:
            bpy.ops.render.render(write_still=False)
        finally:
            _OURS[0] = False
            finish(self.sources, temporary)
        if self.animation:
            img = bpy.data.images.get("Render Result")
            if img is None:
                return
            if self.movie:
                path = os.path.join(self.tmpdir, f"frame_{frame:06d}.png")
                settings = scene.render.image_settings
                media = getattr(settings, "media_type", None)
                fmt = settings.file_format
                if media is not None:
                    settings.media_type = 'IMAGE'         # Blender 5: PNG is an image format
                settings.file_format = 'PNG'
                try:
                    img.save_render(path, scene=scene)
                finally:
                    if media is not None:
                        settings.media_type = media
                    settings.file_format = fmt
                self.pngs.append(path)
            else:
                path = scene.render.frame_path(frame=frame)
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
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
        if cancelled and self.movie:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.movie = False
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
        if self.animation:
            wm.progress_end()
            context.scene.frame_set(self.orig)
        context.workspace.status_text_set(None)
        if cancelled:
            self._record(False)
            self.report({'WARNING'}, f"CodeNodes render stopped after {self.done} frame(s)")
            return {'CANCELLED'}
        self._write_movie(context)
        self._record(True)
        self.report({'INFO'}, self._summary())
        return {'FINISHED'}


def _copy_rna(src, dst):
    for prop in src.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.is_readonly:
            continue
        try:
            setattr(dst, prop.identifier, getattr(src, prop.identifier))
        except (AttributeError, TypeError, ValueError):
            pass


def encode_movie(scene, pngs):
    """Write rendered PNG frames as the movie the scene's output settings ask for, with Blender's own
    movie writer: a temporary scene plays them as an image strip and renders its sequencer."""
    tmp = bpy.data.scenes.new("CodeNodes movie")
    try:
        r, s = tmp.render, scene.render
        for attr in ("resolution_x", "resolution_y", "resolution_percentage", "fps", "fps_base", "filepath",
                     "pixel_aspect_x", "pixel_aspect_y", "use_file_extension"):
            setattr(r, attr, getattr(s, attr))
        if hasattr(r.image_settings, "media_type"):
            r.image_settings.media_type = s.image_settings.media_type     # Blender 5: before the format
        r.image_settings.file_format = s.image_settings.file_format
        _copy_rna(s.image_settings, r.image_settings)
        _copy_rna(s.ffmpeg, r.ffmpeg)
        r.use_sequencer, r.use_compositing = True, False
        tmp.view_settings.view_transform = 'Standard'   # the PNGs already carry the scene's colour management
        tmp.view_settings.look = 'None'
        tmp.view_settings.exposure, tmp.view_settings.gamma = 0.0, 1.0
        tmp.frame_start = scene.frame_start
        tmp.frame_end = scene.frame_start + len(pngs) - 1
        se = tmp.sequence_editor_create()
        strips = getattr(se, "strips", None)
        if strips is None:
            strips = se.sequences
        strip = strips.new_image(name="CodeNodes frames", filepath=pngs[0], channel=1,
                                 frame_start=scene.frame_start)
        for path in pngs[1:]:
            strip.elements.append(os.path.basename(path))
        try:
            strip.colorspace_settings.name = 'sRGB'
        except (AttributeError, TypeError):
            pass
        bpy.ops.render.render(animation=True, scene=tmp.name)
    finally:
        bpy.data.scenes.remove(tmp)


def wants_codenodes_render(scene):
    from . import prefs
    p = prefs.get()
    if p is not None and not p.render_gpu_nodes:
        return False
    return scene is not None and scene_has_gpu_nodes(scene)


class CODENODES_OT_render_auto(bpy.types.Operator):
    """Render the scene; with GPU code nodes, CodeNodes steps them for each frame and renders them too"""
    bl_idname = "codenodes.render_auto"
    bl_label = "Render"
    bl_description = ("Render the scene. When it has GPU code nodes, CodeNodes runs their GPU code for each "
                      "frame, turns the results into geometry and renders that, so they're in the picture; "
                      "otherwise this is Blender's own render")

    animation: BoolProperty(name="Animation", default=False)
    use_viewport: BoolProperty(name="Use 3D Viewport", default=False)

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        if wants_codenodes_render(context.scene):
            ret = bpy.ops.codenodes.render('INVOKE_DEFAULT', animation=self.animation)
        else:
            ret = bpy.ops.render.render('INVOKE_DEFAULT', animation=self.animation, use_viewport=self.use_viewport)
        return {'FINISHED'} if ret & {'FINISHED', 'RUNNING_MODAL'} else {'CANCELLED'}


class _SkipRender:
    """Hands Blender's Render menu drawing through, leaving out its first two render.render entries
    (ours take their place)."""

    def __init__(self, layout, skip):
        self._layout, self._skip = layout, skip

    def operator(self, idname, *args, **kwargs):
        if self._skip and idname == "render.render":
            self._skip -= 1
            return types.SimpleNamespace()
        return self._layout.operator(idname, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._layout, name)


_menu_orig = [None]


def _render_menu_draw(self, context):
    orig = _menu_orig[0]
    if not wants_codenodes_render(context.scene):
        return orig(self, context)
    layout = self.layout
    op = layout.operator(CODENODES_OT_render_auto.bl_idname, text="Render Image", icon='RENDER_STILL')
    op.use_viewport = True
    op = layout.operator(CODENODES_OT_render_auto.bl_idname, text="Render Animation", icon='RENDER_ANIMATION')
    op.animation, op.use_viewport = True, True
    return orig(types.SimpleNamespace(layout=_SkipRender(layout, 2)), context)


_keymaps = []


def _add_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new(name="Screen", space_type='EMPTY')
    for ctrl, anim in ((False, False), (True, True)):
        kmi = km.keymap_items.new(CODENODES_OT_render_auto.bl_idname, 'F12', 'PRESS', ctrl=ctrl)
        kmi.properties.animation = anim
        kmi.properties.use_viewport = True
        _keymaps.append((km, kmi))


# ---- command-line renders: blender -b file.blend -a (or -f N), Blender 5.2+ -----------------------------------
# In background mode Blender renders on the main thread and, with gpu.init() (5.2), add-ons have the GPU. But
# once the first frame has rendered, Python's GPU context is gone until the render ends (GPU work between frames
# crashes, measured on 5.2.2). So at render_init, before anything renders, every frame of the range is stepped
# and made real on the GPU and kept as a mesh; frame_change_pre then only swaps the frame's meshes in (no GPU).
# Anywhere else (a render job in a window, whose handlers run on the render thread) these do nothing.

_OURS = [False]                          # the CodeNodes render loop is rendering: it does the stepping itself
_cli = {"meshes": None, "originals": {}, "temporary": [], "sources": []}


def _cli_wanted():
    return (bpy.app.background and not _OURS[0] and threading.current_thread() is threading.main_thread())


def _frames_to_render(scene):
    frames = list(range(scene.frame_start, scene.frame_end + 1, max(1, scene.frame_step)))
    if scene.frame_current not in frames:
        frames.append(scene.frame_current)
    return frames


def sync_graph():
    """Find the chains now. In a fresh `blender -b scene.blend` the node-graph sync (which runs from timers
    and depsgraph updates) hasn't run yet, so without this every source would run without its stages."""
    was = live._render_active[0]
    live._render_active[0] = False
    try:
        for _ in range(3):
            gn_link.sync()
            live._flush()
    finally:
        live._render_active[0] = was


@bpy.app.handlers.persistent
def _cli_render_init(*_args):
    scene = bpy.context.scene
    if not _cli_wanted() or not scene_has_gpu_nodes(scene):
        return
    if not gpu_guard.available():
        print(f"CodeNodes: GPU nodes are left out of this render: {gpu_guard._init['error']}. Bake them first, "
              "or use Blender 5.2 or later")
        return
    start, meshes, temporary = scene.frame_current, {}, []
    t0 = time.perf_counter()
    live._render_active[0] = False       # nothing renders yet: the GPU may run
    try:
        sync_graph()
        sources = gpu_sources()
        for frame in sorted(_frames_to_render(scene)):
            temporary = prepare_frame(scene, frame, sources)   # its render links stay on until the end
            meshes[frame] = {src.name: src.data.copy() for src in sources if src.type == 'MESH'}
        scene.frame_set(start)
    finally:
        live._render_active[0] = True
    _cli.update(meshes=meshes, temporary=temporary, sources=[s.name for s in sources],
                originals={s.name: s.data for s in sources if s.type == 'MESH'})
    print(f"CodeNodes: GPU nodes stepped and made real for {len(meshes)} frame(s) in "
          f"{time.perf_counter() - t0:.1f} s, before rendering")


@bpy.app.handlers.persistent
def _cli_frame(scene, depsgraph=None):
    if _cli["meshes"] is None:
        return
    frame = _cli["meshes"].get(scene.frame_current)
    if frame is None:
        print(f"CodeNodes: frame {scene.frame_current} wasn't prepared; its GPU nodes are left as they are")
        return
    for name, mesh in frame.items():
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.data = mesh


@bpy.app.handlers.persistent
def _cli_render_end(*_args):
    if _cli["meshes"] is None:
        return
    for name, mesh in _cli["originals"].items():
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.data = mesh
    for frame in _cli["meshes"].values():
        for mesh in frame.values():
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
    finish([o for o in map(bpy.data.objects.get, _cli["sources"]) if o is not None],
           [o for o in _cli["temporary"] if o.name in bpy.data.objects])
    _cli.update(meshes=None, originals={}, temporary=[], sources=[])


_CLI_HANDLERS = (("render_init", _cli_render_init), ("frame_change_pre", _cli_frame),
                 ("render_complete", _cli_render_end), ("render_cancel", _cli_render_end))


classes = (CODENODES_OT_render, CODENODES_OT_render_auto)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    funcs = bpy.types.TOPBAR_MT_render._dyn_ui_initialize()
    if funcs and funcs[0] is not _render_menu_draw:
        _menu_orig[0] = funcs[0]
        funcs[0] = _render_menu_draw
    _add_keymaps()
    for name, fn in _CLI_HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)


def unregister():
    for name, fn in _CLI_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    funcs = bpy.types.TOPBAR_MT_render._dyn_ui_initialize()
    if funcs and funcs[0] is _render_menu_draw and _menu_orig[0] is not None:
        funcs[0] = _menu_orig[0]
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
