"""Render form: a code node's native Geometry Nodes twin, compiled from the same code as its GPU preview.

    void deform(inout Vertex v)   -->  Geometry -> Store value / color -> Set Position -> Geometry
    vec3 warp(vec3 q)             -->  Geometry -> Set Position -> Geometry

The GPU code stays the live viewport preview. The render form needs no GPU and no add-on when it is
evaluated, so F12, Cycles, EEVEE, `blender -b` and render farms all see the real geometry.

The GLSL goes through bake_nodes' translator into ExpressNode's language, taught a few more things
here: the vertex's fields, writing one component (`v.position.z += h`, `q.xy = ...`), declarations
without a value, `if` / `else` (turned into guarded assignments, since nodes evaluate both sides), and
colour inputs. ExpressNode then builds the nodes. What has no node version raises CannotConvert,
naming the line: loops, a `return` inside an `if`, CodeNodes' GPU noise and random helpers (stock nodes
can't reproduce them exactly), and function inputs (the wired node's code isn't followed yet).
"""

from __future__ import annotations

import re

from . import bake_nodes, decl
from .bake_nodes import HELPERS, TAKEN, TYPES, CannotConvert

# the GPU-only helpers (noise and random numbers have no exact twin in stock nodes)
GPU_ONLY = bake_nodes.NO_TWIN | {"gnoise", "curl", "curlNoise", "cnHash33", "cnPotential", "rand1", "rand3",
                                 "randBall", "randSphere", "emitPoint", "emitNormal"}
# Vertex fields: (type, what they start as). `value` starts as what an earlier stage stored (0 if none),
# like a GPU chain hands it on.
VERTEX = {"position": ("vec3", "P"), "normal": ("vec3", "N"), "color": ("vec4", "vec4(1.0, 1.0, 1.0, 1.0)"),
          "value": ("float", 'attr("value", "float")'), "index": ("float", "(i * 1.0)")}
ZERO = {"float": "0.0", "int": "0.0", "bool": "0.0", "vec2": "vec2(0.0, 0.0)", "vec3": "vec3(0.0, 0.0, 0.0)",
        "vec4": "vec4(0.0, 0.0, 0.0, 0.0)"}
WIDTH = {"vec2": 2, "vec3": 3, "vec4": 4}
ENTRIES = {"deform": "void", "warp": "vec3"}


def kind_of(source):
    """'deform' or 'warp': what the code's render form does. Raises CannotConvert for anything else."""
    found = decl.roles(source) & set(ENTRIES)
    if not found:
        raise CannotConvert("only GPU Mesh code (deform) and warps have a render form yet")
    return "deform" if "deform" in found else "warp"


