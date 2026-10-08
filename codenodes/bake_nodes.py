"""Bake to Nodes (animation phase 3): the code itself becomes a Geometry Nodes network.

No files and no GPU: the distance function is rebuilt from stock nodes and evaluated
natively on every frame, so the object renders anywhere, animates, and keeps its sliders.

    GLSL sdf(p)  --translate-->  a Python expression  --ExpressNode-->  a node group
    node group -> Volume Cube (density = -sdf) -> Volume to Mesh -> the surface

The compiling is done by ExpressNode (quacktheplanet/ExpressNode), which must be
installed; this module translates CodeNodes' GLSL into its language. Only a part of GLSL
translates: local variables, maths, `?:`, your own helper functions and CodeNodes' distance
helpers. Loops, `if` statements and noise don't (CodeNodes' GPU noise has no exact twin in
stock nodes), and those say so, so the caller can fall back to Bake to Disk.
"""

from __future__ import annotations

import re

from . import mod_inputs, sdf_code
from .sdf_code import SdfCodeError


class CannotConvert(SdfCodeError):
    """The code uses something that has no node version (yet). Bake to Disk instead."""


# CodeNodes' helpers, written the way ExpressNode takes them (component by component
# where GLSL would lean on vector max/abs, which it doesn't have)
HELPERS = {
    "sdSphere": "def sdSphere(p, r):\n    return length(p) - r",
    "sdBox": ("def sdBox(p, b):\n"
              "    qx = abs(p.x) - b.x\n    qy = abs(p.y) - b.y\n    qz = abs(p.z) - b.z\n"
              "    return length(vec3(max(qx, 0.0), max(qy, 0.0), max(qz, 0.0))) + min(max(qx, max(qy, qz)), 0.0)"),
    "sdRoundBox": "def sdRoundBox(p, b, r):\n    return sdBox(p, b - vec3(r, r, r)) - r",
    "sdTorus": "def sdTorus(p, R, r):\n    return length(vec2(length(vec2(p.x, p.y)) - R, p.z)) - r",
    "sdCapsule": ("def sdCapsule(p, a, b, r):\n    pa = p - a\n    ba = b - a\n"
                  "    h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0)\n    return length(pa - ba * h) - r"),
    "sdCylinder": ("def sdCylinder(p, r, h):\n    dx = length(vec2(p.x, p.y)) - r\n    dy = abs(p.z) - h\n"
                   "    return min(max(dx, dy), 0.0) + length(vec2(max(dx, 0.0), max(dy, 0.0)))"),
    "smin": ("def smin(a, b, k):\n    h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)\n"
             "    return mix(b, a, h) - k * h * (1.0 - h)"),
    "smax": "def smax(a, b, k):\n    return -smin(-a, -b, k)",
    "rotateZ": ("def rotateZ(p, a):\n    c = cos(a)\n    s = sin(a)\n"
                "    return vec3(c * p.x - s * p.y, s * p.x + c * p.y, p.z)"),
    "rotateX": ("def rotateX(p, a):\n    c = cos(a)\n    s = sin(a)\n"
                "    return vec3(p.x, c * p.y - s * p.z, s * p.y + c * p.z)"),
    "rotateY": ("def rotateY(p, a):\n    c = cos(a)\n    s = sin(a)\n"
                "    return vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z)"),
}
HELPER_USES = {"sdRoundBox": ["sdBox"], "smax": ["smin"]}
NO_TWIN = {"noise3", "fbm3", "cnHash"}
FUNCTIONS = {"sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "pow", "exp", "log", "abs",
             "floor", "ceil", "round", "mod", "sign", "min", "max", "clamp", "mix", "smoothstep",
             "fract", "step", "length", "dot", "cross", "normalize", "reflect", "vec2", "vec3", "vec4"}
TYPES = {"float", "vec2", "vec3", "vec4", "int", "bool"}
# names ExpressNode gives a meaning of its own, and Python words
TAKEN = {"P", "N", "i", "t", "frame", "dt", "and", "or", "not", "is", "in", "lambda", "def",
         "pass", "None", "True", "False", "from", "global", "with", "yield", "class", "del",
         "import", "as", "try", "except", "finally", "raise", "assert", "async", "await",
         "elif", "nonlocal", "print", "attr", "set_attr", "obj", "noise", "voronoi"}

