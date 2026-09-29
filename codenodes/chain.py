"""Chains of code nodes, compiled into one GPU program.

Code nodes are wired like Geometry Nodes: a particle source, then any number of stages (each one a
node you write), then To Geometry or nothing (drawn live):

    [Firefly Swarm] -> [Wander] -> [Rise] -> [Blink] -> [Firefly Look] -> (live) / To Geometry
          spawn          behave     behave    born+behave    look

A mesh chain works the same way: [GPU Mesh] or plain geometry -> [Bend] -> [Ripple] -> To Geometry.

Every node's code keeps its own names: its functions, constants and sliders get a per-node prefix
(n0_, n1_, ...), so two nodes can both define `strength` or `hash()`. The chain is then stitched into
the entry points the GPU runs:

    cnSpawn(p)      the source's spawn, then every stage's born(p)
    cnUpdate(p, dt) the source's update, then every stage's behave(p, dt), in order
    cnWarp(q)       every warp(q) in order: where particles are drawn and made real, not where they
                    are simulated, so a Bend stretches the swirl without changing its motion
    look(p)         the last stage that has one
    deform(v)       for meshes: each deform(v), and warps applied to v.position, in order

Function inputs (`// @in func vec3 wind(vec3 p)`) are wired to another node's function output
(`// @out func field`); the provider's code is included once with its own prefix and the input name
becomes an alias for it. Per-particle attributes (`// @out attr brightness 1.0`) share one vec4 of
state per particle, so any later node can read or write `p.brightness`.

Pure Python: the composite is plain text, compiled by particles.py / deform.py.
"""

from __future__ import annotations

import re

from . import decl
from .sdf_code import SdfCodeError

# composed source (sha1) -> [(first line, last line, node name, that node's own code lines)], so compiler
# errors can name the node and the line in the code you wrote
SEGMENTS: dict[str, list] = {}

# top-level declarations (functions, constants) that get the per-node prefix; `void` included
_DECL = re.compile(r"^(?:const\s+)?(?:void|float|int|uint|bool|[iu]?vec[234]|mat[234])\s+([A-Za-z_]\w*)\s*[\(=;]",
                   re.M)
_ZERO = {"float": "0.0", "int": "0", "bool": "false", "vec2": "vec2(0.0)", "vec3": "vec3(0.0)",
         "vec4": "vec4(0.0)", "uint": "0u", "mat3": "mat3(1.0)", "mat4": "mat4(1.0)"}


class Unit:
    """One code node in a chain: its name (for messages), code and slider values."""

    def __init__(self, name, code, values=None, funcs=None):
        self.name = name
        self.code = code
        self.values = dict(values or {})          # slider name -> value (colour parts by part name)
        self.funcs = dict(funcs or {})            # function-input name -> (provider Unit, export name)
        self.decls = None


class Composite:
    """A compiled chain: the source to hand to the GPU compiler, and the values for its sliders."""

    def __init__(self):
        self.source = ""
        self.values = {}
        self.segments = []            # (first line, last line, node name, first line of that node's code)
        self.attrs = []               # [(name, default)] in cnX order
        self.has_look = False
        self.has_warp = False
        self.shape = None             # the last Look's @shape
        self.look_unit = None         # prefix of the node whose look() is used (its size / flap sliders)
        self.warnings = []
        self.slots = []               # (node name, slider name, key in values): to refresh values cheaply
        # what the simulation depends on (the source and every born/behave stage, the functions they
        # call and the per-particle attributes): two pipelines with the same sim_key can share one GPU
        # simulation and differ only in how they're drawn (looks, warps)
        self.sim_key = ""

    def values_from(self, lookup):
        """Fresh values from `lookup(node name, slider name)` (None keeps the value composed in)."""
        out = dict(self.values)
        for node, name, key in self.slots:
            v = lookup(node, name)
            if v is not None:
                out[key] = float(v)
        return out

    def locate(self, line):
        """(node name, line in that node's code) for a line of `source`, or None."""
        for first, last, name, _ in self.segments:
            if first <= line <= last:
                return name, line - first + 1
        return None

    def value(self, name, default=0.0):
        return self.values.get(name, default)


def translate_errors(text, source):
    """Rewrite "line N: ..." (N a line of the composed `source`) as the node and the line in its own
    code. A chain of one keeps the plain "line N" wording."""
    import hashlib
    segs = SEGMENTS.get(hashlib.sha1(source.encode()).hexdigest())
    if not segs:
        return text
    owners = {s[2] for s in segs}
    out, mapped = [], False
    for block in text.split("\n"):
        m = re.match(r"^line (\d+): (.*)$", block)
        if not m:
            if block.startswith("    ") and mapped:
                continue                   # the composed line: the node's own line is shown instead
            mapped = False
            out.append(block)
            continue
        n, msg = int(m.group(1)), m.group(2)
        hit = next(((a, name, code) for a, b, name, code in segs if a <= n <= b), None)
        if hit is None:
            mapped = False
            out.append(block)
            continue
        a, name, code = hit
        k = n - a + 1
        where = f"line {k}" if len(owners) == 1 else f"node '{name.replace('CN · ', '')}', line {k}"
        own = code[k - 1].strip() if code and 1 <= k <= len(code) else ""
        out.append(f"{where}: {msg}" + (f"\n    {own}" if own else ""))
        mapped = True
    return "\n".join(out)


