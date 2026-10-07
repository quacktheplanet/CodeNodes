"""A Geometry Nodes tree as plain data, and back again — losslessly.

This is the piece everything else stands on. If a tree can be read out, changed and
written back without losing anything, then an assistant can understand an existing
setup, edit part of it, and leave the rest alone. If the round trip is lossy, every
edit quietly damages the file.

The form is ordinary JSON-able data:

    {"name": ..., "interface": [...], "nodes": [...], "links": [...], "groups": [...]}

A node records its type, its settings (the dropdowns, which are not sockets), the values
of any input left unconnected, and — for nodes whose sockets you add yourself, like
Capture Attribute or a Repeat zone — the items that make those sockets. Links name
sockets by identifier as read, but a hand-written description can use plain socket
names instead: ["Cube", "Mesh"] rather than ["Cube", "Mesh_001"].

Socket identifiers are not stable across a rebuild. Group sockets are renumbered when an
interface is rebuilt, and item sockets come from a counter that survives deletions
(remove an item and add one, and you get "Value_2" where a fresh node says "Value_1").
So a node with sockets like that also records the identifiers it had, and writing maps
the recorded ones onto the new ones by position.
"""

from __future__ import annotations

import re

import bpy

from .. import mod_inputs
from .catalog import _base_props, _plain

FORMAT = 2
SKIP_PROPS = {"location", "location_absolute", "width", "height", "select", "show_options",
              "show_preview", "show_texture", "use_custom_color", "color", "label", "name",
              "parent", "warning_propagation", "bl_description", "node_tree", "hide", "mute"}
SKIP_ITEM_PROPS = {"rna_type", "color", "identifier"}
IFACE_FIXED = {"rna_type", "item_type", "parent", "position", "index", "name", "identifier",
               "socket_type", "in_out", "select", "bl_socket_idname"}
# set these before default_value, which is clamped to them
IFACE_ORDER = ("min_value", "max_value", "subtype")

_DATA = {"Object": "objects", "Material": "materials", "Collection": "collections",
         "Image": "images", "Text": "texts", "VectorFont": "fonts", "Texture": "textures",
         "GeometryNodeTree": "node_groups", "ShaderNodeTree": "node_groups", "Mesh": "meshes"}


class BuildError(ValueError):
    """Something in the description could not be built, said plainly."""


# ---- reading ---------------------------------------------------------------------------

def _datablock(value):
    return hasattr(value, "id_data") and hasattr(value, "name") and hasattr(value, "users")


def _skip_setting(identifier):
    return (identifier in _base_props() or identifier in SKIP_PROPS
            or identifier.startswith("active_"))


def _settings(node):
    """The dropdowns and toggles that are not sockets — what makes a Math node MULTIPLY."""
    out = {}
    for prop in node.bl_rna.properties:
        if prop.is_readonly or _skip_setting(prop.identifier):
            continue
        value = getattr(node, prop.identifier, None)
        if value is None:
            continue
        if _datablock(value):
            out[prop.identifier] = {"datablock": value.name, "kind": type(value).__name__}
        else:
            out[prop.identifier] = _plain(value)
    return out


def _item_collections(node):
    """Collections of items that make sockets: capture_items, repeat_items, enum_items…"""
    return [p.identifier for p in node.bl_rna.properties
            if p.type == 'COLLECTION' and p.identifier not in ("inputs", "outputs", "internal_links")
            and hasattr(getattr(node, p.identifier, None), "new")]


def _items(node):
    out = {}
    for coll_name in _item_collections(node):
        records = []
        for item in getattr(node, coll_name):
            record = {}
            for prop in item.bl_rna.properties:
                if prop.is_readonly or prop.identifier in SKIP_ITEM_PROPS:
                    continue
                record[prop.identifier] = _plain(getattr(item, prop.identifier))
            records.append(record)
        out[coll_name] = records
    return out


def _dynamic(node):
    """Whether this node's socket identifiers can change when it is rebuilt."""
    return (node.bl_idname in ('NodeGroupInput', 'NodeGroupOutput')
            or getattr(node, "node_tree", None) is not None
            or getattr(node, "paired_output", None) is not None
            or bool(_item_collections(node)))


