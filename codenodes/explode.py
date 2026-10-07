"""Explode and Collapse: a code node's top-level pieces onto the graph, and back into the code.

Explode takes the pieces at the top level of a node's code and makes each one a node of its own, wired
back into the node:

    const float CALM = 0.92;            ->  a Value node CALM            (the code gets  // @in float CALM 0.92)
    const vec3 PAL[3] = vec3[3](...);   ->  a List node PAL, one row each (// @in list vec3 PAL; PAL[i] -> PAL(i))
    vec3 curl(vec3 p) { ... }           ->  a Function node curl          (// @in func vec3 curl(vec3 p))
    void behave(...) / spawn / look ... ->  stays: the node's own entry points (and the functions it offers)

Each piece's declaration line goes where the piece was, so Collapse puts every piece back in its place:
Explode then Collapse gives back the code exactly. A piece that can't stand on its own stays in the code:
a constant another top-level declaration needs (`const float B = A * 2.0;`, an array size), a function
using things only the code has (structs, #defines, globals, a hidden or material input), or one written
twice (overloads).

Pure Python: no bpy, so it can be tested anywhere. The Blender side (explode_ops.py) builds the nodes.
"""

from __future__ import annotations

import re

from . import decl
from .sdf_code import SdfCodeError

ENTRY = {"spawn", "update", "born", "behave", "look", "warp", "deform", "sdf", "color"}
_NAME = r"[A-Za-z_]\w*"
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_FUNC = re.compile(rf"^\s*({_NAME})\s+({_NAME})\s*\(([^)]*)\)\s*\{{")
_CONST = re.compile(rf"^\s*const\s+(float|int)\s+({_NAME})\s*=\s*({_NUM})\s*;\s*(?://.*)?$")
_ARRAY = re.compile(rf"^\s*const\s+(float|int|vec2|vec3|vec4)\s+({_NAME})\s*\[\s*(\d*)\s*\]\s*=")
_IDENT = re.compile(r"(?<![.\w])([A-Za-z_]\w*)\b")
_KEYWORDS = {"void", "float", "int", "uint", "bool", "vec2", "vec3", "vec4", "ivec2", "ivec3", "ivec4", "mat2",
             "mat3", "mat4", "const", "in", "out", "inout", "return", "if", "else", "for", "while", "do",
             "break", "continue", "struct", "true", "false", "discard", "uvec2", "uvec3", "uvec4"}


class Item:
    """One top-level piece of a code: lines [first, last] (0-based, inclusive)."""

    def __init__(self, kind, name, first, last, text, **kw):
        self.kind, self.name, self.first, self.last, self.text = kind, name, first, last, text
        self.ret = kw.get("ret")
        self.args = kw.get("args")
        self.type = kw.get("type")
        self.value = kw.get("value")        # a constant's literal, as written
        self.rows = kw.get("rows")          # an array's elements: [float] or [(x, y, z)]


class Piece:
    """What Explode makes of an item: kind 'value' | 'list' | 'func', and the code for a node when it has one."""

    def __init__(self, item, code=None, needs=()):
        self.item = item
        self.kind = {"const": "value", "array": "list", "func": "func"}[item.kind]
        self.name = item.name
        self.code = code
        self.needs = list(needs)            # [(kind, name)]: what the piece's node takes: slider / value / func / list


class Exploded:
    def __init__(self, core, pieces, originals):
        self.core = core                    # the node's code with each piece replaced by its declaration
        self.pieces = pieces                # [Piece] in the order they appear
        self.originals = originals          # {name: the piece's text as it was}: Collapse writes these back


def _blank_comments(lines):
    """Each line with comments replaced by spaces (so braces and names in comments don't count)."""
    out, block = [], False
    for line in lines:
        res, i = [], 0
        while i < len(line):
            if block:
                j = line.find("*/", i)
                if j < 0:
                    res.append(" " * (len(line) - i))
                    i = len(line)
                else:
                    res.append(" " * (j + 2 - i))
                    i = j + 2
                    block = False
            elif line.startswith("//", i):
                res.append(" " * (len(line) - i))
                i = len(line)
            elif line.startswith("/*", i):
                block = True
            else:
                res.append(line[i])
                i += 1
        out.append("".join(res))
    return out


