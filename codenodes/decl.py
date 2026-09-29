"""What a code node declares about itself: its inputs, its outputs, and the parts it plays.

A code node is a node you write on the fly. The code says which sockets the node has:

    // @in  float speed 1.0 0 4          a slider (the older `// @param speed 1.0 0 4` still works)
    // @in  int   count 3 1 10           a whole number
    // @in  color tint 1.0 0.6 0.2       a colour
    // @in  func  vec3 wind(vec3 p)      a function input: wire a function in, call wind(p)
                                         (`= 1e9` after it: what it returns when nothing is wired)
    // @out func  field                  your function `field` becomes an output socket
    // @out attr  brightness 1.0         a per-particle value later nodes (and Geometry Nodes) can read
    // @shape firefly                    how a Look node draws each particle: point, glow or firefly
    // @in  hidden lightX 0.0            a value the add-on fills in itself (no socket), e.g. light data
    // @in  material mat                 a Material socket: its Principled BSDF values arrive as
                                         mat_base (vec3), mat_roughness, mat_metallic, mat_emit (vec3,
                                         colour × strength) and mat_alpha

The streams a node takes and gives follow from the functions it defines:

    spawn(inout Particle p)                a particle source: a Particles output
    born / behave / look                   a particle stage: Particles in, Particles out
    deform(inout Vertex v)                 a mesh stage: Mesh in, Mesh out
    warp(vec3 p)                           a space warp: works on both, so it has both

Pure Python: no bpy, so it can be tested anywhere.
"""

from __future__ import annotations

import re

from .sdf_code import _PARAM_RE, _RESERVED, _TAKEN, Param, SdfCodeError

DECL_LINE = re.compile(r"^\s*//\s*@(in|out|attr|shape)\b(.*)$")
_NAME = r"[A-Za-z_]\w*"
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
TYPES = ("float", "int", "vec2", "vec3", "vec4", "bool")
SHAPES = ("point", "glow", "firefly", "streak")
# what a Material input brings in (as hidden sliders): base colour, roughness, metallic, emission
MATERIAL_PARTS = (("base_r", 0.8), ("base_g", 0.8), ("base_b", 0.8), ("roughness", 0.5), ("metallic", 0.0),
                  ("emit_r", 0.0), ("emit_g", 0.0), ("emit_b", 0.0), ("alpha", 1.0))
MAX_ATTRS = 4


class FuncIn:
    __slots__ = ("name", "ret", "args", "line", "default")

    def __init__(self, name, ret, args, line, default=None):
        self.name, self.ret, self.args, self.line = name, ret, args, line
        self.default = default        # what it returns when nothing is wired in (None: zero)

    def signature(self):
        return f"{self.ret} {self.name}({self.args})"


class FuncOut:
    __slots__ = ("name", "ret", "args", "line")

    def __init__(self, name, ret, args, line):
        self.name, self.ret, self.args, self.line = name, ret, args, line


class Attr:
    __slots__ = ("name", "default", "line")

    def __init__(self, name, default, line):
        self.name, self.default, self.line = name, default, line


class Decls:
    """Everything one node's code declares."""

    def __init__(self):
        self.params: list[Param] = []        # sliders, in order (a colour is three, name_r/_g/_b)
        self.kinds: dict[str, str] = {}      # param name -> 'FLOAT' | 'INT' | 'COLOR'
        self.colors: dict[str, tuple] = {}   # colour name -> (name_r, name_g, name_b)
        self.func_ins: list[FuncIn] = []
        self.func_outs: list[FuncOut] = []
        self.attrs: list[Attr] = []
        self.shape = None
        self.roles: set[str] = set()
        self.hidden: set[str] = set()        # sliders the add-on fills in itself (no socket)
        self.materials: list[str] = []       # Material inputs (their values arrive as hidden sliders)

    # the sockets this node shows ---------------------------------------------------------------
    def takes_particles(self):
        return bool(self.roles & {"born", "behave", "look", "warp"}) and "spawn" not in self.roles

    def gives_particles(self):
        return "spawn" in self.roles or self.takes_particles()

    def takes_mesh(self):
        return bool(self.roles & {"deform", "warp"})


