"""Profiles, and the operations that turn them into solids.

A **profile** is a 2D path: straight runs, arcs, and curves given as maths. Sweeping one
of those through space makes a solid:

    revolve   spin it around an axis  — vases, lamp shades, bottles, bowls, columns
    extrude   push it along a line    — walls, plates, extrusions, letters

Unlike marching a distance field, this makes the mesh directly, so edges land exactly
where the maths says, corners stay sharp, quads follow the shape, and the UVs mean
something (along the profile, and around or along the sweep).

Pure numpy — no Blender — so it can be tested on its own.
"""

from __future__ import annotations

import math

import numpy as np

from .expr import ExprError, evaluate, number

MAX_POINTS = 100_000
MAX_FACES = 2_000_000
CORNER_ANGLE = math.radians(25.0)      # sharper than this counts as an edge, not a curve


class Profile:
    """A 2D path in the (x, y) plane. x is the radius when revolved."""

    def __init__(self):
        self.points = []        # [(x, y)]
        self.hard = []          # was this point placed as a corner?
        self.closed = False

    # -- building ---------------------------------------------------------------------
    def move(self, x, y):
        if self.points:
            raise ExprError("'move' can only start a profile; use line, arc or curve after that")
        self.points.append((float(x), float(y)))
        self.hard.append(True)
        return self

    def _here(self, what):
        if not self.points:
            raise ExprError(f"'{what}' needs a starting point: put a 'move' first")
        return self.points[-1]

    def line(self, x, y):
        self._here("line")
        self.points.append((float(x), float(y)))
        self.hard.append(True)
        return self

    def arc(self, x, y, radius, steps=16, clockwise=False):
        """A circular arc from where we are to (x, y), bulging by `radius`."""
        x0, y0 = self._here("arc")
        x1, y1 = float(x), float(y)
        dx, dy = x1 - x0, y1 - y0
        span = math.hypot(dx, dy)
        if span < 1e-12:
            raise ExprError("an arc needs to end somewhere else than it starts")
        radius = float(radius)
        if abs(radius) < span / 2 - 1e-9:
            raise ExprError(f"radius {radius:g} is too small to reach that point "
                            f"(needs at least {span / 2:g})")
        height = math.sqrt(max(abs(radius) ** 2 - (span / 2) ** 2, 0.0))
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        nx, ny = -dy / span, dx / span
        side = -1.0 if clockwise else 1.0
        cx, cy = mx + nx * height * side, my + ny * height * side
        a0 = math.atan2(y0 - cy, x0 - cx)
        a1 = math.atan2(y1 - cy, x1 - cx)
        if clockwise and a1 > a0:
            a1 -= math.tau
        if not clockwise and a1 < a0:
            a1 += math.tau
        steps = max(2, int(steps))
        for k in range(1, steps + 1):
            a = a0 + (a1 - a0) * k / steps
            self.points.append((cx + abs(radius) * math.cos(a), cy + abs(radius) * math.sin(a)))
            self.hard.append(k == steps)
        return self

    def curve(self, x_expr, y_expr, steps=24, scope=None):
        """A run of points from maths: x and y as functions of t, which goes 0 → 1."""
        steps = max(1, int(steps))
        t = np.linspace(0.0, 1.0, steps + 1)
        names = dict(scope or {})
        names["t"] = t
        xs = np.broadcast_to(np.asarray(evaluate(x_expr, names), dtype=float), t.shape)
        ys = np.broadcast_to(np.asarray(evaluate(y_expr, names), dtype=float), t.shape)
        if not (np.isfinite(xs).all() and np.isfinite(ys).all()):
            raise ExprError("the curve produced values that are not finite — check the maths")
        first = 1 if self.points else 0          # t=0 repeats where we already are
        if not self.points:
            self.points.append((float(xs[0]), float(ys[0])))
            self.hard.append(True)
        for k in range(max(first, 1), len(t)):
            self.points.append((float(xs[k]), float(ys[k])))
            self.hard.append(k == len(t) - 1)
        return self

    def close(self):
        self.closed = True
        return self

    # -- what came out -------------------------------------------------------------------
    def finish(self):
        """(points, sharp) with repeats removed. sharp marks a real corner."""
        if len(self.points) < 2:
            raise ExprError("a profile needs at least two points")
        pts, hard = [self.points[0]], [self.hard[0]]
        for p, h in zip(self.points[1:], self.hard[1:]):
            if math.dist(p, pts[-1]) > 1e-9:
                pts.append(p)
                hard.append(h)
            elif h:
                hard[-1] = True
        if self.closed and math.dist(pts[0], pts[-1]) < 1e-9:
            pts.pop()
            hard.pop()
        if len(pts) < 2:
            raise ExprError("a profile needs at least two points that aren't in the same place")
        if len(pts) > MAX_POINTS:
            raise ExprError(f"that profile has {len(pts):,} points; keep it under {MAX_POINTS:,}")
        arr = np.array(pts, dtype=np.float64)
        return arr, _corners(arr, np.array(hard, bool), self.closed)

    def length(self):
        pts, _ = self.finish()
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        return float(seg.sum())