def _socket_value(socket):
    value = getattr(socket, "default_value", None)
    if value is None:
        return None
    if _datablock(value):
        return {"datablock": value.name, "kind": type(value).__name__}
    return _plain(value)


def read(tree, groups=True):
    """A node tree as plain data. With groups=True, any node groups it uses come along,
    innermost first, so the description can be rebuilt in an empty file."""
    nodes = []
    for node in tree.nodes:
        entry = {"name": node.name, "type": node.bl_idname,
                 "at": [round(node.location.x), round(node.location.y)]}
        if node.label:
            entry["label"] = node.label
        if node.mute:
            entry["muted"] = True
        if node.hide:
            entry["collapsed"] = True
        if node.use_custom_color:
            entry["color"] = [round(c, 3) for c in node.color]
        if node.parent:
            entry["inside"] = node.parent.name
        if node.bl_idname == 'NodeFrame':
            entry["size"] = [round(node.width), round(node.height)]
        inner = getattr(node, "node_tree", None)
        if inner is not None:
            entry["group"] = inner.name
        paired = getattr(node, "paired_output", None)
        if paired is not None:
            entry["pairs_with"] = paired.name
        items = _items(node)
        if items:
            entry["items"] = items
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
        if _dynamic(node):
            entry["sockets"] = {"in": [s.identifier for s in node.inputs],
                                "out": [s.identifier for s in node.outputs]}
        nodes.append(entry)

    links = []
    for link in tree.links:
        record = {"from": [link.from_node.name, link.from_socket.identifier],
                  "to": [link.to_node.name, link.to_socket.identifier]}
        if link.is_muted:
            record["muted"] = True
        if not (link.from_socket.enabled and link.to_socket.enabled):
            record["hidden"] = True      # on a socket not in use: rebuild it exactly as it was
        links.append(record)

    data = {"format": FORMAT, "name": tree.name, "kind": tree.bl_idname,
            "interface": read_interface(tree), "nodes": nodes, "links": links}
    if groups:
        inner = [read(g, groups=False) for g in dependencies(tree)]
        if inner:
            data["groups"] = inner
    return data


def dependencies(tree):
    """The node groups a tree uses, directly or not, innermost first."""
    order, seen = [], set()

    def visit(t):
        for node in t.nodes:
            inner = getattr(node, "node_tree", None)
            if inner is not None and inner.name not in seen and inner != tree:
                seen.add(inner.name)
                visit(inner)
                order.append(inner)
    visit(tree)
    return order


_iface_defaults = {}


def _fresh_socket_defaults(socket_type, in_out, kind="GeometryNodeTree"):
    """What a brand-new interface socket of this type looks like, so only differences
    need recording. Made in a tree of the same kind: a shader socket does not exist in
    a geometry tree."""
    key = (socket_type, in_out, kind)
    if key not in _iface_defaults:
        scratch = bpy.data.node_groups.new("__codenodes_iface__", kind)
        try:
            s = scratch.interface.new_socket("x", in_out=in_out, socket_type=socket_type)
            _iface_defaults[key] = {p.identifier: _plain(getattr(s, p.identifier))
                                    for p in s.bl_rna.properties
                                    if not p.is_readonly and p.identifier not in IFACE_FIXED}
        except Exception:
            _iface_defaults[key] = {}          # record everything rather than fail the read
        finally:
            bpy.data.node_groups.remove(scratch)
    return _iface_defaults[key]


def read_interface(tree):
    """The group's own inputs and outputs — its contract."""
    items = []
    for item in tree.interface.items_tree:
        parent = item.parent.name if item.parent and getattr(item.parent, "name", "") else None
        if item.item_type == 'PANEL':
            entry = {"panel": item.name, "closed": bool(item.default_closed),
                     "about": item.description or None}
            if parent:
                entry["in_panel"] = parent
            items.append(entry)
            continue
        entry = {"socket": item.name, "in_out": item.in_out, "type": item.socket_type,
                 "identifier": item.identifier}
        fresh = _fresh_socket_defaults(item.socket_type, item.in_out, tree.bl_idname)
        for prop in item.bl_rna.properties:
            if prop.is_readonly or prop.identifier in IFACE_FIXED:
                continue
            value = getattr(item, prop.identifier, None)
            if _datablock(value):
                entry[prop.identifier] = {"datablock": value.name, "kind": type(value).__name__}
                continue
            value = _plain(value)
            if value != fresh.get(prop.identifier):
                entry[prop.identifier] = value
        if parent:
            entry["in_panel"] = parent
        items.append(entry)
    return items


