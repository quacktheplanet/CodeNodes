"""Add-on preferences: Edit › Preferences › Add-ons › CodeNodes."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty


def _link_changed(self, _context):
    from . import server
    if self.link_enabled:
        server.autostart()
    else:
        server.stop()


class CodeNodesPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    link_enabled: BoolProperty(
        name="Let assistants connect",
        description=("Let an assistant such as Claude drive CodeNodes through its MCP server. "
                     "Only programs on this computer can connect"),
        default=True,
        update=_link_changed,
    )

    open_code_editor: BoolProperty(
        name="Also show code next to the graph on selection",
        description=("When you select a code node (or Tab into it) in the Geometry Nodes editor, also show its "
                     "code in a Text Editor next to the graph, splitting the editor if needed. The node's "
                     "Edit Code toggle, a double-click or Ctrl+E open it in a pop-up window either way"),
        default=False,
    )

    preview_while_paused: BoolProperty(
        name="Keep simulating in the viewport while paused",
        description=("With the timeline stopped, live particle chains (and code set to Animate) keep moving in "
                     "the viewport, so behaviour sliders show their effect straight away. The scene frame "
                     "doesn't change, and renders, To Geometry and the GPU Cache always use the real frame"),
        default=True,
    )

    render_gpu_nodes: BoolProperty(
        name="F12 and Render menu include GPU nodes",
        description=("When the scene has GPU code nodes, F12, Ctrl+F12 and Render › Render Image / Animation "
                     "render them: for each frame CodeNodes runs the GPU code, turns the results into geometry, "
                     "then renders that frame (the GPU and the renderer take turns). Off: Blender's own render, "
                     "which only shows what To Geometry has already made"),
        default=True,
    )

    def draw(self, context):
        from . import server
        layout = self.layout
        layout.prop(self, "render_gpu_nodes")
        layout.prop(self, "preview_while_paused")
        layout.prop(self, "open_code_editor")
        layout.prop(self, "link_enabled")
        info = server.status()
        if info["running"]:
            layout.label(text=f"Ready: an assistant on this computer can connect "
                              f"({info['clients']} connected)", icon='CHECKMARK')
        elif self.link_enabled and bpy.app.background:
            layout.label(text="Not started in background mode", icon='INFO')
        elif self.link_enabled:
            layout.label(text=info.get("error") or "Starting…", icon='INFO')
        else:
            layout.label(text="Off", icon='RADIOBUT_OFF')


def get():
    """The add-on's preferences, or None (e.g. when loaded from a repo checkout in tests)."""
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon is not None else None


classes = (CodeNodesPreferences,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
