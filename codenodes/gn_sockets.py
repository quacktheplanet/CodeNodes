"""Everything about a code node that lives on the node itself.

A code node is a Group node, and Blender lets a group show: input sockets (numbers, toggles,
colours, objects, and Menu sockets, which show as dropdowns), collapsible panels of sockets, a
label in its header, and frames inside the group, which can display a Text block. So:

  * settings are input sockets: the template is a Menu, the particle count an integer, colours
    are colour sockets, toggles are booleans, grouped into closed panels (Look, Simulation, Bounds)
  * the code's own `@param` sliders are plain inputs
  * the header label shows the status: live / real, counts, cost, or the error and its line
  * Tab into the group and a frame shows the code, another the full status or error

To Geometry nodes get the same treatment: When is a Menu, the limits and attribute toggles are sockets.
"""

from __future__ import annotations

import bpy

PANELS = ("Look", "Simulation", "Bounds", "Output")
CUSTOM = "Custom"             # the Template dropdown's entry for code that didn't come from a template
EDIT = "✎ Edit Code"          # a toggle that works as a button: on -> the code pops up, and it switches off

COLOR_BY = [("Code", 'CODE'), ("Speed", 'SPEED'), ("Age", 'AGE')]
WHEN = [("Automatic", 'AUTO'), ("Every Frame", 'EVERY_FRAME'), ("When Changed", 'ON_CHANGE'),
        ("Only for Render", 'RENDER_ONLY')]

# (panel or None, socket name, socket type, settings property, min, max) per kind, in order.
SETTINGS = {
    'PARTICLES': [
        (None, "Count", "NodeSocketInt", "count", 1, 16_777_216),
        (None, "Emit From", "NodeSocketObject", "emitter", None, None),
        ("Look", "Colour By", "NodeSocketMenu", "color_by", None, None),
        ("Look", "Slow / Young", "NodeSocketColor", "color_a", None, None),
        ("Look", "Fast / Old", "NodeSocketColor", "color_b", None, None),
        ("Look", "Glow", "NodeSocketBool", "blend", None, None),
        ("Look", "Point Size", "NodeSocketFloat", "point_px", 0.5, 32.0),
        ("Look", "Brightness", "NodeSocketFloat", "gain", 0.0, 8.0),
        ("Look", "Speed Range", "NodeSocketFloat", "speed_range", 0.0001, 50.0),
        ("Simulation", "Substeps", "NodeSocketInt", "substeps", 1, 20),
        ("Simulation", "Pre-warm", "NodeSocketFloat", "prewarm", 0.0, 120.0),
        ("Simulation", "Stagger", "NodeSocketFloat", "stagger", 0.0, 1.0),
    ],
    'MESH': [
        ("Look", "Colour", "NodeSocketColor", "surface_color", None, None),
        ("Look", "Shadows", "NodeSocketBool", "shadows", None, None),
        ("Look", "Ambient Occlusion", "NodeSocketBool", "ao", None, None),
        ("Look", "Fog", "NodeSocketFloat", "fog", 0.0, 1.0),
        ("Look", "Sky", "NodeSocketBool", "sky", None, None),
        ("Look", "Live Resolution", "NodeSocketFloat", "quality", 0.15, 1.0),
        ("Bounds", "Bounds Min", "NodeSocketVector", "bounds_min", None, None),
        ("Bounds", "Bounds Max", "NodeSocketVector", "bounds_max", None, None),
    ],
    'DEFORM': [],
    'SHAPE': [
        ("Look", "Smooth", "NodeSocketBool", "smooth", None, None),
    ],
}

MENU_ITEMS = {"Colour By": [n for n, _ in COLOR_BY], "When": [n for n, _ in WHEN]}


def templates_for(kind):
    from . import gn_link
    return list(gn_link.TEMPLATES.get(kind, {}).keys())


def setting_value(s, prop):
    """The settings value in socket form."""
    v = getattr(s, prop)
    if prop == "blend":
        return v == 'ADD'
    if prop == "color_by":
        return next(n for n, e in COLOR_BY if e == v)
    if prop in ("color_a", "color_b", "surface_color"):
        return (*v, 1.0)
    if prop in ("bounds_min", "bounds_max"):
        return tuple(v)
    return v