def _ident(name):
    """A GLSL-safe piece of a name: no runs of underscores (GLSL reserves names containing '__')."""
    return re.sub(r"_+", "_", re.sub(r"\W", "_", name)).strip("_") or "node"


class _Builder:
    def __init__(self):
        self.header = []              # "// @param" lines
        self.defines = []             # #define lines that must come before any code
        self.chunks = []
        self.line = 1
        self.out = Composite()
        self.done_providers = {}      # id(unit) -> prefix

    def add(self, text, owner=None, code=None):
        n = text.count("\n")
        if owner is not None:
            self.out.segments.append((self.line, self.line + n - 1, owner, code))
        self.chunks.append(text)
        self.line += n

    def unit(self, u, prefix, allow=None):
        """Emit one node's code with its names prefixed. Returns the node's Decls."""
        try:
            d = decl.parse(u.code)
        except SdfCodeError as exc:
            raise SdfCodeError(f"node '{u.name}': {exc}") from None
        u.decls = d
        body = decl.strip(u.code)
        renames = {}
        for p in d.params:
            kind = d.kinds.get(p.name, 'FLOAT')
            if kind == 'INT':
                renames[p.name] = f"{prefix}{p.name}"
                slot = f"{prefix}{p.name}__i"
                self.header.append(f"// @param {slot} {p.default:g} {p.min:g} {p.max:g}\n")
                self.defines.append(f"#define {prefix}{p.name} int(floor({slot} + 0.5))\n")
                self.out.values[slot] = float(u.values.get(p.name, p.default))
                self.out.slots.append((u.name, p.name, slot))
            else:
                renames[p.name] = prefix + p.name
                self.header.append(f"// @param {prefix}{p.name} {p.default:g} {p.min:g} {p.max:g}\n")
                self.out.values[prefix + p.name] = float(u.values.get(p.name, p.default))
                self.out.slots.append((u.name, p.name, prefix + p.name))
        for name in d.colors:
            renames[name] = prefix + name
        self.defines.append(decl.color_defines(d, prefix))
        for f in d.func_ins:
            renames[f.name] = prefix + f.name
        renames.update({x: prefix + x for x in set(_DECL.findall(body))})
        for a in d.attrs:
            renames.pop(a.name, None)                 # attributes are shared by name across the chain
        if renames:
            pat = re.compile(r"(?<![.\w])(" + "|".join(map(re.escape, sorted(renames, key=len, reverse=True)))
                             + r")\b")
            lines = []
            for line in body.split("\n"):
                code, sep, comment = line.partition("//")       # comments keep the names you wrote
                lines.append(pat.sub(lambda m: renames[m.group(1)], code) + sep + comment)
            body = "\n".join(lines)
        # function inputs: an alias for the provider wired in, or a stand-in that returns zero
        for f in d.func_ins:
            link = u.funcs.get(f.name)
            if link is not None:
                provider, export = link
                pprefix = self.provider(provider)
                pd = provider.decls
                if export not in [o.name for o in pd.func_outs]:
                    raise SdfCodeError(f"node '{u.name}': input '{f.name}' is wired to '{provider.name}', "
                                       f"which has no function output called '{export}'")
                self.add(f"#define {prefix}{f.name} {pprefix}{export}\n")
            else:
                zero = f"{f.ret}({f.default})" if f.default else _ZERO.get(f.ret, f"{f.ret}(0.0)")
                self.add(f"{f.ret} {prefix}{f.name}({f.args}) {{ return {zero}; }}\n")
        for a in d.attrs:
            if a.name not in [x[0] for x in self.out.attrs]:
                if len(self.out.attrs) >= decl.MAX_ATTRS:
                    raise SdfCodeError(f"a chain can carry at most {decl.MAX_ATTRS} per-particle attributes")
                self.out.attrs.append((a.name, a.default))
        self.add(body + "\n", owner=u.name, code=u.code.splitlines())
        return d

    def provider(self, p):
        """Include a function provider's code once; returns its prefix."""
        key = id(p)
        if key in self.done_providers:
            return self.done_providers[key]
        prefix = f"f{len(self.done_providers)}_{_ident(p.name)[:12].rstrip('_')}_"
        self.done_providers[key] = prefix
        self.unit(p, prefix)
        return prefix

    def finish(self):
        attr_defs = "".join(f"#define {name} cnX.{'xyzw'[i]}\n" for i, (name, _d) in enumerate(self.out.attrs))
        defaults = [d for _n, d in self.out.attrs] + [0.0] * (4 - len(self.out.attrs))
        attr_defs += f"#define CN_XDEFAULT vec4({', '.join(f'{v:g}' for v in defaults)})\n"
        if self.out.attrs:
            attr_defs += "#define CN_HAS_X\n"
        pre = "".join(self.header) + attr_defs + "".join(self.defines)
        n_pre = pre.count("\n")
        self.out.segments = [(a + n_pre, b + n_pre, who, f) for a, b, who, f in self.out.segments]
        self.out.source = pre + "".join(self.chunks)
        import hashlib
        if len(SEGMENTS) > 128:
            SEGMENTS.clear()
        SEGMENTS[hashlib.sha1(self.out.source.encode()).hexdigest()] = self.out.segments
        return self.out


