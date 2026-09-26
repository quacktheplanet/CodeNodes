"""Bake a CodeNodes output to a file per frame, and read it back with plain Geometry Nodes.

Why: the live path runs GPU code from a Python frame handler, which Blender does not
allow during a render (it crashes, verified 2026-09-26), and background Blender has no
GPU at all. Baking splits the two jobs:

    bake time   (needs a window + GPU)  frame loop -> one .ply per frame
    render time (needs neither)         a generated node group reads the frame's file

The generated group is ordinary Geometry Nodes:

    Scene Time.Frame -> clamp to the baked range -> Float to Int
        -> Format String "<dir>/f_{}.ply" -> Import PLY -> (Set Shade Smooth) -> Output

so every other Geometry Nodes node can work on the result, and final renders and render
farms need neither the GPU nor this add-on.
"""

from __future__ import annotations

import os
import shutil

import numpy as np

from .sdf_code import SdfCodeError

CACHE_ROOT = "//codenodes_cache"
GROUP_PREFIX = "CodeNodes Cache"
MODIFIER_NAME = "CodeNodes Cache"
_FACE = np.dtype([("n", "u1"), ("v", "<u4", (4,))])      # PLY face list: uchar count + uint indices


def _slug(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "cache"


def cache_dir(name, create=True):
    """Absolute path of a bake's folder, next to the .blend when it has been saved."""
    import bpy
    root = CACHE_ROOT
    if not bpy.data.filepath:
        root = os.path.join(bpy.app.tempdir, "codenodes_cache")
    path = os.path.join(bpy.path.abspath(root), _slug(name))
    if create:
        os.makedirs(path, exist_ok=True)
    return path.replace("\\", "/")


def frame_path(directory, frame):
    return f"{directory}/f_{int(frame)}.ply"


def write_ply(path, result):
    """A binary PLY with quad faces. Matches what GN's Import PLY expects."""
    verts = np.ascontiguousarray(result.verts, "<f4")
    quads = np.ascontiguousarray(result.quads, "<u4")
    with open(path, "wb") as f:
        f.write(b"ply\nformat binary_little_endian 1.0\n")
        f.write(b"element vertex %d\nproperty float x\nproperty float y\nproperty float z\n" % len(verts))
        f.write(b"element face %d\nproperty list uchar uint vertex_indices\n" % len(quads))
        f.write(b"end_header\n")
        f.write(verts.tobytes())
        faces = np.empty(len(quads), _FACE)
        faces["n"] = 4
        faces["v"] = quads
        f.write(faces.tobytes())
    return os.path.getsize(path)


def write_points_ply(path, state):
    """A binary PLY of points plus per-point scalars, for particle bakes.

    Vectors are written as three scalars (``velocity_x`` and friends) because PLY
    has no vector type; the reader group puts them back together.
    """
    pos = np.ascontiguousarray(state["position"], "<f4")
    columns, names = [], []
    for key, values in state.items():
        if key == "position":
            continue
        values = np.asarray(values, np.float32)
        if values.ndim == 2:
            for axis in range(values.shape[1]):
                columns.append(values[:, axis])
                names.append(f"{key}_{'xyzw'[axis]}")
        else:
            columns.append(values)
            names.append(key)
    with open(path, "wb") as f:
        f.write(b"ply\nformat binary_little_endian 1.0\n")
        f.write(b"element vertex %d\nproperty float x\nproperty float y\nproperty float z\n" % len(pos))
        for n in names:
            f.write(b"property float %s\n" % n.encode())
        f.write(b"end_header\n")
        data = np.column_stack([pos] + [np.asarray(c, "<f4") for c in columns]).astype("<f4")
        f.write(np.ascontiguousarray(data).tobytes())
    return os.path.getsize(path)


def bake(name, compute, start, end, progress=None, writer=write_ply):
    """Write one .ply per frame. `compute(frame)` returns whatever `writer` takes.

    Restores the current frame afterwards. Returns a summary dict.
    """
    import bpy
    scene = bpy.context.scene
    start, end = int(start), int(end)
    if end < start:
        raise SdfCodeError(f"the frame range is empty ({start} to {end})")
    directory = cache_dir(name)
    for old in os.listdir(directory):
        if old.startswith("f_") and old.endswith(".ply"):
            os.remove(os.path.join(directory, old))
    here = scene.frame_current
    total_bytes, faces = 0, 0
    try:
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            result = compute(frame)
            total_bytes += writer(frame_path(directory, frame), result)
            faces += len(getattr(result, "quads", ()))
            if progress is not None:
                progress(frame - start + 1, end - start + 1)
    finally:
        scene.frame_set(here)
    n = end - start + 1
    return {"dir": directory, "frames": n, "start": start, "end": end,
            "bytes": total_bytes, "avg_faces": faces // max(n, 1)}


def reader_group(name, directory, start, end, smooth=True, as_points=False, radius=0.02):
    """Build (or rebuild) the node group that plays a bake back."""
    import bpy
    group_name = f"{GROUP_PREFIX} · {name}"
    tree = bpy.data.node_groups.get(group_name)
    if tree is None:
        tree = bpy.data.node_groups.new(group_name, "GeometryNodeTree")
    else:
        tree.nodes.clear()
        for item in list(tree.interface.items_tree):
            tree.interface.remove(item)
    tree.use_fake_user = True
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    nodes, links = tree.nodes, tree.links
    out = nodes.new("NodeGroupOutput")
    out.location = (600, 0)
    time = nodes.new("GeometryNodeInputSceneTime")
    time.location = (-800, 0)
    # Hold the first and last baked frames outside the range instead of reading missing files.
    lo = nodes.new("ShaderNodeMath")
    lo.operation = 'MAXIMUM'
    lo.inputs[1].default_value = float(start)
    lo.location = (-620, 0)
    hi = nodes.new("ShaderNodeMath")
    hi.operation = 'MINIMUM'
    hi.inputs[1].default_value = float(end)
    hi.location = (-440, 0)
    as_int = nodes.new("FunctionNodeFloatToInt")
    as_int.location = (-260, 0)
    fmt = nodes.new("FunctionNodeFormatString")
    fmt.location = (-80, 0)
    fmt.format_items.new('INT', "frame")        # the {} slot; without this the node has no input
    fmt.inputs["Format"].default_value = f"{directory}/f_{{}}.ply"
    imp = nodes.new("GeometryNodeImportPLY")
    imp.location = (140, 0)
    links.new(time.outputs["Frame"], lo.inputs[0])
    links.new(lo.outputs[0], hi.inputs[0])
    links.new(hi.outputs[0], as_int.inputs[0])
    links.new(as_int.outputs[0], fmt.inputs["frame"])
    links.new(fmt.outputs["String"], imp.inputs["Path"])
    geo = imp.outputs[0]
    if as_points:
        # PLY has no vector type, so velocity arrived as three scalars: put it back together
        # (Cycles reads a "velocity" attribute for motion blur), then make real points.
        comb = nodes.new("ShaderNodeCombineXYZ")
        comb.location = (320, -220)
        for axis, socket in zip("xyz", ("X", "Y", "Z")):
            attr = nodes.new("GeometryNodeInputNamedAttribute")
            attr.data_type = 'FLOAT'
            attr.inputs["Name"].default_value = f"velocity_{axis}"
            attr.location = (140, -160 - 60 * "xyz".index(axis))
            links.new(attr.outputs["Attribute"], comb.inputs[socket])
        store = nodes.new("GeometryNodeStoreNamedAttribute")
        store.domain = 'POINT'
        store.data_type = 'FLOAT_VECTOR'
        store.inputs["Name"].default_value = "velocity"
        store.location = (360, 0)
        links.new(geo, store.inputs["Geometry"])
        links.new(comb.outputs[0], store.inputs["Value"])
        m2p = nodes.new("GeometryNodeMeshToPoints")
        m2p.location = (520, 0)
        m2p.inputs["Radius"].default_value = radius
        links.new(store.outputs["Geometry"], m2p.inputs["Mesh"])
        # Mesh to Points drops the material, so put it back (the modifier supplies it)
        tree.interface.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
        gin = nodes.new("NodeGroupInput")
        gin.location = (520, -260)
        setmat = nodes.new("GeometryNodeSetMaterial")
        setmat.location = (700, 0)
        links.new(m2p.outputs[0], setmat.inputs["Geometry"])
        links.new(gin.outputs["Material"], setmat.inputs["Material"])
        geo = setmat.outputs[0]
        out.location = (900, 0)
    elif smooth:
        shade = nodes.new("GeometryNodeSetShadeSmooth")
        shade.location = (360, 0)
        links.new(geo, shade.inputs["Geometry"])
        geo = shade.outputs[0]
    links.new(geo, out.inputs[0])
    return tree


def attach(obj, tree):
    """Put the reader group on the object as its Code Mesh Cache modifier."""
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None or mod.type != 'NODES':
        mod = obj.modifiers.new(MODIFIER_NAME, 'NODES')
        # play the cache before anything the user added
        while obj.modifiers[0] != mod:
            obj.modifiers.move(len(obj.modifiers) - 1, 0)
    mod.node_group = tree
    return mod


def detach(obj, remove_files=False, name=None):
    import bpy
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        tree = mod.node_group
        obj.modifiers.remove(mod)
        if tree is not None and tree.users <= 1:
            tree.use_fake_user = False
            bpy.data.node_groups.remove(tree)
    if remove_files:
        directory = cache_dir(name or obj.name, create=False)
        if os.path.isdir(directory):
            shutil.rmtree(directory, ignore_errors=True)


def is_baked(obj):
    mod = obj.modifiers.get(MODIFIER_NAME)
    return mod is not None and mod.type == 'NODES' and mod.node_group is not None


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
