"""Compile a CodeNodes graph into one sdf() program. Pure Python, no bpy.

The graph is plain data, so it can be built by the node editor, by a script or by
Claude, and tested without Blender:

    nodes = {
      "Ball":  {"kind": "code", "code": "// @param r 1.0\\nfloat sdf(vec3 p){ return length(p) - r; }",
                "values": {"r": 0.8}},
      "Box":   {"kind": "code", "code": "float sdf(vec3 p){ return sdBox(p, vec3(0.6)); }"},
      "Blend": {"kind": "smooth_union", "inputs": {"A": "Ball", "B": "Box"}, "values": {"K": 0.3}},
    }
    compile_graph(nodes, "Blend") -> Program(source, values, segments)

Kinds and their inputs:
    code          sdf code (with @param sliders)                     values: its params
    union, subtract, intersect             inputs A, B
    smooth_union, smooth_subtract          inputs A, B               values: K
    transform     input SDF                values: location (3), rotation (3, radians XYZ), scale
    offset        input SDF (grow/shrink)  values: amount
An unconnected SDF input counts as empty space.
"""

from __future__ import annotations

import re

from .sdf_code import SdfCodeError, _PARAM_RE, check_source, parse_params

# Top-level functions and constants (written at the start of a line) get a per-node prefix.
_DECL = re.compile(r"^(?:const\s+)?(?:float|int|uint|bool|[iu]?vec[234]|mat[234])\s+([A-Za-z_]\w*)\s*[\(=;]", re.M)

OPS = {
    "union": ("min(a, b)", ()),
    "subtract": ("max(a, -b)", ()),
    "intersect": ("max(a, b)", ()),
    "smooth_union": ("smin(a, b, max(K, 1e-5))", (("K", 0.25),)),
    "smooth_subtract": ("smax(a, -b, max(K, 1e-5))", (("K", 0.25),)),
}
EMPTY = "1e10"


class Program:
    """Compiled graph: sdf source (with @param lines), slider values, and where each node's code sits."""

    def __init__(self, source, values, segments):
        self.source = source
        self.values = values
        self.segments = segments       # [(first_line, last_line, node_name)], 1-based lines in `source`

    def locate(self, line):
        """(node_name, line within that node's code) for a line of `source`, or None."""
        for first, last, name in self.segments:
            if first <= line <= last:
                return name, line - first + 1
        return None


def _ident(name):
    return re.sub(r"\W", "_", name)


def compile_graph(nodes, root):
    """Compile the graph that feeds `root` (a node name) into a Program."""
    if root not in nodes:
        raise SdfCodeError(f"nothing is connected: node '{root}' not found")
    params, values = [], {}     # (uniform, default, min, max)
    chunks, segments = [], []
    done, visiting = {}, []
    line = [1]                  # next line number in the output

    def add(text, owner=None):
        n = text.count("\n")
        if owner is not None:
            segments.append((line[0], line[0] + n - 1, owner))
        chunks.append(text)
        line[0] += n

    def param(uniform, default, lo, hi, value):
        params.append((uniform, default, lo, hi))
        values[uniform] = float(value)

    def emit(name):
        if name in done:
            return done[name]
        if name in visiting:
            loop = " -> ".join(visiting[visiting.index(name):] + [name])
            raise SdfCodeError(f"the graph loops back on itself: {loop}")
        node = nodes.get(name)
        if node is None:
            raise SdfCodeError(f"a link points to a missing node '{name}'")
        visiting.append(name)
        prefix = f"n{len(done)}_{_ident(name)}_"
        kind = node.get("kind")
        vals = node.get("values", {})
        inputs = node.get("inputs", {})

        if kind == "code":
            code = node.get("code", "")
            try:
                check_source(code)
                own = parse_params(code)
            except SdfCodeError as exc:
                raise SdfCodeError(f"node '{name}': {exc}") from None
            renames = {p.name: prefix + p.name for p in own}
            renames.update({d: prefix + d for d in set(_DECL.findall(code))})
            body = "\n".join("" if _PARAM_RE.match(l) else l for l in code.splitlines())   # keep line numbers
            if renames:
                # (?<!\.) leaves swizzles like p.x alone
                body = re.sub(r"(?<!\.)\b(" + "|".join(map(re.escape, sorted(renames, key=len, reverse=True))) + r")\b",
                              lambda m: renames[m.group(1)], body)
            for prm in own:
                param(renames[prm.name], prm.default, prm.min, prm.max, vals.get(prm.name, prm.default))
            add(body + "\n", owner=name)
            fn = prefix + "sdf"

        elif kind in OPS:
            expr, knobs = OPS[kind]
            a = emit(inputs["A"]) + "(p)" if inputs.get("A") else EMPTY
            b = emit(inputs["B"]) + "(p)" if inputs.get("B") else EMPTY
            for knob, default in knobs:
                param(prefix + knob, default, 0.0, 2.0, vals.get(knob, default))
                expr = re.sub(rf"\b{knob}\b", prefix + knob, expr)
            fn = prefix + "sdf"
            add(f"float {fn}(vec3 p) {{ float a = {a}; float b = {b}; return {expr}; }}\n")

        elif kind == "transform":
            child = emit(inputs["SDF"]) if inputs.get("SDF") else None
            loc = vals.get("location", (0.0, 0.0, 0.0))
            rot = vals.get("rotation", (0.0, 0.0, 0.0))
            for axis, v in zip("xyz", loc):
                param(f"{prefix}loc_{axis}", 0.0, -10.0, 10.0, v)
            for axis, v in zip("xyz", rot):
                param(f"{prefix}rot_{axis}", 0.0, -6.2832, 6.2832, v)
            param(prefix + "scale", 1.0, 0.01, 10.0, vals.get("scale", 1.0))
            fn = prefix + "sdf"
            if child is None:
                add(f"float {fn}(vec3 p) {{ return {EMPTY}; }}\n")
            else:
                # undo the transform on the point: translate, then rotate Z, Y, X (inverse of XYZ Euler), then scale
                add(f"float {fn}(vec3 p) {{\n"
                    f"  vec3 q = p - vec3({prefix}loc_x, {prefix}loc_y, {prefix}loc_z);\n"
                    f"  q = rotateX(rotateY(rotateZ(q, -{prefix}rot_z), -{prefix}rot_y), -{prefix}rot_x);\n"
                    f"  float s = max({prefix}scale, 1e-4);\n"
                    f"  return {child}(q / s) * s;\n}}\n")

        elif kind == "offset":
            child = emit(inputs["SDF"]) if inputs.get("SDF") else None
            param(prefix + "amount", 0.0, -1.0, 1.0, vals.get("amount", 0.0))
            fn = prefix + "sdf"
            body = f"{child}(p) - {prefix}amount" if child else EMPTY
            add(f"float {fn}(vec3 p) {{ return {body}; }}\n")

        else:
            raise SdfCodeError(f"node '{name}' has an unknown kind '{kind}'")

        visiting.pop()
        done[name] = fn
        return fn

    top = emit(root)
    add(f"float sdf(vec3 p) {{ return {top}(p); }}\n")
    header = "".join(f"// @param {u} {d:g} {lo:g} {hi:g}\n" for u, d, lo, hi in params)
    n_head = header.count("\n")
    segments = [(a + n_head, b + n_head, who) for a, b, who in segments]
    return Program(header + "".join(chunks), values, segments)
