"""The CodeNodes node editor: code nodes with typed sockets feeding a Code -> Mesh output.

The editor only gathers the graph into plain data (see graph.py); compiling,
sampling and meshing are the same pipeline the single-object Code Mesh uses.
"""

from __future__ import annotations

import hashlib
import re
import traceback

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import build, graph, sdf_code
from .sdf_code import SdfCodeError

TREE = "CodeNodesTreeType"
SDF = "CN_SocketSDF"
POINTS = "CN_SocketPoints"
DEBOUNCE_S = 0.15
POLL_S = 0.5
_pending: set[str] = set()


# ---- tree and socket -----------------------------------------------------------------

class CodeNodesTree(bpy.types.NodeTree):
    """GPU code as nodes. Connect code into a Mesh Output to build a mesh."""
    bl_idname = TREE
    bl_label = "CodeNodes"
    bl_icon = 'SCRIPT'

    def update(self):
        # Nothing else "uses" a CodeNodes graph, so without a fake user Blender would drop it on save.
        if not self.use_fake_user:
            self.use_fake_user = True
        request(self)


class CN_SocketSDF(bpy.types.NodeSocket):
    """A shape, as a signed distance function"""
    bl_idname = SDF
    bl_label = "SDF"

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return (0.95, 0.55, 0.2, 1.0)

    @classmethod
    def draw_color_simple(cls):
        return (0.95, 0.55, 0.2, 1.0)


class CN_SocketPoints(bpy.types.NodeSocket):
    """A particle system"""
    bl_idname = POINTS
    bl_label = "Points"

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return (0.35, 0.75, 0.95, 1.0)

    @classmethod
    def draw_color_simple(cls):
        return (0.35, 0.75, 0.95, 1.0)


def _changed(self, context):
    request(self.id_data)


def _sim_changed(self, context):
    """A particle setting changed: restart so the change is visible."""
    from . import particles
    tree = self.id_data
    particles.forget(sim_key(tree, self))
    request(tree)


def sim_key(tree, node):
    return f"{tree.name}/{node.name}"


def _text_changed(self, context):
    self.sync_sockets()
    request(self.id_data)


def _operation_changed(self, context):
    self.inputs["K"].hide = not self.operation.startswith("smooth")
    request(self.id_data)


def _is_mesh(self, obj):
    return obj.type == 'MESH'


class CN_Node:
    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE

    def socket_value_update(self, context):
        request(self.id_data)

    def draw_error(self, layout):
        err = getattr(self, "error", "")
        if err:
            box = layout.box()
            box.alert = True
            for line in err.splitlines()[:8]:
                box.label(text=line, icon='ERROR' if not line.startswith(" ") else 'BLANK1')


# ---- nodes ---------------------------------------------------------------------------

class CN_NodeCode(CN_Node, bpy.types.Node):
    """Signed distance code. Each // @param line in the code becomes an input."""
    bl_idname = "CN_NodeCode"
    bl_label = "SDF Code"
    bl_icon = 'SCRIPT'

    text: PointerProperty(type=bpy.types.Text, name="Code", update=_text_changed)
    error: StringProperty()

    def init(self, context):
        self.outputs.new(SDF, "SDF")
        self.width = 230

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "text", text="")
        op = row.operator("codenodes.node_text", text="", icon='ADD' if self.text is None else 'TEXT')
        op.tree, op.node = self.id_data.name, self.name
        self.draw_error(layout)

    def sync_sockets(self):
        """Match the inputs to the code's @param lines; keeps values of inputs that stay."""
        if self.text is None:
            return
        try:
            wanted = sdf_code.parse_params(self.text.as_string())
        except SdfCodeError as exc:
            self.error = str(exc)
            return
        names = [p.name for p in wanted]
        for sock in list(self.inputs):
            if sock.name not in names:
                self.inputs.remove(sock)
        for i, prm in enumerate(wanted):
            sock = self.inputs.get(prm.name)
            if sock is None:
                sock = self.inputs.new('NodeSocketFloat', prm.name)
                sock.default_value = prm.default
            cur = list(self.inputs).index(sock)
            if cur != i:
                self.inputs.move(cur, i)


