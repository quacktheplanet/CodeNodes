"""A tiny kit language for parametric parts: boxes, cylinders and cones, grouped, turned and
repeated, with sizes written as formulas of named parameters.

One description, two outputs:

    mesh(part, values)   -> plain vertices, faces and a material per face (no Blender), so
                            generators can be measured and tested anywhere
    to_graph(gen)        -> a Geometry Nodes group whose inputs are the parameters, so the
                            same part ships as an editable node-group asset

    p = Params(length=(4.0, 0.5, 30.0, "along the belt"))
    part = Group([
        Box((p.length, 0.6, 0.1), at=(0, 0, 0.8), mat="steel"),
        Array(Cyl(0.03, 0.6, rot=(90, 0, 0), mat="roller"), count=floor(p.length / 0.15),
              step=(0.15, 0, 0), at=(-p.length / 2 + 0.075, 0, 0.85)),
    ])

Angles are in degrees. A part's own origin is its centre (boxes, cylinders) and `at` / `rot`
place it in its parent: rotation first (X, then Y, then Z, like Blender's XYZ Euler), then
the move. Nothing here needs Blender.
"""

from __future__ import annotations

import math

DEG = math.pi / 180.0


# ---- formulas -------------------------------------------------------------------------------

class E:
    """A formula over named parameters, evaluated with values or turned into math nodes."""
    __slots__ = ("op", "args")

    def __init__(self, op, *args):
        self.op, self.args = op, args

    # arithmetic builds new formulas
    def __add__(self, o): return E("ADD", self, o)
    def __radd__(self, o): return E("ADD", o, self)
    def __sub__(self, o): return E("SUBTRACT", self, o)
    def __rsub__(self, o): return E("SUBTRACT", o, self)
    def __mul__(self, o): return E("MULTIPLY", self, o)
    def __rmul__(self, o): return E("MULTIPLY", o, self)
    def __truediv__(self, o): return E("DIVIDE", self, o)
    def __rtruediv__(self, o): return E("DIVIDE", o, self)
    def __neg__(self): return E("MULTIPLY", self, -1.0)

    def __repr__(self):
        if self.op == "PARAM":
            return self.args[0]
        return f"{self.op.lower()}({', '.join(map(repr, self.args))})"


def param(name):
    return E("PARAM", name)


def floor(x): return E("FLOOR", x)
def maximum(a, b): return E("MAXIMUM", a, b)
def minimum(a, b): return E("MINIMUM", a, b)
def sin(x): return E("SINE", x)
def cos(x): return E("COSINE", x)


def value(x, values):
    """A number from a formula (or a plain number)."""
    if not isinstance(x, E):
        return float(x)
    if x.op == "PARAM":
        return float(values[x.args[0]])
    a = [value(v, values) for v in x.args]
    op = x.op
    if op == "ADD": return a[0] + a[1]
    if op == "SUBTRACT": return a[0] - a[1]
    if op == "MULTIPLY": return a[0] * a[1]
    if op == "DIVIDE": return a[0] / a[1] if a[1] != 0 else 0.0   # like the Math node
    if op == "FLOOR": return float(math.floor(a[0] + 1e-9))
    if op == "MAXIMUM": return max(a)
    if op == "MINIMUM": return min(a)
    if op == "SINE": return math.sin(a[0])
    if op == "COSINE": return math.cos(a[0])
    raise ValueError(f"unknown formula operation {op}")


def uses_params(x):
    if not isinstance(x, E):
        return False
    return x.op == "PARAM" or any(uses_params(a) for a in x.args)


class Params:
    """Named parameters with defaults and ranges: p.length is the formula for `length`."""

    def __init__(self, **spec):
        self.spec = {}
        for name, item in spec.items():
            item = tuple(item) + (None,) * (4 - len(item))
            default, lo, hi, about = item[:4]
            self.spec[name] = {"default": float(default), "min": lo, "max": hi, "about": about or ""}

    def __getattr__(self, name):
        if name.startswith("_") or name == "spec":
            raise AttributeError(name)
        if name not in self.spec:
            raise AttributeError(f"no parameter called {name!r}")
        return param(name)

    def defaults(self):
        return {k: v["default"] for k, v in self.spec.items()}

    def resolve(self, values=None):
        """Defaults overlaid with `values`, clamped to the ranges. Unknown names are errors."""
        out = self.defaults()
        for k, v in (values or {}).items():
            if k not in self.spec:
                raise KeyError(f"no parameter called {k!r}; there is {', '.join(self.spec)}")
            s = self.spec[k]
            v = float(v)
            if s["min"] is not None:
                v = max(v, s["min"])
            if s["max"] is not None:
                v = min(v, s["max"])
            out[k] = v
        return out


# ---- parts ----------------------------------------------------------------------------------

def _vec(v):
    return tuple(v) if v is not None else (0.0, 0.0, 0.0)