def socket_to_setting(prop, value):
    if prop == "blend":
        return 'ADD' if value else 'SOLID'
    if prop == "color_by":
        return dict(COLOR_BY).get(value)
    if prop in ("color_a", "color_b", "surface_color"):
        return tuple(value)[:3]
    if prop in ("bounds_min", "bounds_max"):
        return tuple(value)
    return value


_decl_cache: dict[str, object] = {}


def decls_of(obj):
    """The code's declarations (decl.Decls), or empty ones while the code has a mistake in it."""
    import hashlib
    from . import decl
    text = obj.codenodes.text
    code = text.as_string() if text is not None else ""
    key = hashlib.sha1(code.encode()).hexdigest()
    got = _decl_cache.get(key)
    if got is None:
        try:
            got = decl.parse(code)
        except Exception:
            got = decl.Decls()
            got.roles = decl.roles(code)
        if len(_decl_cache) > 256:
            _decl_cache.clear()
        _decl_cache[key] = got
    return got


STREAM_IN = {"Particles": "NodeSocketBundle", "Mesh": "NodeSocketGeometry"}


def stream_inputs(obj):
    s = obj.codenodes
    if s.kind == 'DEFORM':
        return [("Mesh", "NodeSocketGeometry")]
    if s.kind != 'STAGE':
        return []
    d = decls_of(obj)
    out = []
    if d.takes_particles():
        out.append(("Particles", "NodeSocketBundle"))
    if d.takes_mesh():
        out.append(("Mesh", "NodeSocketGeometry"))
    return out


SURFACE_LINKS = ("Lights", "Material")      # GPU Surface inputs for Scene Lights and Material Look


def param_sockets(obj):
    """[(name, socket type, default, min, max)] for the code's sliders: floats, whole numbers, colours."""
    out = []
    params = list(obj.codenodes.params)
    for p in params:
        if p.kind == 'HIDDEN':
            continue                        # filled in by the add-on (light data, material values)
        if p.kind == 'COLOR':
            if not p.name.endswith("_r"):
                continue
            base = p.name[:-2]
            g = next((q for q in params if q.name == base + "_g"), None)
            b = next((q for q in params if q.name == base + "_b"), None)
            rgb = (float(p.value), float(g.value) if g else 1.0, float(b.value) if b else 1.0, 1.0)
            out.append((base, "NodeSocketColor", rgb, None, None))
        elif p.kind == 'INT':
            out.append((p.name, "NodeSocketInt", int(round(p.value)), int(p.min), int(p.max)))
        else:
            out.append((p.name, "NodeSocketFloat", float(p.value), float(p.min), float(p.max)))
    return out


def spec(obj):
    """[(panel, name, socket type, default, min, max, items)] the node should show for this source."""
    s = obj.codenodes
    out = [(None, EDIT, "NodeSocketBool", False, None, None, None)]
    for name, stype in stream_inputs(obj):
        out.append((None, name, stype, None, None, None, None))
    names = templates_for(s.kind)
    if names:
        out.append((None, "Template", "NodeSocketMenu", s.template_key or CUSTOM, None, None, names + [CUSTOM]))
    for panel, name, stype, prop, lo, hi in SETTINGS.get(s.kind, []):
        if panel is None:
            out.append((panel, name, stype, setting_value(s, prop), lo, hi, MENU_ITEMS.get(name)))
    for name, stype, default, lo, hi in param_sockets(obj):
        out.append((None, name, stype, default, lo, hi, None))
    if s.kind in ('PARTICLES', 'STAGE', 'DEFORM', 'MESH'):
        d = decls_of(obj)
        for m in d.materials:
            out.append((None, m, "NodeSocketMaterial", None, None, None, None))
        for f in d.func_ins:
            out.append((None, f.name, "NodeSocketClosure", None, None, None, None))
    if s.kind == 'MESH':
        # a surface can be lit by the scene's lights and take a Blender material's values
        for name in SURFACE_LINKS:
            out.append((None, name, "NodeSocketClosure", None, None, None, None))
    for panel, name, stype, prop, lo, hi in SETTINGS.get(s.kind, []):
        if panel is not None:
            out.append((panel, name, stype, setting_value(s, prop), lo, hi, MENU_ITEMS.get(name)))
    return out