def scan(code):
    """The top-level items of a code: functions, scalar constants and constant arrays, in order."""
    lines = code.splitlines()
    clean = _blank_comments(lines)
    items, depth, i = [], 0, 0
    while i < len(lines):
        line = clean[i]
        if depth == 0:
            m = _FUNC.match(line)
            if m and m.group(1) not in ("else", "return") and m.group(2) not in ("if", "for", "while", "switch"):
                end = _close(clean, i, "{", "}")
                if end is not None:
                    items.append(Item("func", m.group(2), i, end, "\n".join(lines[i:end + 1]),
                                      ret=m.group(1), args=m.group(3).strip()))
                    i = end + 1
                    continue
            m = _CONST.match(line)
            if m:
                items.append(Item("const", m.group(2), i, i, lines[i], type=m.group(1), value=m.group(3)))
                i += 1
                continue
            m = _ARRAY.match(line)
            if m:
                end = _statement_end(clean, i)
                if end is not None:
                    text = "\n".join(lines[i:end + 1])
                    rows = _array_rows("\n".join(clean[i:end + 1]), m.group(1))
                    if rows is not None:
                        items.append(Item("array", m.group(2), i, end, text, type=m.group(1), rows=rows))
                    i = end + 1
                    continue
        depth += line.count("{") - line.count("}")
        i += 1
    return items


def _close(clean, start, a, b):
    depth, seen = 0, False
    for k in range(start, len(clean)):
        for ch in clean[k]:
            if ch == a:
                depth += 1
                seen = True
            elif ch == b:
                depth -= 1
                if seen and depth == 0:
                    return k
    return None


def _statement_end(clean, start):
    for k in range(start, len(clean)):
        if ";" in clean[k]:
            return k
    return None


def _array_rows(text, t):
    """The elements of `const T NAME[N] = T[N](a, b, ...);` when every one is a literal, else None."""
    m = re.search(r"=\s*\w+\s*\[\s*\d*\s*\]\s*\((.*)\)\s*;", text, re.S)
    if not m:
        return None
    body = m.group(1)
    if t in ("float", "int"):
        cells = [c.strip() for c in body.split(",")]
        if not all(re.fullmatch(_NUM, c) for c in cells):
            return None
        return [float(c) for c in cells]
    rows = []
    for vm in re.finditer(rf"{t}\s*\(([^()]*)\)", body):
        cells = [c.strip() for c in vm.group(1).split(",")]
        if not all(re.fullmatch(_NUM, c) for c in cells):
            return None
        size = decl.SIZES[t]
        if len(cells) == 1:
            cells = cells * size
        if len(cells) != size:
            return None
        rows.append(tuple(float(c) for c in cells))
    leftover = re.sub(rf"{t}\s*\([^()]*\)", "", body).replace(",", "").strip()
    return rows if rows and not leftover else None


def _idents(text):
    return set(_IDENT.findall("\n".join(_blank_comments(text.splitlines()))))


def _decl_lines(code):
    """{declared name: its declaration line} for the code's sliders, colours, functions and lists."""
    out = {}
    for line in code.splitlines():
        m = decl.DECL_LINE.match(line)
        p = decl._PARAM_RE.match(line)
        if p:
            out[p.group(1)] = line
        elif m and m.group(1) == "in":
            rest, _desc = decl.split_description(m.group(2).strip())
            name = decl._declared_name("in", rest.split(), rest)
            if name:
                out[name] = line
    return out


def _index_to_call(text, name):
    """NAME[expr] -> NAME(expr) and NAME.length() -> NAME_count()."""
    text = re.sub(rf"(?<![.\w]){name}\s*\.\s*length\s*\(\s*\)", f"{name}_count()", text)
    out, i = [], 0
    pat = re.compile(rf"(?<![.\w]){name}\s*\[")
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        j = _match(text, m.end() - 1, "[", "]")
        if j is None:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:m.start()] + f"{name}(" + text[m.end():j] + ")")
        i = j + 1


def _call_to_index(text, name):
    """The reverse: NAME(expr) -> NAME[expr], NAME_count() -> NAME.length()."""
    text = re.sub(rf"(?<![.\w]){name}_count\s*\(\s*\)", f"{name}.length()", text)
    out, i = [], 0
    pat = re.compile(rf"(?<![.\w]){name}\s*\(")
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        j = _match(text, m.end() - 1, "(", ")")
        if j is None:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:m.start()] + f"{name}[" + text[m.end():j] + "]")
        i = j + 1


def _match(text, k, a, b):
    depth = 0
    for j in range(k, len(text)):
        if text[j] == a:
            depth += 1
        elif text[j] == b:
            depth -= 1
            if depth == 0:
                return j
    return None


def _num(v):
    t = f"{float(v):.6g}"
    return t if any(c in t for c in ".eEn") else t + ".0"


HEADER = "// {name}: a piece of {node} (Explode). Collapse puts it back into {node}'s code."


