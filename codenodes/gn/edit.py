"""Small changes to a node tree that already exists, without resending all of it.

Rewriting a whole tree to change one value is slow, costs a lot of tokens for a big
setup, and every rewrite is a chance to drop something. These operations change only
what they name:

    {"op": "set",    "node": "Grid", "values": {"Vertices X": 40}, "settings": {...}}
    {"op": "add",    "node": {"name": "Jitter", "type": "GeometryNodeSetPosition"}}
    {"op": "link",   "from": ["Jitter", "Geometry"], "to": ["Output", "Geometry"]}
    {"op": "unlink", "to": ["Output", "Geometry"]}
    {"op": "insert", "node": {...}, "between": {"from": [...], "to": [...]}}
    {"op": "remove", "node": "Jitter", "bridge": true}
    {"op": "rename", "node": "Grid", "to": "Ground"}
    {"op": "input",  "socket": "Seed", "type": "NodeSocketInt", "default_value": 0}
    {"op": "output", "socket": "Points", "type": "NodeSocketGeometry"}
    {"op": "set_socket", "socket": "Seed", "max_value": 100}
    {"op": "remove_socket", "socket": "Seed"}

A list of them is all-or-nothing: they are tried on a copy first, so a mistake in the
third leaves the tree exactly as it was. Editing in place also keeps the group's socket
identifiers, so values tuned on a modifier stay put.
"""

from __future__ import annotations

import bpy

from . import serialize
from .serialize import BuildError, find_socket


def apply(tree, ops):
    """Apply a list of operations. Returns {"done": [...], "warnings": [...]}."""
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list) or not ops:
        raise BuildError("nodes_edit wants a list of operations, e.g. "
                         "[{'op': 'set', 'node': 'Grid', 'values': {'Vertices X': 40}}]")
    trial = tree.copy()
    try:
        _run(trial, ops)
    finally:
        bpy.data.node_groups.remove(trial)
    done, warnings = _run(tree, ops)
    warnings.extend(serialize.validate(tree))
    return {"done": done, "warnings": warnings}


def _run(tree, ops):
    done, warnings, added = [], [], []
    for number, op in enumerate(ops, 1):
        if not isinstance(op, dict) or "op" not in op:
            raise BuildError(f"step {number}: each operation needs an 'op', got {op!r}")
        handler = _OPS.get(op["op"])
        if handler is None:
            raise BuildError(f"step {number}: there is no operation '{op['op']}'. There is: "
                             + ", ".join(_OPS))
        try:
            done.append(handler(tree, op, warnings, added))
        except BuildError as exc:
            raise BuildError(f"step {number} ({op['op']}): {exc}") from None
    # `added` holds the names of new nodes that were given no position
    unplaced = [tree.nodes[name] for name in added if name in tree.nodes]
    if unplaced:
        serialize.layout(tree, only=unplaced)
    return done, warnings


def _node(tree, name):
    node = tree.nodes.get(name) if isinstance(name, str) else None
    if node is None:
        raise BuildError(f"there is no node called {name!r}. Nodes: "
                         + ", ".join(n.name for n in tree.nodes))
    return node


def _socket(tree, ref, side):
    try:
        node_name, key = ref
    except Exception:
        raise BuildError(f"a socket is [node, socket], got {ref!r}") from None
    node = _node(tree, node_name)
    socket = find_socket(node, key, side)
    if socket is None:
        raise BuildError(f"'{node.name}' ({node.bl_idname}) has no "
                         f"{'input' if side == 'in' else 'output'} '{key}'. It has: "
                         + serialize._socket_list(node, side))
    return socket


def _add(tree, op, warnings, added):
    entry = op.get("node")
    if not isinstance(entry, dict):
        raise BuildError("'add' wants {'node': {'name': ..., 'type': ...}}")
    if entry.get("name") in tree.nodes:
        raise BuildError(f"there is already a node called '{entry['name']}'")
    node = serialize._make_node(tree, entry, warnings)
    if entry.get("pairs_with"):
        node.pair_with_output(_node(tree, entry["pairs_with"]))
    serialize._make_items(node, entry)
    if entry.get("inside"):
        node.parent = _node(tree, entry["inside"])
    serialize._apply_settings(node, entry, warnings)
    serialize._apply_values(node, entry, {}, warnings)
    if entry.get("at") is None:
        added.append(node.name)
    return f"added '{node.name}' ({node.bl_idname})"


