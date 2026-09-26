"""Putting a built Solid into Blender: quads and caps, UVs, and sharp edges.

Kept apart from `shapes/` so that package stays free of bpy and can be tested on its own.
"""

from __future__ import annotations

import numpy as np

from .shapes import ShapeError, parse


def set_uvs(layer, coords):
    """Write UVs, whichever spelling this Blender uses ('vector' in 5.x, 'uv' before)."""
    flat = np.ascontiguousarray(coords, np.float32).ravel()
    for holder, attr in ((getattr(layer, "uv", None), "vector"), (layer.data, "uv")):
        if holder is None:
            continue
        try:
            holder.foreach_set(attr, flat)
            return True
        except (AttributeError, TypeError, RuntimeError):
            continue
    return False


def get_uvs(layer, count):
    flat = np.empty(count * 2, np.float32)
    for holder, attr in ((getattr(layer, "uv", None), "vector"), (layer.data, "uv")):
        if holder is None:
            continue
        try:
            holder.foreach_get(attr, flat)
            return flat.reshape(-1, 2)
        except (AttributeError, TypeError, RuntimeError):
            continue
    return np.zeros((count, 2), np.float32)


def fill_mesh(me, solid, smooth=True, uv_name="UVMap"):
    """Replace a mesh datablock's geometry with a Solid, in place."""
    me.clear_geometry()
    if solid.empty:
        me.update()
        return me
    polygons = solid.polygons()
    me.from_pydata([tuple(v) for v in solid.verts.astype(float)], [], polygons)
    me.validate(verbose=False)

    if solid.uvs is not None and len(solid.uvs) == len(solid.faces):
        layer = me.uv_layers.get(uv_name) or me.uv_layers.new(name=uv_name)
        coords = np.zeros((len(me.loops), 2), np.float32)
        quad_loops = 4 * len(solid.faces)
        coords[:quad_loops] = solid.uvs.reshape(-1, 2)[:quad_loops]
        set_uvs(layer, coords)

    if smooth:
        me.shade_smooth()
    if solid.sharp:
        index = {}
        for edge in me.edges:
            a, b = edge.vertices
            index[(a, b) if a < b else (b, a)] = edge
        for pair in solid.sharp:
            edge = index.get(pair)
            if edge is not None:
                edge.use_edge_sharp = True
    me.update()
    return me


def build(source, values=None):
    """Text -> Solid. Raises ShapeError with a line number."""
    return parse(source).build(values or {})


def params_of(source):
    """The sliders a shape declares, as (name, default, min, max)."""
    return parse(source).params


def stats(solid):
    size = (solid.verts.max(axis=0) - solid.verts.min(axis=0)) if len(solid.verts) else np.zeros(3)
    return {"verts": len(solid.verts), "quads": len(solid.faces), "tris": len(solid.tris),
            "sharp_edges": len(solid.sharp), "size": [round(float(v), 4) for v in size]}


__all__ = ["fill_mesh", "build", "params_of", "stats", "ShapeError"]