class CN_NodeCombine(CN_Node, bpy.types.Node):
    """Combine two shapes"""
    bl_idname = "CN_NodeCombine"
    bl_label = "Combine"
    bl_icon = 'MOD_BOOLEAN'

    operation: EnumProperty(name="Operation", update=_operation_changed, items=[
        ('union', "Union", "Both shapes"),
        ('subtract', "Subtract", "A with B cut away"),
        ('intersect', "Intersect", "Only where they overlap"),
        ('smooth_union', "Smooth Union", "Both shapes, blended where they meet"),
        ('smooth_subtract', "Smooth Subtract", "A with B carved out, with a soft edge"),
    ], default='smooth_union')

    def init(self, context):
        self.inputs.new(SDF, "A")
        self.inputs.new(SDF, "B")
        k = self.inputs.new('NodeSocketFloat', "K")
        k.default_value = 0.25
        self.outputs.new(SDF, "SDF")

    def draw_buttons(self, context, layout):
        layout.prop(self, "operation", text="")

    def draw_label(self):
        return self.operation.replace("_", " ").title()


class CN_NodeTransform(CN_Node, bpy.types.Node):
    """Move, rotate and scale a shape"""
    bl_idname = "CN_NodeTransform"
    bl_label = "Transform"
    bl_icon = 'OBJECT_ORIGIN'

    def init(self, context):
        self.inputs.new(SDF, "SDF")
        self.inputs.new('NodeSocketVector', "Location")
        self.inputs.new('NodeSocketVectorEuler', "Rotation")
        s = self.inputs.new('NodeSocketFloat', "Scale")
        s.default_value = 1.0
        self.outputs.new(SDF, "SDF")


class CN_NodeOffset(CN_Node, bpy.types.Node):
    """Grow (positive) or shrink (negative) a shape"""
    bl_idname = "CN_NodeOffset"
    bl_label = "Offset"
    bl_icon = 'MOD_SOLIDIFY'

    def init(self, context):
        self.inputs.new(SDF, "SDF")
        self.inputs.new('NodeSocketFloat', "Amount")
        self.outputs.new(SDF, "SDF")


class CN_NodeMeshOutput(CN_Node, bpy.types.Node):
    """Code -> Mesh: samples the connected shape on the GPU and builds a real mesh"""
    bl_idname = "CN_NodeMeshOutput"
    bl_label = "Mesh Output"
    bl_icon = 'MESH_DATA'

    target: PointerProperty(type=bpy.types.Object, name="Object", update=_changed, poll=_is_mesh)
    resolution: IntProperty(name="Resolution", default=96, min=8, max=512, soft_max=256, update=_changed)
    bounds_min: FloatVectorProperty(name="Min", default=(-2.0, -2.0, -2.0), subtype='XYZ', update=_changed)
    bounds_max: FloatVectorProperty(name="Max", default=(2.0, 2.0, 2.0), subtype='XYZ', update=_changed)
    live: BoolProperty(name="Live", default=True)
    animate: BoolProperty(name="Animate", default=False)
    smooth: BoolProperty(name="Smooth", default=True, update=_changed)
    error: StringProperty()
    stats: StringProperty()
    code_hash: StringProperty()

    def init(self, context):
        self.inputs.new(SDF, "SDF")
        self.width = 240

    def draw_buttons(self, context, layout):
        layout.prop(self, "target", text="")
        layout.prop(self, "resolution")
        col = layout.column(align=True)
        col.label(text="Bounds")
        row = col.row(align=True)
        row.prop(self, "bounds_min", text="")
        row = col.row(align=True)
        row.prop(self, "bounds_max", text="")
        col.label(text="(min, then max)")
        row = layout.row(align=True)
        row.prop(self, "live", toggle=True)
        row.prop(self, "animate", toggle=True)
        row.prop(self, "smooth", toggle=True)
        from . import cache
        baked = self.target is not None and cache.is_baked(self.target)
        row = layout.row(align=True)
        row.enabled = not baked
        op = row.operator("codenodes.node_build", icon='FILE_REFRESH')
        op.tree, op.node = self.id_data.name, self.name
        if baked:
            box = layout.box()
            box.label(text="Playing the baked cache", icon='FILE_CACHE')
            box.operator("codenodes.unbake", icon='X').object_name = self.target.name
        else:
            op = layout.operator("codenodes.bake", icon='FILE_CACHE')
            op.tree, op.node = self.id_data.name, self.name
        if self.error:
            self.draw_error(layout)
        elif self.stats:
            for part in self.stats.split(" · ")[:2]:
                layout.label(text=part)


