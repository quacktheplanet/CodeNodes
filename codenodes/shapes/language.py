"""The shape language: readable text in, a solid out.

    param height 0.30  0.05 1.0      # a slider, like @param elsewhere in CodeNodes; this comment is its tooltip
    param shade  0.16

    part shade                       # a named part; parts are joined at the end
      profile
        move  shade * 0.45, height
        line  shade, height * 0.45
        curve x = shade * (1 - 0.35 * t)  y = height * 0.45 * (1 - t)  steps 24
      revolve segments 64
      shell 0.004                    # give it thickness

    part base
      profile
        move 0, 0
        line 0.09, 0
        arc  0.10, 0.012  radius 0.014
        line 0.10, 0.02
        line 0, 0.02
        close
      revolve segments 64

Every number can be maths (`height * 0.45`, `sin(t * tau)`), so a shape is a formula,
not a list of coordinates. Nothing here can run code: the words below are the whole
language, and numbers go through the expression evaluator.
"""

from __future__ import annotations

import re

from . import solids
from .expr import ExprError, evaluate, integer, number

VECTOR_WORDS = ("move", "rotate", "scale", "offset")


class ShapeError(ExprError):
    """A problem with a shape description, with the line it came from."""


def _fail(line_no, message):
    raise ShapeError(f"line {line_no}: {message}")


def tokenise(line):
    """Split a line into the leading word and the rest, honouring `name = expression`."""
    parts = line.strip().split(None, 1)
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


# Keywords whose value is one plain word; everything else takes an expression, which
# runs on until the next keyword.
SINGLE_WORD = {"axis", "around", "clockwise", "cap"}

# What each command accepts. Knowing this is what lets `move shade_r * 0.45, height`
# keep "height" as maths while `array 6 around z` reads "around z" as a keyword.
KEYWORDS = {
    "move": (), "line": (),
    "arc": ("radius", "steps", "clockwise"),
    "curve": ("x", "y", "z", "steps"),
    "helix": ("radius", "pitch", "turns", "steps", "start"),
    "revolve": ("segments", "degrees", "angle", "axis", "cap"),
    "extrude": ("depth", "steps", "axis", "taper", "cap"),
    "sweep": ("twist", "scale", "cap"),
    "loft": ("steps", "axis", "cap"),
    "translate": ("x", "y", "z"), "rotate": ("x", "y", "z"), "scale": ("x", "y", "z"),
    "array": ("count", "around", "x", "y", "z"),
    "shell": ("thickness",), "smooth": (), "close": (),
    "bevel": ("width", "segments", "shape", "angle"),
}

PART_MODES = ("add", "subtract", "intersect")
PROFILE_WORDS = ("move", "line", "arc", "curve", "close", "shell")
PATH_WORDS = ("move", "line", "curve", "helix")
SOLID_WORDS = ("revolve", "extrude", "sweep", "loft", "translate", "rotate", "scale",
               "array", "smooth", "bevel")