# ---- writing ---------------------------------------------------------------------------

def write(data, tree=None, name=None, replace=True, warnings=None):
    """Build a node tree from plain data. Returns the tree.

    Pass a list as `warnings` to collect things that were built but look wrong —
    a field going into a socket that takes a single value, say.
    """
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise BuildError("a tree description needs at least a 'nodes' list")
    warnings = warnings if warnings is not None else []

    # groups it uses come first; one that clashes with a different group of the same
    # name is written under a new name rather than over the other one
    renames = {}
    for inner in data.get("groups") or []:
        _write_dependency(inner, warnings, renames)
    if renames:
        data = _renamed(data, renames)

    name = name or data.get("name") or "Geometry Nodes"
    kind = data.get("kind") or "GeometryNodeTree"
    if tree is None:
        tree = bpy.data.node_groups.get(name)
    if tree is None:
        tree = bpy.data.node_groups.new(name, kind)
        tree.use_fake_user = True
        try:
            _build(data, tree, warnings)
        except Exception:
            bpy.data.node_groups.remove(tree)
            raise
        return tree
    if not replace:
        _build(data, tree, warnings)
        return tree

    # Replacing a tree people already use: build the description somewhere else first,
    # so a mistake in it leaves their tree exactly as it was rather than emptied.
    trial = bpy.data.node_groups.new("__codenodes_trial__", tree.bl_idname)
    try:
        _build(data, trial, [])
    finally:
        bpy.data.node_groups.remove(trial)
    users = _snapshot_users(tree)
    tree.nodes.clear()
    tree.interface.clear()
    tree.use_fake_user = True
    _build(data, tree, warnings)
    _restore_users(tree, users, warnings)
    return tree


def _renamed(data, renames):
    data = dict(data)
    nodes = []
    for entry in data["nodes"]:
        if entry.get("group") in renames:
            entry = dict(entry, group=renames[entry["group"]])
        nodes.append(entry)
    data["nodes"] = nodes
    return data


def _build(data, tree, warnings):
    """Everything that turns a description into the nodes of an empty tree."""
    names = [e.get("name") for e in data["nodes"]]
    dup = next((n for n in names if n and names.count(n) > 1), None)
    if dup:
        raise BuildError(f"two nodes are both called '{dup}'")

    # Rebuilding the interface hands out fresh socket identifiers; this maps what was
    # recorded onto what now exists.
    iface_remap = write_interface(tree, data.get("interface") or [], warnings)

    made = {}
    for entry in data["nodes"]:
        node = _make_node(tree, entry, warnings)
        made[entry["name"]] = node
    by_entry = [(e, made[e["name"]]) for e in data["nodes"]]

    # zones have to be paired before the input half has its sockets
    for entry, node in by_entry:
        partner = entry.get("pairs_with")
        if partner:
            if partner not in made:
                raise BuildError(f"zone '{entry['name']}' pairs with '{partner}', which is not "
                                 "in the description")
            try:
                node.pair_with_output(made[partner])
            except Exception as exc:
                raise BuildError(f"could not pair zone '{entry['name']}' with "
                                 f"'{partner}': {exc}") from None
    for entry, node in by_entry:
        _make_items(node, entry)
    for entry, node in by_entry:
        if entry.get("inside"):
            parent = made.get(entry["inside"])
            if parent is None:
                raise BuildError(f"node '{entry['name']}' is inside '{entry['inside']}', which "
                                 "is not in the description")
            node.parent = parent
    for entry, node in by_entry:
        _apply_settings(node, entry, warnings)

    remaps = {}
    for entry, node in by_entry:
        remaps[node.name] = _node_remap(node, entry, iface_remap)
    for entry, node in by_entry:
        _apply_values(node, entry, remaps[node.name], warnings)
    for link in data.get("links") or []:
        _make_link(tree, made, link, remaps)

    placed = [e for e, _ in by_entry if e.get("at") is not None]
    if len(placed) < len(by_entry):
        layout(tree, only=None if not placed else
               [n for e, n in by_entry if e.get("at") is None])

    warnings.extend(validate(tree))
    return tree


