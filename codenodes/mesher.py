"""Signed-distance grid -> quad mesh (surface nets). Pure numpy, no bpy.

The grid is ``vol[z, y, x]``: negative inside, positive outside. Surface nets
puts one vertex in every cell the surface passes through (the average of where
the surface crosses the cell's edges) and one quad across every grid edge that
changes sign. The result is a watertight quad mesh wherever the surface is
closed inside the bounds, with outward-facing normals.
"""

from __future__ import annotations

import numpy as np

# The 8 corners of a cell as (dz, dy, dx), and its 12 edges as corner pairs.
_CORNERS = np.array([(z, y, x) for z in (0, 1) for y in (0, 1) for x in (0, 1)])
_EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8)
          if np.abs(_CORNERS[a] - _CORNERS[b]).sum() == 1]


class MeshResult:
    __slots__ = ("verts", "quads")

    def __init__(self, verts, quads):
        self.verts = verts    # (V, 3) float32, world space
        self.quads = quads    # (F, 4) int32, counter-clockwise seen from outside

    @property
    def empty(self):
        return len(self.quads) == 0


def surface_nets(vol, lo, hi, max_faces=None):
    """Mesh the zero level set of ``vol`` sampled on a regular grid from ``lo`` to ``hi``.

    ``lo``/``hi`` are the world positions (x, y, z) of the first and last samples.
    Non-finite samples count as outside. Raises ValueError if the result would
    exceed ``max_faces``.
    """
    vol = np.asarray(vol, dtype=np.float32)
    if vol.ndim != 3 or min(vol.shape) < 2:
        raise ValueError(f"need a 3D grid at least 2 samples per side, got shape {vol.shape}")
    vol = np.where(np.isfinite(vol), vol, np.float32(1e6))
    nz, ny, nx = vol.shape
    inside = vol < 0

    # Cells with mixed corner signs hold a vertex.
    any_in = np.zeros((nz - 1, ny - 1, nx - 1), bool)
    all_in = np.ones_like(any_in)
    for dz, dy, dx in _CORNERS:
        c = inside[dz:nz - 1 + dz, dy:ny - 1 + dy, dx:nx - 1 + dx]
        any_in |= c
        all_in &= c
    active = any_in & ~all_in
    cz, cy, cx = np.nonzero(active)
    if len(cz) == 0:
        return MeshResult(np.zeros((0, 3), np.float32), np.zeros((0, 4), np.int32))

    # Vertex = mean of the edge crossings in its cell (in grid index units).
    corner_pos = []
    corner_val = []
    for dz, dy, dx in _CORNERS:
        corner_pos.append(np.stack([cx + dx, cy + dy, cz + dz], axis=1).astype(np.float32))
        corner_val.append(vol[cz + dz, cy + dy, cx + dx])
    acc = np.zeros((len(cz), 3), np.float32)
    cnt = np.zeros(len(cz), np.float32)
    for a, b in _EDGES:
        va, vb = corner_val[a], corner_val[b]
        crosses = (va < 0) != (vb < 0)
        denom = np.where(crosses, va - vb, 1.0)
        t = np.clip(np.where(crosses, va / denom, 0.0), 0.0, 1.0)[:, None]
        acc += np.where(crosses[:, None], corner_pos[a] + t * (corner_pos[b] - corner_pos[a]), 0.0)
        cnt += crosses
    verts_idx = acc / np.maximum(cnt, 1)[:, None]

    lo = np.asarray(lo, np.float32)
    hi = np.asarray(hi, np.float32)
    step = (hi - lo) / np.array([nx - 1, ny - 1, nz - 1], np.float32)
    verts = lo + verts_idx * step

    # Look up a cell's vertex by its linear index.
    cell_lin = (cz * (ny - 1) + cy) * (nx - 1) + cx          # sorted (np.nonzero is row-major)

    def vid(z, y, x):
        return np.searchsorted(cell_lin, (z * (ny - 1) + y) * (nx - 1) + x).astype(np.int32)

    quads = []
    # One quad per sign-changing grid edge, joining the 4 cells around it.
    # axis 0 = x-edges, 1 = y-edges, 2 = z-edges.
    for axis in range(3):
        if axis == 0:
            a, b = inside[1:-1, 1:-1, :-1], inside[1:-1, 1:-1, 1:]
        elif axis == 1:
            a, b = inside[1:-1, :-1, 1:-1], inside[1:-1, 1:, 1:-1]
        else:
            a, b = inside[:-1, 1:-1, 1:-1], inside[1:, 1:-1, 1:-1]
        ez, ey, ex = np.nonzero(a != b)
        if len(ez) == 0:
            continue
        flip = a[ez, ey, ex]          # inside at the low end -> surface faces +axis
        if axis == 0:
            z, y, x = ez + 1, ey + 1, ex
            ring = [vid(z - 1, y - 1, x), vid(z - 1, y, x), vid(z, y, x), vid(z, y - 1, x)]
        elif axis == 1:
            z, y, x = ez + 1, ey, ex + 1
            ring = [vid(z - 1, y, x - 1), vid(z, y, x - 1), vid(z, y, x), vid(z - 1, y, x)]
        else:
            z, y, x = ez, ey + 1, ex + 1
            ring = [vid(z, y - 1, x - 1), vid(z, y - 1, x), vid(z, y, x), vid(z, y, x - 1)]
        q = np.stack(ring, axis=1)
        q[~flip] = q[~flip][:, ::-1]
        quads.append(q)
        if max_faces is not None and sum(len(k) for k in quads) > max_faces:
            raise ValueError(f"mesh would have more than {max_faces:,} faces; "
                             "lower the resolution or shrink the bounds")

    quads = np.concatenate(quads) if quads else np.zeros((0, 4), np.int32)
    return MeshResult(verts.astype(np.float32), quads.astype(np.int32))
