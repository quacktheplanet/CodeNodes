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

import re

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

# Tooltips (the hover text in the node editor) for the sockets CodeNodes adds itself. A code's own sliders
# get theirs from the "…" written at the end of their @in / @param line.
FIXED_TIPS = {
    EDIT: "Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. "
          "Double-click the node or press Ctrl+E for the same",
    "Template": "Load one of the ready-made codes for this kind of node. If you had edited the code, your "
                "version is kept in a text named '… (before …)'",
    "Count": "How many particles to simulate. Drawing them live handles millions; To Geometry copies them into "
             "Blender, which costs more",
    "Emit From": "An object whose surface the particles are born on (optional). Without one, the code decides "
                 "where they start",
    "Colour By": "What colours the particles when no Look node is wired: the code's own colour, their speed or "
                 "their age",
    "Slow / Young": "Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age)",
    "Fast / Old": "Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age)",
    "Glow": "Draw additively, so overlapping particles add up to a glow. Off draws them solid",
    "Point Size": "Size of each particle on screen, in pixels, when no Look node sets a size",
    "Brightness": "Overall brightness of the live particles",
    "Speed Range": "The speed (metres per second) that counts as fully fast for Colour By: Speed",
    "Substeps": "Simulation steps per frame. More is steadier for fast or stiff motion, and costs more",
    "Pre-warm": "Seconds simulated before the first frame, so the particles open already in shape",
    "Stagger": "How spread out the births are: 0 = all at once, 1 = evenly over their lifetime",
    "Colour": "Base colour of the live surface (when no Material is wired)",
    "Shadows": "Soft shadows the surface casts on itself in the live view",
    "Ambient Occlusion": "Darkens creases and corners in the live view",
    "Fog": "Distance haze in the live view (0 = none)",
    "Sky": "Draws a sky behind the surface in the live view",
    "Live Resolution": "Resolution of the live view (1 = full). Lower is faster while you work",
    "Bounds Min": "One corner of the box the surface lives in. To Geometry builds the mesh inside it",
    "Bounds Max": "The opposite corner of the box the surface lives in",
    "Smooth": "Smooth shading on the built model",
    "Lights": "Wire a Scene Lights node's light output here to light this surface with the scene's lamps and "
              "world",
    "Material": "Wire a Material Look node's material output here to use a Blender material's colours and "
                "roughness",
}
STREAM_TIPS_IN = {
    "Particles": "The particle stream to work on: wire in the Particles output of the node before",
    "Mesh": "The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex",
}
STREAM_TIPS_OUT = {
    "Particles": "The particle stream after this node: wire it into the next stage, a Look, or To Geometry",
    "Mesh": "The mesh after this node's code. Add To Geometry (To Mesh) to use it in regular nodes or renders",
    "Geometry": "Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real "
                "geometry",
}
REAL_TIPS = {
    "Geometry": "The GPU node or chain to make real: particles become points, surfaces and mesh code become a mesh",
    "Particles": "The particle chain to make real: the particles become points with all their attributes",
    "When": "When to rebuild the real geometry: Automatic (every frame for particles and animated code, "
            "otherwise when something changes), Every Frame, When Changed, or Only for Render",
    "Resolution": "Detail of the mesh built from a surface: cells along each side of its bounds (higher is "
                  "finer and slower)",
    "Max Points": "At most this many particles become points (0 = all of them)",
    "Keep Velocity": "Write each point's velocity and speed (for motion blur, or colouring by speed)",
    "Keep Age": "Write each point's age and life",
}
REAL_TIPS_OUT = {"Geometry": "The real geometry: use it like the output of any Geometry Nodes node"}
CACHE_TIPS = {
    "Particles": "The particle chain to record",
    "Mode": "Live runs the simulation as you work. Cached plays back the recorded frames (instant scrubbing; "
            "renders read them too)",
    "Start": "First frame to record",
    "End": "Last frame to record",
}
CACHE_TIPS_OUT = {"Particles": "The same particles, live or played back from the recording"}
JOIN_TIPS = {"Particles A": "The first particle stream", "Particles B": "The second particle stream"}
JOIN_TIPS_OUT = {"Particles": "Both streams together: everything wired after this applies to both"}


def decl_struct(obj, name):
    from . import decl
    t = obj.codenodes.text
    return decl.struct_fields(t.as_string() if t is not None else "", name)