def _corners(points, hard, closed):
    """A point is a corner if the path actually turns there, not just because it was typed."""
    n = len(points)
    sharp = np.zeros(n, bool)
    for i in range(n):
        if not hard[i]:
            continue
        prev = points[i - 1] if (i > 0 or closed) else None
        nxt = points[(i + 1) % n] if (i < n - 1 or closed) else None
        if prev is None or nxt is None:
            sharp[i] = True                      # an open end is always an edge
            continue
        a = points[i] - prev
        b = nxt - points[i]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na < 1e-12 or nb < 1e-12:
            continue
        angle = math.acos(float(np.clip(np.dot(a / na, b / nb), -1.0, 1.0)))
        sharp[i] = angle > CORNER_ANGLE
    return sharp


class Solid:
    """A mesh: vertices, quads, the triangles that cap the ends, UVs, and sharp edges."""

    __slots__ = ("verts", "faces", "tris", "uvs", "sharp")

    def __init__(self, verts, faces, uvs=None, sharp=None, tris=None):
        self.verts = np.asarray(verts, np.float32)
        self.faces = np.asarray(faces, np.int32).reshape(-1, 4)
        self.tris = np.zeros((0, 3), np.int32) if tris is None else np.asarray(tris, np.int32).reshape(-1, 3)
        self.uvs = None if uvs is None else np.asarray(uvs, np.float32)
        self.sharp = sharp if sharp is not None else set()   # {(a, b)} vertex-index pairs

    def __repr__(self):
        return f"Solid({len(self.verts)} verts, {len(self.faces)} quads, {len(self.tris)} tris)"

    @property
    def empty(self):
        return len(self.faces) == 0 and len(self.tris) == 0

    def polygons(self):
        """Every face as a tuple of vertex indices — quads first, then the caps."""
        return [tuple(int(v) for v in f) for f in self.faces] + \
               [tuple(int(v) for v in t) for t in self.tris]


def _check_size(rings, per_ring):
    if rings * per_ring > MAX_FACES:
        raise ExprError(f"that would make about {rings * per_ring:,} faces; "
                        f"lower the segments or steps (limit {MAX_FACES:,})")