_ROLE_SIGS = {
    "spawn": r"\bvoid\s+spawn\s*\(\s*inout\s+Particle\s+\w+\s*\)",
    "update": r"\bvoid\s+update\s*\(\s*inout\s+Particle\s+\w+\s*,\s*float\s+\w+\s*\)",
    "born": r"\bvoid\s+born\s*\(\s*inout\s+Particle\s+\w+\s*\)",
    "behave": r"\bvoid\s+behave\s*\(\s*inout\s+Particle\s+\w+\s*,\s*float\s+\w+\s*\)",
    "look": r"\bvec4\s+look\s*\(\s*Particle\s+\w+\s*\)",
    "warp": r"\bvec3\s+warp\s*\(\s*vec3\s+\w+\s*\)",
    "deform": r"\bvoid\s+deform\s*\(\s*inout\s+Vertex\s+\w+\s*\)",
    "sdf": r"\bfloat\s+sdf\s*\(\s*vec3\s+\w+\s*\)",
}


def roles(source):
    return {r for r, sig in _ROLE_SIGS.items() if re.search(sig, source)}


def _function_signature(source, name):
    """(return type, args) of a function the code defines, or None."""
    m = re.search(rf"^\s*({_NAME})\s+{re.escape(name)}\s*\(([^)]*)\)\s*\{{", source, re.M)
    return (m.group(1), m.group(2).strip()) if m else None


