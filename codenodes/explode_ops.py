"""Explode and Collapse in the node editor (the code side is explode.py).

Explode turns a code node into a node group you can Tab (or double-click) into, like any node group:
inside are the node itself, now holding only its entry points, and one node per piece of its code (a
Value node per constant, a Function node per helper function, a List node per constant array), wired
into it. Its inputs stay on the outside. Pieces are ordinary code nodes: edit them, rewire them, or
ungroup (Ctrl+Alt+G) to share one with other nodes.

Collapse does the reverse: every piece still wired in goes back into the code where it came from.
Explode then Collapse gives back the code exactly.
"""

from __future__ import annotations

import json

import bpy
from bpy.props import StringProperty

from . import explode, gn_link, gn_sockets, groups
from .sdf_code import SdfCodeError

WRAPPER = gn_link.WRAPPER
META = "codenodes_exploded"           # on the group: what Explode needs to put the pieces back
SKIP = (gn_sockets.EDIT, "Template")


def _label(group):
    name = group.name
    return name[len(gn_link.PREFIX):] if name.startswith(gn_link.PREFIX) else name


def _core(group):
    name = group.get(WRAPPER)
    for n in group.nodes:
        if n.type == 'GROUP' and gn_link.is_code_group(n.node_tree):
            src = gn_link.source_of(n.node_tree)
            if src is not None and src.name == name:
                return n
    return None


def _expose(group, wnode, core):
    """Every input of the core that isn't wired inside becomes an input of the group, value and all."""
    gin = next(n for n in group.nodes if n.type == 'GROUP_INPUT')
    citems = {i.identifier: i for i in core.node_tree.interface.items_tree
              if i.item_type == 'SOCKET' and i.in_out == 'INPUT'}
    for sock in core.inputs:
        if sock.is_linked or sock.name in SKIP or not sock.name or not sock.enabled:
            continue
        ci = citems.get(sock.identifier)
        stype, is_list = groups.interface_type(core, sock)
        item = group.interface.new_socket(sock.name, in_out="INPUT", socket_type=stype)
        if is_list:
            item.structure_type = 'LIST'
        for attr in ("min_value", "max_value", "default_value", "description", "subtype"):
            if ci is not None and hasattr(ci, attr):
                try:
                    setattr(item, attr, getattr(ci, attr))
                except (AttributeError, TypeError, ValueError):
                    pass
        out = next((o for o in gin.outputs if o.identifier == item.identifier), None)
        if out is not None:
            group.links.new(out, sock)
        if hasattr(sock, "default_value") and sock.bl_idname != "NodeSocketMenu":
            try:
                wnode.inputs[item.identifier].default_value = sock.default_value
            except (AttributeError, TypeError, ValueError, KeyError):
                pass


def _gin_out(group, name):
    gin = next(n for n in group.nodes if n.type == 'GROUP_INPUT')
    return gin.outputs.get(name)


def explode_node(tree, node):
    """Explode a code node in `tree`. Returns the group node that replaces it. Raises SdfCodeError."""
    obj = gn_link.source_of(node.node_tree)
    if obj is None or obj.codenodes.text is None:
        raise SdfCodeError("this node has no code")
    label = _label(node.node_tree)
    code = obj.codenodes.text.as_string()
    e = explode.explode(code, label)
    if not e.pieces:
        raise SdfCodeError("nothing to explode: no constants, constant arrays or helper functions that can "
                           "stand on their own (entry points stay in the node)")
    wnode = groups.make_group(tree, [node], f"{label} · pieces")
    group = wnode.node_tree
    group[WRAPPER] = obj.name
    group[META] = json.dumps({"originals": e.originals, "kinds": {p.name: p.kind for p in e.pieces}})
    group.description = (f"{label}, exploded: Tab into it to see and edit its pieces. Collapse (right-click › "
                         f"CodeNodes) puts them back into the code")
    try:
        group.color_tag = 'SCRIPT'
    except (AttributeError, TypeError):
        pass
    core = _core(group)
    _expose(group, wnode, core)
    obj.codenodes.text.from_string(e.core)
    gn_link.sync()                                       # the node's sockets follow its new code
    core = _core(group)
    made = {}
    gin = next(n for n in group.nodes if n.type == 'GROUP_INPUT')
    gin.location = (core.location.x - 760, core.location.y)       # inputs on the left, pieces in a column
    x0 = core.location.x - 420                                     # between them and the node, going down
    for k, p in enumerate(e.pieces):
        loc = (x0, core.location.y - 140 - 190 * k)
        if p.kind == 'value':
            if p.item.type == "int":
                n = group.nodes.new("FunctionNodeInputInt")
                n.integer = int(round(float(p.item.value)))
            else:
                n = group.nodes.new("ShaderNodeValue")
                n.outputs[0].default_value = float(p.item.value)
            n.label = n.name = p.name
            n.location = loc
        else:
            g, err = gn_link.create('STAGE', None, p.name, p.code)
            if err:
                raise SdfCodeError(f"piece '{p.name}': {err}")
            n = gn_link.insert(group, g, loc)
            n.width = 200
        made[p.name] = n
    gn_link.sync()

    def out_of(name):
        n = made[name]
        if n.type in ('VALUE', 'INPUT_INT') or n.bl_idname in ("ShaderNodeValue", "FunctionNodeInputInt"):
            return n.outputs[0]
        return n.outputs.get(name) or n.outputs.get("value") or n.outputs.get(gn_sockets.TABLE_OUT)

    core = _core(group)
    for p in e.pieces:
        to = core.inputs.get(p.name)
        if to is not None:
            group.links.new(out_of(p.name), to)
        for kind, name in p.needs:
            to = made[p.name].inputs.get(name)
            frm = _gin_out(group, name) if kind == "slider" else (out_of(name) if name in made else None)
            if to is not None and frm is not None:
                group.links.new(frm, to)
    for n in group.nodes:
        n.select = False
    gn_link._dirty[0] = True
    return wnode