def code_tips(obj):
    """(input tips, output tips, node description) for a code node."""
    d = decls_of(obj)
    tips_in = dict(FIXED_TIPS)
    tips_in.update(STREAM_TIPS_IN)
    tips_out = dict(STREAM_TIPS_OUT)
    for name, text in d.descriptions.items():
        tips_in[name] = text
        tips_out[name] = text
    if obj.codenodes.kind == 'SHAPE':
        # a shape's sliders: the comment at the end of a `param` line is its tooltip
        t = obj.codenodes.text
        for line in (t.as_string() if t is not None else "").splitlines():
            m = re.match(r"^\s*param\s+([A-Za-z_]\w*)[^#]*(?:#\s*(.*\S))?\s*$", line)
            if m:
                tips_in[m.group(1)] = m.group(2) or (f"The shape's '{m.group(1)}' (its `param` line in the code). "
                                                     f"End that line with # and a sentence to make this tooltip")
    for a in d.attrs:
        tips_out.setdefault(a.name, f"The per-particle value '{a.name}' this node writes, as a field: after "
                                    f"To Geometry every point carries it")
    for f in d.func_outs:
        tips_out.setdefault(f.name, f"The function '{f.name}': wire it into a function input of another node")
    for f in d.func_ins:
        tips_in.setdefault(f.name, f"Wire a function here (e.g. from a field node); the code calls {f.name}(…)")
        args = ", ".join(f.arg_names())
        example = f"min({f.plain_use()}, 100)" if f.ret == "int" else f"{f.plain_use()} * 0.4"
        tips_in[use_socket(f.name)] = (
            f"How this node uses what's wired into '{f.name}': one expression, e.g. {example}. It can "
            f"use {f.name}'s arguments ({args or 'none'}), this node's inputs and the helpers. {f.plain_use()} uses "
            f"it as it is. Kept in the code as  use: …  on the {f.name} line")
    for l in d.lists:
        what = (f"records ({', '.join(f for _t, f in (decl_struct(obj, l.type) or []))})" if l.is_record()
                else f"{l.type} values")
        if l.name in d.descriptions:
            if l.is_record():
                tips_in[l.name] = f"{d.descriptions[l.name]} (each row: {what[len('records ('):-1]})"
        else:
            tips_in[l.name] = (f"A list of {what}: wire in a List node"
                               f"{'' if l.is_record() else ', one of its columns or any list (e.g. Field to List)'}"
                               f". The code reads "
                               f"{l.name}_count() and {l.name}(i)")
    if d.table is not None:
        rows = len(d.table)
        tips_out[TABLE_OUT] = (f"The whole table ({rows} row{'s' if rows != 1 else ''}): wire it into a list "
                               f"input of records")
        for c in d.table.columns:
            tips_out[c.name] = f"The column '{c.name}' as a list ({rows} value{'s' if rows != 1 else ''})"
    for m in d.materials:
        tips_in.setdefault(m, "A Blender material: its Principled BSDF colours, roughness, metallic and "
                              "emission come into the code")
    return tips_in, tips_out, d.summary


def apply_tips(group, tips_in, tips_out, summary=None, prefix=""):
    """Write hover tooltips onto the group's sockets (and its description). Only writes what differs, so a
    steady file causes no updates."""
    for item in group.interface.items_tree:
        if item.item_type != 'SOCKET':
            continue
        text = (tips_in if item.in_out == 'INPUT' else tips_out).get(item.name)
        if text is not None and getattr(item, "description", text) != text:
            try:
                item.description = text
            except (AttributeError, TypeError):
                pass
    if summary:
        want = prefix + summary
        if group.description != want:
            group.description = want


GPU_NODE_NOTE = " (Drawn live on the GPU; add To Geometry after it to use it in nodes or renders.)"


def apply_code_tips(group, obj):
    tips_in, tips_out, summary = code_tips(obj)
    note = GPU_NODE_NOTE if obj.codenodes.kind in ('PARTICLES', 'STAGE', 'DEFORM', 'MESH') else ""
    apply_tips(group, tips_in, tips_out, (summary + note) if summary else None)


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


_last_table = {}


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
            if got.table is not None:
                _last_table[obj.name] = got.table
        except Exception:
            got = decl.Decls()
            got.roles = decl.roles(code)
            if re.search(r"^\s*//\s*@list\b", code, re.M):
                # a List with a mistake keeps its last good table, so its outputs (and links) stay
                got.table = _last_table.get(obj.name) or decl.Table()
        if len(_decl_cache) > 256:
            _decl_cache.clear()
        _decl_cache[key] = got
    return got


STREAM_IN = {"Particles": "NodeSocketBundle", "Mesh": "NodeSocketGeometry"}
LIST = "|LIST"            # appended to a socket type: a list socket (Blender 5.2+), e.g. NodeSocketVector|LIST
LIST_SOCKET = {"float": "NodeSocketFloat", "int": "NodeSocketInt", "vec2": "NodeSocketVector",
               "vec3": "NodeSocketVector", "vec4": "NodeSocketColor"}
