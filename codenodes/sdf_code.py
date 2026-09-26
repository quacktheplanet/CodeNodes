"""SDF source handling that needs no Blender: the helper library, parameter
annotations, and the full compute-shader source. Also maps compiler errors back
to the user's own line numbers.

User code must define ``float sdf(vec3 p)``: negative inside, positive outside,
in Blender units. It can use ``uTime`` (seconds) and ``uFrame``, the helpers in
``PRELUDE``, and parameters declared with a comment:

    // @param radius 1.0 0.1 3.0      -> a float named `radius`, default 1, slider 0.1..3
"""

from __future__ import annotations

import re

MAX_PARAMS = 16

PRELUDE = """\
// ---- CodeNodes helpers (distance functions from the usual raymarching toolkit) ----
float sdSphere(vec3 p, float r) { return length(p) - r; }
float sdBox(vec3 p, vec3 b) { vec3 q = abs(p) - b; return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0); }
float sdRoundBox(vec3 p, vec3 b, float r) { return sdBox(p, b - r) - r; }
float sdTorus(vec3 p, float R, float r) { vec2 q = vec2(length(p.xy) - R, p.z); return length(q) - r; }
float sdCapsule(vec3 p, vec3 a, vec3 b, float r) { vec3 pa = p - a, ba = b - a; float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0); return length(pa - ba * h) - r; }
float sdCylinder(vec3 p, float r, float h) { vec2 d = abs(vec2(length(p.xy), p.z)) - vec2(r, h); return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)); }
float smin(float a, float b, float k) { float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0); return mix(b, a, h) - k * h * (1.0 - h); }
float smax(float a, float b, float k) { return -smin(-a, -b, k); }
vec3 rotateZ(vec3 p, float a) { float c = cos(a), s = sin(a); return vec3(c * p.x - s * p.y, s * p.x + c * p.y, p.z); }
vec3 rotateX(vec3 p, float a) { float c = cos(a), s = sin(a); return vec3(p.x, c * p.y - s * p.z, s * p.y + c * p.z); }
vec3 rotateY(vec3 p, float a) { float c = cos(a), s = sin(a); return vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z); }
float cnHash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float noise3(vec3 x) {
  vec3 i = floor(x), f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(cnHash(i), cnHash(i + vec3(1, 0, 0)), f.x), mix(cnHash(i + vec3(0, 1, 0)), cnHash(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(cnHash(i + vec3(0, 0, 1)), cnHash(i + vec3(1, 0, 1)), f.x), mix(cnHash(i + vec3(0, 1, 1)), cnHash(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}
float fbm3(vec3 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise3(p); p = p * 2.03 + 11.7; a *= 0.5; } return v; }
// ---- user code ----
"""

MAIN = """
// ---- CodeNodes sampler ----
void main() {
  ivec2 px = ivec2(gl_GlobalInvocationID.xy);
  int tx = px.x / cnDims.x, ty = px.y / cnDims.y;
  int z = ty * cnTiles + tx;
  if (tx >= cnTiles || z < cnSlab.x || z >= cnSlab.y || z >= cnDims.z) return;
  ivec3 c = ivec3(px.x - tx * cnDims.x, px.y - ty * cnDims.y, z);
  vec3 p = cnLo + (cnHi - cnLo) * vec3(c) / vec3(max(cnDims - 1, ivec3(1)));
  imageStore(cnGrid, px, vec4(sdf(p)));
}
"""

TEMPLATE = """\
// Code -> Mesh: define sdf(p). Negative inside, positive outside, in Blender units.
// Helpers: sdSphere sdBox sdRoundBox sdTorus sdCapsule sdCylinder smin smax rotateX/Y/Z noise3 fbm3
// Time: uTime (seconds), uFrame. Sliders: declare them like the lines below.
// @param blend 0.35 0.0 1.0
// @param wobble 0.08 0.0 0.3

float sdf(vec3 p) {
  float d = sdSphere(p, 0.85);
  for (int i = 0; i < 5; i++) {
    float fi = float(i);
    vec3 c = vec3(sin(uTime * (0.7 + 0.13 * fi) + fi * 1.3),
                  cos(uTime * (0.5 + 0.17 * fi) + fi * 2.1),
                  sin(uTime * (0.9 + 0.11 * fi) + fi * 0.7)) * 1.15;
    d = smin(d, sdSphere(p - c, 0.30 + 0.05 * fi), blend + 0.2);
  }
  return d + wobble * sin(5.0 * p.x + uTime * 2.0) * sin(5.0 * p.y + uTime * 1.7) * sin(5.0 * p.z + uTime * 1.3);
}
"""