def is_exploded(group):
    return group is not None and WRAPPER in group and META in group


def collapse_node(tree, wnode):
    """Put an exploded node's pieces back into its code. Returns the code node that replaces the group."""
    group = wnode.node_tree
    core = _core(group)
    if core is None:
        raise SdfCodeError("the node this group was exploded from isn't inside it any more")
    obj = gn_link.source_of(core.node_tree)
    meta = json.loads(group[META])
    kinds = meta.get("kinds", {})
    pieces, used = {}, []
    for name, kind in kinds.items():
        sock = core.inputs.get(name)
        link = gn_link._feeding(group, sock) if sock is not None else None
        if link is None:
            continue
        n = link.from_node
        if kind == 'value' and n.bl_idname == "ShaderNodeValue":
            pieces[name] = ('value', n.outputs[0].default_value)
        elif kind == 'value' and n.bl_idname == "FunctionNodeInputInt":
            pieces[name] = ('value', n.integer)
        elif n.type == 'GROUP' and gn_link.is_code_group(n.node_tree):
            src = gn_link.source_of(n.node_tree)
            d = gn_sockets.decls_of(src)
            if kind == 'list' and d.table is not None:
                pieces[name] = ('list', d.table)
            elif kind == 'func':
                pieces[name] = ('func', src.codenodes.text.as_string())
            else:
                continue
        else:
            continue
        used.append(n)
    code = explode.collapse(obj.codenodes.text.as_string(), pieces, meta.get("originals", {}),
                            arrays=[n for n, k in kinds.items() if k == 'list'])
    # the group's outside wiring, as (core socket name, other end)
    inner_in = {}                              # group input identifier -> core input name
    for l in group.links:
        if l.from_node.type == 'GROUP_INPUT' and l.to_node == core:
            inner_in[l.from_socket.identifier] = l.to_socket.name
    inner_out = {}                             # group output identifier -> core output name
    for l in group.links:
        if l.to_node.type == 'GROUP_OUTPUT' and l.from_node == core:
            inner_out[l.to_socket.identifier] = l.from_socket.name
    # (menus are left out: CodeNodes sets them from the node's settings, and assigning a menu value on a
    # node whose group was just rebuilt can crash Blender)
    values = {inner_in[s.identifier]: _plain(s.default_value) for s in wnode.inputs
              if s.identifier in inner_in and hasattr(s, "default_value") and not s.is_linked
              and s.bl_idname != "NodeSocketMenu"}
    outer_in = [(l.from_node.name, l.from_socket.identifier, inner_in.get(l.to_socket.identifier))
                for l in tree.links if l.to_node == wnode]
    outer_out = [(inner_out.get(l.from_socket.identifier), l.to_node.name, l.to_socket.identifier)
                 for l in tree.links if l.from_node == wnode]
    loc = wnode.location.copy()
    new_name = groups.copy_node(tree, core, loc).name
    tree.nodes.remove(wnode)
    for n in group.nodes:                       # the pieces go; the node itself is now `new`
        if n in used and n.type == 'GROUP':
            _remove_code_node(n.node_tree)
    bpy.data.node_groups.remove(group)
    obj.codenodes.text.from_string(code)
    new = tree.nodes[new_name]                 # found again: removing data-blocks can free node pointers
    for name, v in values.items():
        s = new.inputs.get(name)
        if s is not None:
            try:
                s.default_value = v
            except (AttributeError, TypeError, ValueError):
                pass
    for fn, fid, name in outer_in:
        s = new.inputs.get(name) if name else None
        frm = next((o for o in tree.nodes[fn].outputs if o.identifier == fid), None) if fn in tree.nodes else None
        if s is not None and frm is not None:
            tree.links.new(frm, s)
    for name, tn, tid in outer_out:
        o = new.outputs.get(name) if name else None
        to = next((i for i in tree.nodes[tn].inputs if i.identifier == tid), None) if tn in tree.nodes else None
        if o is not None and to is not None:
            tree.links.new(o, to)
    gn_link.sync()                          # wired first: a To Geometry left unconnected would reshape itself
    gn_link._dirty[0] = True
    return tree.nodes.get(new_name)