def _write_dependency(inner, warnings, renames):
    """Make sure a group the description depends on exists. An identical one already here
    is used as it is; a *different* group that happens to share the name is left alone
    and this one is written beside it under a new name."""
    inner = _renamed(inner, renames) if renames else inner
    wanted = inner.get("name") or "Group"
    existing = bpy.data.node_groups.get(wanted)
    if existing is None:
        return write(inner, name=wanted, warnings=warnings)
    if _same(read(existing, groups=False), inner):
        return existing
    n = 1
    while bpy.data.node_groups.get(f"{wanted}.{n:03d}") is not None:
        candidate = bpy.data.node_groups[f"{wanted}.{n:03d}"]
        if _same(read(candidate, groups=False), inner):
            renames[wanted] = candidate.name
            return candidate
        n += 1
    tree = write(inner, name=f"{wanted}.{n:03d}", warnings=warnings)
    renames[wanted] = tree.name
    return tree


def _same(a, b):
    """Whether two descriptions build the same tree: nodes, settings, values, interface
    and every link — ignoring positions and the identifiers a rebuild renumbers."""
    if len(a.get("nodes", [])) != len(b.get("nodes", [])):
        return False

    def nodes(d):
        return sorted(({k: v for k, v in n.items() if k not in ("at", "sockets", "values")}
                       for n in d["nodes"]), key=lambda n: n["name"])

    def iface(d):
        return [{k: v for k, v in i.items() if k != "identifier"} for i in d.get("interface", [])]

    def links(d):
        # group sockets are renumbered on a rebuild, so name them by interface position
        order = {i.get("identifier"): n for n, i in enumerate(d.get("interface", []))}
        kinds = {n["name"]: n["type"] for n in d["nodes"]}

        def end(node, socket):
            if kinds.get(node) in ('NodeGroupInput', 'NodeGroupOutput'):
                return (node, order.get(socket, socket))
            return (node, socket)
        return sorted((end(*l["from"]), end(*l["to"])) for l in d.get("links", []))

    def values(d):
        return sorted((n["name"], sorted(n.get("values", {}).items(), key=str))
                      for n in d["nodes"] if n["type"] not in ('NodeGroupInput', 'NodeGroupOutput'))
    try:
        return (nodes(a) == nodes(b) and iface(a) == iface(b) and links(a) == links(b)
                and str(values(a)) == str(values(b)))
    except (KeyError, TypeError, ValueError):
        return False


def _make_node(tree, entry, warnings):
    kind = entry.get("type")
    if not kind:
        raise BuildError(f"node '{entry.get('name', '?')}' has no type")
    if not entry.get("name"):
        raise BuildError(f"a {kind} node has no name; every node needs one so links can "
                         "refer to it")
    try:
        node = tree.nodes.new(kind)
    except Exception as exc:
        raise BuildError(f"'{kind}' is not a node type that can be added here ({exc}). "
                         "nodes_find looks up the right name") from None
    node.name = entry["name"]
    if entry.get("label"):
        node.label = entry["label"]
    if entry.get("at") is not None:
        node.location = entry["at"]
    if entry.get("muted"):
        node.mute = True
    if entry.get("collapsed"):
        node.hide = True
    if entry.get("color"):
        node.use_custom_color = True
        node.color = entry["color"][:3]
    if entry.get("size") and kind == 'NodeFrame':
        node.width, node.height = entry["size"]
    if entry.get("group"):
        inner = bpy.data.node_groups.get(entry["group"])
        if inner is None:
            raise BuildError(f"node '{entry['name']}' wants the group '{entry['group']}', "
                             "which is not in this file")
        node.node_tree = inner
    return node


def _make_items(node, entry):
    for coll_name, records in (entry.get("items") or {}).items():
        coll = getattr(node, coll_name, None)
        if coll is None or not hasattr(coll, "new"):
            raise BuildError(f"node '{node.name}' ({node.bl_idname}) has no '{coll_name}' to "
                             "add items to")
        coll.clear()
        for record in records:
            record = record or {}
            label = record.get("name", "Item")
            try:
                if coll_name == "enum_items":
                    item = coll.new(label)
                elif coll_name == "index_switch_items":
                    item = coll.new()
                else:
                    item = coll.new(record.get("socket_type", 'FLOAT'), label)
            except Exception as exc:
                raise BuildError(f"node '{node.name}': could not add {coll_name} item "
                                 f"{record!r} — {exc}") from None
            for key, value in record.items():
                try:
                    if getattr(item, key, None) != value:
                        setattr(item, key, value)
                except Exception as exc:
                    raise BuildError(f"node '{node.name}': {coll_name} item '{label}' will not "
                                     f"take {key} = {value!r} — {exc}") from None


