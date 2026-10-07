"""Node groups of code nodes, made from Python: what Ctrl+G does in the editor, for Explode and tests.

A chain runs straight through an ordinary node group (gn_link follows it in through the Group Input
and out through the Group Output), so code nodes can be grouped like any nodes.
"""

from __future__ import annotations

import bpy

BASE_TYPES = {'VALUE': "NodeSocketFloat", 'INT': "NodeSocketInt", 'BOOLEAN': "NodeSocketBool",
              'VECTOR': "NodeSocketVector", 'RGBA': "NodeSocketColor", 'STRING': "NodeSocketString",
              'GEOMETRY': "NodeSocketGeometry", 'OBJECT': "NodeSocketObject", 'MATERIAL': "NodeSocketMaterial",
              'COLLECTION': "NodeSocketCollection", 'IMAGE': "NodeSocketImage", 'BUNDLE': "NodeSocketBundle",
              'CLOSURE': "NodeSocketClosure", 'MENU': "NodeSocketMenu", 'ROTATION': "NodeSocketRotation",
              'MATRIX': "NodeSocketMatrix", 'TEXTURE': "NodeSocketTexture"}
_SKIP_PROPS = {"rna_type", "name", "label", "location", "location_absolute", "width", "height", "dimensions",
               "select", "parent", "inputs", "outputs", "internal_links", "type", "bl_idname", "bl_label",
               "bl_description", "bl_icon", "bl_static_type", "bl_width_default", "bl_width_min",
               "bl_width_max", "bl_height_default", "bl_height_min", "bl_height_max", "node_tree",
               "is_active_output", "warning_propagation", "color", "use_custom_color", "show_options",
               "show_preview", "hide", "mute", "show_texture", "color_tag"}


def interface_type(node, sock):
    """(socket type, is a list) for an interface socket made from `sock`."""
    g = node.node_tree if node.type == 'GROUP' else None
    if g is not None:
        items = [i for i in g.interface.items_tree if i.item_type == 'SOCKET'
                 and i.in_out == ('OUTPUT' if sock.is_output else 'INPUT')]
        item = next((i for i in items if i.identifier == sock.identifier), None)
        if item is not None:
            return item.socket_type, getattr(item, "structure_type", "") == 'LIST'
    return BASE_TYPES.get(sock.type, "NodeSocketFloat"), False


def copy_node(dst_tree, node, location=None):
    """A copy of `node` in `dst_tree`: same kind, settings, node group and typed input values."""
    c = dst_tree.nodes.new(node.bl_idname)
    if node.type == 'GROUP':
        c.node_tree = node.node_tree
    for p in node.bl_rna.properties:
        if p.identifier in _SKIP_PROPS or p.is_readonly or p.type in ('POINTER', 'COLLECTION'):
            continue
        try:
            setattr(c, p.identifier, getattr(node, p.identifier))
        except (AttributeError, TypeError, ValueError):
            pass
    c.label, c.width, c.hide = node.label, node.width, node.hide
    c.location = location if location is not None else node.location
    for src, dst in zip(node.inputs, c.inputs):
        if hasattr(src, "default_value") and src.identifier == dst.identifier:
            try:
                dst.default_value = src.default_value
            except (AttributeError, TypeError, ValueError):
                pass
    return c


def _new_item(iface, name, stype, is_list, in_out):
    item = iface.new_socket(name, in_out=in_out, socket_type=stype)
    if is_list:
        try:
            item.structure_type = 'LIST'
        except (AttributeError, TypeError):
            pass
    return item


def make_group(tree, nodes, name):
    """Put `nodes` into a new node group, wired as before (like Ctrl+G). Returns the group node."""
    nodes = list(nodes)
    names = {n.name for n in nodes}
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    iface = group.interface
    cx = sum(n.location.x for n in nodes) / len(nodes)
    cy = sum(n.location.y for n in nodes) / len(nodes)
    minx = min(n.location.x for n in nodes)
    maxx = max(n.location.x + n.width for n in nodes)
    gin, gout = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (minx - cx - 300, 0), (maxx - cx + 120, 0)
    copies = {n.name: copy_node(group, n, (n.location.x - cx, n.location.y - cy)) for n in nodes}
    ins, outs, outer_in, outer_out, inner = {}, {}, [], [], []
    for l in list(tree.links):
        a, b = l.from_node.name in names, l.to_node.name in names
        if a and b:
            inner.append((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier))
        elif b:                                 # comes in: one group input per outside socket
            key = (l.from_node.name, l.from_socket.identifier)
            if key not in ins:
                stype, is_list = interface_type(l.to_node, l.to_socket)
                ins[key] = _new_item(iface, _unique_name(iface, l.to_socket.name, 'INPUT'), stype, is_list,
                                     'INPUT')
            outer_in.append((l.from_node.name, l.from_socket.identifier, ins[key].identifier))
            inner.append((None, ins[key].identifier, l.to_node.name, l.to_socket.identifier))
        elif a:                                 # goes out: one group output per inside socket
            key = (l.from_node.name, l.from_socket.identifier)
            if key not in outs:
                stype, is_list = interface_type(l.from_node, l.from_socket)
                outs[key] = _new_item(iface, _unique_name(iface, l.from_socket.name, 'OUTPUT'), stype, is_list,
                                      'OUTPUT')
                inner.append((l.from_node.name, l.from_socket.identifier, False, outs[key].identifier))
            outer_out.append((outs[key].identifier, l.to_node.name, l.to_socket.identifier))
    for fn, fid, tn, tid in inner:
        frm = _by_id(gin.outputs, fid) if fn is None else _by_id(copies[fn].outputs, fid)
        to = _by_id(gout.inputs, tid) if tn is False else _by_id(copies[tn].inputs, tid)
        if frm is not None and to is not None:
            group.links.new(frm, to)
    for n in nodes:
        tree.nodes.remove(n)
    gnode = tree.nodes.new("GeometryNodeGroup")
    gnode.node_tree = group
    gnode.location = (cx, cy)
    gnode.width = 200
    for fn, fid, iid in outer_in:
        frm = _by_id(tree.nodes[fn].outputs, fid) if fn in tree.nodes else None
        to = _by_id(gnode.inputs, iid)
        if frm is not None and to is not None:
            tree.links.new(frm, to)
    for oid, tn, tid in outer_out:
        frm = _by_id(gnode.outputs, oid)
        to = _by_id(tree.nodes[tn].inputs, tid) if tn in tree.nodes else None
        if frm is not None and to is not None:
            tree.links.new(frm, to)
    return gnode


def _by_id(sockets, identifier):
    return next((s for s in sockets if s.identifier == identifier), None)


def _unique_name(iface, name, in_out):
    taken = {i.name for i in iface.items_tree if i.item_type == 'SOCKET' and i.in_out == in_out}
    if name not in taken:
        return name
    k = 2
    while f"{name} {k}" in taken:
        k += 1
    return f"{name} {k}"