def _plain(v):
    """A socket value copied out (a colour or vector is a view into the socket, gone with its node)."""
    return tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v


def _remove_code_node(group):
    src = gn_link.source_of(group)
    if src is not None:
        text, me = src.codenodes.text, src.data
        bpy.data.objects.remove(src)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
        if text is not None and text.users == 0:
            bpy.data.texts.remove(text)
    if group.users == 0:
        bpy.data.node_groups.remove(group)


# ---- the editor ----------------------------------------------------------------------------------------

def _tree_and_node(context):
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None)
    node = tree.nodes.active if tree is not None else None
    return tree, node


def _in_gn(context):
    space = getattr(context, "space_data", None)
    return getattr(space, "type", "") == 'NODE_EDITOR' and getattr(space, "tree_type", "") == "GeometryNodeTree"


class CODENODES_OT_explode(bpy.types.Operator):
    bl_idname = "codenodes.explode"
    bl_label = "Explode Code Node"
    bl_description = ("Turn this code node into a node group of its pieces: one node per constant, constant "
                      "array and helper function, wired back in. Tab or double-click into it to edit them")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree, node = _tree_and_node(context)
        return _in_gn(context) and node is not None and node.type == 'GROUP' \
            and gn_link.is_code_group(node.node_tree)

    def execute(self, context):
        tree, node = _tree_and_node(context)
        try:
            w = explode_node(tree, node)
        except SdfCodeError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        tree.nodes.active = w
        self.report({'INFO'}, f"{len(json.loads(w.node_tree[META])['kinds'])} pieces: Tab into the group to see them")
        return {'FINISHED'}


class CODENODES_OT_collapse(bpy.types.Operator):
    bl_idname = "codenodes.collapse"
    bl_label = "Collapse Into Code"
    bl_description = "Put the pieces of an exploded code node back into its code (the group becomes one node)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree, node = _tree_and_node(context)
        if not _in_gn(context):
            return False
        if node is not None and node.type == 'GROUP' and is_exploded(node.node_tree):
            return True
        return is_exploded(tree)

    def execute(self, context):
        tree, node = _tree_and_node(context)
        if not (node is not None and node.type == 'GROUP' and is_exploded(node.node_tree)):
            bpy.ops.node.tree_path_parent()          # inside the group: collapse the group we're in
            tree, node = _tree_and_node(context)
            if node is None or not is_exploded(getattr(node, "node_tree", None)):
                return {'CANCELLED'}
        try:
            collapse_node(tree, node)
        except SdfCodeError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CODENODES_OT_enter_group(bpy.types.Operator):
    bl_idname = "codenodes.enter_group"
    bl_label = "Open Group"
    bl_description = "Go into this node group of code nodes (like Tab)"

    @classmethod
    def poll(cls, context):
        tree, node = _tree_and_node(context)
        return _in_gn(context) and node is not None and node.type == 'GROUP' and node.select \
            and gn_link.holds_code(node.node_tree)

    def execute(self, context):
        return bpy.ops.node.group_edit(exit=False)


def _context_menu(self, context):
    if not _in_gn(context):
        return
    tree, node = _tree_and_node(context)
    if node is None or node.type != 'GROUP':
        return
    layout = self.layout
    if gn_link.is_code_group(node.node_tree):
        layout.separator()
        layout.operator(CODENODES_OT_explode.bl_idname, icon='MOD_EXPLODE')
    elif is_exploded(node.node_tree):
        layout.separator()
        layout.operator(CODENODES_OT_collapse.bl_idname, icon='FULLSCREEN_EXIT')


classes = (CODENODES_OT_explode, CODENODES_OT_collapse, CODENODES_OT_enter_group)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if hasattr(bpy.types, "NODE_MT_context_menu"):
        bpy.types.NODE_MT_context_menu.append(_context_menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Node Editor", space_type='NODE_EDITOR')
        kmi = km.keymap_items.new(CODENODES_OT_enter_group.bl_idname, 'LEFTMOUSE', 'DOUBLE_CLICK')
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    if hasattr(bpy.types, "NODE_MT_context_menu"):
        bpy.types.NODE_MT_context_menu.remove(_context_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