class Part:
    def __init__(self, at=None, rot=None, mat=None):
        self.at, self.rot, self.mat = _vec(at), _vec(rot), mat


class Box(Part):
    def __init__(self, size, at=None, rot=None, mat=None):
        super().__init__(at, rot, mat)
        self.size = tuple(size)


class Cyl(Part):
    """A cylinder along its own Z, centred."""

    def __init__(self, radius, depth, at=None, rot=None, mat=None, segments=16):
        super().__init__(at, rot, mat)
        self.radius, self.depth, self.segments = radius, depth, int(segments)


class Cone(Part):
    """A cone or frustum along its own Z, centred; radius2 is the top (a plain 0 makes a
    point, as the Cone node does)."""

    def __init__(self, radius1, radius2, depth, at=None, rot=None, mat=None, segments=16):
        super().__init__(at, rot, mat)
        self.radius1, self.radius2, self.depth, self.segments = radius1, radius2, depth, int(segments)


class Group(Part):
    def __init__(self, parts, at=None, rot=None):
        super().__init__(at, rot)
        self.parts = list(parts)


class Array(Part):
    """`count` copies of a part (count is floored), each moved one more `step`."""

    def __init__(self, part, count, step, at=None, rot=None):
        super().__init__(at, rot)
        self.part, self.count, self.step = part, count, tuple(step)


# ---- plain mesh -----------------------------------------------------------------------------