class CN_NodeParticles(CN_Node, bpy.types.Node):
    """A particle solver you write: spawn() places a particle, update() moves it"""
    bl_idname = "CN_NodeParticles"
    bl_label = "Particles"
    bl_icon = 'PARTICLES'

    text: PointerProperty(type=bpy.types.Text, name="Code", update=_text_changed)
    count: IntProperty(name="Particles", default=20000, min=1, max=2_000_000, soft_max=500_000,
                       update=_sim_changed)
    substeps: IntProperty(name="Substeps", default=1, min=1, max=20, update=_sim_changed,
                          description="Solver steps per frame; raise it if fast particles jitter")
    stagger: FloatProperty(name="Stagger", default=1.0, min=0.0, max=1.0, update=_sim_changed,
                           description="Spread starting ages so particles don't all die at once")
    error: StringProperty()

    def init(self, context):
        self.outputs.new(POINTS, "Points")
        self.width = 240

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "text", text="")
        op = row.operator("codenodes.node_text", text="", icon='ADD' if self.text is None else 'TEXT')
        op.tree, op.node = self.id_data.name, self.name
        col = layout.column(align=True)
        col.prop(self, "count")
        col.prop(self, "substeps")
        col.prop(self, "stagger", slider=True)
        self.draw_error(layout)

    def sync_sockets(self):
        """Match the inputs to the code's @param lines, keeping the values that stay."""
        if self.text is None:
            return
        try:
            wanted = sdf_code.parse_params(self.text.as_string())
        except SdfCodeError as exc:
            self.error = str(exc)
            return
        names = [p.name for p in wanted]
        for sock in list(self.inputs):
            if sock.name not in names:
                self.inputs.remove(sock)
        for i, prm in enumerate(wanted):
            sock = self.inputs.get(prm.name)
            if sock is None:
                sock = self.inputs.new('NodeSocketFloat', prm.name)
                sock.default_value = prm.default
            cur = list(self.inputs).index(sock)
            if cur != i:
                self.inputs.move(cur, i)


class CN_NodePointsOutput(CN_Node, bpy.types.Node):
    """Show a particle system as a real point cloud on an object"""
    bl_idname = "CN_NodePointsOutput"
    bl_label = "Points Output"
    bl_icon = 'OUTLINER_OB_POINTCLOUD'

    target: PointerProperty(type=bpy.types.Object, name="Object", update=_changed, poll=_is_mesh)
    point_radius: FloatProperty(name="Point Size", default=0.02, min=0.0, soft_max=0.5, update=_changed)
    live: BoolProperty(name="Live", default=True)
    animate: BoolProperty(name="Animate", default=True)
    error: StringProperty()
    stats: StringProperty()
    code_hash: StringProperty()

    def init(self, context):
        self.inputs.new(POINTS, "Points")
        self.width = 240

    def draw_buttons(self, context, layout):
        from . import cache
        layout.prop(self, "target", text="")
        layout.prop(self, "point_radius")
        row = layout.row(align=True)
        row.prop(self, "live", toggle=True)
        row.prop(self, "animate", toggle=True)
        baked = self.target is not None and cache.is_baked(self.target)
        row = layout.row(align=True)
        row.enabled = not baked
        op = row.operator("codenodes.node_build", text="Simulate", icon='FILE_REFRESH')
        op.tree, op.node = self.id_data.name, self.name
        if baked:
            box = layout.box()
            box.label(text="Playing the baked cache", icon='FILE_CACHE')
            box.operator("codenodes.unbake", icon='X').object_name = self.target.name
        else:
            op = layout.operator("codenodes.bake", icon='FILE_CACHE')
            op.tree, op.node = self.id_data.name, self.name
        if self.error:
            self.draw_error(layout)
        elif self.stats:
            layout.label(text=self.stats)