TABLE_OUT = "List"        # a List node's output with the whole table (a bundle of its columns)


def lists_native():
    """Blender 5.2 lets a node group have list sockets; before that a list travels on a bundle socket
    (CodeNodes reads the wiring either way, so lists work on every version)."""
    return bpy.app.version >= (5, 2, 0)


def list_input_type(l):
    if l.is_record() or not lists_native():
        return "NodeSocketBundle"
    return LIST_SOCKET[l.type] + LIST


def column_type(c):
    if not lists_native():
        return "NodeSocketBundle"
    return {1: "NodeSocketFloat", 2: "NodeSocketVector", 3: "NodeSocketVector", 4: "NodeSocketColor"}[c.size] + LIST


def stype_of(item):
    """An interface socket's type as the spec writes it (list sockets get |LIST)."""
    st = item.socket_type
    if getattr(item, "structure_type", "") == 'LIST':
        st += LIST
    return st


def _split_type(stype):
    return (stype[:-len(LIST)], True) if stype.endswith(LIST) else (stype, False)


USE = " · use"            # the use line under a function input: "wind · use"
USES_SEEN = "cn_uses_seen"  # on the source object: the use lines last shown on the node, by input


def use_socket(name):
    return name + USE


def _sync_uses(obj, values, user):
    """Keep each function input's use line the same on the node and in the code, whichever changed.
    True if the code changed."""
    from . import decl
    from .sdf_code import SdfCodeError
    s = obj.codenodes
    if s.text is None:
        return False
    d = decls_of(obj)
    if not d.func_ins:
        return False
    seen = dict(obj.get(USES_SEEN, {}))
    code = s.text.as_string()
    changed = False
    for f in d.func_ins:
        in_code = f.use_line()
        typed = values.get(use_socket(f.name))
        typed = typed.strip() if isinstance(typed, str) else None
        last = seen.get(f.name)
        # a new socket starts with the code's use line, so a different value was typed on the node
        if typed and typed != (last if last is not None else in_code) and typed != in_code:
            try:                                   # typed on the node: it goes into the code
                code = decl.set_use(code, f.name, typed)
                seen[f.name] = typed
                changed = True
            except SdfCodeError as exc:
                s.last_error = f"use line of '{f.name}': {exc}"
                seen[f.name] = typed               # don't retry until it's edited again
            continue
        if in_code != last or (typed is not None and typed != in_code):
            seen[f.name] = in_code                 # the code changed (or a new node): the node follows
            _set_input(user, use_socket(f.name), in_code)
    if changed:
        s.text.from_string(code)
    if seen != dict(obj.get(USES_SEEN, {})):
        obj[USES_SEEN] = seen
    return changed


def _set_input(user, name, value):
    """Set an unlinked input's value on a group node or a modifier."""
    if user is None:
        return
    from . import mod_inputs
    kind, _owner, thing = user
    try:
        if kind == 'node':
            sock = thing.inputs.get(name)
            if sock is not None and not sock.is_linked and sock.default_value != value:
                sock.default_value = value
        else:
            item = next((i for i in thing.node_group.interface.items_tree
                         if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == name), None)
            if item is not None:
                inputs = mod_inputs.of(thing)
                if inputs.get(item.identifier) != value:
                    inputs[item.identifier] = value
    except (AttributeError, TypeError, ReferenceError, KeyError):
        pass


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
            out.append((None, use_socket(f.name), "NodeSocketString", f.use_line(), None, None, None))
        for l in d.lists:
            out.append((None, l.name, list_input_type(l), None, None, None, None))
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
    elif s.kind == 'STAGE' and d.table is not None:
        out.append((TABLE_OUT, "NodeSocketBundle"))
        for c in d.table.columns:
            out.append((c.name, column_type(c)))
        return out
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
    have = [(i.name, stype_of(i)) for i in _outputs(group.interface)]
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
                saved.append((tree.name, node.name,
                              [(l.from_socket.name, l.to_node.name, l.to_socket.identifier, l.to_socket.name)
                               for l in tree.links if l.from_node == node]))
    iface = group.interface
    for item in _outputs(iface):
        if (item.name, stype_of(item)) not in wanted:
            iface.remove(item)
    for name, stype in wanted:
        if not any(i.name == name and stype_of(i) == stype for i in _outputs(iface)):
            base, is_list = _split_type(stype)
            item = iface.new_socket(name, in_out="OUTPUT", socket_type=base)
            if is_list:
                item.structure_type = 'LIST'
    for pos, (name, stype) in enumerate(wanted):
        item = next(i for i in _outputs(iface) if i.name == name and stype_of(i) == stype)
        if item.position != pos:
            iface.move(item, pos)
    for tree_name, node_name, links in saved:           # names only: re-found after the rebuild
        tree = bpy.data.node_groups.get(tree_name)
        node = tree.nodes.get(node_name) if tree is not None else None
        if node is None:
            continue
        for name, to_node_name, to_id, to_name in links:
            to_node = tree.nodes.get(to_node_name)
            to = _socket_by(to_node.inputs, to_id, to_name) if to_node is not None else None
            if to is None:
                continue
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
    return [(_panel_of(i), i.name, stype_of(i)) for i in _inputs(group.interface)]


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


