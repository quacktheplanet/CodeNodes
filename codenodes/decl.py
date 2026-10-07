"""What a code node declares about itself: its inputs, its outputs, and the parts it plays.

A code node is a node you write on the fly. The code says which sockets the node has:

    // @in  float speed 1.0 0 4          a slider (the older `// @param speed 1.0 0 4` still works)
    // @in  int   count 3 1 10           a whole number
    // @in  color tint 1.0 0.6 0.2       a colour
    // @in  func  vec3 wind(vec3 p)      a function input: wire a function in, call wind(p)
                                         (`= 1e9` after it: what it returns when nothing is wired;
                                         `use: wind(p) * 0.4` after that: how this node uses what's
                                         wired in, shown and edited on the node as its use line)
    // @out func  field                  your function `field` becomes an output socket
    // @out attr  brightness 1.0         a per-particle value later nodes (and Geometry Nodes) can read
    // @shape firefly                    how a Look node draws each particle: point, glow or firefly
    // @in  list  vec3 points            a list: wire a List node (or one of its columns) in; the code reads
                                         points_count() and points(i). A list of records names a struct
                                         the code defines: `// @in list Body bodies` with
                                         `struct Body { float mass; vec3 color; };`, read as bodies(i).mass
    // @list                             this node is a List: the lines after it are a table (see
                                         parse_table), and its columns are its outputs
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

DECL_LINE = re.compile(r"^\s*//\s*@(in|out|attr|shape|list)\b(.*)$")
_NAME = r"[A-Za-z_]\w*"
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
TYPES = ("float", "int", "vec2", "vec3", "vec4", "bool")
SIZES = {"float": 1, "int": 1, "vec2": 2, "vec3": 3, "vec4": 4}      # list element types
MAX_ROWS = 4096
SHAPES = ("point", "glow", "firefly", "streak")
# what a Material input brings in (as hidden sliders): base colour, roughness, metallic, emission
MATERIAL_PARTS = (("base_r", 0.8), ("base_g", 0.8), ("base_b", 0.8), ("roughness", 0.5), ("metallic", 0.0),
                  ("emit_r", 0.0), ("emit_g", 0.0), ("emit_b", 0.0), ("alpha", 1.0))
MAX_ATTRS = 4


class FuncIn:
    __slots__ = ("name", "ret", "args", "line", "default", "use")

    def __init__(self, name, ret, args, line, default=None, use=None):
        self.name, self.ret, self.args, self.line = name, ret, args, line
        self.default = default        # what it returns when nothing is wired in (None: zero)
        self.use = use                # how this node uses it: an expression, or None for as it is

    def signature(self):
        return f"{self.ret} {self.name}({self.args})"

    def arg_names(self):
        return arg_names(self.args)

    def plain_use(self):
        """The use line that changes nothing: `wind(p, t)`."""
        return f"{self.name}({', '.join(self.arg_names())})"

    def use_line(self):
        """The use line as shown on the node: the written one, or the plain call."""
        return self.use or self.plain_use()

    def wraps(self):
        """True when the use line does more than call the function as it is."""
        return self.use is not None and _squash(self.use) != _squash(self.plain_use())


class FuncOut:
    __slots__ = ("name", "ret", "args", "line")

    def __init__(self, name, ret, args, line):
        self.name, self.ret, self.args, self.line = name, ret, args, line


class ListIn:
    __slots__ = ("name", "type", "line")

    def __init__(self, name, type_, line):
        self.name, self.type, self.line = name, type_, line

    def is_record(self):
        return self.type not in SIZES


class Column:
    __slots__ = ("name", "size", "values")

    def __init__(self, name, size):
        self.name, self.size, self.values = name, size, []

    def glsl_type(self):
        return {1: "float", 2: "vec2", 3: "vec3", 4: "vec4"}[self.size]


class Table:
    """A List node's data: named number columns (1 to 4 numbers each) and an optional label column."""

    def __init__(self):
        self.columns: list[Column] = []
        self.labels: list[str] = []
        self.label_name = ""

    def __len__(self):
        return len(self.columns[0].values) if self.columns else len(self.labels)

    def column(self, name):
        return next((c for c in self.columns if c.name == name), None)


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
        self.lists: list[ListIn] = []        # list inputs
        self.table: Table | None = None      # set when this node is a List (`// @list`)
        self.descriptions: dict[str, str] = {}   # socket name -> the "…" written at the end of its line
        self.summary = ""                    # the code's first comment line: the node's own description

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