def _euler_matrix(rx, ry, rz):
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry),
                              math.cos(rz), math.sin(rz))
    # Rz @ Ry @ Rx, Blender's XYZ order
    return ((cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
            (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
            (-sy, cy * sx, cy * cx))


def _apply(m, t, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + t[0],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + t[1],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + t[2])


class Mesh:
    def __init__(self):
        self.verts, self.faces, self.mats = [], [], []

    def add(self, verts, faces, mat):
        base = len(self.verts)
        self.verts.extend(verts)
        self.faces.extend(tuple(i + base for i in f) for f in faces)
        self.mats.extend([mat or "default"] * len(faces))

    def bounds(self):
        if not self.verts:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs, ys, zs = zip(*self.verts)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def materials(self):
        seen = []
        for m in self.mats:
            if m not in seen:
                seen.append(m)
        return seen


def _box(sx, sy, sz):
    x, y, z = sx / 2, sy / 2, sz / 2
    v = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
         (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return v, f


def _frustum(r1, r2, depth, n):
    h = depth / 2
    v = [(r1 * math.cos(2 * math.pi * i / n), r1 * math.sin(2 * math.pi * i / n), -h) for i in range(n)]
    f = [tuple(reversed(range(n)))]
    if r2 <= 1e-9:
        v.append((0.0, 0.0, h))
        f += [(i, (i + 1) % n, n) for i in range(n)]
    else:
        v += [(r2 * math.cos(2 * math.pi * i / n), r2 * math.sin(2 * math.pi * i / n), h) for i in range(n)]
        f.append(tuple(range(n, 2 * n)))
        f += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    return v, f


def mesh(part, values, out=None, m=None, t=None):
    """The part as a Mesh, with every formula evaluated at `values`."""
    out = out if out is not None else Mesh()
    ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    m = m or ident
    t = t or (0.0, 0.0, 0.0)
    ev = lambda x: value(x, values)          # noqa: E731
    # this part's own placement in its parent
    lm = _euler_matrix(*(ev(a) * DEG for a in part.rot))
    lt = tuple(ev(a) for a in part.at)
    wm = tuple(tuple(sum(m[i][k] * lm[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    wt = _apply(m, t, lt)
    if isinstance(part, Group):
        for child in part.parts:
            mesh(child, values, out, wm, wt)
    elif isinstance(part, Array):
        n = int(max(0.0, math.floor(ev(part.count) + 1e-9)))
        step = tuple(ev(a) for a in part.step)
        for k in range(n):
            ct = _apply(wm, wt, tuple(s * k for s in step))
            mesh(part.part, values, out, wm, ct)
    else:
        if isinstance(part, Box):
            v, f = _box(*(ev(a) for a in part.size))
        elif isinstance(part, Cyl):
            r = ev(part.radius)
            v, f = _frustum(r, r, ev(part.depth), part.segments)
        elif isinstance(part, Cone):
            top = 0.0 if not uses_params(part.radius2) and float(part.radius2) <= 0 else max(ev(part.radius2), 1e-6)
            v, f = _frustum(ev(part.radius1), top, ev(part.depth), part.segments)
        else:
            raise TypeError(f"not a part: {part!r}")
        out.add([_apply(wm, wt, p) for p in v], f, part.mat)
    return out


# ---- Geometry Nodes -------------------------------------------------------------------------

class _Emitter:
    def __init__(self, g, inputs, materials):
        self.g, self.inputs, self.materials = g, inputs, materials
        self.cache = {}

    def num(self, x):
        """A float, or the socket computing it."""
        if not isinstance(x, E):
            return float(x)
        if not uses_params(x):
            return value(x, {})
        key = repr(x)
        if key in self.cache:
            return self.cache[key]
        if x.op == "PARAM":
            out = self.inputs[x.args[0]]
        else:
            args = [self.num(a) for a in x.args]
            if all(isinstance(a, float) for a in args):
                out = value(x, {})
            elif x.op == "FLOOR":
                # a hair above, like the plain mesh, so 2.9999 from float maths floors to 3
                out = self.g.math("FLOOR", self.g.math("ADD", args[0], 1e-6))
            else:
                out = self.g.math(x.op, *args)
        self.cache[key] = out
        return out

    def vec(self, v):
        parts = [self.num(a) for a in v]
        if all(isinstance(a, float) for a in parts):
            return tuple(parts)
        return self.g.xyz(*parts)

    def rot(self, v):
        return self.vec(tuple(a * DEG if not isinstance(a, E) else a * DEG for a in v))

    def place(self, geo, part):
        moved = any(isinstance(a, E) or a for a in part.at) or any(isinstance(a, E) or a for a in part.rot)
        if not moved:
            return geo
        return self.g.node("GeometryNodeTransform",
                           {"Geometry": geo, "Translation": self.vec(part.at),
                            "Rotation": self.rot(part.rot)})["Geometry"]

    def material(self, geo, part):
        if part.mat and part.mat in self.materials:
            return self.g.node("GeometryNodeSetMaterial",
                               {"Geometry": geo, "Material": self.materials[part.mat]})["Geometry"]
        return geo

    def emit(self, part):
        g = self.g
        if isinstance(part, Group):
            geos = [self.emit(c) for c in part.parts]
            geo = g.join(*geos) if len(geos) > 1 else geos[0]
        elif isinstance(part, Array):
            child = self.emit(part.part)
            count = self.num(floor(part.count) if isinstance(part.count, E) else math.floor(part.count))
            line = g.node("GeometryNodeMeshLine", {"Count": count, "Start Location": (0.0, 0.0, 0.0),
                                                   "Offset": self.vec(part.step)},
                          mode="OFFSET")["Mesh"]
            inst = g.node("GeometryNodeInstanceOnPoints", {"Points": line, "Instance": child})["Instances"]
            geo = g.node("GeometryNodeRealizeInstances", {"Geometry": inst})["Geometry"]
        elif isinstance(part, Box):
            geo = self.material(g.node("GeometryNodeMeshCube", {"Size": self.vec(part.size)})["Mesh"], part)
        elif isinstance(part, Cyl):
            geo = self.material(g.node("GeometryNodeMeshCylinder",
                                       {"Vertices": part.segments, "Radius": self.num(part.radius),
                                        "Depth": self.num(part.depth)}, fill_type="NGON")["Mesh"], part)
        elif isinstance(part, Cone):
            geo = self.material(g.node("GeometryNodeMeshCone",
                                       {"Vertices": part.segments, "Radius Bottom": self.num(part.radius1),
                                        "Radius Top": self.num(part.radius2), "Depth": self.num(part.depth)},
                                       fill_type="NGON")["Mesh"], part)
        else:
            raise TypeError(f"not a part: {part!r}")
        return self.place(geo, part)


def _builder():
    """gn.builder needs no Blender, but its package imports modules that do: load the file
    on its own when bpy is missing (tests)."""
    try:
        from ..gn import builder
        return builder
    except ImportError:
        import importlib.util
        import os
        import sys
        name = __package__.rsplit(".", 1)[0] + ".gn_builder_standalone"
        if name not in sys.modules:
            path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gn", "builder.py")
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules[name] = mod
        return sys.modules[name]


def materials_of(part, found=None):
    found = found if found is not None else []
    if isinstance(part, Group):
        for c in part.parts:
            materials_of(c, found)
    elif isinstance(part, Array):
        materials_of(part.part, found)
    elif part.mat and part.mat not in found:
        found.append(part.mat)
    return found


def to_graph(name, about, params, part, panels=None):
    """A Geometry Nodes Graph (see gn.builder) making `part`, one input per parameter and
    one material input per material the part uses."""
    g = _builder().Graph(name, about)
    inputs = {}
    for key, s in params.spec.items():
        label = key.replace("_", " ").title()
        inputs[key] = g.input(label, "float", s["default"], s["min"], s["max"], s["about"])
    mats = {}
    used = materials_of(part)
    if used:
        g.panel("Materials", closed=True)
        for m in used:
            mats[m] = g.input(m.replace("_", " ").title(), "material")
        g.panel(None)
    geo = _Emitter(g, inputs, mats).emit(part)
    g.output("Geometry", "geometry", geo)
    return g
