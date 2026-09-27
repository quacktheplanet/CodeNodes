"""A Geometry Nodes tree as plain data, and back again — losslessly.

This is the piece everything else stands on. If a tree can be read out, changed and
written back without losing anything, then an assistant can understand an existing
setup, edit part of it, and leave the rest alone. If the round trip is lossy, every
edit quietly damages the file.

The form is ordinary JSON-able data:

    {"name": ..., "interface": [...], "nodes": [...], "links": [...]}

A node records its type, its settings (the dropdowns, which are not sockets), and the
values of any input left unconnected. Links are named by socket identifier, which is
stable, rather than by position.
"""

from __future__ import annotations

import bpy

from .catalog import _base_props, _plain

FORMAT = 1
SKIP_PROPS = {"location", "location_absolute", "width", "height", "select", "show_options",
              "show_preview", "show_texture", "use_custom_color", "color", "label", "name",
              "parent", "warning_propagation", "bl_description"}


def _settings(node):
    """The dropdowns and toggles that are not sockets — what makes a Math node MULTIPLY."""
    out = {}
    for prop in node.bl_rna.properties:
        if prop.is_readonly or prop.identifier in _base_props() or prop.identifier in SKIP_PROPS:
            continue
        value = getattr(node, prop.identifier, None)
        if value is None:
            continue
        if hasattr(value, "id_data") and hasattr(value, "name"):     # a datablock reference
            out[prop.identifier] = {"datablock": value.name, "kind": type(value).__name__}
        else:
            out[prop.identifier] = _plain(value)
    return out


def _socket_value(socket):
    value = getattr(socket, "default_value", None)
    if value is None:
        return None
    if hasattr(value, "id_data") and hasattr(value, "name"):
        return {"datablock": value.name, "kind": type(value).__name__}
    return _plain(value)


def read(tree):
    """A node tree as plain data."""
    nodes = []
    for node in tree.nodes:
        entry = {"name": node.name, "type": node.bl_idname,
                 "at": [round(node.location.x), round(node.location.y)]}
        if node.label:
            entry["label"] = node.label
        if node.mute:
            entry["muted"] = True
        if node.parent:
            entry["inside"] = node.parent.name
        if node.bl_idname == 'NodeFrame':
            entry["size"] = [round(node.width), round(node.height)]
        settings = _settings(node)
        if settings:
            entry["settings"] = settings
        values = {}
        for socket in node.inputs:
            if socket.is_linked or not socket.enabled:
                continue
            value = _socket_value(socket)
            if value is not None:
                values[socket.identifier] = value
        if values:
            entry["values"] = values
        if node.bl_idname == 'NodeGroup' or getattr(node, "node_tree", None) is not None:
            inner = getattr(node, "node_tree", None)
            if inner is not None:
                entry["group"] = inner.name
        paired = getattr(node, "paired_output", None)
        if paired is not None:
            entry["pairs_with"] = paired.name
        nodes.append(entry)

    links = []
    for link in tree.links:
        links.append({"from": [link.from_node.name, link.from_socket.identifier],
                      "to": [link.to_node.name, link.to_socket.identifier]})

    return {"format": FORMAT, "name": tree.name, "kind": tree.bl_idname,
            "interface": read_interface(tree), "nodes": nodes, "links": links}


def read_interface(tree):
    """The group's own inputs and outputs — its contract."""
    items = []
    for item in tree.interface.items_tree:
        if item.item_type == 'PANEL':
            items.append({"panel": item.name, "closed": bool(item.default_closed),
                          "about": item.description or None})
            continue
        entry = {"socket": item.name, "in_out": item.in_out, "type": item.socket_type,
                 "identifier": item.identifier}
        for attr in ("description", "subtype", "attribute_domain", "default_attribute_name"):
            value = getattr(item, attr, None)
            if value:
                entry[attr] = value
        for attr in ("default_value", "min_value", "max_value"):
            if hasattr(item, attr):
                entry[attr] = _plain(getattr(item, attr))
        for attr in ("hide_value", "hide_in_modifier", "force_non_field"):
            if getattr(item, attr, False):
                entry[attr] = True
        if item.parent and getattr(item.parent, "name", None):
            entry["in_panel"] = item.parent.name
        items.append(entry)
    return items


# ---- writing ---------------------------------------------------------------------------

class BuildError(ValueError):
    """Something in the description could not be built, said plainly."""


def write(data, tree=None, name=None, replace=True):
    """Build a node tree from plain data. Returns the tree."""
    if not isinstance(data, dict) or "nodes" not in data:
        raise BuildError("a tree description needs at least a 'nodes' list")
    name = name or data.get("name") or "Geometry Nodes"
    kind = data.get("kind") or "GeometryNodeTree"
    if tree is None:
        tree = bpy.data.node_groups.get(name)
    if tree is None:
        tree = bpy.data.node_groups.new(name, kind)
    elif replace:
        tree.nodes.clear()
        for item in list(tree.interface.items_tree):
            tree.interface.remove(item)
    tree.use_fake_user = True

    # Rebuilding the interface hands out fresh socket identifiers, so anything that
    # referred to the old ones (every link to Group Input or Group Output) has to be
    # translated. This is the map from what was recorded to what now exists.
    remap = write_interface(tree, data.get("interface") or [])

    made = {}
    frames = []
    for entry in data["nodes"]:
        made[entry["name"]] = _make_node(tree, entry, frames, remap)
    for entry, node in ((e, made[e["name"]]) for e in data["nodes"]):
        if entry.get("inside"):
            parent = made.get(entry["inside"])
            if parent is not None:
                node.parent = parent
    # zones have to be paired before their sockets appear
    for entry in data["nodes"]:
        partner = entry.get("pairs_with")
        if partner and partner in made:
            node = made[entry["name"]]
            if hasattr(node, "pair_with_output"):
                try:
                    node.pair_with_output(made[partner])
                except Exception as exc:
                    raise BuildError(f"could not pair zone '{entry['name']}' with "
                                     f"'{partner}': {exc}") from None
    # settings and values again: pairing and some settings change which sockets exist
    for entry in data["nodes"]:
        _apply_settings(made[entry["name"]], entry, second_pass=True, remap=remap)

    for link in data.get("links") or []:
        _make_link(tree, made, link, remap)
    return tree