_TOKEN = re.compile(r"\s*(?:(\d+\.\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|\d+(?:[eE][+-]?\d+)?)[fF]?"
                    r"|([A-Za-z_]\w*)|(\+=|-=|\*=|/=|==|!=|<=|>=|&&|\|\||\+\+|--|[-+*/%<>=!?:;,.(){}\[\]]))")


def _strip_comments(source):
    source = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _tokens(source):
    out, at, line = [], 0, 1
    while at < len(source):
        if not source[at:].strip():
            break
        m = _TOKEN.match(source, at)
        if not m:
            bad = source[at:].lstrip()[:1]
            raise CannotConvert(f"line {line}: '{bad}' isn't something nodes can be made from")
        line += source[at:m.start(0) + len(m.group(0)) - len(m.group(0).lstrip())].count("\n")
        if m.group(1) is not None:
            out.append(("num", m.group(1), line))
        elif m.group(2) is not None:
            out.append(("name", m.group(2), line))
        else:
            out.append(("op", m.group(3), line))
        at = m.end()
    return out


class _Translator:
    def __init__(self, source, params, time_offset):
        self.toks = _tokens(_strip_comments(source))
        self.i = 0
        self.params = {p.name for p in params}
        self.time_offset = time_offset
        self.user_fns = {}          # name -> python def text
        self.used_helpers = set()
        self.local = {}             # glsl name -> python name, per function

    # -- token helpers -------------------------------------------------------------------
    def peek(self, k=0):
        return self.toks[self.i + k] if self.i + k < len(self.toks) else ("eof", "", self.line())

    def line(self):
        return self.toks[min(self.i, len(self.toks) - 1)][2] if self.toks else 1

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise CannotConvert(f"line {tok[2]}: expected '{value}', found '{tok[1] or 'the end'}'")
        self.i += 1
        return tok

    def at(self, value):
        return self.peek()[1] == value

    def refuse(self, what):
        raise CannotConvert(f"line {self.line()}: {what} can't become nodes yet; Bake to Disk instead")

    # -- names ---------------------------------------------------------------------------
    def name(self, glsl):
        if glsl in self.local:
            return self.local[glsl]
        if glsl in self.params:
            return glsl + "_" if glsl in TAKEN else glsl
        if glsl == "uTime":
            return f"(t - {self.time_offset!r})" if self.time_offset else "t"
        if glsl == "uFrame":
            return "frame"
        raise CannotConvert(f"line {self.line()}: '{glsl}' is not defined")

    def declare(self, glsl):
        py = glsl + "_" if glsl in TAKEN else glsl
        self.local[glsl] = py
        return py

    # -- the program ---------------------------------------------------------------------
    def program(self):
        sdf = None
        while self.peek()[0] != "eof":
            tok = self.peek()
            if tok[1] == "const":
                self.refuse("a global constant")
            if tok[1] in ("#define", "#") or tok[1].startswith("#"):
                self.refuse("#define")
            if tok[1] not in TYPES and tok[1] != "void":
                raise CannotConvert(f"line {tok[2]}: expected a function, found '{tok[1]}'")
            rtype = self.take()[1]
            fname = self.take()[1]
            text = self.function(fname, rtype)
            if fname == "sdf":
                sdf = text
            else:
                if fname in HELPERS or fname in NO_TWIN:
                    raise CannotConvert(f"line {tok[2]}: '{fname}' is already one of CodeNodes' helpers")
                self.user_fns[fname] = text
        if sdf is None:
            raise CannotConvert("there is no `float sdf(vec3 p)` to convert")
        return sdf

    def function(self, fname, rtype):
        self.local = {}
        self.take("(")
        args = []
        while not self.at(")"):
            if self.peek()[1] in ("out", "inout"):
                self.refuse("an out parameter")
            if self.at("in"):
                self.take()
            atype = self.take()[1]
            if atype not in TYPES:
                raise CannotConvert(f"line {self.line()}: unknown type '{atype}'")
            args.append(self.take()[1])
            if self.at(","):
                self.take()
        self.take(")")
        self.take("{")
        if fname == "sdf":
            if len(args) != 1:
                raise CannotConvert("sdf takes exactly one argument, the position")
            header = []
            self.local[args[0]] = self.declare(args[0])
            body = [f"{self.local[args[0]]} = P"]
        else:
            header = [self.declare(a) for a in args]
            body = []
        body += self.block()
        if fname == "sdf":
            return body
        return f"def {fname}({', '.join(header)}):\n" + "\n".join("    " + line for line in body)

    def block(self):
        lines = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise CannotConvert("a { is never closed")
            lines += self.statement()
        self.take("}")
        return lines

    def statement(self):
        tok = self.peek()
        word = tok[1]
        if word in ("for", "while", "do"):
            self.refuse("a loop")
        if word in ("if", "switch"):
            self.refuse("an if statement (a ? b : c works)")
        if word in ("break", "continue", "discard"):
            self.refuse(f"'{word}'")
        if word == "return":
            self.take()
            value = self.expr()
            self.take(";")
            return [f"return {value}"]
        if word == "const":
            self.take()
            word = self.peek()[1]
        if word in TYPES:
            self.take()
            lines = []
            while True:
                name = self.take()[1]
                if self.at("["):
                    self.refuse("an array")
                if not self.at("="):
                    raise CannotConvert(f"line {tok[2]}: '{name}' needs a value where it is declared")
                self.take("=")
                value = self.expr()
                lines.append(f"{self.declare(name)} = {value}")
                if self.at(","):
                    self.take()
                    continue
                self.take(";")
                return lines
        if tok[0] == "name":
            name = self.take()[1]
            if self.at(".") or self.at("["):
                self.refuse("changing one component of a vector")
            op = self.take()[1]
            if op in ("++", "--"):
                self.take(";")
                target = self.name(name)
                return [f"{target} = {target} {op[0]} 1.0"]
            if op not in ("=", "+=", "-=", "*=", "/="):
                raise CannotConvert(f"line {tok[2]}: unexpected '{op}'")
            value = self.expr()
            self.take(";")
            target = self.name(name) if name in self.local else None
            if target is None:
                raise CannotConvert(f"line {tok[2]}: '{name}' is not a local variable")
            if op == "=":
                return [f"{target} = {value}"]
            return [f"{target} = {target} {op[0]} ({value})"]
        raise CannotConvert(f"line {tok[2]}: unexpected '{word}'")

    # -- expressions (output fully bracketed, so precedence can't drift) -----------------
    def expr(self):
        cond = self.or_expr()
        if self.at("?"):
            self.take()
            a = self.expr()
            self.take(":")
            b = self.expr()
            return f"({a} if {cond} else {b})"
        return cond

    def _binary(self, sub, ops, py=None):
        left = sub()
        while self.peek()[1] in ops:
            op = self.take()[1]
            right = sub()
            left = f"({left} {(py or {}).get(op, op)} {right})"
        return left

    def or_expr(self):
        return self._binary(self.and_expr, ("||",), {"||": "or"})

    def and_expr(self):
        return self._binary(self.eq_expr, ("&&",), {"&&": "and"})

    def eq_expr(self):
        return self._binary(self.rel_expr, ("==", "!="))

    def rel_expr(self):
        return self._binary(self.add_expr, ("<", ">", "<=", ">="))

    def add_expr(self):
        return self._binary(self.mul_expr, ("+", "-"))

    def mul_expr(self):
        return self._binary(self.unary, ("*", "/", "%"))

    def unary(self):
        if self.at("-"):
            self.take()
            return f"(-{self.unary()})"
        if self.at("+"):
            self.take()
            return self.unary()
        if self.at("!"):
            self.take()
            return f"(not {self.unary()})"
        return self.postfix()

    def postfix(self):
        value = self.primary()
        while self.at(".") or self.at("["):
            if self.at("."):
                self.take()
                member = self.take()[1]
                if not re.fullmatch(r"[xyzwrgba]{1,4}", member):
                    raise CannotConvert(f"line {self.line()}: '.{member}' isn't a vector component")
                value = f"{value}.{member.translate(str.maketrans('rgba', 'xyzw'))}"
            else:
                self.take()
                index = self.expr()
                self.take("]")
                value = f"{value}[{index}]"
        return value

    def primary(self):
        tok = self.take()
        kind, text = tok[0], tok[1]
        if kind == "num":
            return text if any(c in text for c in ".eE") else text + ".0"
        if text == "(":
            inner = self.expr()
            self.take(")")
            return f"({inner})"
        if text in ("true", "false"):
            return "1.0" if text == "true" else "0.0"
        if kind != "name":
            raise CannotConvert(f"line {tok[2]}: unexpected '{text}'")
        if not self.at("("):
            return self.name(text)
        self.take("(")
        args = []
        while not self.at(")"):
            args.append(self.expr())
            if self.at(","):
                self.take()
        self.take(")")
        return self.call(text, args, tok[2])

    def call(self, fn, args, line):
        if fn in NO_TWIN:
            raise CannotConvert(f"line {line}: {fn}() is CodeNodes' GPU noise, which stock nodes can't "
                                "reproduce exactly; Bake to Disk instead")
        if fn in HELPERS:
            self._need(fn)
            return f"{fn}({', '.join(args)})"
        if fn in self.user_fns:
            return f"{fn}({', '.join(args)})"
        if fn == "float":
            return f"({args[0]})"
        if fn in ("int", "uint", "bool"):
            raise CannotConvert(f"line {line}: {fn}() can't become nodes yet; Bake to Disk instead")
        if fn in ("vec2", "vec3", "vec4") and len(args) == 1:
            n = int(fn[-1])
            return f"{fn}({', '.join([args[0]] * n)})"
        simple = {"inversesqrt": lambda a: f"(1.0 / sqrt({a[0]}))",
                  "exp2": lambda a: f"pow(2.0, {a[0]})",
                  "log2": lambda a: f"(log({a[0]}) / 0.6931471805599453)",
                  "distance": lambda a: f"length(({a[0]}) - ({a[1]}))",
                  "radians": lambda a: f"(({a[0]}) * 0.017453292519943295)",
                  "degrees": lambda a: f"(({a[0]}) * 57.29577951308232)"}
        if fn in simple:
            return simple[fn](args)
        if fn == "atan" and len(args) == 2:
            return f"atan2({args[0]}, {args[1]})"
        if fn in FUNCTIONS:
            return f"{fn}({', '.join(args)})"
        raise CannotConvert(f"line {line}: '{fn}' has no node version yet; Bake to Disk instead")

    def _need(self, fn):
        if fn not in self.used_helpers:
            self.used_helpers.add(fn)
            for dep in HELPER_USES.get(fn, ()):
                self._need(dep)


