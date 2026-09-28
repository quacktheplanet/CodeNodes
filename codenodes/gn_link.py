"""CodeNodes inside Geometry Nodes.

Blender doesn't let add-ons define nodes inside a Geometry Nodes tree, so a code node
there is an ordinary Group node:

    [ Code · Donut ]  (a node group, one per inserted node)
      inputs:  Resolution + one per `@param` / `param` line in the code
      inside:  Object Info -> Group Output
      reads:   a hidden source object in the "CodeNodes Sources" collection, which the
               usual Code Mesh / Code Shape / Code Particles machinery builds

Changing a value on the group node (or on a modifier using the group), or editing the
code, rebuilds the source object, and Object Info hands the new geometry to the rest of
the tree. Renders read the stored result and never run GPU code.

Values that come in through a link are followed back to a Value / Integer node, a
reroute, or the host tree's Group Input (the modifier's value). Anything computed by
other nodes can't be read from Python, so the value typed on the socket is used instead.
"""

from __future__ import annotations

import traceback

import bpy

from . import live, props, sdf_code

SOURCES = "CodeNodes Sources"
TAG = "codenodes_source"         # on the node group: the source object's name
KIND_KEY = "codenodes_kind"
PREFIX = "Code · "
POLL_S = 0.25

KINDS = {
    'MESH': ("Code Mesh (SDF)", "A surface from a signed distance function, sampled on the GPU", 'SCRIPT'),
    'SHAPE': ("Code Shape", "A model built from a parametric description: exact edges, clean quads",
              'MESH_CYLINDER'),
    'PARTICLES': ("Code Particles", "Points moved by a solver you write, on the GPU", 'PARTICLES'),
}

TEMPLATES = {
    'MESH': {
        "Donut": """\
// A donut. sdf(p) is the distance to the surface: negative inside, positive outside (metres).
// Each @param line below becomes an input on the node.
// @param major 0.8 0.2 2.0
// @param minor 0.3 0.05 1.0
float sdf(vec3 p) {
  return sdTorus(p, major, minor);
}
""",
        "Rounded Box": """\
// A box with rounded edges.
// @param size 0.8 0.1 2.0
// @param roundness 0.15 0.0 0.5
float sdf(vec3 p) {
  return sdRoundBox(p, vec3(size), min(roundness, size));
}
""",
        "Gyroid Ball": """\
// A sphere carved into a gyroid lattice.
// @param radius 1.0 0.2 2.0
// @param cells 6.0 1.0 16.0
// @param thickness 0.05 0.01 0.3
float sdf(vec3 p) {
  vec3 q = p * cells;
  float g = abs(dot(sin(q), cos(q.yzx))) / cells - thickness;
  return max(sdSphere(p, radius), g);
}
""",
        "Blob": sdf_code.TEMPLATE,
    },
    'SHAPE': {
        "Desk Lamp": None,          # filled in from shapes.TEMPLATE at first use
        "Vase": """\
# A vase: one curved profile, spun around the vertical axis.
param height  0.40  0.10 1.00
param belly   0.12  0.03 0.40
param neck    0.05  0.02 0.20

part body
  profile
    move 0, 0
    line belly * 0.7, 0
    curve x = belly * 0.7 + (neck - belly * 0.7) * t + belly * 0.5 * sin(t * pi)   y = height * t   steps 24
    line 0, height
    close
  revolve segments 64
""",
    },
    'PARTICLES': {
        "Swirl": None,              # particles.TEMPLATE
        "Fountain": """\
// A fountain: particles shoot up from the origin and fall back under gravity.
// @param power 4.0 0.5 10.0
// @param gravity 9.8 0.0 20.0
void spawn(inout Particle p) {
  vec3 r = rand3(p.seed);
  p.position = vec3(0.0);
  p.velocity = vec3((r.x - 0.5) * 1.2, (r.y - 0.5) * 1.2, power * (0.8 + 0.4 * r.z));
  p.life = 1.5 + rand1(p.seed * 7.1);
}
void update(inout Particle p, float dt) {
  p.velocity.z -= gravity * dt;
  p.position += p.velocity * dt;
}
""",
    },
}
DEFAULT_TEMPLATE = {'MESH': "Donut", 'SHAPE': "Desk Lamp", 'PARTICLES': "Swirl"}