def split_args(text, keywords=()):
    """Split `0.1, 0.02 radius 0.014` into ["0.1", "0.02"], {"radius": "0.014"}.

    Also handles `x = a * t  y = b`. Only the names in `keywords` start a keyword, so
    a variable of the same name inside an expression is left alone.
    """
    text = text.strip()
    if not text:
        return [], {}
    wanted = {k.lower() for k in keywords}
    if not wanted:
        return [p.strip() for p in text.split(",") if p.strip()], {}

    # A word only starts a keyword if it is outside brackets, is not the first token of
    # the previous keyword's value, and does not follow an operator. That last rule is
    # what keeps `turns (length - thick) / pitch` reading `pitch` as a variable, while
    # `pitch pitch` still reads as "the keyword, then a parameter of the same name".
    found, depth, skip_to, ignore_at = [], 0, 0, -1
    for match in re.finditer(r"[()]|[A-Za-z_]\w*", text):
        token = match.group(0)
        if token == "(":
            depth += 1
            continue
        if token == ")":
            depth -= 1
            continue
        if match.start() == ignore_at:
            ignore_at = -1
            continue
        before = text[:match.start()].rstrip()
        if depth or match.start() < skip_to or token.lower() not in wanted \
                or (before and before[-1] in "+-*/%,=("):
            continue
        word = token.lower()
        after = text[match.end():]
        equals = re.match(r"\s*=\s*", after)
        value_start = match.end() + (equals.end() if equals else 0)
        if word in SINGLE_WORD:
            single = re.match(r"\s*(\S+)", text[value_start:])
            if not single:
                continue
            found.append((match.start(), word, single.group(1).strip(",")))
            skip_to = value_start + single.end()
        else:
            found.append((match.start(), word, value_start))     # end filled in below
            lead = re.match(r"\s*", text[value_start:])
            ignore_at = value_start + lead.end()                 # the value's first token

    named, ends = {}, [f[0] for f in found[1:]] + [len(text)]
    for (start, word, value), end in zip(found, ends):
        if isinstance(value, str):
            named[word] = value
        else:
            named[word] = text[value:end].strip().rstrip(",").strip()
    head = text[:found[0][0]].strip().rstrip(",") if found else text
    positional = [p.strip() for p in head.split(",") if p.strip()] if head else []
    return positional, named


class Part:
    """One piece of a shape: its solid, how it combines, and any bevel asked for."""

    __slots__ = ("name", "mode", "solid", "bevel")

    def __init__(self, name, mode, solid, bevel=None):
        self.name, self.mode, self.solid, self.bevel = name, mode, solid, bevel

    def __repr__(self):
        return f"Part({self.name!r}, {self.mode}, {self.solid})"


class Shape:
    """A parsed shape: its sliders, its parts, and what to do to the finished result."""

    def __init__(self, params, parts, finish=None):
        self.params = params            # [(name, default, min, max)]
        self.parts = parts              # [(name, mode, [operations])]
        self.finish = finish or []      # operations applied to the joined result

    def values(self, overrides=None):
        scope = {name: default for name, default, _lo, _hi in self.params}
        for key, value in (overrides or {}).items():
            if key in scope:
                scope[key] = float(value)
        return scope

    def build_parts(self, overrides=None):
        """Every part as a Part. Booleans and bevels are described, not applied —
        those need Blender's solvers, so `shape_build` does them."""
        scope = self.values(overrides)
        out = []
        for name, mode, ops in self.parts:
            solid, bevel = _build_part(name, ops, scope)
            out.append(Part(name, mode, solid, bevel))
        return out

    def finish_options(self, overrides=None):
        """What to do once the parts are combined: bevel, smooth."""
        scope = self.values(overrides)
        options = {}
        for word, rest, line_no in self.finish:
            positional, named = split_args(rest, KEYWORDS.get(word, ()))
            if word == "bevel":
                options["bevel"] = _bevel_options(positional, named, scope, line_no)
            elif word == "smooth":
                options["smooth"] = True
        return options

    def build(self, overrides=None):
        """The whole shape as one solid, joining the parts.

        Subtract and intersect need Blender, so this adds everything; it is what the
        kernel's own tests use, and what a caller without Blender gets.
        """
        return solids.join([p.solid for p in self.build_parts(overrides)])