_PARAM_RE = re.compile(r"^\s*//\s*@param\s+([A-Za-z_]\w*)\s+([-+0-9.eE]+)(?:\s+([-+0-9.eE]+)\s+([-+0-9.eE]+))?\s*$")
_RESERVED = re.compile(r"^(cn[A-Z_]|gl_|u(Time|Frame)$)")


class SdfCodeError(Exception):
    """A problem with the user's code, worded for the user (and for Claude)."""


class Param:
    __slots__ = ("name", "default", "min", "max")

    def __init__(self, name, default, lo=None, hi=None):
        self.name, self.default = name, default
        self.min = lo if lo is not None else min(0.0, default)
        self.max = hi if hi is not None else max(1.0, default * 2 if default > 0 else 1.0)

    def __repr__(self):
        return f"Param({self.name}={self.default} [{self.min}, {self.max}])"


def parse_params(source):
    params, seen = [], set()
    for n, line in enumerate(source.splitlines(), 1):
        m = _PARAM_RE.match(line)
        if not m:
            if "@param" in line:
                raise SdfCodeError(f"line {n}: couldn't read the @param. Use: // @param name default [min max]")
            continue
        name = m.group(1)
        if _RESERVED.match(name):
            raise SdfCodeError(f"line {n}: '{name}' is a reserved name; pick another")
        if name in seen:
            raise SdfCodeError(f"line {n}: parameter '{name}' is declared twice")
        seen.add(name)
        lo = float(m.group(3)) if m.group(3) else None
        hi = float(m.group(4)) if m.group(4) else None
        params.append(Param(name, float(m.group(2)), lo, hi))
    if len(params) > MAX_PARAMS:
        raise SdfCodeError(f"at most {MAX_PARAMS} @param sliders are supported (found {len(params)})")
    return params


def check_source(source):
    if not re.search(r"\bfloat\s+sdf\s*\(\s*vec3\s+\w+\s*\)", source):
        raise SdfCodeError("the code must define:  float sdf(vec3 p) { ... }")
    for bad in ("imageStore", "imageLoad", "gl_GlobalInvocationID", "barrier("):
        if bad in source:
            raise SdfCodeError(f"'{bad}' isn't allowed in sdf code; just return the distance")


def full_source(source):
    """(compute source, number of lines before the user's first line)."""
    check_source(source)
    return PRELUDE + source + "\n" + MAIN, PRELUDE.count("\n")


# GLSL compiler logs name lines as "file.glsl:12: Error", ":12: Error" (Blender 5.0),
# "0(12)" or "ERROR: 0:12:" depending on Blender version and driver.
_LINE_PATTERNS = [re.compile(r"ERROR:\s*\d+:(\d+):"),
                  re.compile(r"[\w.]*:(\d+):\s*(?:error|Error|ERROR)"),
                  re.compile(r"\b0\((\d+)\)")]


def user_errors(log, offset, source):
    """Turn a raw compiler log into lines about the user's code."""
    user_lines = source.splitlines()
    out = []
    for raw in log.splitlines():
        line = raw.split("|")[-1].strip()
        if not line or "ERROR pyGPU_Shader" in line:
            continue
        num = None
        for pat in _LINE_PATTERNS:
            m = pat.search(line)
            if m:
                num = int(m.groups()[-1])
                break
        if num is None:
            if "rror" in line:
                out.append(line)
            continue
        msg = re.sub(r"^.*?(error|Error|ERROR)\W*", "", line, count=1).strip() or line
        msg = re.sub(r"^\d+:\d+:\s*", "", msg)
        n = num - offset
        if 1 <= n <= len(user_lines):
            out.append(f"line {n}: {msg}\n    {user_lines[n - 1].strip()}")
        else:
            out.append(f"(in CodeNodes' own code, line {num}): {msg}")
    return "\n".join(dict.fromkeys(out)) or log.strip() or "the shader failed to compile"