def template(kind, key=None):
    key = key or DEFAULT_TEMPLATE[kind]
    src = TEMPLATES[kind].get(key)
    if src is None and kind == 'SHAPE':
        from .shapes import TEMPLATE as src
    elif src is None and kind == 'PARTICLES':
        from .particles import TEMPLATE as src
    if src is None:
        raise KeyError(f"no {kind} template called '{key}'")
    return src


# ---- finding things ----------------------------------------------------------------------

def is_code_group(group):
    return group is not None and getattr(group, "bl_idname", "") == "GeometryNodeTree" and TAG in group


def source_of(group):
    """The hidden object a code group reads, or None."""
    if not is_code_group(group):
        return None
    return bpy.data.objects.get(group[TAG])


def code_node(context):
    """The active node in the Node Editor, if it's a code node: (node, group, source)."""
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None)
    node = tree.nodes.active if tree is not None else None
    if node is None or node.type != 'GROUP' or not is_code_group(node.node_tree):
        return None, None, None
    return node, node.node_tree, source_of(node.node_tree)


def users():
    """{group name: [('node', tree, node) | ('mod', object, modifier)]} for every code group in use."""
    found = {}
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "GeometryNodeTree":
            continue
        for node in tree.nodes:
            if node.type == 'GROUP' and is_code_group(node.node_tree):
                found.setdefault(node.node_tree.name, []).append(('node', tree, node))
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == 'NODES' and is_code_group(mod.node_group):
                found.setdefault(mod.node_group.name, []).append(('mod', obj, mod))
    return found


# ---- making them -------------------------------------------------------------------------

def sources_collection(scene=None):
    scene = scene or bpy.context.scene
    col = bpy.data.collections.get(SOURCES)
    if col is None:
        col = bpy.data.collections.new(SOURCES)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    col.hide_viewport = True          # still evaluated for Object Info, never drawn or rendered itself
    col.hide_render = True
    return col


def _unique(name, pool):
    if name not in pool:
        return name
    n = 2
    while f"{name} {n}" in pool:
        n += 1
    return f"{name} {n}"


def _move_to_sources(obj):
    col = sources_collection()
    for c in list(obj.users_collection):
        if c != col:
            c.objects.unlink(obj)
    if obj.name not in col.objects:
        col.objects.link(obj)
    obj.location = (0.0, 0.0, 0.0)


def make_source(kind, source, label):
    """Build a hidden Code Mesh / Shape / Particles object from `source`. (object, error or "")."""
    from . import api
    name = _unique(f"CN · {label}", bpy.data.objects)
    if kind == 'SHAPE':
        r = api.code_to_shape(source, name=name)
    elif kind == 'PARTICLES':
        r = api.code_to_particles(source, name=name)
    else:
        r = api.code_to_mesh(source, name=name)
    obj = bpy.data.objects.get(r.get("object") or name)
    if obj is None:                   # the code didn't even parse: make an empty source to fix later
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(obj)
        s = obj.codenodes
        text = bpy.data.texts.new(f"{name}.{'shape' if kind == 'SHAPE' else 'sdf'}")
        text.from_string(source)
        s.enabled, s.text = True, text
        s.kind = kind
        s.last_error = r.get("error") or ""
    _move_to_sources(obj)
    return obj, r.get("error") or ""


def wanted_inputs(obj):
    """[(name, socket type, default, min, max)] the group should expose for this source."""
    s = obj.codenodes
    out = []
    if s.kind == 'MESH':
        out.append(("Resolution", "NodeSocketInt", int(s.resolution), 8, 512))
    for p in s.params:
        out.append((p.name, "NodeSocketFloat", float(p.value), float(p.min), float(p.max)))
    return out