def _resolve(value):
    """A recorded datablock reference, found again by name (and kind, when known)."""
    if isinstance(value, dict) and "datablock" in value:
        kind = value.get("kind")
        places = [_DATA[kind]] if kind in _DATA else list(dict.fromkeys(_DATA.values()))
        for place in places:
            found = getattr(bpy.data, place).get(value["datablock"])
            if found is not None:
                return found
        return None
    return value


def _apply_settings(node, entry, warnings):
    """Settings can depend on each other (Compare's mode only exists for vectors), so
    anything refused is retried after the rest have been set."""
    pending = []
    for key, value in (entry.get("settings") or {}).items():
        if _skip_setting(key):
            continue
        resolved = _resolve(value)
        if resolved is None and isinstance(value, dict):
            warnings.append(f"node '{node.name}': {key} wants '{value.get('datablock')}', which "
                            "is not in this file, so it was left empty")
            continue
        pending.append((key, value, resolved))
    for _ in range(3):
        failed = []
        for key, value, resolved in pending:
            try:
                if getattr(node, key) != resolved:
                    setattr(node, key, resolved)
            except Exception as exc:
                failed.append((key, value, resolved, exc))
        if not failed:
            return
        if len(failed) == len(pending):
            break
        pending = [(k, v, r) for k, v, r, _ in failed]
    key, value, _, exc = failed[0]
    hint = ""
    prop = node.bl_rna.properties.get(key)
    if prop is None:
        hint = (" It has no setting of that name; its settings are: "
                + ", ".join(p.identifier for p in node.bl_rna.properties
                            if not p.is_readonly and not _skip_setting(p.identifier)))
    elif prop.type == 'ENUM':
        hint = " It accepts: " + ", ".join(i.identifier for i in prop.enum_items)
    raise BuildError(f"node '{node.name}' ({node.bl_idname}): cannot set {key} = {value!r} — "
                     f"{exc}.{hint}") from None


def _node_remap(node, entry, iface_remap):
    """Recorded socket identifier -> the one this rebuilt node actually has."""
    remap = {}
    if node.bl_idname in ('NodeGroupInput', 'NodeGroupOutput'):
        remap.update(iface_remap)
    recorded = entry.get("sockets")
    if recorded:
        for side, sockets in (("in", node.inputs), ("out", node.outputs)):
            old = recorded.get(side) or []
            new = [s.identifier for s in sockets]
            if len(old) == len(new):
                for a, b in zip(old, new):
                    remap.setdefault((side, a), b)
    return remap


_TYPED_SUFFIX = re.compile(r"_(\d{3}|INT|FLOAT|VEC3|VEC2|COL|STR|BOOL|ROT|MATRIX)$")


def find_socket(node, key, side, remap=None, used=None, exact=False):
    """A socket by identifier (as read) or by plain name (as a person writes it).

    A socket in use wins over a hidden one: Random Value's hidden vector output has
    the identifier "Value", but someone writing "Value" means the one they can see.
    When several enabled inputs share a name — a Math node's two "Value" inputs — the
    first one not already connected is taken. `exact` matches the identifier only.
    """
    sockets = node.inputs if side == "in" else node.outputs
    if remap:
        key_mapped = remap.get((side, key), remap.get(key, key))
    else:
        key_mapped = key
    by_id = next((s for s in sockets if s.identifier == key_mapped), None)
    if by_id is None and key_mapped != key:
        by_id = next((s for s in sockets if s.identifier == key), None)
    if exact or (by_id is not None and by_id.enabled):
        return by_id
    named = [s for s in sockets if s.enabled and s.name == key]
    if not named and by_id is not None:
        return by_id
    if not named:
        named = [s for s in sockets if s.enabled and s.name.lower() == str(key).lower()]
    if len(named) > 1 and side == "in":
        free = [s for s in named if not s.is_linked and (used is None or s not in used)]
        if free:
            return free[0]
    if not named:
        # Blender 5.2 gives typed nodes (Compare, Random Value, Switch...) only the current type's
        # sockets, named without the type: an older file's "B_INT" or "Value_001" is "B" or "Value"
        base = _TYPED_SUFFIX.sub("", str(key_mapped))
        if base != key_mapped:
            named = [s for s in sockets if s.enabled and s.identifier == base]
    return named[0] if named else None