class CN_NodeShape(CN_Node, bpy.types.Node):
    """A model built from a parametric description: profiles, revolve, extrude, sweep"""
    bl_idname = "CN_NodeShape"
    bl_label = "Shape"
    bl_icon = 'MESH_CYLINDER'

    text: PointerProperty(type=bpy.types.Text, name="Description", update=_text_changed)
    target: PointerProperty(type=bpy.types.Object, name="Object", update=_changed, poll=_is_mesh)
    smooth: BoolProperty(name="Smooth", default=True, update=_changed)
    live: BoolProperty(name="Live", default=True)
    animate: BoolProperty(name="Animate", default=False)     # shapes have no time
    error: StringProperty()
    stats: StringProperty()
    code_hash: StringProperty()

    def init(self, context):
        self.width = 250

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "text", text="")
        op = row.operator("codenodes.node_text", text="", icon='ADD' if self.text is None else 'TEXT')
        op.tree, op.node = self.id_data.name, self.name
        layout.prop(self, "target", text="")
        row = layout.row(align=True)
        row.prop(self, "live", toggle=True)
        row.prop(self, "smooth", toggle=True)
        op = layout.operator("codenodes.node_build", text="Build", icon='FILE_REFRESH')
        op.tree, op.node = self.id_data.name, self.name
        op = layout.operator("codenodes.profile_to_curve", icon='OUTLINER_OB_CURVE')
        op.tree, op.node = self.id_data.name, self.name
        if self.error:
            self.draw_error(layout)
        elif self.stats:
            layout.label(text=self.stats)

    def sync_sockets(self):
        """Match the inputs to the description's `param` lines."""
        if self.text is None:
            return
        from .shapes import ShapeError, parse
        try:
            wanted = parse(self.text.as_string()).params
        except ShapeError as exc:
            self.error = str(exc)
            return
        names = [p[0] for p in wanted]
        for sock in list(self.inputs):
            if sock.name not in names:
                self.inputs.remove(sock)
        for i, (name, default, _lo, _hi) in enumerate(wanted):
            sock = self.inputs.get(name)
            if sock is None:
                sock = self.inputs.new('NodeSocketFloat', name)
                sock.default_value = default
            cur = list(self.inputs).index(sock)
            if cur != i:
                self.inputs.move(cur, i)


NODE_CLASSES = (CN_NodeCode, CN_NodeCombine, CN_NodeTransform, CN_NodeOffset, CN_NodeMeshOutput,
                CN_NodeParticles, CN_NodePointsOutput, CN_NodeShape)


# ---- gathering the graph -------------------------------------------------------------

def _source(sock, kind=SDF):
    """The node feeding an input, following reroutes and muted nodes; None if nothing."""
    seen = set()
    while sock is not None and sock.is_linked:
        link = sock.links[0]
        if not link.is_valid or link.is_muted:
            return None
        node = link.from_node
        if node.name in seen:
            return None
        seen.add(node.name)
        if node.bl_idname == 'NodeReroute':
            sock = node.inputs[0] if node.inputs else None
            continue
        if node.mute:                      # a muted node passes its first matching input through
            sock = next((s for s in node.inputs if s.bl_idname == kind), None)
            continue
        return node
    return None