_STRUCT = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s*\{([^}]*)\}\s*;", re.S)


def struct_fields(source, name):
    """[(type, field name)] of `struct name { ... };` in the code, or None if it isn't defined."""
    for m in _STRUCT.finditer(strip_comments(source)):
        if m.group(1) == name:
            out = []
            for part in m.group(2).split(";"):
                bits = part.replace(",", " , ").split()
                if not bits:
                    continue
                t = bits[0]
                for f in " ".join(bits[1:]).split(","):
                    if f.strip():
                        out.append((t, f.strip()))
            return out
    return None


def strip_comments(source):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def _number(s):
    try:
        return float(s)
    except ValueError:
        return None


def parse_table(source):
    """A List node's table: the first line after `// @list` (comments skipped) names the columns, each
    line after it is a row. Separate cells with spaces or commas. A column written `color:3` takes three
    numbers (2 to 4 make a vector); a column whose cells aren't numbers is the label column (names
    shown to people, not sent to the GPU).

        // @list
        name    mass  radius  color:3
        Rocky   1.0   0.6     0.55 0.45 0.35
        Ocean   2.4   0.9     0.10 0.30 0.70
    """
    lines = source.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^\s*//\s*@list\b", l)), None)
    t = Table()
    if start is None:
        return t
    rows = []
    for n, line in enumerate(lines[start + 1:], start + 2):
        body = line.split("//")[0].strip()
        if body:
            rows.append((n, [c for c in re.split(r"[\s,]+", body) if c]))
    if not rows:
        return t
    n0, header = rows[0]
    spec = []
    for cell in header:
        m = re.fullmatch(rf"({_NAME})(?::([1-4]))?", cell)
        if not m:
            raise SdfCodeError(f"line {n0}: '{cell}' isn't a column name (letters, digits and _; add :3 for "
                               f"three numbers)")
        spec.append((m.group(1), int(m.group(2) or 1)))
    if len({nm for nm, _ in spec}) != len(spec):
        raise SdfCodeError(f"line {n0}: two columns have the same name")
    if len(rows) - 1 > MAX_ROWS:
        raise SdfCodeError(f"a List holds at most {MAX_ROWS} rows (this one has {len(rows) - 1})")
    # the label column: a single-number column whose cells (in every row) aren't all numbers
    label = None
    for k, (nm, size) in enumerate(spec):
        if size == 1 and any(_number(_cell(r, spec, k)) is None for _n, r in rows[1:]):
            label = k
            break
    for k, (nm, size) in enumerate(spec):
        if k == label:
            t.label_name = nm
        else:
            t.columns.append(Column(nm, size))
    width = sum(size for _nm, size in spec)
    for n, cells in rows[1:]:
        if len(cells) != width:
            raise SdfCodeError(f"line {n}: {len(cells)} cells, but the columns need {width}")
        i = 0
        for k, (nm, size) in enumerate(spec):
            got = cells[i:i + size]
            i += size
            if k == label:
                t.labels.append(got[0])
                continue
            nums = [_number(c) for c in got]
            if any(v is None for v in nums):
                raise SdfCodeError(f"line {n}: '{' '.join(got)}' in column '{nm}' isn't a number")
            t.column(nm).values.append(nums[0] if size == 1 else tuple(nums))
    return t


def _cell(cells, spec, k):
    i = sum(size for _nm, size in spec[:k])
    return cells[i] if i < len(cells) else ""