class _Translator(bake_nodes._Translator):
    def __init__(self, source, decls, time_offset, entry):
        super().__init__(source, decls.params, time_offset)
        self.entry = entry
        self.colors = decls.colors
        self.func_ins = {f.name for f in decls.func_ins}
        self.types = {}             # glsl local -> type, per function
        self.vertex = None          # the Vertex argument's name, inside deform()
        self.fields_used = set()
        self.fields_written = set()
        self.guards = []            # names holding the conditions of the ifs we're inside
        self.etypes = {}            # translated expression -> its GLSL type, where known
        self.rtypes = {}            # user function -> its return type
        self.count = 0

    def fresh(self, stem):
        self.count += 1
        return f"cn_{stem}{self.count}"

    # -- names ---------------------------------------------------------------------------
    def name(self, glsl):
        if glsl in self.local:
            return self.local[glsl]
        if glsl in self.colors:
            parts = [bake_nodes._Translator.name(self, part) for part in self.colors[glsl]]
            return f"vec3({', '.join(parts)})"
        if glsl == "uSceneTime":
            glsl = "uTime"              # the render form runs on the timeline's time anyway
        return super().name(glsl)

    def declare_typed(self, glsl, gtype):
        if self.guards and glsl in self.local:
            self.refuse(f"declaring '{glsl}' again inside an if")
        self.types[glsl] = gtype
        return self.declare(glsl)

    def field(self, name):
        if name not in VERTEX:
            raise CannotConvert(f"line {self.line()}: a Vertex has no '{name}'")
        self.fields_used.add(name)
        return f"v_{name}"

    # -- the program ---------------------------------------------------------------------
    def program(self):
        body = None
        while self.peek()[0] != "eof":
            tok = self.peek()
            if tok[1] == "const":
                self.refuse("a global constant")
            if tok[1].startswith("#"):
                self.refuse("#define")
            if tok[1] not in TYPES and tok[1] != "void":
                raise CannotConvert(f"line {tok[2]}: expected a function, found '{tok[1]}'")
            rtype = self.take()[1]
            fname = self.take()[1]
            text = self.function(fname, rtype)
            if fname == self.entry:
                body = text
            else:
                if fname in HELPERS or fname in GPU_ONLY:
                    raise CannotConvert(f"line {tok[2]}: '{fname}' is already one of CodeNodes' helpers")
                self.user_fns[fname] = text
        if body is None:
            raise CannotConvert(f"there is no {self.entry}() to convert")
        return body

    def function(self, fname, rtype):
        self.local, self.types, self.vertex = {}, {}, None
        self.rtypes[fname] = rtype
        self.take("(")
        args = []
        while not self.at(")"):
            qual = self.take()[1] if self.peek()[1] in ("in", "out", "inout") else "in"
            atype = self.take()[1]
            aname = self.take()[1]
            if fname == "deform" and atype == "Vertex" and qual == "inout":
                self.vertex = aname
            elif qual != "in":
                self.refuse("an out parameter")
            elif atype not in TYPES:
                raise CannotConvert(f"line {self.line()}: unknown type '{atype}'")
            args.append((atype, aname))
            if self.at(","):
                self.take()
        self.take(")")
        self.take("{")
        entry = fname == self.entry
        if entry and fname == "warp":
            if len(args) != 1:
                raise CannotConvert("warp takes exactly one argument, the position")
            header, body = [], [f"{self.declare_typed(args[0][1], 'vec3')} = P"]
        elif entry:
            header, body = [], []
        else:
            header, body = [self.declare_typed(n, t) for t, n in args], []
        body += self.block()
        if entry and fname == "deform":
            start = [f"v_{f} = {VERTEX[f][1]}" for f in VERTEX if f in self.fields_used | {"position"}]
            outs = [f'set_attr("{f}", v_{f})' for f in ("value", "color") if f in self.fields_written]
            return start + body + outs + ["return v_position"]
        if entry:
            return body
        return f"def {fname}({', '.join(header)}):\n" + "\n".join("    " + line for line in body)

    # -- statements ----------------------------------------------------------------------
    def body(self):
        if self.at("{"):
            self.take()
            return self.block()
        return self.statement()

    def guarded(self, target, value):
        if not self.guards:
            return f"{target} = {value}"
        return f"{target} = ({value} if {self.guards[-1]} else {target})"

    def statement(self):
        tok = self.peek()
        word = tok[1]
        if word == "if":
            self.take()
            self.take("(")
            cond = self.expr()
            self.take(")")
            raw, yes = self.fresh("if"), self.fresh("then")
            outer = self.guards[-1] if self.guards else None
            lines = [f"{raw} = {cond}", f"{yes} = ({outer} and {raw})" if outer else f"{yes} = {raw}"]
            self.guards.append(yes)
            lines += self.body()
            self.guards.pop()
            if self.at("else"):
                self.take()
                no = self.fresh("else")
                lines.append(f"{no} = ({outer} and (not {raw}))" if outer else f"{no} = (not {raw})")
                self.guards.append(no)
                lines += self.body()
                self.guards.pop()
            return lines
        if word == "return":
            if self.guards:
                self.refuse("a return inside an if (assign a variable, return it at the end)")
            if self.entry == "deform" and self.vertex is not None:
                self.refuse("a return in deform()")
            return super().statement()
        if word in ("const",) + tuple(TYPES):
            if word == "const":
                self.take()
            gtype = self.take()[1]
            lines = []
            while True:
                name = self.take()[1]
                if self.at("["):
                    self.refuse("an array")
                if self.at("="):
                    self.take()
                    value = self.expr()
                else:
                    value = ZERO.get(gtype, "0.0")
                lines.append(f"{self.declare_typed(name, gtype)} = {value}")
                if self.at(","):
                    self.take()
                    continue
                self.take(";")
                return lines
        if tok[0] == "name" and word not in ("for", "while", "do", "switch", "break", "continue", "discard"):
            return self.assignment()
        return super().statement()

    def assignment(self):
        tok = self.take()
        name = tok[1]
        if name == self.vertex and self.at("."):
            self.take()
            fname = self.take()[1]
            target, gtype = self.field(fname), VERTEX.get(fname, ("float",))[0]
            self.fields_written.add(fname)
        elif name in self.local:
            target, gtype = self.local[name], self.types.get(name)
        else:
            raise CannotConvert(f"line {tok[2]}: '{name}' is not a local variable")
        comps = None
        if self.at("."):
            self.take()
            comps = self.take()[1].translate(str.maketrans("rgba", "xyzw"))
            if gtype not in WIDTH or not re.fullmatch(r"[xyzw]{1,4}", comps):
                raise CannotConvert(f"line {tok[2]}: can't assign to '.{comps}' here")
        if self.at("["):
            self.refuse("assigning through [ ]")
        op = self.take()[1]
        if op in ("++", "--"):
            self.take(";")
            op, value = op[0] + "=", "1.0"
        elif op in ("=", "+=", "-=", "*=", "/="):
            value = self.expr()
            self.take(";")
        else:
            raise CannotConvert(f"line {tok[2]}: unexpected '{op}'")
        current = f"{target}.{comps}" if comps else target
        if op != "=":
            value = f"({current} {op[0]} ({value}))"
        if not comps:
            return [self.guarded(target, value)]
        lines = []
        if len(comps) > 1:
            tmp = self.fresh("part")
            lines.append(f"{tmp} = {value}")
        parts = []
        for k, c in enumerate("xyzw"[:WIDTH[gtype]]):
            if c in comps:
                parts.append(value if len(comps) == 1 else f"{tmp}.{'xyzw'[comps.index(c)]}")
            else:
                parts.append(f"{target}.{c}")
        lines.append(self.guarded(target, f"{gtype}({', '.join(parts)})"))
        return lines

    # -- expressions (with their types, so vec4(c, 1.0) can be spelled out component by component) ----
    def typed(self, text, gtype):
        if gtype:
            self.etypes[text] = gtype
        return text

    def widest(self, *texts):
        known = [self.etypes.get(t) for t in texts if self.etypes.get(t)]
        return max(known, key=lambda g: WIDTH.get(g, 1)) if known else None

    def expr(self):
        cond = self.or_expr()
        if self.at("?"):
            self.take()
            a = self.expr()
            self.take(":")
            b = self.expr()
            return self.typed(f"({a} if {cond} else {b})", self.widest(a, b))
        return cond

    def _binary(self, sub, ops, py=None):
        left = sub()
        while self.peek()[1] in ops:
            op = self.take()[1]
            right = sub()
            gtype = "bool" if op in ("<", ">", "<=", ">=", "==", "!=", "&&", "||") else self.widest(left, right)
            left = self.typed(f"({left} {(py or {}).get(op, op)} {right})", gtype)
        return left

    def unary(self):
        if self.at("-"):
            self.take()
            inner = self.unary()
            return self.typed(f"(-{inner})", self.etypes.get(inner))
        return super().unary()

    def postfix(self):
        tok = self.peek()
        if self.vertex is not None and tok[1] == self.vertex and self.peek(1)[1] == ".":
            self.take()
            self.take(".")
            fname = self.take()[1]
            value = self.typed(self.field(fname), VERTEX[fname][0])
        else:
            value = self.primary()
        while self.at(".") or self.at("["):
            if self.at("."):
                self.take()
                member = self.take()[1]
                if not re.fullmatch(r"[xyzwrgba]{1,4}", member):
                    raise CannotConvert(f"line {self.line()}: '.{member}' isn't a vector component")
                value = self.typed(f"{value}.{member.translate(str.maketrans('rgba', 'xyzw'))}",
                                   "float" if len(member) == 1 else f"vec{len(member)}")
            else:
                self.take()
                index = self.expr()
                self.take("]")
                value = self.typed(f"{value}[{index}]", "float")
        return value

    def primary(self):
        tok = self.peek()
        value = super().primary()
        if tok[0] == "num":
            return self.typed(value, "float")
        if tok[1] == "(":
            return self.typed(value, self.etypes.get(value[1:-1]))
        if tok[0] == "name" and value not in self.etypes:
            if tok[1] in self.local:
                return self.typed(value, self.types.get(tok[1]))
            if tok[1] in self.colors:
                return self.typed(value, "vec3")
            return self.typed(value, "float")         # sliders and time
        return value

    def call(self, fn, args, line):
        if fn in self.func_ins:
            raise CannotConvert(f"line {line}: '{fn}' is a function input; the render form doesn't follow "
                                "what's wired into it yet")
        if fn in GPU_ONLY:
            raise CannotConvert(f"line {line}: {fn}() is CodeNodes' GPU noise or randomness, which stock nodes "
                                "can't reproduce exactly")
        if fn in WIDTH and len(args) > 1:
            n = WIDTH[fn]
            widths = [WIDTH.get(self.etypes.get(a), 1) for a in args]
            if sum(widths) != n:
                raise CannotConvert(f"line {line}: can't tell the sizes of the parts of this {fn}()")
            parts = []
            for a, w in zip(args, widths):
                parts += [a] if w == 1 else [f"({a}).{c}" for c in "xyzw"[:w]]
            return self.typed(f"{fn}({', '.join(parts)})", fn)
        text = super().call(fn, args, line)
        if fn in WIDTH:
            gtype = fn
        elif fn in ("length", "dot", "distance", "float") or fn.startswith("sd") or fn in ("smin", "smax"):
            gtype = "float"
        elif fn in ("cross", "rotateX", "rotateY", "rotateZ"):
            gtype = "vec3"
        elif fn in self.rtypes:
            gtype = self.rtypes[fn]
        else:
            gtype = self.widest(*args)
        return self.typed(text, gtype)