def gather(tree):
    """Plain-data description of every node, for graph.compile_graph."""
    data = {}
    for node in tree.nodes:
        if node.bl_idname == "CN_NodeCode":
            data[node.name] = {"kind": "code", "code": node.text.as_string() if node.text else "",
                               "values": {s.name: s.default_value for s in node.inputs}}
        elif node.bl_idname == "CN_NodeCombine":
            data[node.name] = {"kind": node.operation,
                               "inputs": {k: getattr(_source(node.inputs[k]), "name", None) for k in ("A", "B")},
                               "values": {"K": node.inputs["K"].default_value}}
        elif node.bl_idname == "CN_NodeTransform":
            data[node.name] = {"kind": "transform", "inputs": {"SDF": getattr(_source(node.inputs["SDF"]), "name", None)},
                               "values": {"location": tuple(node.inputs["Location"].default_value),
                                          "rotation": tuple(node.inputs["Rotation"].default_value),
                                          "scale": node.inputs["Scale"].default_value}}
        elif node.bl_idname == "CN_NodeOffset":
            data[node.name] = {"kind": "offset", "inputs": {"SDF": getattr(_source(node.inputs["SDF"]), "name", None)},
                               "values": {"amount": node.inputs["Amount"].default_value}}
    return data


def _code_hash(tree):
    h = hashlib.sha1()
    for node in sorted(tree.nodes, key=lambda n: n.name):
        if node.bl_idname in ("CN_NodeCode", "CN_NodeParticles", "CN_NodeShape") and node.text is not None:
            h.update(node.name.encode() + b"\0" + node.text.as_string().encode() + b"\0")
    return h.hexdigest()


def _attribute(message, prog, tree):
    """Point 'line N' in a compile error at the node and line it came from, and flag that node."""
    def repl(m):
        where = prog.locate(int(m.group(1)))
        if where is None:
            return m.group(0)
        name, local = where
        node = tree.nodes.get(name)
        if node is not None and hasattr(node, "error"):
            node.error = f"line {local}: " + message.split(m.group(0), 1)[1].split("\n")[0].strip()
        return f"node '{name}', line {local}:"
    return re.sub(r"line (\d+):", repl, message)


_building: set[str] = set()


def build_output(tree, out):
    """Bring an output node up to date. Returns "" or an error message."""
    _building.add(tree.name)       # changes made while building (sockets, target) don't queue a rebuild
    try:
        if out.bl_idname == "CN_NodeShape":
            return _build_shape_node(tree, out)
        if out.bl_idname == "CN_NodePointsOutput":
            return _build_points_output(tree, out)
        return _build_output(tree, out)
    finally:
        _building.discard(tree.name)
        _pending.discard(tree.name)


def compute_output(tree, out):
    """(MeshResult, stats) for the graph feeding `out` at the current frame.

    Raises SdfCodeError, with any compile error already pointed back at the node it
    came from. Also refreshes each code node's sockets and clears old errors.
    """
    from . import live
    for node in tree.nodes:
        if node.bl_idname == "CN_NodeCode":
            node.error = ""
            node.sync_sockets()
    out.code_hash = _code_hash(tree)
    root = _source(out.inputs["SDF"])
    if root is None:
        raise SdfCodeError("connect a shape to the Mesh Output's SDF input")
    prog = graph.compile_graph(gather(tree), root.name)
    seconds, frame = live.scene_time()
    try:
        return build.compute(prog.source, tuple(out.bounds_min), tuple(out.bounds_max), out.resolution,
                             time_s=seconds, frame=frame, values=prog.values)
    except SdfCodeError as exc:
        raise SdfCodeError(_attribute(str(exc), prog, tree)) from None


def _build_output(tree, out):
    if not tree.use_fake_user:
        tree.use_fake_user = True
    try:
        result, st = compute_output(tree, out)
        target = out.target
        if target is None:
            name = f"{tree.name} Mesh"
            target = bpy.data.objects.new(name, bpy.data.meshes.new(name))
            bpy.context.scene.collection.objects.link(target)
            out.target = target
        build.swap_mesh(target, result, out.smooth)
    except SdfCodeError as exc:
        out.error = str(exc)
        return out.error
    except Exception as exc:  # never let a bug take Blender down
        traceback.print_exc()
        out.error = f"internal error ({type(exc).__name__}): {exc}"
        return out.error
    nx, ny, nz = st["dims"]
    out.error = ""
    out.stats = (f"{st['faces']:,} faces · grid {nx}×{ny}×{nz} · "
                 f"GPU {st['sample_s'] * 1000:.0f} ms · mesh {st['mesh_s'] * 1000:.0f} ms")
    return ""