NATIVE_ROWS = 1024         # a List up to this long also gives real Geometry Nodes lists on its outputs
TABLE_HASH = "cn_table_hash"


def build_table_nodes(group, obj):
    """Inside a List node's group: real Geometry Nodes lists on its outputs (Blender 5.2+), so native nodes
    (Get List Item, List Length, ...) can use the table too. Each column is Field to List over an Index
    Switch holding its values; the List output bundles the columns. Rebuilt only when the table changes."""
    import hashlib
    d = decls_of(obj)
    t = d.table
    if t is None or not lists_native():
        return
    key = hashlib.sha1(repr(([(c.name, c.size, c.values) for c in t.columns], NATIVE_ROWS)).encode()).hexdigest()
    if group.get(TABLE_HASH) == key:
        return
    for n in [n for n in group.nodes if n.name.startswith("List · ")]:
        group.nodes.remove(n)
    gout = next((n for n in group.nodes if n.type == 'GROUP_OUTPUT'), None)
    if gout is None:
        return
    rows = len(t)
    native = 0 < rows <= NATIVE_ROWS
    bundle = group.nodes.new("NodeCombineBundle")
    bundle.name = bundle.label = "List · bundle"
    bundle.location = (gout.location.x - 250, gout.location.y + 200)
    if native:
        index = group.nodes.new("GeometryNodeInputIndex")
        index.name = "List · index"
        index.location = (gout.location.x - 1100, gout.location.y)
        index.hide = True
    for k, c in enumerate(t.columns):
        dtype = {1: 'FLOAT', 2: 'VECTOR', 3: 'VECTOR', 4: 'RGBA'}[c.size]
        item = bundle.bundle_items.new(dtype, c.name)
        out = gout.inputs.get(c.name)
        if not native:
            continue
        y = gout.location.y - 220 * k
        sw = group.nodes.new("GeometryNodeIndexSwitch")
        sw.name = f"List · {c.name} values"
        sw.data_type = dtype
        sw.location = (gout.location.x - 850, y)
        sw.hide = True
        while len(sw.index_switch_items) < rows:
            sw.index_switch_items.new()
        while len(sw.index_switch_items) > rows:
            sw.index_switch_items.remove(sw.index_switch_items[-1])
        for r, v in enumerate(c.values):
            vals = v if isinstance(v, tuple) else (v,)
            sock = sw.inputs[r + 1]
            if c.size == 1:
                sock.default_value = float(vals[0])
            elif c.size == 4:
                sock.default_value = tuple(float(x) for x in vals)
            else:
                sock.default_value = tuple(float(x) for x in vals) + (0.0,) * (3 - len(vals))
        group.links.new(index.outputs[0], sw.inputs["Index"])
        ftl = group.nodes.new("GeometryNodeFieldToList")
        ftl.name = f"List · {c.name}"
        ftl.location = (gout.location.x - 550, y)
        ftl.list_items.new(dtype, c.name)
        ftl.inputs["Count"].default_value = rows
        group.links.new(sw.outputs[0], ftl.inputs[c.name])
        if out is not None:
            group.links.new(ftl.outputs[0], out)
        group.links.new(ftl.outputs[0], bundle.inputs[c.name])
    if gout.inputs.get(TABLE_OUT) is not None:
        group.links.new(bundle.outputs[0], gout.inputs[TABLE_OUT])
    group[TABLE_HASH] = key


def sync_interface(group, obj):
    """Make the group's inputs and outputs match the code and the kind's settings. True if anything
    changed."""
    wanted = spec(obj)
    outs_changed = sync_outputs(group, obj)
    if outs_changed:
        group.pop(TABLE_HASH, None)
    try:
        build_table_nodes(group, obj)
    except (AttributeError, TypeError, RuntimeError, KeyError):
        pass
    if signature(group) == [(w[0], w[1], w[2]) for w in wanted]:
        _sync_menus(group, wanted)
        apply_code_tips(group, obj)
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
    apply_code_tips(group, obj)
    return True