def translate(source, time_offset=0.0):
    """GLSL sdf code -> (ExpressNode source, params). Raises CannotConvert."""
    params = sdf_code.parse_params(source)
    tr = _Translator(source, params, time_offset)
    body = tr.program()
    args = ", ".join(f"{(p.name + '_') if p.name in TAKEN else p.name}={float(p.default)!r}"
                     for p in params)
    lines = [f"def sdf(P{', ' + args if args else ''}):"]
    order = [h for h in HELPERS if h in tr.used_helpers]      # dependencies come first
    order.sort(key=lambda h: 0 if h in ("sdBox", "smin") else 1)
    for name in order:
        lines += ["    " + line for line in HELPERS[name].splitlines()]
    for text in tr.user_fns.values():
        lines += ["    " + line for line in text.splitlines()]
    lines += ["    " + line for line in body]
    return "\n".join(lines) + "\n", params


# ---- in Blender ---------------------------------------------------------------------------

WRAPPER_PREFIX = "CN Nodes "


# ExpressNode's package was renamed from `coding_nodes` to `expressnode`, and it ships as a Blender
# extension (then it lives under bl_ext.<repository>.expressnode). Try every place it can be.
EXPRESSNODE_PACKAGES = ("expressnode", "coding_nodes")


def _expression_nodes():
    import importlib
    import sys
    names = list(EXPRESSNODE_PACKAGES)
    for mod in list(sys.modules):            # an installed extension: bl_ext.<repo>.expressnode
        if mod.startswith("bl_ext.") and mod.rsplit(".", 1)[-1] in EXPRESSNODE_PACKAGES:
            names.append(mod)
    for name in names:
        for sub in ("backend.pipeline", "coding_nodes.backend.pipeline", "expressnode.backend.pipeline"):
            try:
                return importlib.import_module(f"{name}.{sub}").build_in_blender
            except (ImportError, AttributeError):
                continue
    raise CannotConvert("Bake to Nodes needs the ExpressNode add-on "
                        "(github.com/quacktheplanet/ExpressNode) installed and enabled") from None