def compose_particles(head, stages=()):
    """A particle chain: `head` (the source, defines spawn) and `stages` in order."""
    b = _Builder()
    hd = b.unit(head, "n0_")
    if "spawn" not in hd.roles:
        raise SdfCodeError(f"node '{head.name}': a particle source must define  void spawn(inout Particle p)")
    borns, behaves, warps, look = [], [], [], None
    if "look" in hd.roles:
        look = "n0_"
        b.out.shape = hd.shape
    for i, st in enumerate(stages, 1):
        prefix = f"n{i}_"
        d = b.unit(st, prefix)
        useful = False
        if "born" in d.roles:
            borns.append(prefix)
            useful = True
        if "behave" in d.roles:
            behaves.append(prefix)
            useful = True
        if "warp" in d.roles:
            warps.append(prefix)
            useful = True
        if "look" in d.roles:
            look = prefix
            b.out.shape = d.shape or b.out.shape
            useful = True
        if not useful and not d.func_outs:
            b.out.warnings.append(f"'{st.name}' has nothing for particles to do (no born, behave, look "
                                  f"or warp)")
    import hashlib
    sim_units = [(head.name, tuple(sorted(hd.roles & {"spawn", "update", "born", "behave"})))]
    sim_units += [(st.name, tuple(sorted(st.decls.roles & {"born", "behave"}))) for st in stages
                  if st.decls is not None and st.decls.roles & {"born", "behave"}]
    sim_names = {n for n, _r in sim_units}
    sim_funcs = []
    for u in [head, *stages]:
        if u.name in sim_names:
            sim_funcs += sorted((u.name, k, v[0].name, v[1]) for k, v in u.funcs.items())
    b.out.sim_key = hashlib.sha1(repr((sim_units, sim_funcs, b.out.attrs)).encode()).hexdigest()
    glue = ["\n// ---- the chain ----\n"]
    glue.append("void cnSpawn(inout Particle p) {\n  n0_spawn(p);\n"
                + "".join(f"  {pf}born(p);\n" for pf in borns) + "}\n")
    # A source with its own update() moves its particles itself (behaviours' velocity changes carry
    # into its next step); otherwise the chain moves each particle by its velocity after the behaviours.
    if "update" in hd.roles:
        upd, move = "  n0_update(p, dt);\n", ""
    else:
        upd, move = "", "  p.position += p.velocity * dt;\n"
    glue.append("void cnUpdate(inout Particle p, float dt) {\n" + upd
                + "".join(f"  {pf}behave(p, dt);\n" for pf in behaves) + move + "}\n")
    glue.append("vec3 cnWarp(vec3 q) {\n" + "".join(f"  q = {pf}warp(q);\n" for pf in warps) + "  return q;\n}\n")
    if look is not None:
        glue.append(f"vec4 look(Particle p) {{ return {look}look(p); }}\n")
    b.add("".join(glue))
    out = b.finish()
    out.has_look = look is not None
    out.has_warp = bool(warps)
    out.look_unit = look
    return out


def compose_mesh(units):
    """A mesh chain: each unit's deform(v) and warp(q) in order (the first may be a GPU Mesh node)."""
    if not units:
        raise SdfCodeError("nothing to run on the mesh")
    b = _Builder()
    calls = []
    for i, u in enumerate(units):
        prefix = f"n{i}_"
        d = b.unit(u, prefix)
        if "deform" in d.roles:
            calls.append(f"  {prefix}deform(v);\n")
        if "warp" in d.roles:
            calls.append(f"  v.position = {prefix}warp(v.position);\n")
        if "deform" not in d.roles and "warp" not in d.roles and not d.func_outs:
            b.out.warnings.append(f"'{u.name}' has nothing to do to a mesh (no deform or warp)")
    b.add("\n// ---- the chain ----\nvoid deform(inout Vertex v) {\n" + "".join(calls) + "}\n")
    out = b.finish()
    out.has_warp = any("warp(" in c for c in calls)
    return out
