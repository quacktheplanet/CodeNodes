"""Code -> Mesh pipeline: sample on the GPU, mesh with surface nets, swap the
result into a Blender object. Failures raise SdfCodeError and leave the object
as it was."""

from __future__ import annotations

import time

import numpy as np

from . import mesher, sampler
from .sdf_code import SdfCodeError

MAX_FACES = 4_000_000


def compute(source, lo, hi, resolution, time_s=0.0, frame=0.0, values=None):
    """(MeshResult, stats). No Blender data is touched."""
    t0 = time.perf_counter()
    vol, stats = sampler.sample(source, lo, hi, resolution, time_s, frame, values)
    t1 = time.perf_counter()
    try:
        result = mesher.surface_nets(vol, lo, hi, max_faces=MAX_FACES)
    except ValueError as exc:
        raise SdfCodeError(str(exc)) from exc
    stats.update(sample_s=t1 - t0, mesh_s=time.perf_counter() - t1,
                 verts=len(result.verts), faces=len(result.quads))
    return result, stats


def make_mesh(name, result, smooth=True):
    import bpy
    me = bpy.data.meshes.new(name)
    v, f = len(result.verts), len(result.quads)
    if f:
        me.vertices.add(v)
        me.vertices.foreach_set("co", result.verts.ravel())
        me.loops.add(4 * f)
        me.loops.foreach_set("vertex_index", result.quads.ravel())
        me.polygons.add(f)
        me.polygons.foreach_set("loop_start", np.arange(0, 4 * f, 4, dtype=np.int32))
        me.update(calc_edges=True)
        if smooth:
            me.shade_smooth()
    return me


def swap_mesh(obj, result, smooth=True):
    """Give `obj` a new mesh built from `result`, keeping its materials."""
    import bpy
    if obj.type != 'MESH':
        raise SdfCodeError(f"'{obj.name}' is not a mesh object")
    if obj.mode == 'EDIT':
        raise SdfCodeError(f"'{obj.name}' is in Edit Mode; leave Edit Mode to rebuild it")
    old = obj.data
    me = make_mesh(old.name, result, smooth)
    for mat in old.materials:
        me.materials.append(mat)
    obj.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return me