OUTPUT_KINDS = ("CN_NodeMeshOutput", "CN_NodePointsOutput", "CN_NodeShape")


def _build_shape_node(tree, node):
    """Build a Shape node's description into its object."""
    from . import shape_build
    try:
        if node.text is None:
            raise SdfCodeError(f"node '{node.name}' has no description: pick a text block")
        node.sync_sockets()
        node.code_hash = _code_hash(tree)
        source = node.text.as_string()
        values = {s.name: s.default_value for s in node.inputs}
        target = node.target
        if target is None:
            name = f"{tree.name} Shape"
            target = bpy.data.objects.new(name, bpy.data.meshes.new(name))
            bpy.context.scene.collection.objects.link(target)
            node.target = target
        if target.mode == 'EDIT':
            raise SdfCodeError(f"'{target.name}' is in Edit Mode; leave it to rebuild")
        shape_build.build_into(target.data, source, values, node.smooth)
        st = shape_build.mesh_stats(target.data)
    except SdfCodeError as exc:
        node.error = str(exc)
        return node.error
    except Exception as exc:
        traceback.print_exc()
        node.error = f"internal error ({type(exc).__name__}): {exc}"
        return node.error
    node.error = ""
    node.stats = (f"{st['quads']:,} quads + {st['tris']:,} tris · {st['sharp_edges']:,} sharp · "
                  f"{st['size'][0]:g} × {st['size'][1]:g} × {st['size'][2]:g} m")
    return ""


def outputs(tree):
    return [n for n in tree.nodes if n.bl_idname in OUTPUT_KINDS]


def simulate_points(tree, out):
    """Step the particle system feeding `out` to the current frame. (state, stats)."""
    from . import live, particles
    src = _source(out.inputs["Points"], POINTS)
    if src is None or src.bl_idname != "CN_NodeParticles":
        raise SdfCodeError("connect a Particles node to the Points Output")
    if src.text is None:
        raise SdfCodeError(f"node '{src.name}' has no code: pick a text block")
    src.error = ""
    src.sync_sockets()
    source = src.text.as_string()
    out.code_hash = _code_hash(tree)
    values = {s.name: s.default_value for s in src.inputs}
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    try:
        return particles.simulate(sim_key(tree, src), source, src.count, scene.frame_current,
                                  scene.frame_start, fps, values, src.substeps, src.stagger)
    except SdfCodeError as exc:
        src.error = str(exc).split("didn't compile:\n")[-1]
        raise SdfCodeError(f"node '{src.name}': {exc}") from None


def _build_points_output(tree, out):
    from . import particles
    try:
        state, st = simulate_points(tree, out)
        target = out.target
        if target is None:
            name = f"{tree.name} Points"
            target = bpy.data.objects.new(name, bpy.data.meshes.new(name))
            bpy.context.scene.collection.objects.link(target)
            out.target = target
        particles.ensure_points_modifier(target, out.point_radius)
        particles.fill_points(target.data, state)
    except SdfCodeError as exc:
        out.error = str(exc)
        return out.error
    except Exception as exc:
        traceback.print_exc()
        out.error = f"internal error ({type(exc).__name__}): {exc}"
        return out.error
    out.error = ""
    out.stats = (f"{st['count']:,} particles · frame {st['frame']} · "
                 f"{st['steps']} step{'s' if st['steps'] != 1 else ''} · {st['sim_s'] * 1000:.0f} ms")
    return ""


# ---- live rebuilding -----------------------------------------------------------------

