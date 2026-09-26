"""Surface nets mesher: pure numpy, no Blender.

    python tests/test_mesher.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "codenodes"))
import mesher  # noqa: E402

_checks = 0


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        raise SystemExit(1)
    print(f"  ok: {msg}")


def grid(n, lo=-1.5, hi=1.5):
    g = np.linspace(lo, hi, n, dtype=np.float32)
    z, y, x = np.meshgrid(g, g, g, indexing="ij")
    return x, y, z, (lo,) * 3, (hi,) * 3


def edge_use(quads):
    """How many times each undirected edge is used, and whether directed edges are unique."""
    e = np.stack([quads, np.roll(quads, -1, axis=1)], axis=2).reshape(-1, 2)
    und = np.sort(e, axis=1)
    _, counts = np.unique(und, axis=0, return_counts=True)
    directed_unique = len(np.unique(e, axis=0)) == len(e)
    return counts, directed_unique


def face_normals(verts, quads):
    p = verts[quads]
    n = np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1])
    return n, p.mean(axis=1)


def main():
    # Sphere
    x, y, z, lo, hi = grid(64)
    m = mesher.surface_nets(np.sqrt(x * x + y * y + z * z) - 1.0, lo, hi)
    r = np.linalg.norm(m.verts, axis=1)
    counts, directed_unique = edge_use(m.quads)
    n, c = face_normals(m.verts, m.quads)
    outward = (np.einsum("ij,ij->i", n, c) > 0).mean()
    check(len(m.quads) > 1000, f"sphere has faces ({len(m.quads)})")
    check(np.abs(r - 1.0).max() < 0.02, f"sphere vertices on the surface (max radius error {np.abs(r - 1).max():.4f})")
    check((counts == 2).all(), "sphere is watertight (every edge shared by exactly 2 faces)")
    check(directed_unique, "sphere winding is consistent")
    check(outward == 1.0, f"sphere normals face outward ({outward:.1%})")
    V, F = len(m.verts), len(m.quads)
    E = len(counts)
    check(V - E + F == 2, f"sphere Euler characteristic is 2 (got {V - E + F})")

    # Torus: one hole -> Euler characteristic 0
    q = np.sqrt(x * x + y * y) - 0.9
    m = mesher.surface_nets(np.sqrt(q * q + z * z) - 0.35, lo, hi)
    counts, directed_unique = edge_use(m.quads)
    V, E, F = len(m.verts), len(counts), len(m.quads)
    check((counts == 2).all() and directed_unique, "torus is watertight and consistently wound")
    check(V - E + F == 0, f"torus Euler characteristic is 0 (got {V - E + F})")

    # Box: sharp edges still watertight, normals outward
    d = np.maximum(np.maximum(np.abs(x), np.abs(y)), np.abs(z)) - 0.8
    m = mesher.surface_nets(d, lo, hi)
    counts, _ = edge_use(m.quads)
    n, c = face_normals(m.verts, m.quads)
    check((counts == 2).all(), "box is watertight")
    check((np.einsum("ij,ij->i", n, c) > 0).all(), "box normals face outward")

    # Non-cubic bounds and grid: world coordinates follow the bounds per axis
    gx = np.linspace(-2, 2, 41, dtype=np.float32)
    gy = np.linspace(-1, 1, 21, dtype=np.float32)
    gz = np.linspace(0, 1, 11, dtype=np.float32)
    zz, yy, xx = np.meshgrid(gz, gy, gx, indexing="ij")
    ell = np.sqrt((xx / 1.5) ** 2 + (yy / 0.7) ** 2 + ((zz - 0.5) / 0.35) ** 2) - 1.0
    m = mesher.surface_nets(ell, (-2, -1, 0), (2, 1, 1))
    ext = m.verts.max(axis=0) - m.verts.min(axis=0)
    check(np.allclose(ext, [3.0, 1.4, 0.7], atol=0.12), f"non-cubic grid maps to world bounds (extent {np.round(ext, 2)})")

    # Robustness
    check(mesher.surface_nets(np.ones((8, 8, 8)), lo, hi).empty, "all-outside grid gives an empty mesh")
    check(mesher.surface_nets(-np.ones((8, 8, 8)), lo, hi).empty, "all-inside grid gives an empty mesh")
    x, y, z, lo, hi = grid(32)
    v = np.sqrt(x * x + y * y + z * z) - 1.0
    v[0, 0, 0] = np.nan
    v[5, 5, 5] = np.inf
    m = mesher.surface_nets(v, lo, hi)
    check(np.isfinite(m.verts).all() and len(m.quads) > 0, "NaN / inf samples are ignored safely")
    big = np.sqrt(x * x + y * y + z * z) - 2.0          # sphere larger than the bounds
    m = mesher.surface_nets(big, lo, hi)
    check(np.isfinite(m.verts).all() and (m.quads < len(m.verts)).all(), "surface cut by the bounds still gives valid indices")
    try:
        mesher.surface_nets(np.sqrt(x * x + y * y + z * z) - 1.0, lo, hi, max_faces=100)
        check(False, "face limit is enforced")
    except ValueError as e:
        check("more than 100" in str(e), "face limit is enforced with a clear message")
    try:
        mesher.surface_nets(np.zeros((1, 5, 5)), lo, hi)
        check(False, "too-small grid is rejected")
    except ValueError:
        check(True, "too-small grid is rejected")

    print(f"\nAll {_checks} checks passed.")


if __name__ == "__main__":
    main()