def output_spec(obj):
    """[(name, socket type)] the node gives: its stream, function outputs, and per-particle attributes."""
    s = obj.codenodes
    d = decls_of(obj)
    out = []
    if s.kind == 'PARTICLES':
        out.append(("Particles", "NodeSocketBundle"))
    elif s.kind == 'STAGE':
        if d.gives_particles():
            out.append(("Particles", "NodeSocketBundle"))
        if d.takes_mesh():
            out.append(("Mesh", "NodeSocketGeometry"))
    else:
        out.append(("Geometry", "NodeSocketGeometry"))
    for f in d.func_outs:
        out.append((f.name, "NodeSocketClosure"))
    if s.kind in ('PARTICLES', 'STAGE'):
        for a in d.attrs:
            out.append((a.name, "NodeSocketFloat"))
    return out


def _outputs(iface):
    return [i for i in iface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'OUTPUT']


def sync_outputs(group, obj):
    """Make the group's outputs match what the code gives. True if anything changed.

    Attribute outputs are fields: a Named Attribute node inside reads the value from the geometry it
    is used on, which after To Geometry carries every per-particle attribute."""
    wanted = output_spec(obj)
    have = [(i.name, i.socket_type) for i in _outputs(group.interface)]
    if have == wanted:
        _wire_attr_outputs(group, obj)
        return False
    # remember what each output fed, by name, in every tree using the group
    saved = []
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "GeometryNodeTree":
            continue
        for node in tree.nodes:
            if node.type == 'GROUP' and node.node_tree == group:
                saved.append((tree, node, [(l.from_socket.name, l.to_socket) for l in tree.links
                                           if l.from_node == node]))
    iface = group.interface
    for item in _outputs(iface):
        if (item.name, item.socket_type) not in wanted:
            iface.remove(item)
    for name, stype in wanted:
        if not any(i.name == name and i.socket_type == stype for i in _outputs(iface)):
            iface.new_socket(name, in_out="OUTPUT", socket_type=stype)
    for pos, (name, stype) in enumerate(wanted):
        item = next(i for i in _outputs(iface) if i.name == name and i.socket_type == stype)
        if item.position != pos:
            iface.move(item, pos)
    for tree, node, links in saved:
        for name, to in links:
            out = node.outputs.get(name)
            if out is None and name in ("Geometry", "Particles"):          # the stream was renamed
                out = next((o for o in node.outputs if o.name in ("Particles", "Mesh", "Geometry")), None)
            if out is not None:
                try:
                    tree.links.new(out, to)
                except RuntimeError:
                    pass
    _wire_attr_outputs(group, obj)
    _hook_menus(group)
    return True


def _wire_attr_outputs(group, obj):
    gout = next((n for n in group.nodes if n.type == 'GROUP_OUTPUT'), None)
    if gout is None:
        return
    for sock in gout.inputs:
        if sock.bl_idname != "NodeSocketFloat" or not sock.name:
            continue
        name = f"Attr · {sock.name}"
        na = group.nodes.get(name)
        if na is None:
            na = group.nodes.new("GeometryNodeInputNamedAttribute")
            na.name = na.label = name
            na.data_type = 'FLOAT'
            na.location = (gout.location.x - 260, gout.location.y - 80 - 60 * len(
                [n for n in group.nodes if n.name.startswith("Attr · ")]))
            na.hide = True
        if na.inputs["Name"].default_value != sock.name:
            na.inputs["Name"].default_value = sock.name
        if not any(l.to_socket == sock for l in group.links):
            group.links.new(na.outputs["Attribute"], sock)