def build(obj):
    """Make the node version of a Code Mesh object and put it on the object.

    Returns the wrapper node group. The object stops being driven from code (its text
    stays), and the modifier carries the sliders, with the object's current values.
    """
    import bpy
    s = obj.codenodes
    if s.text is None or s.kind != 'MESH':
        raise CannotConvert(f"'{obj.name}' is not a Code Mesh object")
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    source, params = translate(s.text.as_string(), time_offset=scene.frame_start / fps)
    build_in_blender = _expression_nodes()
    try:
        expr_tree = build_in_blender(source)
    except Exception as exc:
        raise CannotConvert(f"ExpressNode could not compile it: {exc}") from None
    expr_tree.use_fake_user = True

    name = WRAPPER_PREFIX + obj.name
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.use_fake_user = True
    iface = tree.interface
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    res_sock = iface.new_socket("Resolution", in_out="INPUT", socket_type="NodeSocketInt")
    res_sock.default_value, res_sock.min_value, res_sock.max_value = s.resolution, 8, 512
    for p in params:
        sock = iface.new_socket(p.name, in_out="INPUT", socket_type="NodeSocketFloat")
        sock.default_value, sock.min_value, sock.max_value = p.default, p.min, p.max
    nodes, links = tree.nodes, tree.links
    gin, gout = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    field = nodes.new("GeometryNodeGroup")
    field.node_tree = expr_tree
    for p in params:
        target = next((i for i in field.inputs if i.name in (p.name, p.name + "_")), None)
        if target is not None:
            links.new(gin.outputs[p.name], target)
    result = next(o for o in field.outputs if o.type == 'VALUE')
    neg = nodes.new("ShaderNodeMath")
    neg.operation = 'MULTIPLY'
    neg.inputs[1].default_value = -1.0
    links.new(result, neg.inputs[0])
    cube = nodes.new("GeometryNodeVolumeCube")
    links.new(neg.outputs[0], cube.inputs["Density"])
    lo, hi = tuple(s.bounds_min), tuple(s.bounds_max)
    cube.inputs["Min"].default_value, cube.inputs["Max"].default_value = lo, hi
    # nothing outside the box counts as solid (the default background of 0 would)
    cube.inputs["Background"].default_value = -1.0e6
    longest = max(h - l for l, h in zip(lo, hi)) or 1.0
    for axis, key in enumerate(("Resolution X", "Resolution Y", "Resolution Z")):
        share = nodes.new("ShaderNodeMath")
        share.operation = 'MULTIPLY'
        share.inputs[1].default_value = (hi[axis] - lo[axis]) / longest
        links.new(gin.outputs["Resolution"], share.inputs[0])
        links.new(share.outputs[0], cube.inputs[key])
    mesh = nodes.new("GeometryNodeVolumeToMesh")
    links.new(cube.outputs["Volume"], mesh.inputs["Volume"])
    mesh.inputs["Threshold"].default_value = 0.0
    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.inputs["Shade Smooth"].default_value = bool(s.smooth)
    links.new(mesh.outputs["Mesh"], smooth.inputs["Mesh"])
    links.new(smooth.outputs["Mesh"], gout.inputs["Geometry"])
    for k, node in enumerate((gin, field, neg, cube, mesh, smooth, gout)):
        node.location = (k * 220, 0)

    for mod in [m for m in obj.modifiers if m.type == 'NODES' and m.node_group
                and m.node_group.name.startswith(WRAPPER_PREFIX)]:
        obj.modifiers.remove(mod)
    mod = obj.modifiers.new("Code as Nodes", 'NODES')
    mod.node_group = tree
    for item in tree.interface.items_tree:
        if item.item_type != 'SOCKET' or item.in_out != 'INPUT':
            continue
        if item.name == "Resolution":
            mod_inputs.of(mod)[item.identifier] = int(s.resolution)
        else:
            current = next((prm.value for prm in s.params if prm.name == item.name), None)
            if current is not None:
                mod_inputs.of(mod)[item.identifier] = float(current)
    s.enabled = False                      # the nodes drive it now; the code text stays
    s.animate = False
    obj.data.clear_geometry()
    obj.update_tag()
    return tree
