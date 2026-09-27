"""A node tree described in plain words, following the flow from left to right.

Reading raw node data works, but it is not how anyone understands a setup. This says
what goes in, what each node does to it — only the settings and values that differ
from a fresh node, and where each connected input comes from — and what comes out.
"""

from __future__ import annotations

from . import catalog, serialize


def _fmt(value):
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, list):
        if all(isinstance(v, (int, float)) for v in value):
            return "(" + ", ".join(_fmt(float(v)) for v in value) + ")"
        return str(value)
    if isinstance(value, dict) and "datablock" in value:
        return f"{value.get('kind', '')} '{value['datablock']}'"
    return str(value)


def _differs(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) > 1e-6
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return any(_differs(x, y) for x, y in zip(a, b))
    return a != b


def _order(tree):
    nodes = [n for n in tree.nodes if n.bl_idname != 'NodeFrame']
    depth = {n.name: 0 for n in nodes}
    for _ in range(len(nodes)):
        changed = False
        for link in tree.links:
            a, b = link.from_node.name, link.to_node.name
            if a in depth and b in depth and depth[b] < depth[a] + 1:
                depth[b], changed = depth[a] + 1, True
        if not changed:
            break
    return sorted(nodes, key=lambda n: (depth[n.name], -n.location.y))


def _source(link):
    node, socket = link.from_node, link.from_socket
    while node.bl_idname == 'NodeReroute':
        feeding = [l for l in node.id_data.links if l.to_node == node]
        if not feeding:
            break
        node, socket = feeding[0].from_node, feeding[0].from_socket
    if node.bl_idname == 'NodeGroupInput':
        return f'the group input "{socket.name}"'
    return f"{node.name}.{socket.name}"


def _iface_line(item):
    kind = item.socket_type.replace("NodeSocket", "").lower()
    bits = [kind]
    if hasattr(item, "default_value") and item.socket_type not in ("NodeSocketGeometry",):
        try:
            value = item.default_value
            if value is not None and not serialize._datablock(value):
                bits.append(f"default {_fmt(catalog._plain(value))}")
        except Exception:
            pass
    lo, hi = getattr(item, "min_value", None), getattr(item, "max_value", None)
    if lo is not None and hi is not None and abs(float(hi)) < 1e30:
        bits.append(f"{_fmt(float(lo))}–{_fmt(float(hi))}")
    text = f"{item.name} ({', '.join(bits)})"
    if item.description:
        text += f" — {item.description}"
    if item.parent and getattr(item.parent, "name", ""):
        text += f" [panel {item.parent.name}]"
    return text


def explain(tree):
    """{"text": ..., "problems": [...], "unused": [...]} for one tree."""
    lines = [f'"{tree.name}": {len(tree.nodes)} nodes, {len(tree.links)} links']
    ins = [i for i in tree.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT']
    outs = [i for i in tree.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'OUTPUT']
    lines.append("Takes: " + ("; ".join(_iface_line(i) for i in ins) or "nothing"))
    lines.append("Gives: " + ("; ".join(_iface_line(i) for i in outs) or "nothing"))
    lines.append("")

    step = 0
    for node in _order(tree):
        if node.bl_idname in ('NodeGroupInput', 'NodeReroute'):
            continue
        step += 1
        spec = catalog.describe(node.bl_idname)
        title = spec.get("label", node.bl_idname) if "error" not in spec else node.bl_idname
        if getattr(node, "node_tree", None) is not None:
            title = f'the group "{node.node_tree.name}"'
        head = f"{node.name}" + (f" ({node.label})" if node.label else "") + f" — {title}"
        extras = []
        defaults = spec.get("settings", {})
        for key, value in serialize._settings(node).items():
            if key in defaults and _differs(value, defaults[key].get("default")):
                extras.append(f"{key} {_fmt(value)}")
        if node.mute:
            extras.append("MUTED")
        if node.parent:
            extras.append(f'in frame "{node.parent.label or node.parent.name}"')
        paired = getattr(node, "paired_output", None)
        if paired is not None:
            extras.append(f"zone ends at {paired.name}")
        for coll in serialize._item_collections(node):
            names = [getattr(i, "name", "") or str(n) for n, i in enumerate(getattr(node, coll))]
            if names:
                extras.append(f"{coll.replace('_', ' ')}: {', '.join(names)}")
        if extras:
            head += " [" + "; ".join(extras) + "]"

        parts = []
        default_inputs = {i["identifier"]: i.get("default") for i in spec.get("inputs", [])}
        for socket in node.inputs:
            if not socket.enabled or socket.bl_idname == 'NodeSocketVirtual':
                continue
            linked = [l for l in tree.links if l.to_socket == socket]
            if linked:
                parts.append(f"{socket.name} ← " + " + ".join(_source(l) for l in linked))
                continue
            value = serialize._socket_value(socket)
            if value is None:
                continue
            default = default_inputs.get(socket.identifier)
            if default is None or _differs(value, default):
                parts.append(f"{socket.name} = {_fmt(value)}")
        line = f"{step}. {head}"
        if parts:
            line += ": " + "; ".join(parts)
        lines.append(line)

    problems = serialize.validate(tree)
    unused = serialize.unused(tree)
    if unused:
        lines.append("")
        lines.append("Not affecting the result: " + ", ".join(unused))
    if problems:
        lines.append("")
        lines.append("Problems:")
        lines += [f"- {p}" for p in problems]
    return {"text": "\n".join(lines), "problems": problems, "unused": unused}