def _inputs(iface):
    return [i for i in iface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT']


def _panel_of(item):
    parent = getattr(item, "parent", None)
    return parent.name if parent is not None and getattr(parent, "item_type", "") == 'PANEL' and parent.name else None


def signature(group):
    return [(_panel_of(i), i.name, i.socket_type) for i in _inputs(group.interface)]


def wanted_signature(obj):
    return [(w[0], w[1], w[2]) for w in spec(obj)]


def ensure_menu_switch(group, socket_name, items):
    """A Menu Switch inside the group, fed by the group input, that defines a Menu socket's items."""
    name = f"Menu · {socket_name}"
    ms = group.nodes.get(name)
    if ms is None:
        ms = group.nodes.new("GeometryNodeMenuSwitch")
        ms.name = ms.label = name
        ms.location = (-700, -120 * (len([n for n in group.nodes if n.type == 'MENU_SWITCH'])))
        ms.hide = True
    if [i.name for i in ms.enum_items] != list(items):
        ms.enum_items.clear()
        for it in items:
            ms.enum_items.new(it)
    gin = next((n for n in group.nodes if n.type == 'GROUP_INPUT'), None)
    if gin is not None:
        out = gin.outputs.get(socket_name)
        if out is not None and not any(l.to_node == ms and l.from_socket == out for l in group.links):
            group.links.new(out, ms.inputs["Menu"])


def sync_interface(group, obj):
    """Make the group's inputs and outputs match the code and the kind's settings. True if anything
    changed."""
    wanted = spec(obj)
    outs_changed = sync_outputs(group, obj)
    if signature(group) == [(w[0], w[1], w[2]) for w in wanted]:
        _sync_menus(group, wanted)
        return outs_changed
    saved = _save_users(group)
    iface = group.interface
    for item in _inputs(iface):
        iface.remove(item)
    for item in [i for i in iface.items_tree if i.item_type == 'PANEL']:
        iface.remove(item)
    panels = {}
    # root sockets first, then panels in a fixed order (Blender keeps sockets above panels)
    for panel, name, stype, default, lo, hi, items in wanted:
        if panel is not None:
            continue
        _new_socket(iface, name, stype, default, lo, hi, None)
    for pname in PANELS:
        rows = [w for w in wanted if w[0] == pname]
        if not rows:
            continue
        panels[pname] = iface.new_panel(pname, default_closed=True)
        for panel, name, stype, default, lo, hi, items in rows:
            _new_socket(iface, name, stype, default, lo, hi, panels[pname])
    _sync_menus(group, wanted)
    _restore_users(group, saved, {w[1]: w[3] for w in wanted})
    return True


def _save_users(group):
    """Links and typed values going into every node that uses `group`, by input name."""
    saved = []
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "GeometryNodeTree":
            continue
        for node in tree.nodes:
            if node.type != 'GROUP' or node.node_tree != group:
                continue
            links = [(l.from_socket, l.to_socket.name) for l in tree.links if l.to_node == node]
            values = {}
            for sock in node.inputs:
                if hasattr(sock, "default_value") and sock.bl_idname != "NodeSocketMenu":
                    try:
                        values[sock.name] = (sock.bl_idname, tuple(sock.default_value)
                                             if hasattr(sock.default_value, "__len__") and not isinstance(
                                                 sock.default_value, str) else sock.default_value)
                    except TypeError:
                        pass
                elif sock.bl_idname == "NodeSocketMenu":
                    values[sock.name] = (sock.bl_idname, sock.default_value)
            saved.append((tree, node, links, values))
    return saved


def _restore_users(group, saved, defaults=None):
    """Put back what went into each node; sockets that are new get the spec's default (Blender gives a
    socket added to a group in use a value of 0, not the interface default)."""
    defaults = defaults or {}
    for tree, node, links, values in saved:
        for sock in node.inputs:
            got = values.get(sock.name)
            if got is None or got[0] != sock.bl_idname:
                d = defaults.get(sock.name)
                if d is not None and hasattr(sock, "default_value") and sock.bl_idname not in (
                        "NodeSocketGeometry", "NodeSocketObject", "NodeSocketBundle", "NodeSocketClosure"):
                    try:
                        sock.default_value = d
                    except (TypeError, ValueError, AttributeError):
                        pass
                continue
            if not hasattr(sock, "default_value"):
                continue
            try:
                if got[1] not in (None, ""):
                    sock.default_value = got[1]
            except (TypeError, ValueError, AttributeError):
                pass
        for from_socket, name in links:
            to = node.inputs.get(name)
            if to is not None:
                try:
                    tree.links.new(from_socket, to)
                except RuntimeError:
                    pass


def _new_socket(iface, name, stype, default, lo, hi, parent):
    kw = {"parent": parent} if parent is not None else {}
    item = iface.new_socket(name, in_out="INPUT", socket_type=stype, **kw)
    if lo is not None:
        item.min_value, item.max_value = lo, hi
    if default is not None and stype not in ("NodeSocketMenu", "NodeSocketGeometry", "NodeSocketBundle",
                                             "NodeSocketClosure"):
        try:
            item.default_value = default
        except (TypeError, ValueError, AttributeError):
            pass
    if stype == "NodeSocketFloat" and name in ("Stagger", "Live Resolution"):
        try:
            item.subtype = 'FACTOR'
        except (TypeError, AttributeError):
            pass
    return item


def _sync_menus(group, wanted):
    for panel, name, stype, default, lo, hi, items in wanted:
        if stype == "NodeSocketMenu" and items:
            ensure_menu_switch(group, name, items)
    _hook_menus(group)


def _hook_menus(group):
    """Blender greys out a dropdown whose Menu Switch feeds nothing. Each switch outputs something
    empty of the group's first output type (geometry, a bundle, a closure) and is joined into that
    output: nothing changes, and the dropdowns stay lit."""
    switches = [n for n in group.nodes if n.type == 'MENU_SWITCH']
    gout = next((n for n in group.nodes if n.type == 'GROUP_OUTPUT'), None)
    if not switches or gout is None:
        return
    out_sock = next((s for s in gout.inputs if s.bl_idname in ("NodeSocketGeometry", "NodeSocketBundle",
                                                                 "NodeSocketClosure")), None)
    if out_sock is None:
        return
    kind = {"NodeSocketGeometry": 'GEOMETRY', "NodeSocketBundle": 'BUNDLE',
            "NodeSocketClosure": 'CLOSURE'}[out_sock.bl_idname]
    for ms in switches:
        if ms.data_type != kind:
            ms.data_type = kind
    if kind == 'CLOSURE':                 # no join for closures: the (only) switch feeds the output
        if not any(l.to_socket == out_sock for l in group.links):
            group.links.new(switches[0].outputs[0], out_sock)
        return
    join_type = "GeometryNodeJoinGeometry" if kind == 'GEOMETRY' else "NodeJoinBundle"
    join = group.nodes.get("Menus")
    if join is not None and join.bl_idname != join_type:
        group.nodes.remove(join)
        join = None
    if join is None:
        join = group.nodes.new(join_type)
        join.name = join.label = "Menus"
        join.hide = True
        join.location = (gout.location.x - 120, gout.location.y - 160)
    if not any(l.from_node == join and l.to_socket == out_sock for l in group.links):
        prev = next((l for l in group.links if l.to_socket == out_sock), None)
        if prev is not None:
            src = prev.from_socket
            group.links.remove(prev)
            if src.node != join:
                group.links.new(src, join.inputs[0])
        group.links.new(join.outputs[0], out_sock)
    for ms in switches:
        if not any(l.from_node == ms and l.to_node == join for l in group.links):
            group.links.new(ms.outputs[0], join.inputs[0])


def set_menu_defaults(tree, node, obj):
    """After the menus exist, show the current template / colour mode on the node."""
    for panel, name, stype, default, lo, hi, items in spec(obj):
        if stype != "NodeSocketMenu":
            continue
        if node.get(f"cn_menu_ok_{name}"):
            continue                           # from here on the dropdown is the user's
        sock = node.inputs.get(name)
        if sock is not None and not sock.is_linked and default and sock.default_value != default:
            try:
                sock.default_value = default
            except (TypeError, ValueError):
                pass


def menu_ready(node, name, expected, current):
    """A Menu socket shows its first item until Blender has evaluated it. A dropdown only counts as the
    user's once it has shown the value we set at least once; until then it's left alone."""
    key = f"cn_menu_ok_{name}"
    if node.get(key):
        return True
    if current == expected:
        node[key] = True
    return False


def apply(obj, values, user=None):
    """Push the node's socket values onto the source's settings. True if the result changes."""
    from . import gn_link
    s = obj.codenodes
    changed = False
    tmpl = values.get("Template")
    node = user[2] if user is not None and user[0] == 'node' else None
    if node is not None and tmpl is not None and menu_ready(node, "Template", s.template_key or CUSTOM, tmpl):
        if tmpl and tmpl != s.template_key and tmpl in templates_for(s.kind):
            switch_template(obj, tmpl, user)
            return True
    for panel, name, stype, prop, lo, hi in SETTINGS.get(s.kind, []):
        if name not in values or values[name] is None:
            continue
        if stype == "NodeSocketMenu" and (node is None or not menu_ready(node, name, setting_value(s, prop),
                                                                         values[name])):
            continue
        v = socket_to_setting(prop, values[name])
        if v is None:
            continue
        cur = getattr(s, prop)
        if prop in ("color_a", "color_b", "surface_color", "bounds_min", "bounds_max"):
            same = all(abs(float(a) - float(b)) < 1e-6 for a, b in zip(cur, v))
        elif isinstance(cur, float):
            same = abs(cur - float(v)) < 1e-9
        else:
            same = cur == v
        if not same:
            try:
                setattr(s, prop, v)
                changed = True
            except (TypeError, ValueError):
                pass
    for p in s.params:
        if p.kind == 'COLOR':
            base, part = p.name[:-2], "rgb".index(p.name[-1])
            col = values.get(base)
            v = tuple(col)[part] if col is not None and hasattr(col, "__len__") else None
        else:
            v = values.get(p.name)
        if v is not None and abs(float(v) - p.value) > 1e-9:
            p["value"] = float(v)
            changed = True
    return changed


def switch_template(obj, key, user=None):
    """The Template dropdown changed: load that template (the old code is kept in a backup text)."""
    from . import gn_link, props
    s = obj.codenodes
    text = s.text
    try:
        new = gn_link.template(s.kind, key)
    except KeyError:
        return
    if text is not None:
        old = text.as_string()
        try:
            old_template = gn_link.template(s.kind, s.template_key) if s.template_key else None
        except KeyError:
            old_template = None
        if old.strip() and old != old_template:
            backup = bpy.data.texts.new(gn_link._unique(f"{text.name} (before {key})", bpy.data.texts))
            backup.from_string(old)
        text.from_string(new)
    s.template_key = key
    gn_link.apply_template_settings(obj, key)
    try:
        props.sync_params(s, new, 'SHAPE' if s.kind == 'SHAPE' else 'MESH')
    except Exception:
        pass
    if user is not None and user[0] == 'node':
        _kind, tree, node = user
        group = node.node_tree
        sync_interface(group, obj)
        # show the template's own settings on the node
        for panel, name, stype, prop, lo, hi in SETTINGS.get(s.kind, []):
            sock = node.inputs.get(name)
            if sock is not None and not sock.is_linked and stype not in ("NodeSocketMenu", "NodeSocketObject"):
                try:
                    sock.default_value = setting_value(s, prop)
                except (TypeError, ValueError):
                    pass
        for name, stype, default, lo, hi in param_sockets(obj):
            sock = node.inputs.get(name)
            if sock is not None and not sock.is_linked:
                try:
                    sock.default_value = default
                except (TypeError, ValueError):
                    pass
        if group.name.startswith(gn_link.PREFIX):
            group.name = gn_link._unique(gn_link.PREFIX + key, bpy.data.node_groups)


# ---- status in the graph ---------------------------------------------------------------------------

def _fmt_count(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else (f"{n / 1e3:.0f}k" if n >= 1e3 else str(n))


def status_line(obj):
    s = obj.codenodes
    if s.last_error:
        first = s.last_error.replace("live: ", "").replace("the code didn't compile:\n", "").splitlines()[0]
        return "⚠ " + first[:60]
    if s.kind == 'SHAPE':
        return "real"
    live = s.real_mode in ("NONE", "RENDER_ONLY")
    if s.kind == 'PARTICLES':
        return f"{'live' if live else 'real'} · {_fmt_count(s.count)}"
    if s.kind == 'DEFORM':
        return "live" if live else "real"
    if s.kind == 'STAGE':
        from . import links
        if obj.name in links.MESH_HEADS:
            return "live" if live else "real"
        heads = links.heads_of(obj)
        if heads:
            return "in chain"
        d = decls_of(obj)
        return "function" if d.func_outs and not d.roles & {"born", "behave", "look", "warp", "deform"} \
            else "not connected"
    return "live" if live else "real"


def label_for(group, obj):
    from . import gn_link
    base = group.name[len(gn_link.PREFIX):] if group.name.startswith(gn_link.PREFIX) else group.name
    return f"{base} · {status_line(obj)}"


def status_text(obj):
    """The full status (error with its line, stats) as a Text block shown inside the group."""
    name = f"CN Status · {obj.name}"
    text = bpy.data.texts.get(name)
    if text is None:
        text = bpy.data.texts.new(name)
    s = obj.codenodes
    body = s.last_error if s.last_error else (s.stats or "ok")
    body = body.replace(" · ", "\n")
    if text.as_string() != body:
        text.from_string(body)
    return text


def ensure_frames(group, obj):
    """Frames inside the group that show the code and the status (Tab into the node to see them)."""
    s = obj.codenodes
    code = group.nodes.get("Code")
    if code is None:
        code = group.nodes.new("NodeFrame")
        code.name = "Code"
        code.location = (-1500, 400)
        code.width, code.height = 900, 900
        code.shrink = False
        code.label_size = 16
    if code.text != s.text:
        code.text = s.text
    code.label = f"Code: {s.text.name if s.text else '(none)'} (edit it in the Text Editor)"
    st = group.nodes.get("Status")
    if st is None:
        st = group.nodes.new("NodeFrame")
        st.name = "Status"
        st.location = (-1500, 560)
        st.width, st.height = 900, 140
        st.shrink = False
    st.text = status_text(obj)
    st.label = "Status"


def update_status(group, obj, nodes):
    """Refresh header labels of every node using `group`, and the frames inside it."""
    label = label_for(group, obj)
    for node in nodes:
        if node.label != label:
            node.label = label
    try:
        ensure_frames(group, obj)
    except (AttributeError, TypeError):
        pass


# ---- To Geometry's sockets -------------------------------------------------------------------------

REAL_SPEC = {
    'MESH': [("Resolution", "NodeSocketInt", 128, 8, 512)],
    'PARTICLES': [("Max Points", "NodeSocketInt", 0, 0, 16_777_216),
                  ("Keep Velocity", "NodeSocketBool", True, None, None),
                  ("Keep Age", "NodeSocketBool", True, None, None)],
    'DEFORM': [],
}


def real_spec(kind, src=None):
    first = ("Particles", "NodeSocketBundle") if kind == 'PARTICLES' else ("Geometry", "NodeSocketGeometry")
    out = [(None, first[0], first[1], None, None, None, None),
           (None, "When", "NodeSocketMenu", "Automatic", None, None, MENU_ITEMS["When"])]
    for name, stype, default, lo, hi in REAL_SPEC.get(kind or "", []):
        if name == "Resolution" and src is not None:
            default = int(src.codenodes.resolution)      # start from the surface's own detail
        out.append((None, name, stype, default, lo, hi, None))
    return out


def sync_real_interface(group, kind, src=None):
    wanted = real_spec(kind, src)
    if signature(group) != [(w[0], w[1], w[2]) for w in wanted]:
        # what feeds the stream input in each tree, so it can be wired back after its type changes
        feeds = []
        for tree in bpy.data.node_groups:
            if tree.bl_idname != "GeometryNodeTree":
                continue
            for node in tree.nodes:
                if node.type == 'GROUP' and node.node_tree == group and node.inputs:
                    l = next((l for l in tree.links if l.to_node == node and l.to_socket == node.inputs[0]), None)
                    if l is not None:
                        feeds.append((tree, node, l.from_socket))
        saved = _save_users(group)
        iface = group.interface
        for item in _inputs(iface):
            if (item.name, item.socket_type) not in [(w[1], w[2]) for w in wanted]:
                iface.remove(item)
        for pos, (panel, name, stype, default, lo, hi, items) in enumerate(wanted):
            item = next((i for i in _inputs(iface) if i.name == name), None)
            if item is None or item.socket_type != stype:
                if item is not None:
                    iface.remove(item)
                item = _new_socket(iface, name, stype, default, lo, hi, None)
        for pos, w in enumerate(wanted):
            item = next(i for i in _inputs(iface) if i.name == w[1])
            if item.position != pos + 1:
                iface.move(item, pos + 1)
        # a geometry input passes through the Join inside (harmless on geometry that's real already)
        gin = next((n for n in group.nodes if n.type == 'GROUP_INPUT'), None)
        join = next((n for n in group.nodes if n.type == 'JOIN_GEOMETRY'), None)
        geo = gin.outputs.get("Geometry") if gin is not None else None
        if geo is not None and join is not None and not any(l.from_node == gin and l.to_node == join
                                                             for l in group.links):
            group.links.new(geo, join.inputs[0])
        _sync_menus(group, wanted)
        _restore_users(group, saved, {w[1]: w[3] for w in wanted})
        for tree, node, from_socket in feeds:
            if node.inputs and not node.inputs[0].is_linked:
                try:
                    tree.links.new(from_socket, node.inputs[0])
                except RuntimeError:
                    pass
        return
    _sync_menus(group, wanted)


WHEN_MAP = dict(WHEN)