def explode(code, node="the node"):
    """Exploded(core code, pieces, originals). Raises SdfCodeError when the code has a mistake in it."""
    d = decl.parse(code)
    if d.table is not None:
        raise SdfCodeError("a List is already one piece")
    keep = ENTRY | {f.name for f in d.func_outs}
    items = scan(code)
    lines = code.splitlines()
    clean = _blank_comments(lines)
    names = [it.name for it in items]
    params = {p.name for p in d.params} | set(d.colors) | {f.name for f in d.func_ins} | {l.name for l in d.lists}
    unsafe_params = set(d.hidden) | set(d.materials) | {f"{m}_{part}" for m in d.materials
                                                        for part, _v in decl.MATERIAL_PARTS}
    unsafe_params |= {c for c, parts in d.colors.items() if set(parts) & set(d.hidden)}
    # everything at the top level that isn't one of the items (structs, #defines, globals, kept things)
    item_lines = {k for it in items for k in range(it.first, it.last + 1)}
    rest = "\n".join(clean[k] for k in range(len(lines)) if k not in item_lines)
    rest_ids = _idents(rest)
    top_other = _top_level_names(rest)
    in_brackets = set()
    for m in re.finditer(r"\[([^\]]*)\]", "\n".join(clean)):
        in_brackets |= _idents(m.group(1))
    dup = {n for n in names if names.count(n) > 1}

    cands = {}
    for it in items:
        if it.name in dup or it.name in params:
            continue
        if it.kind == "const" and (it.name in rest_ids or it.name in in_brackets):
            continue                  # another top-level declaration (or an array size) needs a constant
        if it.kind == "func" and it.name in keep:
            continue
        cands[it.name] = it
    # constants other kept items use at the top level (const B = A * 2, an array of A) stay
    for it in items:
        if it.name not in cands and it.kind in ("const", "array"):
            for n in _idents(it.text) - {it.name}:
                if n in cands and cands[n].kind == "const":
                    del cands[n]
    # a function can go when everything it uses can come in through a socket
    changed = True
    while changed:
        changed = False
        for name, it in list(cands.items()):
            if it.kind != "func":
                continue
            used = _idents(it.text) - {name} - _KEYWORDS
            bad = (used & top_other) | (used & unsafe_params) | \
                  {n for n in used & set(names) if n not in cands}
            if bad:
                del cands[name]
                changed = True
    if not cands:
        return Exploded(code, [], {})

    decls = _decl_lines(code)
    arrays = [n for n, it in cands.items() if it.kind == "array"]
    out_lines = list(lines)
    pieces, originals = [], {}
    for it in sorted((cands[n] for n in cands), key=lambda x: x.first):
        originals[it.name] = it.text
        if it.kind == "const":
            line = f"// @in {it.type} {it.name} {it.value}"
            pieces.append(Piece(it))
        elif it.kind == "array":
            line = f"// @in list {it.type} {it.name}"
            table = decl.Table()
            col = decl.Column("value", 1 if it.type in ("float", "int") else decl.SIZES[it.type])
            col.values = list(it.rows)
            table.columns.append(col)
            body = decl.set_table(f"// {it.name}: a piece of {node} (Explode). One row per element.\n// @list\n",
                                  table)
            pieces.append(Piece(it, body))
        else:
            line = f"// @in func {it.ret} {it.name}({it.args})"
            used = _idents(it.text) - {it.name}
            needs, head = [], [HEADER.format(name=it.name, node=node)]
            for n in sorted(used & params, key=lambda x: list(decls).index(x) if x in decls else 0):
                if n in decls:
                    head.append(decls[n].strip())
                    needs.append(("slider", n))
            for n in sorted(used & set(cands), key=lambda x: cands[x].first):
                o = cands[n]
                if o.kind == "const":
                    head.append(f"// @in {o.type} {n} {o.value}")
                    needs.append(("value", n))
                elif o.kind == "array":
                    head.append(f"// @in list {o.type} {n}")
                    needs.append(("list", n))
                else:
                    head.append(f"// @in func {o.ret} {n}({o.args})")
                    needs.append(("func", n))
            head.append(f"// @out func {it.name}")
            text = it.text
            for a in arrays:
                text = _index_to_call(text, a)
            pieces.append(Piece(it, "\n".join(head) + "\n" + text + "\n", needs))
        for k in range(it.first, it.last + 1):
            out_lines[k] = None
        out_lines[it.first] = line
    core = "\n".join(l for l in out_lines if l is not None) + ("\n" if code.endswith("\n") else "")
    for a in arrays:
        core = _rewrite_code_only(core, a, _index_to_call)
    return Exploded(core, pieces, originals)