def _socket_by(sockets, identifier, name):
    """Re-find a socket after a rebuild: by identifier first, then by name."""
    for sock in sockets:
        if sock.identifier == identifier:
            return sock
    return sockets.get(name) if name else None


def _save_users(group):
    """Links and typed values going into every node that uses `group`, by input name.

    Everything is kept as names (tree, node, socket), never as Blender objects: rebuilding the group's
    interface frees the node's sockets, and touching a freed socket later can crash Blender."""
    saved = []
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "GeometryNodeTree":
            continue
        for node in tree.nodes:
            if node.type != 'GROUP' or node.node_tree != group:
                continue
            links = [(l.from_node.name, l.from_socket.identifier, l.from_socket.name, l.to_socket.name)
                     for l in tree.links if l.to_node == node]
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
            saved.append((tree.name, node.name, links, values))
    return saved


def _restore_users(group, saved, defaults=None):
    """Put back what went into each node; sockets that are new get the spec's default (Blender gives a
    socket added to a group in use a value of 0, not the interface default)."""
    defaults = defaults or {}
    for tree_name, node_name, links, values in saved:
        tree = bpy.data.node_groups.get(tree_name)
        node = tree.nodes.get(node_name) if tree is not None else None
        if node is None:
            continue
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
        for from_node_name, from_id, from_name, to_name in links:
            src = tree.nodes.get(from_node_name)
            to = node.inputs.get(to_name)
            frm = _socket_by(src.outputs, from_id, from_name) if src is not None else None
            if to is not None and frm is not None:
                try:
                    tree.links.new(frm, to)
                except RuntimeError:
                    pass


def _new_socket(iface, name, stype, default, lo, hi, parent):
    kw = {"parent": parent} if parent is not None else {}
    stype, is_list = _split_type(stype)
    item = iface.new_socket(name, in_out="INPUT", socket_type=stype, **kw)
    if is_list:
        item.structure_type = 'LIST'
        return item
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
    if s.kind in ('PARTICLES', 'STAGE', 'DEFORM', 'MESH') and _sync_uses(obj, values, user):
        changed = True
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
        tree_name, node_name = tree.name, node.name
        group = node.node_tree
        sync_interface(group, obj)
        tree = bpy.data.node_groups.get(tree_name)            # re-find: the rebuild freed the old sockets
        node = tree.nodes.get(node_name) if tree is not None else None
        if node is None:
            return
        group = node.node_tree
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
    """1500 -> 1.5k, 2500 -> 2.5k, 20000 -> 20k, 1000000 -> 1.0M (one decimal under ten, so it isn't rounded off)."""
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        k = n / 1e3
        return f"{k:.1f}".rstrip("0").rstrip(".") + "k" if k < 10 else f"{k:.0f}k"
    return str(n)


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
    if s.kind in ('PARTICLES', 'STAGE', 'DEFORM'):
        from . import links
        warn = live_mod().chain_warnings(obj, links.heads_of(obj) or [links.base_name(obj)])
        if warn:
            return "⚠ " + warn[0][:60]
    if s.kind == 'STAGE':
        from . import links
        if obj.name in links.MESH_HEADS:
            return "live" if live else "real"
        users = links.users_of(obj.name)
        d = decls_of(obj)
        if d.table is not None:
            rows = len(d.table)
            return f"{rows} row{'s' if rows != 1 else ''}" + (f" · used by {len(users)}" if users else "")
        if users and d.func_outs and not d.roles & {"born", "behave", "look", "warp", "deform"}:
            return f"used by {len(users)}"
        heads = links.heads_of(obj)
        if heads:
            return "in chain"
        d = decls_of(obj)
        return "function" if d.func_outs and not d.roles & {"born", "behave", "look", "warp", "deform"} \
            else "not connected"
    return "live" if live else "real"


def live_mod():
    from . import live
    return live


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
        apply_tips(group, REAL_TIPS, REAL_TIPS_OUT)
        for tree, node, from_socket in feeds:
            if node.inputs and not node.inputs[0].is_linked:
                try:
                    tree.links.new(from_socket, node.inputs[0])
                except RuntimeError:
                    pass
        return
    _sync_menus(group, wanted)
    apply_tips(group, REAL_TIPS, REAL_TIPS_OUT)


WHEN_MAP = dict(WHEN)