def translate(source, time_offset=0.0):
    """GLSL deform / warp code -> (ExpressNode source, params, kind). Raises CannotConvert."""
    kind = kind_of(source)
    try:
        decls = decl.parse(source)
    except Exception as exc:
        raise CannotConvert(str(exc)) from None
    tr = _Translator(source, decls, time_offset, kind)
    body = tr.program()
    args = ", ".join(f"{(p.name + '_') if p.name in TAKEN else p.name}={float(p.default)!r}"
                     for p in decls.params)
    lines = [f"def {kind}(P{', ' + args if args else ''}):"]
    order = [h for h in HELPERS if h in tr.used_helpers]
    order.sort(key=lambda h: 0 if h in ("sdBox", "smin") else 1)
    for name in order:
        lines += ["    " + line for line in HELPERS[name].splitlines()]
    for text in tr.user_fns.values():
        lines += ["    " + line for line in text.splitlines()]
    lines += ["    " + line for line in body]
    return "\n".join(lines) + "\n", decls.params, kind


# ---- in Blender ---------------------------------------------------------------------------

PREFIX = "CN Render Form "


def build(source, name, time_offset=None):
    """The render form as a Geometry-in / Geometry-out node group called PREFIX + name, its inputs the
    code's sliders. `time_offset` (seconds) is subtracted from the scene's time; by default the scene's
    first frame, so uTime is 0 there, as in the live preview."""
    import bpy
    if time_offset is None:
        scene = bpy.context.scene
        time_offset = scene.frame_start / (scene.render.fps / (scene.render.fps_base or 1.0))
    py, params, kind = translate(source, time_offset)
    build_in_blender = bake_nodes._expression_nodes()
    try:
        tree = build_in_blender(py, apply_mode="absolute", suffix=" " + name)
    except Exception as exc:
        raise CannotConvert(f"ExpressNode could not compile it: {exc}") from None
    _colour_attribute(tree)
    full = PREFIX + name
    old = bpy.data.node_groups.get(full)
    if old is not None and old != tree:
        bpy.data.node_groups.remove(old)
    tree.name = full
    tree["codenodes_render_form"] = kind
    return tree


def _colour_attribute(tree):
    """ExpressNode stores a vec4 as a vector; CodeNodes' `color` is a colour attribute (as the GPU path
    writes it), so a material's Attribute node and the viewport's colour display read it alike."""
    for node in list(tree.nodes):
        if node.bl_idname != "GeometryNodeStoreNamedAttribute" or node.inputs["Name"].default_value != "color":
            continue
        if node.data_type == 'FLOAT_COLOR':
            continue
        value = next(s for s in node.inputs if s.name == "Value" and s.enabled)
        link = next((l for l in tree.links if l.to_socket == value), None)
        source = link.from_socket if link else None
        node.data_type = 'FLOAT_COLOR'
        if source is not None:
            tree.links.new(source, next(s for s in node.inputs if s.name == "Value" and s.enabled))