def set_table(source, table):
    """The code with the lines after `// @list` replaced by `table`, written as aligned columns."""
    lines = source.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^\s*//\s*@list\b", l)), None)
    head = lines[:start + 1] if start is not None else lines + ["// @list"]
    header = ([table.label_name] if table.label_name else []) + [
        c.name if c.size == 1 else f"{c.name}:{c.size}" for c in table.columns]
    rows = [header]
    for r in range(len(table)):
        row = [table.labels[r]] if table.label_name else []
        for c in table.columns:
            v = c.values[r]
            row.append(" ".join(_fmt(x) for x in (v if isinstance(v, tuple) else (v,))))
        rows.append(row)
    widths = {}
    for row in rows:
        for k, cell in enumerate(row):
            widths[k] = max(widths.get(k, 0), len(cell))
    body = ["  ".join(cell.ljust(widths[k]) for k, cell in enumerate(row)).rstrip() for row in rows]
    return "\n".join(head + body) + "\n"


def _fmt(v):
    return f"{v:.6g}"


def arg_names(args):
    """`vec3 p, float t` -> ["p", "t"] (qualifiers like `in` are skipped)."""
    names = []
    for a in args.split(","):
        bits = a.split()
        if len(bits) >= 2:
            names.append(re.sub(r"\[.*$", "", bits[-1]))
    return names


def arg_types(args):
    """`in vec3 p, float t` -> ["vec3", "float"]."""
    types = []
    for a in args.split(","):
        bits = [b for b in a.split() if b not in ("in", "const", "highp", "mediump", "lowp")]
        if bits:
            types.append(bits[0])
    return types


def _squash(expr):
    return re.sub(r"\s+", "", expr)


_USE = re.compile(r"\s+use:\s*(.*?)\s*$")


def use_ok(expr):
    """"" if a use line can go into the code, else why not (it must be one expression on one line)."""
    if not expr.strip():
        return "the use line is empty"
    if "\n" in expr or "//" in expr or ";" in expr or '"' in expr:
        return "a use line is one expression: no ';', comments or quotes"
    depth = 0
    for ch in expr:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            break
    if depth != 0:
        return "the brackets in the use line don't match"
    return ""


