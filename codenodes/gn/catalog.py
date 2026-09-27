"""What nodes exist, and what each one takes — generated from Blender itself.

Nobody should hand-maintain a list of 320 node types. Blender already knows every
node's sockets, their types, whether each one accepts a field or only a single value,
and what its dropdown settings can be set to. This reads that out.

The field-versus-value distinction is the one people and models get wrong most often
in Geometry Nodes, and it turns out to be visible in the API: a socket drawn as a
diamond accepts a field, a line socket does not.
"""

from __future__ import annotations

import bpy

_cache = {}

# Properties every node has; not worth reporting.
_BASE_PROPS = set()

SHAPE_FIELD = {'DIAMOND', 'DIAMOND_DOT'}


def _base_props():
    global _BASE_PROPS
    if not _BASE_PROPS:
        _BASE_PROPS = {p.identifier for p in bpy.types.GeometryNode.bl_rna.properties}
    return _BASE_PROPS


def _scratch_tree():
    tree = bpy.data.node_groups.get("__codenodes_scratch__")
    if tree is None:
        tree = bpy.data.node_groups.new("__codenodes_scratch__", "GeometryNodeTree")
        tree.use_fake_user = False
    return tree


def _socket_info(socket):
    info = {"name": socket.name, "identifier": socket.identifier, "type": socket.bl_idname,
            "field": socket.display_shape in SHAPE_FIELD}
    if getattr(socket, "is_multi_input", False):
        info["multi"] = True
    value = getattr(socket, "default_value", None)
    if value is not None and not hasattr(value, "id_data"):
        try:
            info["default"] = list(value) if hasattr(value, "__len__") and not isinstance(value, str) else value
        except Exception:
            pass
    return info


def describe(idname, tree=None):
    """Everything about one node type: sockets, settings, and what the settings accept."""
    if idname in _cache:
        return _cache[idname]
    tree = tree or _scratch_tree()
    try:
        node = tree.nodes.new(idname)
    except Exception as exc:
        return {"name": idname, "error": f"not a node type here ({exc})"}
    try:
        settings = {}
        for prop in node.bl_rna.properties:
            if prop.is_readonly or prop.identifier in _base_props():
                continue
            entry = {"type": prop.type.lower()}
            if prop.type == 'ENUM':
                entry["options"] = [i.identifier for i in prop.enum_items]
            entry["default"] = _plain(getattr(node, prop.identifier, None))
            if prop.description:
                entry["about"] = prop.description
            settings[prop.identifier] = entry
        info = {
            "name": idname,
            "label": getattr(type(node), "bl_label", idname),
            "inputs": [_socket_info(s) for s in node.inputs],
            "outputs": [_socket_info(s) for s in node.outputs],
            "settings": settings,
        }
        doc = (type(node).__doc__ or "").strip()
        if doc:
            info["about"] = doc.split("\n")[0]
        if node.bl_idname.endswith(("SimulationInput", "RepeatInput", "ForeachGeometryElementInput")):
            info["zone"] = "input half of a zone; pair it with its output"
        _cache[idname] = info
        return info
    finally:
        tree.nodes.remove(node)


def _plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "__len__"):
        try:
            return [_plain(v) for v in value]
        except Exception:
            return str(value)
    return str(value)


def node_types(prefixes=("GeometryNode", "FunctionNode", "ShaderNode")):
    """Every node type that can actually be created in a geometry node tree."""
    key = ("__types__", prefixes)
    if key in _cache:
        return _cache[key]
    tree = _scratch_tree()
    found = []
    for name in dir(bpy.types):
        if not name.startswith(prefixes):
            continue
        try:
            node = tree.nodes.new(name)
        except Exception:
            continue
        found.append(name)
        tree.nodes.remove(node)
    found.sort()
    _cache[key] = found
    return found


def _words(text):
    out, word = [], []
    for ch in text:
        if ch.isupper() and word:
            out.append("".join(word))
            word = [ch]
        else:
            word.append(ch)
    out.append("".join(word))
    return " ".join(out)


def search(query="", limit=40, detail=False):
    """Find node types by name. `Distribute`, `curve`, `noise`, `boolean`…"""
    words = [w.lower() for w in str(query).split()]
    hits = []
    for idname in node_types():
        readable = _words(idname).lower()
        if all(w in readable for w in words):
            hits.append(idname)
    hits = hits[:int(limit)]
    if not detail:
        return {"ok": True, "query": query, "found": len(hits),
                "nodes": [{"name": n, "label": _words(n.replace("GeometryNode", "")
                                                     .replace("FunctionNode", "")
                                                     .replace("ShaderNode", ""))} for n in hits]}
    return {"ok": True, "query": query, "found": len(hits), "nodes": [describe(n) for n in hits]}


def summary():
    """How big the surface is, and where the stateful bits are."""
    types = node_types()
    zones = [t for t in types if any(k in t for k in
                                     ("Simulation", "Repeat", "Foreach", "Bundle", "Closure"))]
    return {"ok": True, "total": len(types),
            "geometry": sum(1 for t in types if t.startswith("GeometryNode")),
            "function": sum(1 for t in types if t.startswith("FunctionNode")),
            "shader": sum(1 for t in types if t.startswith("ShaderNode")),
            "zones": zones,
            "note": ("a socket with \"field\": true takes a field (a value that varies per "
                     "element); one without takes a single value")}


def clear():
    _cache.clear()
    tree = bpy.data.node_groups.get("__codenodes_scratch__")
    if tree is not None:
        bpy.data.node_groups.remove(tree)