def parse(source):
    """Text -> Shape. Raises ShapeError with a line number."""
    params, parts, finish = [], [], []
    current = None                       # (name, mode, [ops])
    block = None                         # the profile or path being written into
    in_finish = False
    seen = set()

    for line_no, raw in enumerate(source.splitlines(), 1):
        line = raw.split("#")[0].rstrip()
        if not line.strip():
            continue
        word, rest = tokenise(line)

        if word == "finish":
            in_finish = True
            block = None
            continue

        if in_finish:
            if word in ("bevel", "smooth"):
                finish.append((word, rest, line_no))
                continue
            _fail(line_no, f"after `finish` only bevel and smooth make sense, not '{word}'")

        if word == "param":
            bits = rest.split()
            if not bits:
                _fail(line_no, "param needs a name and a default, e.g. `param height 0.3`")
            name = bits[0]
            if not re.match(r"^[a-zA-Z_]\w*$", name):
                _fail(line_no, f"'{name}' is not a usable name")
            if name in seen:
                _fail(line_no, f"'{name}' is declared twice")
            if name in ("t", "pi", "tau", "e"):
                _fail(line_no, f"'{name}' is reserved")
            seen.add(name)
            try:
                default = number(bits[1], name=name) if len(bits) > 1 else 1.0
                lo = number(bits[2], name=name) if len(bits) > 3 else min(0.0, default)
                hi = number(bits[3], name=name) if len(bits) > 3 else max(1.0, default * 2 or 1.0)
            except ExprError as exc:
                _fail(line_no, str(exc))
            params.append((name, default, lo, hi))
            continue

        if word == "part":
            bits = rest.split()
            mode = "add"
            if bits and bits[-1].lower() in PART_MODES:
                mode = bits.pop().lower()
            name = " ".join(bits) or f"part{len(parts) + 1}"
            current = (name, mode, [])
            parts.append(current)
            block = None
            continue

        if current is None:              # everything before the first `part` is one unnamed part
            current = ("shape", "add", [])
            parts.append(current)

        if word in ("profile", "path"):
            block = (word, [])
            current[2].append((word, block[1], line_no))
            continue

        if word in PROFILE_WORDS or word in PATH_WORDS:
            if block is None:
                _fail(line_no, f"'{word}' belongs inside a `profile` or `path` block")
            allowed = PROFILE_WORDS if block[0] == "profile" else PATH_WORDS
            if word not in allowed:
                _fail(line_no, f"'{word}' can't be used inside a `{block[0]}` block")
            block[1].append((word, rest, line_no))
            continue

        if word in SOLID_WORDS:
            current[2].append((word, rest, line_no))
            block = None
            continue

        _fail(line_no, f"'{word}' is not a shape command. Try: param, part, finish, profile, path, "
                       + ", ".join(sorted(set(PROFILE_WORDS + PATH_WORDS + SOLID_WORDS))))

    if not parts:
        raise ShapeError("nothing to build: describe a profile and a revolve or extrude")
    return Shape(params, parts, finish)


def _vector(positional, named, scope, default=(0.0, 0.0, 0.0), line_no=0):
    """Three numbers from `x, y, z` (or one positional number meaning all three, as in
    `scale 2`), or from named components (`rotate x 90`, `translate y 1 z 2`), where the
    ones left out keep `default` (0 for moves and turns, 1 for scale)."""
    if positional:
        if len(positional) == 1:
            v = number(positional[0], scope, name="value")
            return (v, v, v)
        if len(positional) != 3:
            _fail(line_no, f"expected three numbers, got {len(positional)}")
        return tuple(number(b, scope, name="value") for b in positional)
    if not any(named.get(k) is not None for k in ("x", "y", "z")):
        return tuple(default)
    return tuple(number(named[k], scope, name=k) if named.get(k) is not None else float(d)
                 for k, d in zip(("x", "y", "z"), default))


def _bevel_options(positional, named, scope, line_no):
    width = positional[0] if positional else named.get("width")
    if width is None:
        _fail(line_no, "bevel needs a width, e.g. `bevel 0.002 segments 2`")
    return {"width": number(width, scope, "bevel width"),
            "segments": integer(named.get("segments", 2), scope, "segments", 1, 64),
            "shape": number(named.get("shape", 0.5), scope, "shape"),
            "angle": number(named.get("angle", 30.0), scope, "angle")}


PATH_KEYWORDS = dict(KEYWORDS, line=("steps",))     # a path's straight run can be divided