def _set(tree, op, warnings, added):
    node = _node(tree, op.get("node"))
    changed = []
    if "items" in op:
        # Rebuilding items renumbers their sockets, which drops their links; note each
        # link by socket name and reconnect whatever still has a socket of that name.
        tree_links = node.id_data.links
        ins = [(l.from_socket, l.to_socket.name) for l in tree_links if l.to_node == node]
        outs = [(l.from_socket.name, l.to_socket) for l in tree_links if l.from_node == node]
        serialize._make_items(node, {"items": op["items"]})
        for source, label in ins:
            target = find_socket(node, label, "in")
            if target is not None:
                tree_links.new(source, target)
        for label, target in outs:
            source = find_socket(node, label, "out")
            if source is not None:
                tree_links.new(source, target)
        changed.append("items")
    if "settings" in op:
        serialize._apply_settings(node, {"name": node.name, "settings": op["settings"]}, warnings)
        changed += list(op["settings"])
    if "values" in op:
        serialize._apply_values(node, {"values": op["values"]}, {}, warnings)
        changed += list(op["values"])
    for key, attr in (("label", "label"), ("muted", "mute"), ("at", "location"),
                      ("collapsed", "hide")):
        if key in op:
            setattr(node, attr, op[key])
            changed.append(key)
    if "inside" in op:
        node.parent = _node(tree, op["inside"]) if op["inside"] else None
        changed.append("frame")
    if not changed:
        raise BuildError("'set' changed nothing; give it values, settings, items, label, at, "
                         "muted, collapsed or inside")
    return f"set {', '.join(map(str, changed))} on '{node.name}'"


def _link(tree, op, warnings, added):
    out = _socket(tree, op.get("from"), "out")
    into = _socket(tree, op.get("to"), "in")
    tree.links.new(out, into)
    return f"linked {out.node.name}.{out.name} → {into.node.name}.{into.name}"


def _unlink(tree, op, warnings, added):
    into = _socket(tree, op.get("to"), "in")
    out = _socket(tree, op["from"], "out") if op.get("from") else None
    gone = [l for l in tree.links if l.to_socket == into and (out is None or l.from_socket == out)]
    if not gone:
        raise BuildError(f"nothing is connected to {into.node.name}.{into.name}"
                         + (f" from {out.node.name}.{out.name}" if out else ""))
    for link in gone:
        tree.links.remove(link)
    return f"disconnected {len(gone)} link(s) into {into.node.name}.{into.name}"


def _insert(tree, op, warnings, added):
    between = op.get("between") or {}
    out = _socket(tree, between.get("from"), "out")
    into = _socket(tree, between.get("to"), "in")
    existing = [l for l in tree.links if l.from_socket == out and l.to_socket == into]
    if not existing:
        feeding = [f"{l.from_node.name}.{l.from_socket.name}" for l in tree.links
                   if l.to_socket == into]
        raise BuildError(f"{out.node.name}.{out.name} is not connected to "
                         f"{into.node.name}.{into.name}; "
                         + (f"that input is fed by {', '.join(feeding)}" if feeding
                            else "nothing is connected to that input"))
    _add(tree, {"node": op.get("node")}, warnings, added)
    node = tree.nodes[op["node"]["name"]]
    if op.get("in"):
        node_in = _socket(tree, [node.name, op["in"]], "in")
    else:
        node_in = next((s for s in node.inputs if s.enabled and s.type == out.type), None)
    if op.get("out"):
        node_out = _socket(tree, [node.name, op["out"]], "out")
    else:
        node_out = next((s for s in node.outputs if s.enabled and s.type == into.type), None)
    if node_in is None or node_out is None:
        raise BuildError(f"could not tell which sockets of '{node.name}' to use; say "
                         "'in' and 'out'")
    tree.links.remove(existing[0])
    tree.links.new(out, node_in)
    tree.links.new(node_out, into)
    if node.name in added:
        node.location = ((out.node.location.x + into.node.location.x) / 2,
                         (out.node.location.y + into.node.location.y) / 2 - 40)
        added.remove(node.name)
    return f"inserted '{node.name}' between {out.node.name} and {into.node.name}"