def revolve(profile, segments=48, degrees=360.0, axis="z", cap=True):
    """Spin a profile around an axis. x becomes the radius, y goes along the axis."""
    points, sharp = profile.finish()
    segments = max(3, int(segments))
    degrees = float(degrees)
    if abs(degrees) < 1e-6:
        raise ExprError("revolve needs an angle to sweep through")
    full = abs(degrees) >= 359.999
    if (points[:, 0] < -1e-9).any():
        raise ExprError("a revolved profile can't have negative x: x is the distance from the axis")
    rings = segments if full else segments + 1
    _check_size(rings, len(points))

    angles = np.radians(np.linspace(0.0, degrees, rings, endpoint=not full))
    radius = points[:, 0][None, :]
    height = points[:, 1][None, :]
    ca, sa = np.cos(angles)[:, None], np.sin(angles)[:, None]
    x, y, z = radius * ca, radius * sa, np.broadcast_to(height, (rings, len(points)))
    verts = _orient(np.stack([x, y, z], axis=-1).reshape(-1, 3), axis)

    # distance along the profile, for a v coordinate that doesn't stretch
    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
    if profile.closed:
        along = np.concatenate([along, [along[-1] + np.linalg.norm(points[0] - points[-1])]])[:len(points)]
    v = along / (along[-1] or 1.0)
    u = np.abs(angles) / (math.radians(abs(degrees)) or 1.0)

    faces, uvs, sharp_edges = _stitch(rings, len(points), u, v,
                                      close_rings=full, close_profile=profile.closed,
                                      sharp_along=sharp)
    solid = Solid(verts, faces, uvs, sharp_edges)
    if cap and not profile.closed and not full:
        _add_flat_caps(solid, rings, len(points))
    return solid


def extrude(profile, depth=1.0, steps=1, axis="z", cap=True, taper=1.0):
    """Push a profile along an axis. A closed profile gets flat caps."""
    points, sharp = profile.finish()
    depth = float(depth)
    steps = max(1, int(steps))
    rings = steps + 1
    _check_size(rings, len(points))

    t = np.linspace(0.0, 1.0, rings)
    scale = 1.0 + (float(taper) - 1.0) * t
    xy = points[None, :, :] * scale[:, None, None]
    z = (t * depth)[:, None]
    verts = _orient(np.stack([xy[..., 0], xy[..., 1],
                              np.broadcast_to(z, xy.shape[:2])], axis=-1).reshape(-1, 3), axis)

    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
    v = along / (along[-1] or 1.0)
    faces, uvs, sharp_edges = _stitch(rings, len(points), t, v,
                                      close_rings=False, close_profile=profile.closed,
                                      sharp_along=sharp)
    solid = Solid(verts, faces, uvs, sharp_edges)
    if cap and profile.closed:
        _add_fan_caps(solid, rings, len(points))
    return solid


def _orient(verts, axis):
    """The sweep is built along +Z; turn it to face another way if asked."""
    axis = str(axis).lower()
    if axis in ("z", "+z"):
        return verts
    x, y, z = verts[:, 0], verts[:, 1], verts[:, 2]
    if axis in ("y", "+y"):
        return np.stack([x, -z, y], axis=-1)
    if axis in ("x", "+x"):
        return np.stack([z, y, -x], axis=-1)
    raise ExprError(f"axis must be x, y or z (got {axis!r})")


def _stitch(rings, count, u, v, close_rings, close_profile, sharp_along):
    """Quads between consecutive rings, with UVs and the edges that stay sharp."""
    ring_pairs = rings if close_rings else rings - 1
    prof_pairs = count if close_profile else count - 1
    a = np.arange(rings)[:, None]
    b = np.arange(count)[None, :]
    idx = (a * count + b)

    r0 = np.arange(ring_pairs)[:, None]
    r1 = (r0 + 1) % rings
    p0 = np.arange(prof_pairs)[None, :]
    p1 = (p0 + 1) % count
    faces = np.stack([idx[r0, p0], idx[r1, p0], idx[r1, p1], idx[r0, p1]], axis=-1).reshape(-1, 4)

    uv_ring = np.repeat(u[:, None], count, axis=1)
    uv_prof = np.repeat(v[None, :], rings, axis=0)
    corner_uv = np.stack([uv_ring, uv_prof], axis=-1)
    uvs = np.stack([corner_uv[r0, p0], corner_uv[r1, p0], corner_uv[r1, p1], corner_uv[r0, p1]],
                   axis=-2).reshape(-1, 4, 2)

    sharp = set()
    for j in np.nonzero(sharp_along)[0]:          # the corner runs the length of the sweep
        for r in range(ring_pairs):
            sharp.add(_pair(int(idx[r, j]), int(idx[(r + 1) % rings, j])))
    return faces, uvs, sharp