def _rewrite_code_only(code, name, fn):
    """Apply fn to the code outside comments and declaration lines."""
    out = []
    for line in code.splitlines():
        if decl.DECL_LINE.match(line) or decl._PARAM_RE.match(line):
            out.append(line)
            continue
        c, sep, comment = line.partition("//")
        out.append(fn(c, name) + sep + comment)
    return "\n".join(out) + ("\n" if code.endswith("\n") else "")


def _top_level_names(rest):
    """Names a code declares at the top level outside its items: structs, #defines, globals."""
    names = set()
    for m in re.finditer(rf"\bstruct\s+({_NAME})", rest):
        names.add(m.group(1))
    for m in re.finditer(rf"^\s*#\s*define\s+({_NAME})", rest, re.M):
        names.add(m.group(1))
    depth = 0
    for line in rest.splitlines():
        if depth == 0:
            m = re.match(rf"^\s*(?:const\s+|uniform\s+)?({_NAME})\s+({_NAME})\s*(?:\[[^\]]*\])?\s*[=;]", line)
            if m and m.group(1) not in ("return",):
                names.add(m.group(2))
        depth += line.count("{") - line.count("}")
    return names


def piece_body(piece_code):
    """A function piece's own function text: its code without the header comment and declaration lines."""
    out = []
    for line in piece_code.splitlines():
        if decl.DECL_LINE.match(line) or decl._PARAM_RE.match(line):
            continue
        if not out and re.match(r"^\s*//.*\(Explode\)", line):
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def collapse(core, pieces, originals=None, arrays=()):
    """The code with pieces put back where their declaration lines are.

    `pieces` is {name: ('value', number) | ('list', decl.Table or rows) | ('func', piece code)} for the
    inputs wired to a piece; anything else stays an input. `originals` (from Explode) is used for a piece
    that hasn't changed, so Explode then Collapse gives back the code exactly. `arrays` names the lists that
    were arrays (their calls go back to indexing)."""
    originals = originals or {}
    lines = core.splitlines()
    out = []
    collapsed_arrays = []
    for line in lines:
        m = decl.DECL_LINE.match(line)
        name = None
        if m and m.group(1) == "in":
            rest, _desc = decl.split_description(m.group(2).strip())
            name = decl._declared_name("in", rest.split(), rest)
        if name is None or name not in pieces:
            out.append(line)
            continue
        kind, data = pieces[name]
        orig = originals.get(name)
        bits = m.group(2).split()
        if kind == "value":
            t, lit = bits[0], bits[2] if len(bits) > 2 else "0"
            same = orig is not None and abs(float(lit) - float(data)) < 1e-6 * max(1.0, abs(float(lit)))
            if same:
                out.append(orig)
            else:
                v = str(int(round(float(data)))) if t == "int" else _num(data)
                out.append(f"const {t} {name} = {v};")
        elif kind == "list":
            t = bits[1]
            rows = _rows_of(data)
            o = scan(orig)[0] if orig else None
            if o is not None and o.rows is not None and _same_rows(o.rows, rows):
                out.append(orig)
            else:
                n = len(rows)
                if t in ("float", "int"):
                    cells = [str(int(round(r))) if t == "int" else _num(r) for r in rows]
                else:
                    cells = [f"{t}({', '.join(_num(x) for x in r)})" for r in rows]
                out.append(f"const {t} {name}[{n}] = {t}[{n}](" + ", ".join(cells) + ");")
            if name in arrays:
                collapsed_arrays.append(name)
        else:
            body = piece_body(data)
            for a in arrays:
                body = _call_to_index(body, a)
            out.append(orig if orig is not None and _squash(orig) == _squash(body) else body)
    code = "\n".join(out) + ("\n" if core.endswith("\n") else "")
    for a in collapsed_arrays:
        code = _rewrite_code_only(code, a, _call_to_index)
    return code


def _rows_of(data):
    if isinstance(data, decl.Table):
        c = data.column("value") or (data.columns[0] if data.columns else None)
        return list(c.values) if c is not None else []
    return list(data)


def _same_rows(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        xs = x if isinstance(x, tuple) else (x,)
        ys = y if isinstance(y, tuple) else (y,)
        if len(xs) != len(ys) or any(abs(p - q) > 1e-6 * max(1.0, abs(p)) for p, q in zip(xs, ys)):
            return False
    return True


def _squash(t):
    return re.sub(r"\s+", "", t)