def _socket_list(node, side):
    sockets = node.inputs if side == "in" else node.outputs
    shown = []
    for s in sockets:
        if not s.enabled or s.bl_idname == 'NodeSocketVirtual':
            continue
        shown.append(s.name if s.name == s.identifier else f"{s.name} ({s.identifier})")
    return ", ".join(shown) or "nothing"


def _apply_values(node, entry, remap, warnings):
    used = set()
    for key, value in (entry.get("values") or {}).items():
        socket = find_socket(node, key, "in", remap, used)
        if socket is None:
            raise BuildError(f"node '{node.name}' ({node.bl_idname}) has no input '{key}'. It "
                             f"has: {_socket_list(node, 'in')}")
        used.add(socket)
        if not hasattr(socket, "default_value"):
            warnings.append(f"node '{node.name}': input '{socket.name}' has no value to set; "
                            "it has to be connected")
            continue
        resolved = _resolve(value)
        if resolved is None and isinstance(value, dict):
            warnings.append(f"node '{node.name}': input '{socket.name}' wants "
                            f"'{value.get('datablock')}', which is not in this file")
            continue
        try:
            socket.default_value = resolved
        except Exception as exc:
            raise BuildError(f"node '{node.name}': input '{socket.name}' will not take "
                             f"{value!r} — {exc}") from None


def _make_link(tree, made, link, remaps):
    try:
        (from_name, from_id), (to_name, to_id) = link["from"], link["to"]
    except Exception:
        raise BuildError("a link should look like "
                         f"{{'from': [node, socket], 'to': [node, socket]}}, got {link!r}") from None
    a, b = made.get(from_name), made.get(to_name)
    if a is None or b is None:
        missing = from_name if a is None else to_name
        raise BuildError(f"link from '{from_name}' to '{to_name}': the node '{missing}' does "
                         f"not exist. Nodes: {', '.join(made) or 'none'}")
    exact = bool(link.get("hidden"))
    out = find_socket(a, from_id, "out", remaps.get(a.name), exact=exact)
    into = find_socket(b, to_id, "in", remaps.get(b.name), exact=exact)
    if out is None:
        raise BuildError(f"'{from_name}' ({a.bl_idname}) has no output '{from_id}'. It has: "
                         + _socket_list(a, "out"))
    if into is None:
        raise BuildError(f"'{to_name}' ({b.bl_idname}) has no input '{to_id}'. It has: "
                         + _socket_list(b, "in"))
    made_link = tree.links.new(out, into)
    if link.get("muted"):
        made_link.is_muted = True
    return made_link


def write_interface(tree, items, warnings=None):
    """Rebuild a group's inputs and outputs. Returns {recorded identifier: new one}."""
    warnings = warnings if warnings is not None else []
    panels, remap = {}, {}
    for item in items:
        parent = panels.get(item.get("in_panel"))
        if item.get("in_panel") and parent is None:
            raise BuildError(f"'{item.get('socket') or item.get('panel')}' is in panel "
                             f"'{item['in_panel']}', which comes later or does not exist")
        if "panel" in item:
            panel = tree.interface.new_panel(item["panel"], description=item.get("about") or "",
                                             default_closed=bool(item.get("closed")))
            if parent is not None:
                tree.interface.move_to_parent(panel, parent, len(parent.interface_items))
            panels[item["panel"]] = panel
            continue
        if "socket" not in item or "type" not in item:
            raise BuildError(f"an interface entry needs 'socket' (its name) and 'type' "
                             f"(e.g. NodeSocketFloat), got {item!r}")
        try:
            socket = tree.interface.new_socket(item["socket"], in_out=item.get("in_out", "INPUT"),
                                               socket_type=item["type"], parent=parent)
        except Exception as exc:
            raise BuildError(f"could not add the {item.get('in_out', 'INPUT').lower()} "
                             f"'{item['socket']}' of type {item['type']} — {exc}") from None
        if item.get("identifier"):
            remap[item["identifier"]] = socket.identifier
        keys = [k for k in IFACE_ORDER if k in item]
        keys += [k for k in item if k not in keys and k not in IFACE_FIXED
                 and k not in ("socket", "type", "in_panel")]
        for attr in keys:
            if not hasattr(socket, attr):
                continue
            value = _resolve(item[attr])
            try:
                setattr(socket, attr, value)
            except Exception as exc:
                warnings.append(f"group socket '{item['socket']}': could not set {attr} = "
                                f"{item[attr]!r} ({exc})")
    return remap