def sync_interface(group, obj):
    """Make the group's inputs match the code's sliders. Returns True if anything changed."""
    iface = group.interface
    wanted = wanted_inputs(obj)
    names = [w[0] for w in wanted]
    current = [i for i in iface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT']
    changed = False
    for item in current:
        if item.name not in names or item.socket_type != dict((w[0], w[1]) for w in wanted)[item.name]:
            iface.remove(item)
            changed = True
    have = {i.name: i for i in iface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT'}
    for pos, (name, stype, default, lo, hi) in enumerate(wanted):
        item = have.get(name)
        if item is None:
            item = iface.new_socket(name, in_out="INPUT", socket_type=stype)
            item.default_value = default
            changed = True
        if (item.min_value, item.max_value) != (lo, hi):
            item.min_value, item.max_value = lo, hi
    # inputs in the code's order, after the Geometry output
    for pos, name in enumerate(names):
        item = next(i for i in iface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT'
                    and i.name == name)
        target = pos + 1
        if item.position != target:
            iface.move(item, target)
    return changed


def build_group(obj, label):
    """A node group whose Geometry output is `obj`'s code result."""
    group = bpy.data.node_groups.new(_unique(PREFIX + label, bpy.data.node_groups), "GeometryNodeTree")
    group[TAG] = obj.name
    group[KIND_KEY] = obj.codenodes.kind
    group.description = ("CodeNodes: geometry made by code. Select the node and open the "
                         "Node Editor sidebar (N) › CodeNodes to edit the code")
    if hasattr(group, "color_tag"):
        try:
            group.color_tag = 'SCRIPT'
        except TypeError:
            pass
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    sync_interface(group, obj)
    nodes = group.nodes
    gin, gout = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.name = info.label = "Code Result"
    info.transform_space = 'ORIGINAL'
    info.inputs["Object"].default_value = obj
    group.links.new(info.outputs["Geometry"], gout.inputs["Geometry"])
    gin.location, info.location, gout.location = (-400, 0), (-150, 0), (150, 0)
    return group


def create(kind, key=None, label=None, source=None):
    """A new code group and its source. Returns (group, error or "")."""
    source = source if source is not None else template(kind, key)
    label = label or key or DEFAULT_TEMPLATE[kind]
    obj, err = make_source(kind, source, label)
    return build_group(obj, label), err


def ensure_tree(context):
    """The tree the Node Editor is editing; if it's empty, give the active mesh a Geometry Nodes
    modifier with a fresh tree (like the editor's New button). None if there's nothing to use."""
    space = context.space_data
    tree = getattr(space, "edit_tree", None)
    if tree is not None:
        return tree
    obj = context.active_object
    if obj is None or obj.type not in {'MESH', 'CURVE', 'POINTCLOUD', 'CURVES'}:
        return None
    tree = bpy.data.node_groups.new("Geometry Nodes", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-300, 0), (300, 0)
    tree.links.new(gin.outputs[0], gout.inputs[0])
    mod = obj.modifiers.new("GeometryNodes", 'NODES')
    mod.node_group = tree
    try:
        space.node_tree = tree
    except (AttributeError, TypeError):
        pass
    return tree


HOST = "codenodes_host"          # on a Geometry Nodes tree made by add_object


def add_object(kind, key=None, source=None, label=None, location=(0.0, 0.0, 0.0), collection=None):
    """A new object whose geometry comes from a code node in its own Geometry Nodes tree.

    What Add › Mesh › Code Mesh / Code Shape / Code Particles makes. Returns
    (object, tree, node, error or "").
    """
    label = label or key or DEFAULT_TEMPLATE[kind]
    group, err = create(kind, key, label, source)
    name = _unique(f"Code {label}", bpy.data.objects)
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    if collection is None:
        collection = getattr(bpy.context, "collection", None) or bpy.context.scene.collection
    collection.objects.link(obj)
    obj.location = location
    tree = bpy.data.node_groups.new(_unique(name, bpy.data.node_groups), "GeometryNodeTree")
    tree[HOST] = group.name
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-420, 0), (320, 0)
    node = insert(tree, group, (-90, 60))
    tree.links.new(node.outputs["Geometry"], gout.inputs["Geometry"])
    mod = obj.modifiers.new("CodeNodes", 'NODES')
    mod.node_group = tree
    return obj, tree, node, err


def host_code_nodes(obj):
    """[(tree, node)] for every code node in `obj`'s Geometry Nodes modifiers."""
    out = []
    for mod in getattr(obj, "modifiers", ()):
        if mod.type != 'NODES' or mod.node_group is None:
            continue
        for node in mod.node_group.nodes:
            if node.type == 'GROUP' and is_code_group(node.node_tree):
                out.append((mod.node_group, node))
    return out


def find_user(group):
    """(tree, node) of the first Group node using a code group, or (None, None)."""
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "GeometryNodeTree":
            continue
        for node in tree.nodes:
            if node.type == 'GROUP' and node.node_tree == group:
                return tree, node
    return None, None


def insert(tree, group, location=(0.0, 0.0)):
    node = tree.nodes.new("GeometryNodeGroup")
    node.node_tree = group
    node.location = location
    node.width = 180
    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node
    return node


# ---- keeping them in sync ----------------------------------------------------------------

def _host_trees_value(tree, socket_identifier):
    """A host tree's Group Input value, read from the first modifier using the tree."""
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == 'NODES' and mod.node_group == tree:
                try:
                    return mod[socket_identifier]
                except KeyError:
                    return None
    return None


def resolve(tree, socket, depth=0):
    """A group node input's value, following simple links. None when it can't be known."""
    if not socket.is_linked:
        return getattr(socket, "default_value", None)
    if depth > 16:
        return None
    link = next((l for l in tree.links if l.to_socket == socket and not l.is_muted), None)
    if link is None:
        return getattr(socket, "default_value", None)
    src_node, out = link.from_node, link.from_socket
    if src_node.type == 'REROUTE':
        return resolve(tree, src_node.inputs[0], depth + 1)
    if src_node.type == 'VALUE':
        return out.default_value
    if src_node.bl_idname in ("FunctionNodeInputInt", "FunctionNodeInputFloat"):
        return getattr(src_node, "integer", None) if src_node.bl_idname == "FunctionNodeInputInt" \
            else getattr(src_node, "value", None)
    if src_node.type == 'GROUP_INPUT':
        return _host_trees_value(tree, out.identifier)
    return None                        # computed by other nodes: not readable from Python


def read_values(user):
    """{input name: value} from a group node or modifier, for inputs whose value is known."""
    kind, owner, thing = user
    values = {}
    if kind == 'node':
        for sock in thing.inputs:
            v = resolve(owner, sock)
            if v is not None:
                values[sock.name] = v
    else:
        for item in thing.node_group.interface.items_tree:
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT':
                try:
                    values[item.name] = thing[item.identifier]
                except KeyError:
                    pass
    return values


def apply_values(obj, values):
    """Write known input values onto the source's sliders. True if something changed."""
    s = obj.codenodes
    changed = False
    res = values.get("Resolution")
    if s.kind == 'MESH' and res is not None and int(res) != s.resolution:
        s["resolution"] = max(8, min(512, int(res)))
        changed = True
    for p in s.params:
        v = values.get(p.name)
        if v is not None and abs(float(v) - p.value) > 1e-9:
            p["value"] = float(v)
            changed = True
    return changed


def split(group, user):
    """Give a duplicated node its own group, source object and code text."""
    obj = source_of(group)
    kind, owner, thing = user
    new_obj = obj.copy()
    new_obj.data = obj.data.copy()
    new_obj.name = _unique(obj.name, bpy.data.objects)
    s = new_obj.codenodes
    if obj.codenodes.text is not None:
        old = obj.codenodes.text
        text = bpy.data.texts.new(_unique(old.name, bpy.data.texts))
        text.from_string(old.as_string())
        s.text = text
    sources_collection().objects.link(new_obj)
    new_group = group.copy()
    new_group.name = _unique(group.name, bpy.data.node_groups)
    new_group[TAG] = new_obj.name
    info = next((n for n in new_group.nodes if n.type == 'OBJECT_INFO'), None)
    if info is not None:
        info.inputs["Object"].default_value = new_obj
    if kind == 'node':
        values = {sock.identifier: getattr(sock, "default_value", None) for sock in thing.inputs}
        thing.node_tree = new_group
        for sock in thing.inputs:              # keep what was typed on the copy
            if values.get(sock.identifier) is not None:
                sock.default_value = values[sock.identifier]
    else:
        thing.node_group = new_group
    live.request(new_obj)
    return new_group


def sync():
    """One pass: split duplicates, match inputs to code, push values, queue rebuilds."""
    if live._rendering() or not any(is_code_group(g) for g in bpy.data.node_groups):
        return
    for gname, lst in users().items():
        group = bpy.data.node_groups.get(gname)
        obj = source_of(group)
        if obj is None:
            continue
        for user in lst[1:]:
            split(group, user)
        user = lst[0]
        s = obj.codenodes
        if s.enabled and [i[0] for i in wanted_inputs(obj)] != [
                i.name for i in group.interface.items_tree
                if i.item_type == 'SOCKET' and i.in_out == 'INPUT']:
            sync_interface(group, obj)
        if s.enabled and apply_values(obj, read_values(user)) and s.live:
            live.request(obj)


_dirty = [True]


def _poll():
    try:
        if _dirty[0]:
            _dirty[0] = False
            sync()
    except Exception:
        traceback.print_exc()
    return POLL_S


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    for u in depsgraph.updates:
        if isinstance(u.id, (bpy.types.NodeTree, bpy.types.Object)):
            _dirty[0] = True
            return


@bpy.app.handlers.persistent
def _on_load(*_args):
    _dirty[0] = True


def _slow_tick():
    _dirty[0] = True                    # catches changes that send no depsgraph update (e.g. code text)
    return 1.0


# ---- Make Native -------------------------------------------------------------------------

def make_native(group):
    """Replace a Code Mesh group with a real Geometry Nodes network (via ExpressNode).

    Returns the native group. Raises bake_nodes.CannotConvert with a reason otherwise."""
    from . import bake_nodes
    obj = source_of(group)
    if obj is None or obj.codenodes.kind != 'MESH':
        raise bake_nodes.CannotConvert("only Code Mesh (SDF) nodes can be made native; shapes and "
                                       "particles have no node version yet")
    lst = users().get(group.name, [])
    before = [read_values(u) for u in lst]
    native = bake_nodes.build(obj)            # adds a modifier on the source; we take the group
    for mod in [m for m in obj.modifiers if m.type == 'NODES' and m.node_group == native]:
        obj.modifiers.remove(mod)
    native.name = _unique(group.name.replace(PREFIX, "Nodes · ", 1), bpy.data.node_groups)
    for (kind, owner, thing), values in zip(lst, before):
        if kind == 'node':
            thing.node_tree = native
            for sock in thing.inputs:
                if sock.name in values and not sock.is_linked:
                    sock.default_value = values[sock.name]
        else:
            thing.node_group = native
            for item in native.interface.items_tree:
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name in values:
                    thing[item.identifier] = values[item.name]
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    bpy.data.node_groups.remove(group)
    return native


# ---- cleaning up on save -----------------------------------------------------------------

@bpy.app.handlers.persistent
def _on_save(*_args):
    """Remove sources whose code group is gone (deleted node, then saved)."""
    try:
        used = {g[TAG] for g in bpy.data.node_groups if is_code_group(g) and g.users > 0}
        col = bpy.data.collections.get(SOURCES)
        if col is None:
            return
        for obj in list(col.objects):
            if obj.codenodes.enabled and obj.name not in used:
                bpy.data.objects.remove(obj)
    except Exception:
        traceback.print_exc()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.handlers.save_pre.append(_on_save)
    bpy.app.timers.register(_poll, first_interval=POLL_S, persistent=True)
    bpy.app.timers.register(_slow_tick, first_interval=1.0, persistent=True)


def unregister():
    for fn in (_poll, _slow_tick):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load), (bpy.app.handlers.save_pre, _on_save)):
        if fn in lst:
            lst.remove(fn)