def _remove(tree, op, warnings, added):
    node = _node(tree, op.get("node"))
    name = node.name
    if op.get("bridge"):
        # join each outgoing link to an incoming one of the same type — a Set Position's
        # Geometry, never its Offset — preferring an input with the output's name
        incoming = [l for l in tree.links if l.to_node == node and l.to_socket.enabled]
        pairs = []
        for out_link in [l for l in tree.links if l.from_node == node]:
            same = [l for l in incoming if l.to_socket.type == out_link.from_socket.type]
            named = [l for l in same if l.to_socket.name == out_link.from_socket.name]
            pick = (named or same or [None])[0]
            if pick is not None:
                pairs.append((pick.from_socket, out_link.to_socket))
        for source, target in pairs:
            tree.links.new(source, target)
    tree.nodes.remove(node)
    return f"removed '{name}'" + (" and joined up the flow around it" if op.get("bridge") else "")


def _rename(tree, op, warnings, added):
    node = _node(tree, op.get("node"))
    new = op.get("to")
    if not new or new in tree.nodes:
        raise BuildError(f"cannot rename to {new!r}: " + ("taken" if new else "no name given"))
    old, node.name = node.name, new
    if old in added:
        added[added.index(old)] = new
    return f"renamed '{old}' to '{new}'"


def _iface_socket(tree, op, in_out=None):
    name = op.get("socket")
    in_out = in_out or op.get("in_out", "INPUT")
    found = [i for i in tree.interface.items_tree
             if i.item_type == 'SOCKET' and i.name == name and i.in_out == in_out]
    if not found:
        raise BuildError(f"the group has no {in_out.lower()} called {name!r}. It has: "
                         + ", ".join(i.name for i in tree.interface.items_tree
                                     if i.item_type == 'SOCKET' and i.in_out == in_out))
    return found[0]


def _new_socket(in_out):
    def handler(tree, op, warnings, added):
        entry = dict(op)
        entry.pop("op")
        entry["in_out"] = in_out
        if "socket" not in entry or "type" not in entry:
            raise BuildError(f"'{in_out.lower()}' wants 'socket' (a name) and 'type' "
                             "(e.g. NodeSocketFloat)")
        panel = None
        if entry.get("in_panel"):
            panel = next((i for i in tree.interface.items_tree if i.item_type == 'PANEL'
                          and i.name == entry["in_panel"]), None)
            if panel is None:
                raise BuildError(f"there is no panel called {entry['in_panel']!r}")
        try:
            socket = tree.interface.new_socket(entry["socket"], in_out=in_out,
                                               socket_type=entry["type"], parent=panel)
        except Exception as exc:
            raise BuildError(f"could not add '{entry['socket']}': {exc}") from None
        _set_socket_props(socket, entry, warnings)
        return f"added the group {in_out.lower()} '{socket.name}'"
    return handler


def _set_socket_props(socket, entry, warnings):
    keys = [k for k in serialize.IFACE_ORDER if k in entry]
    keys += [k for k in entry if k not in keys and k not in serialize.IFACE_FIXED
             and k not in ("socket", "type", "in_panel", "in_out", "op", "rename")]
    for attr in keys:
        if not hasattr(socket, attr):
            raise BuildError(f"group sockets have no '{attr}'")
        try:
            setattr(socket, attr, serialize._resolve(entry[attr]))
        except Exception as exc:
            raise BuildError(f"could not set {attr} = {entry[attr]!r} on '{socket.name}': "
                             f"{exc}") from None


def _set_iface(tree, op, warnings, added):
    socket = _iface_socket(tree, op)
    if op.get("rename"):
        socket.name = op["rename"]
    if op.get("type") and op["type"] != socket.socket_type:
        socket.socket_type = op["type"]
    _set_socket_props(socket, {k: v for k, v in op.items() if k != "type"}, warnings)
    return f"changed the group socket '{socket.name}'"


def _remove_iface(tree, op, warnings, added):
    socket = _iface_socket(tree, op)
    name = socket.name
    tree.interface.remove(socket)
    return f"removed the group socket '{name}'"


_OPS = {"add": _add, "set": _set, "link": _link, "unlink": _unlink, "insert": _insert,
        "remove": _remove, "rename": _rename, "input": _new_socket("INPUT"),
        "output": _new_socket("OUTPUT"), "set_socket": _set_iface,
        "remove_socket": _remove_iface}