def _build_path(ops, scope):
    path = solids.Path()
    for word, rest, line_no in ops:
        positional, named = split_args(rest, PATH_KEYWORDS.get(word, ()))
        try:
            if word in ("move", "line"):
                if len(positional) != 3:
                    _fail(line_no, f"inside a path, '{word}' needs three numbers: `{word} x, y, z`")
                x, y, z = (number(v, scope, name=word) for v in positional)
                if word == "move":
                    path.move(x, y, z)
                else:
                    path.line(x, y, z, steps=integer(named.get("steps", 1), scope, "steps", 1, 4096))
            elif word == "curve":
                missing = [k for k in ("x", "y", "z") if k not in named]
                if missing:
                    _fail(line_no, "a path curve needs x, y and z, e.g. "
                                   "`curve x = cos(t*tau)  y = sin(t*tau)  z = t  steps 64`")
                path.curve(named["x"], named["y"], named["z"],
                           steps=integer(named.get("steps", 32), scope, "steps", 1, 8192), scope=scope)
            elif word == "helix":
                for need in ("radius", "pitch", "turns"):
                    if need not in named:
                        _fail(line_no, "helix needs radius, pitch and turns, e.g. "
                                       "`helix radius 0.02 pitch 0.006 turns 5`")
                path.helix(number(named["radius"], scope, "radius"),
                           number(named["pitch"], scope, "pitch"),
                           number(named["turns"], scope, "turns"),
                           steps=integer(named["steps"], scope, "steps", 2, 20000) if "steps" in named else None,
                           start=number(named.get("start", 0.0), scope, "start"))
        except ShapeError:
            raise
        except ExprError as exc:
            _fail(line_no, str(exc))
    return path


def _build_profile(ops, scope):
    p = solids.Profile()
    for word, rest, line_no in ops:
        positional, named = split_args(rest, KEYWORDS.get(word, ()))
        try:
            if word == "shell":
                value = positional[0] if positional else named.get("thickness")
                if value is None:
                    _fail(line_no, "shell needs a thickness, e.g. `shell 0.004`")
                p = solids.shell(p, number(value, scope, "thickness"))
            elif word == "close":
                p.close()
            elif word in ("move", "line"):
                if len(positional) != 2:
                    _fail(line_no, f"'{word}' needs two numbers: `{word} x, y`")
                x, y = (number(v, scope, name=word) for v in positional)
                (p.move if word == "move" else p.line)(x, y)
            elif word == "arc":
                if len(positional) != 2:
                    _fail(line_no, "'arc' needs `arc x, y radius r`")
                if "radius" not in named:
                    _fail(line_no, "'arc' needs a radius, e.g. `arc 0.1, 0.02 radius 0.014`")
                x, y = (number(v, scope, name="arc") for v in positional)
                p.arc(x, y, number(named["radius"], scope, name="radius"),
                      steps=integer(named.get("steps", 12), scope, "steps", 2, 4096),
                      clockwise=str(named.get("clockwise", "")).lower() in ("1", "true", "yes"))
            elif word == "curve":
                if "x" not in named or "y" not in named:
                    _fail(line_no, "'curve' needs x and y, e.g. `curve x = r*t  y = h*t  steps 24`")
                p.curve(named["x"], named["y"],
                        steps=integer(named.get("steps", 24), scope, "steps", 1, 4096),
                        scope=scope)
        except ShapeError:
            raise
        except ExprError as exc:
            _fail(line_no, str(exc))
    return p