def set_use(source, name, expr):
    """The code with function input `name`'s use line set to `expr` (None or the plain call removes it).
    Raises SdfCodeError if there's no such input or the expression can't go on the line."""
    expr = expr.strip() if expr else None
    for i, line in enumerate(source.splitlines()):
        m = DECL_LINE.match(line)
        if not m or m.group(1) != "in":
            continue
        rest, desc = split_description(m.group(2).strip())
        fm = re.fullmatch(rf"func\s+({_NAME})\s+({_NAME})\s*\(([^)]*)\)(.*)", rest)
        if not fm or fm.group(2) != name:
            continue
        tail = _USE.sub("", fm.group(4))
        if expr is not None:
            why = use_ok(expr)
            if why:
                raise SdfCodeError(why)
            f = FuncIn(name, fm.group(1), fm.group(3).strip(), 0, use=expr)
            if not f.wraps():
                expr = None
        indent = line[:len(line) - len(line.lstrip())]
        new = f"{indent}// @in func {fm.group(1)} {name}({fm.group(3).strip()}){tail.rstrip()}"
        if expr is not None:
            new += f" use: {expr}"
        if desc:
            new += f'  "{desc}"'
        lines = source.splitlines()
        lines[i] = new
        return "\n".join(lines) + ("\n" if source.endswith("\n") else "")
    raise SdfCodeError(f"the code has no function input called '{name}'")


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
        if not d.summary:
            cm = re.match(r"^\s*//\s*(?!@)(.+?)\s*$", line)
            if cm:
                d.summary = cm.group(1)
        m = _PARAM_RE.match(line)
        if m:
            lo = float(m.group(3)) if m.group(3) else None
            hi = float(m.group(4)) if m.group(4) else None
            add_param(m.group(1), float(m.group(2)), lo, hi, 'FLOAT', n)
            if m.group(5):
                d.descriptions[m.group(1)] = m.group(5).strip()
            continue
        m = DECL_LINE.match(line)
        if not m:
            continue
        word, rest = m.group(1), m.group(2).strip()
        rest, desc = split_description(rest)
        bits = rest.split()
        if desc:
            named = _declared_name(word, bits, rest)
            if named:
                d.descriptions[named] = desc
        if word == "list":
            d.table = parse_table(source)
            break
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
            if kind == "list":
                lm = re.fullmatch(rf"list\s+({_NAME})\s+({_NAME})\s*", rest)
                if not lm:
                    raise SdfCodeError(f"line {n}: a list input is  // @in list vec3 name  (float, int, vec2, "
                                       f"vec3, vec4, or a struct the code defines)")
                t, name = lm.group(1), lm.group(2)
                if name in seen:
                    raise SdfCodeError(f"line {n}: '{name}' is declared twice")
                seen.add(name)
                if t not in SIZES and struct_fields(source, t) is None:
                    raise SdfCodeError(f"line {n}: '{t}' isn't a list type: use float, int, vec2, vec3, vec4 or "
                                       f"define  struct {t} {{ ... }};  in the code")
                d.lists.append(ListIn(name, t, n))
                continue
            if kind == "func":
                um = _USE.search(rest)
                use = um.group(1) if um else None
                head = rest[:um.start()] if um else rest
                fm = re.fullmatch(rf"func\s+({_NAME})\s+({_NAME})\s*\(([^)]*)\)\s*(?:=\s*(.+?))?\s*", head)
                if not fm:
                    raise SdfCodeError(f"line {n}: a function input is  // @in func vec3 name(vec3 p)"
                                       f"  (optionally followed by  = what it returns when unwired, then"
                                       f"  use: how this node uses it)")
                if use is not None:
                    why = use_ok(use)
                    if why:
                        raise SdfCodeError(f"line {n}: {why}")
                d.func_ins.append(FuncIn(fm.group(2), fm.group(1), fm.group(3).strip(), n, fm.group(4), use))
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
                                   f"list, material or hidden")
    if len(d.attrs) > MAX_ATTRS:
        raise SdfCodeError(f"at most {MAX_ATTRS} per-particle attributes (found {len(d.attrs)})")
    d.roles = roles(source)
    # a distance function is always offered to other nodes (e.g. particles colliding with a surface)
    if "sdf" in d.roles and "sdf" not in [f.name for f in d.func_outs]:
        sig = _function_signature(source, "sdf")
        d.func_outs.append(FuncOut("sdf", "float", sig[1] if sig else "vec3 p", 0))
    return d


def split_description(rest):
    """`float calm 0.92 0 0.999 "How smoothly they turn"` -> ("float calm 0.92 0 0.999", "How smoothly…")."""
    m = re.match(r'^(.*?)\s*"([^"]*)"\s*$', rest)
    return (m.group(1), m.group(2).strip()) if m else (rest, "")


def _declared_name(word, bits, rest):
    """The socket name a declaration line makes (for its description)."""
    if word == "attr":
        return bits[0] if bits else None
    if word == "out":
        return bits[1] if len(bits) > 1 else None
    if word == "in" and bits:
        if bits[0] == "list":
            return bits[2] if len(bits) > 2 else None
        if bits[0] == "func":
            fm = re.match(rf"func\s+{_NAME}\s+({_NAME})", rest)
            return fm.group(1) if fm else None
        return bits[1] if len(bits) > 1 else None
    return None


def strip(source):
    """The code with every declaration line blanked (line numbers stay the same). A List's table goes too."""
    if re.search(r"^\s*//\s*@list\b", source, re.M):
        return "\n" * source.count("\n")
    return "\n".join("" if (_PARAM_RE.match(l) or DECL_LINE.match(l)) else l for l in source.splitlines())


def color_defines(d, prefix=""):
    """`#define tint vec3(tint_r, tint_g, tint_b)` for each colour input (names already prefixed)."""
    return "".join(f"#define {prefix}{name} vec3({prefix}{r}, {prefix}{g}, {prefix}{b})\n"
                   for name, (r, g, b) in d.colors.items())