# ---- keeping what the user tuned -------------------------------------------------------

def _copy_idprop(value):
    if hasattr(value, "to_list"):
        return value.to_list()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _keyed(things):
    """(key, thing) with the key a name plus which occurrence of that name it is, so two
    inputs both called "Material" (one per panel, say) keep their own values."""
    seen, out = {}, []
    for thing in things:
        n = seen.get(thing.name, 0)
        seen[thing.name] = n + 1
        out.append((f"{thing.name}#{n}", thing))
    return out


def _group_inputs(tree):
    return [i for i in tree.interface.items_tree
            if i.item_type == 'SOCKET' and i.in_out == 'INPUT']


def _snapshot_users(tree):
    """Before a rebuild: the modifier values set on this group, and how other groups had
    it wired, keyed by socket name and occurrence since identifiers are about to change."""
    mods = []
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type != 'NODES' or mod.node_group != tree:
                continue
            values = {}
            inputs = mod_inputs.of(mod)
            for key, item in _keyed(_group_inputs(tree)):
                record = {}
                for suffix in ("", "_use_attribute", "_attribute_name"):
                    if item.identifier + suffix in inputs.keys():
                        record[suffix] = _copy_idprop(inputs[item.identifier + suffix])
                if record:
                    values[key] = record
            mods.append((obj.name, mod.name, values))
    parents = []
    for other in bpy.data.node_groups:
        if other == tree:
            continue
        for node in other.nodes:
            if getattr(node, "node_tree", None) != tree:
                continue
            in_keys = {s.as_pointer(): k for k, s in _keyed(node.inputs)}
            out_keys = {s.as_pointer(): k for k, s in _keyed(node.outputs)}
            ins = [(l.from_node.name, l.from_socket.identifier, in_keys[l.to_socket.as_pointer()])
                   for l in other.links if l.to_node == node]
            outs = [(out_keys[l.from_socket.as_pointer()], l.to_node.name, l.to_socket.identifier)
                    for l in other.links if l.from_node == node]
            values = {k: _socket_value(s) for k, s in _keyed(node.inputs)
                      if not s.is_linked and hasattr(s, "default_value")}
            parents.append((other.name, node.name, ins, outs, values))
    return mods, parents


def _restore_users(tree, users, warnings):
    mods, parents = users
    by_key = {k: i.identifier for k, i in _keyed(_group_inputs(tree))}
    for obj_name, mod_name, values in mods:
        obj = bpy.data.objects.get(obj_name)
        mod = obj.modifiers.get(mod_name) if obj else None
        if mod is None:
            continue
        for key, record in values.items():
            ident = by_key.get(key)
            if ident is None:
                continue
            for suffix, value in record.items():
                try:
                    mod_inputs.of(mod)[ident + suffix] = value
                except Exception:
                    pass
        obj.update_tag()
    for other_name, node_name, ins, outs, values in parents:
        other = bpy.data.node_groups.get(other_name)
        node = other.nodes.get(node_name) if other else None
        if node is None:
            continue
        inputs, outputs = dict(_keyed(node.inputs)), dict(_keyed(node.outputs))
        for from_node, from_id, key in ins:
            src = other.nodes.get(from_node)
            a = next((s for s in src.outputs if s.identifier == from_id), None) if src else None
            b = inputs.get(key)
            if a and b:
                other.links.new(a, b)
        for key, to_node, to_id in outs:
            dst = other.nodes.get(to_node)
            a = outputs.get(key)
            b = next((s for s in dst.inputs if s.identifier == to_id), None) if dst else None
            if a and b:
                other.links.new(a, b)
        for key, value in values.items():
            socket = inputs.get(key)
            if socket is not None and not socket.is_linked and value is not None:
                try:
                    socket.default_value = _resolve(value)
                except Exception:
                    pass