def _flush():
    names = list(_pending)
    _pending.clear()
    for name in names:
        tree = bpy.data.node_groups.get(name)
        if tree is not None and tree.bl_idname == TREE:
            for out in outputs(tree):
                if out.live:
                    build_output(tree, out)
    return None


def request(tree):
    if tree is None or getattr(tree, "bl_idname", None) != TREE or tree.name in _building:
        return
    _pending.add(tree.name)
    if not bpy.app.timers.is_registered(_flush):
        bpy.app.timers.register(_flush, first_interval=DEBOUNCE_S)


def _rendering():
    try:
        return bpy.app.is_job_running('RENDER')
    except Exception:
        return False


def _poll_text():
    try:
        if not _rendering():
            for tree in bpy.data.node_groups:
                if tree.bl_idname != TREE:
                    continue
                live_outs = [o for o in outputs(tree) if o.live]
                if live_outs and _code_hash(tree) != live_outs[0].code_hash:
                    request(tree)
    except Exception:
        traceback.print_exc()
    return POLL_S


@bpy.app.handlers.persistent
def _on_frame(scene, depsgraph=None):
    if _rendering():
        return
    try:
        for tree in bpy.data.node_groups:
            if tree.bl_idname == TREE:
                for out in outputs(tree):
                    if out.animate:
                        build_output(tree, out)
    except Exception:
        traceback.print_exc()


# ---- operators -----------------------------------------------------------------------

def _node(tree_name, node_name):
    tree = bpy.data.node_groups.get(tree_name)
    return tree, (tree.nodes.get(node_name) if tree else None)


class CODENODES_OT_node_build(bpy.types.Operator):
    bl_idname = "codenodes.node_build"
    bl_label = "Build Mesh"
    bl_description = "Compile the connected nodes and rebuild the mesh"
    bl_options = {'REGISTER', 'UNDO'}
    tree: StringProperty()
    node: StringProperty()

    def execute(self, context):
        tree, out = _node(self.tree, self.node)
        if out is None:
            return {'CANCELLED'}
        err = build_output(tree, out)
        if err:
            self.report({'ERROR'}, err.splitlines()[0])
            return {'CANCELLED'}
        self.report({'INFO'}, out.stats)
        return {'FINISHED'}


class CODENODES_OT_node_text(bpy.types.Operator):
    bl_idname = "codenodes.node_text"
    bl_label = "Code Text"
    bl_description = "Create the node's code from a template, or show it in a Text Editor"
    tree: StringProperty()
    node: StringProperty()

    def execute(self, context):
        from .ops import show_text
        tree, node = _node(self.tree, self.node)
        if node is None:
            return {'CANCELLED'}
        if node.text is None:
            text = bpy.data.texts.new(f"{node.name}.sdf")
            text.from_string(STARTER_CODE)
            node.text = text
        show_text(context, node.text, self)
        return {'FINISHED'}


STARTER_CODE = """\
// @param radius 0.8 0.1 2.0
float sdf(vec3 p) {
  return sdSphere(p, radius);
}
"""


def new_demo_graph(name="CodeNodes Graph"):
    """A small starting graph: two code nodes, a smooth union, a transform, a mesh output."""
    tree = bpy.data.node_groups.new(name, TREE)
    tree.use_fake_user = True
    t1 = bpy.data.texts.new("Blob.sdf")
    t1.from_string("// @param radius 0.9 0.1 2.0\n// @param wobble 0.06 0.0 0.3\nfloat sdf(vec3 p) {\n"
                   "  return sdSphere(p, radius) + wobble * sin(6.0 * p.x + uTime) * sin(6.0 * p.y) * sin(6.0 * p.z);\n}\n")
    t2 = bpy.data.texts.new("Ring.sdf")
    t2.from_string("// @param major 1.2 0.2 3.0\n// @param minor 0.22 0.02 1.0\nfloat sdf(vec3 p) {\n"
                   "  return sdTorus(p, major, minor);\n}\n")
    blob = tree.nodes.new("CN_NodeCode"); blob.name = blob.label = "Blob"; blob.location = (-520, 160); blob.text = t1
    ring = tree.nodes.new("CN_NodeCode"); ring.name = ring.label = "Ring"; ring.location = (-520, -140); ring.text = t2
    tilt = tree.nodes.new("CN_NodeTransform"); tilt.location = (-260, -140)
    tilt.inputs["Rotation"].default_value = (0.5, 0.0, 0.0)
    comb = tree.nodes.new("CN_NodeCombine"); comb.location = (-20, 60)
    out = tree.nodes.new("CN_NodeMeshOutput"); out.location = (220, 60)
    links = tree.links
    links.new(ring.outputs["SDF"], tilt.inputs["SDF"])
    links.new(blob.outputs["SDF"], comb.inputs["A"])
    links.new(tilt.outputs["SDF"], comb.inputs["B"])
    links.new(comb.outputs["SDF"], out.inputs["SDF"])
    return tree, out