def parse(source):
    """Read every declaration. Raises SdfCodeError naming the line for a bad one."""
    d = Decls()
    seen = set()

    def add_param(name, default, lo, hi, kind, n):
        if _RESERVED.match(name) or name in _TAKEN:
            raise SdfCodeError(f"line {n}: '{name}' is a reserved name; pick another")
        if name in seen:
            raise SdfCodeError(f"line {n}: '{name}' is declared twice")
        seen.add(name)
        d.params.append(Param(name, default, lo, hi))
        d.kinds[name] = kind

    for n, line in enumerate(source.splitlines(), 1):
        m = _PARAM_RE.match(line)
        if m:
            lo = float(m.group(3)) if m.group(3) else None
            hi = float(m.group(4)) if m.group(4) else None
            add_param(m.group(1), float(m.group(2)), lo, hi, 'FLOAT', n)
            continue
        m = DECL_LINE.match(line)
        if not m:
            continue
        word, rest = m.group(1), m.group(2).strip()
        bits = rest.split()
        if word == "shape":
            if not bits or bits[0] not in SHAPES:
                raise SdfCodeError(f"line {n}: @shape takes one of: {', '.join(SHAPES)}")
            d.shape = bits[0]
        elif word == "attr" or (word == "out" and bits[:1] == ["attr"]):
            b = bits if word == "attr" else bits[1:]
            if not b or not re.fullmatch(_NAME, b[0]):
                raise SdfCodeError(f"line {n}: write it as  // @out attr name [default]")
            default = float(b[1]) if len(b) > 1 else 0.0
            if b[0] in ("position", "velocity", "age", "life", "seed", "cnX") or _RESERVED.match(b[0]) \
                    or b[0] in _TAKEN:
                raise SdfCodeError(f"line {n}: '{b[0]}' is already taken; name the attribute something else")
            if b[0] not in [a.name for a in d.attrs]:
                d.attrs.append(Attr(b[0], default, n))
        elif word == "out":
            if bits[:1] != ["func"] or len(bits) < 2 or not re.fullmatch(_NAME, bits[1]):
                raise SdfCodeError(f"line {n}: an output is  // @out func name  or  // @out attr name")
            sig = _function_signature(source, bits[1])
            if sig is None:
                raise SdfCodeError(f"line {n}: '@out func {bits[1]}' but the code defines no function "
                                   f"called {bits[1]}")
            d.func_outs.append(FuncOut(bits[1], sig[0], sig[1], n))
        else:  # @in
            if not bits:
                raise SdfCodeError(f"line {n}: write it as  // @in float name default [min max]")
            kind = bits[0]
            if kind == "func":
                fm = re.fullmatch(rf"func\s+({_NAME})\s+({_NAME})\s*\(([^)]*)\)\s*(?:=\s*(.+?))?\s*", rest)
                if not fm:
                    raise SdfCodeError(f"line {n}: a function input is  // @in func vec3 name(vec3 p)"
                                       f"  (optionally followed by  = what it returns when unwired)")
                d.func_ins.append(FuncIn(fm.group(2), fm.group(1), fm.group(3).strip(), n, fm.group(4)))
            elif kind in ("float", "int"):
                nm = re.fullmatch(rf"(?:float|int)\s+({_NAME})\s+({_NUM})(?:\s+({_NUM})\s+({_NUM}))?\s*", rest)
                if not nm:
                    raise SdfCodeError(f"line {n}: write it as  // @in {kind} name default [min max]")
                lo = float(nm.group(3)) if nm.group(3) else None
                hi = float(nm.group(4)) if nm.group(4) else None
                add_param(nm.group(1), float(nm.group(2)), lo, hi, kind.upper(), n)
            elif kind == "hidden":
                hm = re.fullmatch(rf"hidden\s+(?:float\s+)?({_NAME})(?:\s+({_NUM}))?\s*", rest)
                if not hm:
                    raise SdfCodeError(f"line {n}: write it as  // @in hidden name [default]")
                add_param(hm.group(1), float(hm.group(2) or 0.0), -1e9, 1e9, 'HIDDEN', n)
                d.hidden.add(hm.group(1))
            elif kind == "material":
                mm = re.fullmatch(rf"material\s+({_NAME})\s*", rest)
                if not mm:
                    raise SdfCodeError(f"line {n}: write it as  // @in material name")
                m = mm.group(1)
                if m in seen:
                    raise SdfCodeError(f"line {n}: '{m}' is declared twice")
                seen.add(m)
                d.materials.append(m)
                for part, default in MATERIAL_PARTS:
                    add_param(f"{m}_{part}", default, -1e9, 1e9, 'HIDDEN', n)
                    d.hidden.add(f"{m}_{part}")
                d.colors[f"{m}_base"] = tuple(f"{m}_base_{c}" for c in "rgb")
                d.colors[f"{m}_emit"] = tuple(f"{m}_emit_{c}" for c in "rgb")
            elif kind in ("color", "colour"):
                cm = re.fullmatch(rf"colou?r\s+({_NAME})(?:\s+({_NUM})\s+({_NUM})\s+({_NUM}))?\s*", rest)
                if not cm:
                    raise SdfCodeError(f"line {n}: write it as  // @in color name r g b")
                name = cm.group(1)
                rgb = [float(cm.group(i)) if cm.group(i) else 1.0 for i in (2, 3, 4)]
                parts = tuple(f"{name}_{c}" for c in "rgb")
                if name in seen:
                    raise SdfCodeError(f"line {n}: '{name}' is declared twice")
                seen.add(name)
                for part, v in zip(parts, rgb):
                    d.params.append(Param(part, v, 0.0, 1.0))
                    d.kinds[part] = 'COLOR'
                d.colors[name] = parts
            else:
                raise SdfCodeError(f"line {n}: unknown input type '{kind}'. Use float, int, color, func, "
                                   f"material or hidden")
    if len(d.attrs) > MAX_ATTRS:
        raise SdfCodeError(f"at most {MAX_ATTRS} per-particle attributes (found {len(d.attrs)})")
    d.roles = roles(source)
    # a distance function is always offered to other nodes (e.g. particles colliding with a surface)
    if "sdf" in d.roles and "sdf" not in [f.name for f in d.func_outs]:
        sig = _function_signature(source, "sdf")
        d.func_outs.append(FuncOut("sdf", "float", sig[1] if sig else "vec3 p", 0))
    return d


def strip(source):
    """The code with every declaration line blanked (line numbers stay the same)."""
    return "\n".join("" if (_PARAM_RE.match(l) or DECL_LINE.match(l)) else l for l in source.splitlines())


def color_defines(d, prefix=""):
    """`#define tint vec3(tint_r, tint_g, tint_b)` for each colour input (names already prefixed)."""
    return "".join(f"#define {prefix}{name} vec3({prefix}{r}, {prefix}{g}, {prefix}{b})\n"
                   for name, (r, g, b) in d.colors.items())