def _pair(a, b):
    return (a, b) if a < b else (b, a)


def _fan(solid, ring, wrap):
    """Fill a ring of vertices with triangles around a new centre point."""
    centre = len(solid.verts)
    solid.verts = np.vstack([solid.verts, solid.verts[ring].mean(axis=0)[None, :]])
    a = ring if wrap else ring[:-1]
    b = np.roll(ring, -1) if wrap else ring[1:]
    solid.tris = np.vstack([solid.tris,
                            np.stack([np.full(len(a), centre), a, b], axis=-1)])
    solid.uvs = None            # the caps need their own layout; drop rather than lie
    for i, j in zip(a, b):
        solid.sharp.add(_pair(int(i), int(j)))


def _add_flat_caps(solid, rings, count):
    """Close the two flat ends of a part-turn revolve."""
    _fan(solid, np.arange(count), wrap=False)
    _fan(solid, (np.arange(count) + (rings - 1) * count)[::-1], wrap=False)


def _add_fan_caps(solid, rings, count):
    """Close a closed profile at both ends of an extrusion."""
    _fan(solid, np.arange(count)[::-1], wrap=True)
    _fan(solid, np.arange(count) + (rings - 1) * count, wrap=True)


def join(solids):
    """Put several solids into one mesh (no boolean — they just sit together)."""
    solids = [s for s in solids if s is not None and not s.empty]
    if not solids:
        return Solid(np.zeros((0, 3), np.float32), np.zeros((0, 4), np.int32))
    verts, faces, tris, sharp = [], [], [], set()
    uvs = [] if all(s.uvs is not None for s in solids) else None
    offset = 0
    for s in solids:
        verts.append(s.verts)
        faces.append(s.faces + offset)
        tris.append(s.tris + offset)
        if uvs is not None:
            uvs.append(s.uvs)
        for a, b in s.sharp:
            sharp.add(_pair(a + offset, b + offset))
        offset += len(s.verts)
    return Solid(np.vstack(verts), np.vstack(faces),
                 np.vstack(uvs) if uvs else None, sharp, np.vstack(tris))


def transform(solid, move=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1)):
    """Move, turn and scale a solid. Rotation is degrees, applied X then Y then Z."""
    verts = solid.verts.astype(np.float64) * np.asarray(scale, float)
    rx, ry, rz = (math.radians(number(a, name="rotation")) for a in rotate)
    for angle, (i, j) in ((rx, (1, 2)), (ry, (2, 0)), (rz, (0, 1))):
        if abs(angle) < 1e-12:
            continue
        c, s = math.cos(angle), math.sin(angle)
        a, b = verts[:, i].copy(), verts[:, j].copy()
        verts[:, i], verts[:, j] = a * c - b * s, a * s + b * c
    verts += np.asarray(move, float)
    flipped = np.prod(np.asarray(scale, float)) < 0
    faces = solid.faces[:, ::-1] if flipped else solid.faces
    tris = solid.tris[:, ::-1] if flipped else solid.tris
    return Solid(verts, faces, solid.uvs, set(solid.sharp), tris)


def array(solid, count, move=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), around=None):
    """Repeat a solid: in a line, or `around` an axis by equal turns."""
    count = int(count)
    if count < 1:
        raise ExprError("an array needs a count of at least 1")
    each = len(solid.faces) + len(solid.tris)
    if count * max(each, 1) > MAX_FACES:
        raise ExprError(f"{count} copies would be about {count * each:,} faces")
    copies = []
    for i in range(count):
        if around is not None:
            copies.append(transform(solid, rotate=(0, 0, 360.0 * i / count) if str(around).lower() == "z"
                                    else ((360.0 * i / count, 0, 0) if str(around).lower() == "x"
                                          else (0, 360.0 * i / count, 0))))
        else:
            copies.append(transform(solid,
                                    move=tuple(m * i for m in move),
                                    rotate=tuple(r * i for r in rotate),
                                    scale=tuple(s ** i for s in scale)))
    return join(copies)
