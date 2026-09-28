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

    def draw(self, context):
        from . import server
        layout = self.layout
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


classes = (CodeNodesPreferences,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