# ---- layout and checking ---------------------------------------------------------------

def _height(node):
    rows = sum(1 for s in node.outputs if s.enabled) + sum(1 for s in node.inputs if s.enabled)
    return 60 + 22 * rows


def layout(tree, only=None):
    """Arrange nodes left to right by how far they are from the start of the flow.

    With `only`, just those nodes are placed, each beside whatever feeds it, so a
    layout someone made by hand is left alone.
    """
    if only:
        for node in only:
            feeders = [l.from_node for l in tree.links if l.to_node == node]
            fed = [l.to_node for l in tree.links if l.from_node == node]
            if feeders:
                node.location = (max(f.location.x for f in feeders) + 260,
                                 sum(f.location.y for f in feeders) / len(feeders))
            elif fed:
                node.location = (min(f.location.x for f in fed) - 260,
                                 sum(f.location.y for f in fed) / len(fed))
        return
    nodes = [n for n in tree.nodes if n.bl_idname != 'NodeFrame']
    depth = {n.name: 0 for n in nodes}
    for _ in range(len(nodes)):
        changed = False
        for link in tree.links:
            a, b = link.from_node.name, link.to_node.name
            if a in depth and b in depth and depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
                changed = True
        if not changed:
            break
    # anything that feeds nothing further along sits just left of what it feeds
    columns = {}
    for node in nodes:
        columns.setdefault(depth[node.name], []).append(node)
    for col, members in columns.items():
        total = sum(_height(n) + 30 for n in members)
        y = total / 2
        for node in members:
            node.location = (col * 260, y)
            y -= _height(node) + 30


_CONVERTIBLE = {'VALUE', 'INT', 'BOOLEAN', 'VECTOR', 'RGBA', 'ROTATION'}


def validate(tree):
    """What looks wrong in a tree, each said in terms of the node it is on."""
    problems = []
    for link in tree.links:
        a, b = link.from_socket, link.to_socket
        where = f"'{link.from_node.name}' → '{link.to_node.name}'"
        if not b.enabled or not a.enabled:
            problems.append(f"{where}: connected to '{(b if not b.enabled else a).name}', which "
                            "the node does not use with its current settings")
            continue
        if not link.is_valid:
            if a.type == b.type or {a.type, b.type} <= _CONVERTIBLE:
                problems.append(f"{where}: '{a.name}' is a field (it varies per element) but "
                                f"'{b.name}' on '{link.to_node.name}' takes a single value")
            else:
                problems.append(f"{where}: a {a.type.lower()} output ('{a.name}') cannot go "
                                f"into a {b.type.lower()} input ('{b.name}')")
    if tree.bl_idname == 'GeometryNodeTree':
        outs = [n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput']
        geo_out = [i for i in tree.interface.items_tree if i.item_type == 'SOCKET'
                   and i.in_out == 'OUTPUT' and i.socket_type == 'NodeSocketGeometry']
        if geo_out and not outs:
            problems.append("there is no Group Output node, so nothing comes out")
        elif geo_out and not any(s.is_linked for n in outs for s in n.inputs
                                 if s.type == 'GEOMETRY'):
            problems.append("the Group Output's geometry is not connected, so nothing comes out")
    return problems


def unused(tree):
    """Nodes that do not affect what comes out — worth knowing when reading a setup."""
    reach = set()
    frontier = [n for n in tree.nodes if n.bl_idname in ('NodeGroupOutput', 'GeometryNodeViewer')]
    while frontier:
        node = frontier.pop()
        if node.name in reach:
            continue
        reach.add(node.name)
        for link in tree.links:
            if link.to_node == node:
                frontier.append(link.from_node)
        paired = getattr(node, "paired_output", None)
        if paired is not None:
            frontier.append(paired)
        # the input half of a zone is reached through its output
        for other in tree.nodes:
            if getattr(other, "paired_output", None) == node:
                frontier.append(other)
    return [n.name for n in tree.nodes
            if n.name not in reach and n.bl_idname not in ('NodeFrame', 'NodeGroupInput')]