def _build_part(name, ops, scope):
    profiles = []
    path = None
    solid = None
    smooth_only = False
    bevel = None

    for entry in ops:
        word, rest, line_no = entry
        if word == "profile":
            profiles.append(_build_profile(rest, scope))
            continue
        if word == "path":
            path = _build_path(rest, scope)
            continue
        profile = profiles[-1] if profiles else None
        positional, named = split_args(rest, KEYWORDS.get(word, ()))
        try:
            if word == "revolve":
                if profile is None:
                    _fail(line_no, "revolve needs a profile above it")
                solid = solids.revolve(
                    profile,
                    segments=integer(named.get("segments", 48), scope, "segments", 3, 4096),
                    degrees=number(named.get("degrees", named.get("angle", 360)), scope, "degrees"),
                    axis=named.get("axis", "z"),
                    cap=str(named.get("cap", "yes")).lower() not in ("0", "no", "false"))
                profile = None
            elif word == "extrude":
                if profile is None:
                    _fail(line_no, "extrude needs a profile above it")
                depth = positional[0] if positional else named.get("depth", 1.0)
                solid = solids.extrude(
                    profile,
                    depth=number(depth, scope, "depth"),
                    steps=integer(named.get("steps", 1), scope, "steps", 1, 4096),
                    axis=named.get("axis", "z"),
                    taper=number(named.get("taper", 1.0), scope, "taper"),
                    cap=str(named.get("cap", "yes")).lower() not in ("0", "no", "false"))
                profiles = []
            elif word == "sweep":
                if profile is None:
                    _fail(line_no, "sweep needs a profile above it")
                if path is None:
                    _fail(line_no, "sweep needs a `path` block saying where to carry the profile")
                solid = solids.sweep(
                    profile, path,
                    twist=number(named.get("twist", 0.0), scope, "twist"),
                    scale_end=number(named.get("scale", 1.0), scope, "scale"),
                    cap=str(named.get("cap", "yes")).lower() not in ("0", "no", "false"))
                profiles, path = [], None
            elif word == "loft":
                if len(profiles) < 2:
                    _fail(line_no, f"loft needs at least two profiles above it (found {len(profiles)})")
                solid = solids.loft(
                    profiles,
                    steps=integer(named.get("steps", 1), scope, "steps", 1, 512),
                    axis=named.get("axis", "z"),
                    cap=str(named.get("cap", "yes")).lower() not in ("0", "no", "false"))
                profiles = []
            elif word == "bevel":
                bevel = _bevel_options(positional, named, scope, line_no)
            elif word in ("translate", "rotate", "scale"):
                if solid is None:
                    _fail(line_no, f"{word} needs something to act on")
                vec = _vector(positional, named, scope,
                              default=(1.0, 1.0, 1.0) if word == "scale" else (0.0, 0.0, 0.0),
                              line_no=line_no)
                solid = solids.transform(solid, **{{"translate": "move"}.get(word, word): vec})
            elif word == "array":
                if solid is None:
                    _fail(line_no, "array needs something to repeat")
                count = integer(positional[0] if positional else named.get("count", 2),
                                scope, "count", 1, 4096)
                around = named.get("around")
                step = (0.0, 0.0, 0.0) if around else _vector(
                    [], {k: named[k] for k in ("x", "y", "z") if k in named}, scope, line_no=line_no)
                if around is None and step == (0.0, 0.0, 0.0):
                    _fail(line_no, "array needs a direction (x, y or z) or `around z`")
                solid = solids.array(solid, count, move=step, around=around)
            elif word == "smooth":
                smooth_only = True
        except ShapeError:
            raise
        except ExprError as exc:
            _fail(line_no, str(exc))

    if solid is None:
        raise ShapeError(f"part '{name}' never becomes a solid: add a revolve, extrude, "
                         "sweep or loft")
    if smooth_only:
        solid.sharp = set()
    return solid, bevel


TEMPLATE = """\
# A desk lamp. Every number can be maths, so the whole thing is one formula.
param height   0.34   0.10 0.80    # Height of the lamp, in metres
param shade_r  0.14   0.03 0.40    # Radius of the shade at its rim
param stem_r   0.010  0.003 0.05   # Thickness of the stem
param base_r   0.095  0.03 0.30    # Radius of the base

part shade
  profile
    move  shade_r * 0.38, height
    curve x = shade_r * (0.38 + 0.62 * t)   y = height - height * 0.22 * t   steps 20
    line  shade_r * 0.98, height - height * 0.23
  revolve segments 72

part stem
  profile
    move 0, 0.016
    line stem_r, 0.016
    line stem_r, height - height * 0.24
    line 0, height - height * 0.24
    close
  revolve segments 32

part base
  profile
    move 0, 0
    line base_r * 0.92, 0
    arc  base_r, 0.008  radius 0.010
    line base_r * 0.96, 0.016
    line 0, 0.016
    close
  revolve segments 72
"""