class CODENODES_OT_new_graph(bpy.types.Operator):
    bl_idname = "codenodes.new_graph"
    bl_label = "New Node Graph"
    bl_description = "Create a starter CodeNodes graph and build its mesh"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        tree, out = new_demo_graph()
        err = build_output(tree, out)
        editors = [a for a in context.screen.areas if a.type == 'NODE_EDITOR']
        if editors:
            space = editors[0].spaces.active
            space.tree_type = TREE
            space.node_tree = tree
        if err:
            self.report({'WARNING'}, err.splitlines()[0])
        else:
            self.report({'INFO'}, "Open a Node Editor and pick the CodeNodes editor type to see the graph")
        return {'FINISHED'}


# ---- add menu ------------------------------------------------------------------------

def _add_menu(self, context):
    space = context.space_data
    if getattr(space, "tree_type", None) != TREE:
        return
    layout = self.layout
    for cls in NODE_CLASSES:
        op = layout.operator("node.add_node", text=cls.bl_label, icon=cls.bl_icon)
        op.type = cls.bl_idname
        op.use_transform = True
    layout.operator("codenodes.new_particle_graph", text="Particles Starter", icon='PARTICLES')


def new_particle_graph(name="CodeNodes Particles"):
    """A starting graph: a Particles node feeding a Points Output."""
    from . import particles
    tree = bpy.data.node_groups.new(name, TREE)
    tree.use_fake_user = True
    text = bpy.data.texts.new("Particles.sdf")
    text.from_string(particles.TEMPLATE)
    node = tree.nodes.new("CN_NodeParticles")
    node.name = node.label = "Solver"
    node.location = (-300, 0)
    node.text = text
    out = tree.nodes.new("CN_NodePointsOutput")
    out.location = (60, 0)
    tree.links.new(node.outputs["Points"], out.inputs["Points"])
    return tree, out


class CODENODES_OT_new_particle_graph(bpy.types.Operator):
    bl_idname = "codenodes.new_particle_graph"
    bl_label = "New Particle Graph"
    bl_description = "Create a starter particle graph and simulate it"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        tree, out = new_particle_graph()
        err = build_output(tree, out)
        editors = [a for a in context.screen.areas if a.type == 'NODE_EDITOR']
        if editors:
            space = editors[0].spaces.active
            space.tree_type = TREE
            space.node_tree = tree
        if err:
            self.report({'WARNING'}, err.splitlines()[0])
        return {'FINISHED'}


classes = (CodeNodesTree, CN_SocketSDF, CN_SocketPoints) + NODE_CLASSES + (
    CODENODES_OT_node_build, CODENODES_OT_node_text, CODENODES_OT_new_graph,
    CODENODES_OT_new_particle_graph)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.NODE_MT_add.append(_add_menu)
    bpy.app.handlers.frame_change_post.append(_on_frame)
    bpy.app.timers.register(_poll_text, first_interval=POLL_S, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_poll_text):
        bpy.app.timers.unregister(_poll_text)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    if _on_frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame)
    bpy.types.NODE_MT_add.remove(_add_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