def _group_socket(node):
    """Group Input and Group Output sockets come from the interface, so their
    identifiers are reassigned when it is rebuilt."""
    return node.bl_idname in ('NodeGroupInput', 'NodeGroupOutput')


def _translate(node, identifier, remap):
    return remap.get(identifier, identifier) if remap and _group_socket(node) else identifier


def _make_node(tree, entry, frames, remap=None):
    kind = entry.get("type")
    if not kind:
        raise BuildError(f"node '{entry.get('name', '?')}' has no type")
    try:
        node = tree.nodes.new(kind)
    except Exception as exc:
        raise BuildError(f"'{kind}' is not a node type that can be added here ({exc})") from None
    node.name = entry["name"]
    if node.name != entry["name"]:
        raise BuildError(f"two nodes are both called '{entry['name']}'")
    if entry.get("label"):
        node.label = entry["label"]
    if entry.get("at"):
        node.location = entry["at"]
    if entry.get("muted"):
        node.mute = True
    if entry.get("size") and kind == 'NodeFrame':
        node.width, node.height = entry["size"]
    if entry.get("group"):
        inner = bpy.data.node_groups.get(entry["group"])
        if inner is None:
            raise BuildError(f"node '{entry['name']}' wants the group '{entry['group']}', "
                             "which is not in this file")
        node.node_tree = inner
    _apply_settings(node, entry, second_pass=False, remap=remap)
    return node


def _resolve(value):
    if isinstance(value, dict) and "datablock" in value:
        for collection in (bpy.data.objects, bpy.data.materials, bpy.data.collections,
                           bpy.data.images, bpy.data.node_groups, bpy.data.textures):
            found = collection.get(value["datablock"])
            if found is not None:
                return found
        return None
    return value


def _apply_settings(node, entry, second_pass, remap=None):
    for key, value in (entry.get("settings") or {}).items():
        resolved = _resolve(value)
        if resolved is None and isinstance(value, dict):
            continue
        try:
            setattr(node, key, resolved)
        except Exception as exc:
            if not second_pass:
                raise BuildError(f"node '{entry['name']}' ({node.bl_idname}): cannot set "
                                 f"{key} = {value!r} — {exc}") from None
    for identifier, value in (entry.get("values") or {}).items():
        wanted = _translate(node, identifier, remap)
        socket = next((s for s in node.inputs if s.identifier == wanted), None)
        if socket is None or not hasattr(socket, "default_value"):
            continue
        resolved = _resolve(value)
        if resolved is None and isinstance(value, dict):
            continue
        try:
            socket.default_value = resolved
        except Exception as exc:
            if second_pass:
                raise BuildError(f"node '{entry['name']}': input '{socket.name}' will not take "
                                 f"{value!r} — {exc}") from None


def _make_link(tree, made, link, remap=None):
    try:
        (from_name, from_id), (to_name, to_id) = link["from"], link["to"]
    except Exception:
        raise BuildError(f"a link should look like "
                         f"{{'from': [node, socket], 'to': [node, socket]}}, got {link!r}") from None
    a, b = made.get(from_name), made.get(to_name)
    if a is None or b is None:
        raise BuildError(f"link between '{from_name}' and '{to_name}': "
                         f"{'the first' if a is None else 'the second'} node does not exist")
    from_id = _translate(a, from_id, remap)
    to_id = _translate(b, to_id, remap)
    out = next((s for s in a.outputs if s.identifier == from_id), None)
    into = next((s for s in b.inputs if s.identifier == to_id), None)
    if out is None:
        raise BuildError(f"'{from_name}' ({a.bl_idname}) has no output '{from_id}'. It has: "
                         + ", ".join(s.identifier for s in a.outputs))
    if into is None:
        raise BuildError(f"'{to_name}' ({b.bl_idname}) has no input '{to_id}'. It has: "
                         + ", ".join(s.identifier for s in b.inputs))
    tree.links.new(out, into)


def write_interface(tree, items):
    """Rebuild a group's inputs and outputs. Returns {recorded identifier: new one}."""
    panels, remap = {}, {}
    for item in items:
        if "panel" in item:
            panel = tree.interface.new_panel(item["panel"], description=item.get("about") or "",
                                             default_closed=bool(item.get("closed")))
            panels[item["panel"]] = panel
            continue
        socket = tree.interface.new_socket(item["socket"], in_out=item.get("in_out", "INPUT"),
                                           socket_type=item["type"],
                                           parent=panels.get(item.get("in_panel")))
        if item.get("identifier"):
            remap[item["identifier"]] = socket.identifier
        for attr in ("description", "subtype", "attribute_domain", "default_attribute_name",
                     "default_value", "min_value", "max_value", "hide_value",
                     "hide_in_modifier", "force_non_field"):
            if attr in item and hasattr(socket, attr):
                try:
                    setattr(socket, attr, item[attr])
                except Exception:
                    pass
    return remap
