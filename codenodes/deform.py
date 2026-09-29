"""GPU Mesh: code that runs on every vertex of a mesh coming in from Geometry Nodes.

    void deform(inout Vertex v)     // move it, recolour it, compute a value

`Vertex` has `position`, `normal`, `color` (vec4), `value` (a float you may set) and `index`.
The mesh keeps its topology; positions, a `color` attribute and a `value` attribute come out.

Blender can't hand a node's incoming geometry to Python, so CodeNodes keeps a hidden "tap":
an object whose Geometry Nodes are a trimmed copy of the host tree ending at the GPU Mesh
node's input. Its evaluated mesh is what the node receives.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np

from .sdf_code import PRELUDE, SdfCodeError, param_defines, parse_params, user_errors

GROUP = 16
ROW = 1024

VERTEX_PRELUDE = """\
// ---- CodeNodes vertex helpers ----
struct Vertex { vec3 position; vec3 normal; vec4 color; float value; float index; };
// ---- your code ----
"""

MAIN = """
// ---- CodeNodes vertex runner ----
void main() {
  ivec2 ij = ivec2(gl_GlobalInvocationID.xy);
  int idx = ij.y * cnRow + ij.x;
  if (idx >= cnCount) return;
  Vertex v;
  vec4 a = texelFetch(cnInP, ij, 0);
  vec4 n = texelFetch(cnInN, ij, 0);
  v.position = a.xyz; v.normal = n.xyz; v.color = vec4(1.0); v.value = 0.0; v.index = float(idx);
  deform(v);
  imageStore(cnOutP, ij, vec4(v.position, v.value));
  imageStore(cnOutC, ij, v.color);
}
"""

TEMPLATES = {
    "Wave": """\
// Wave: ripples travel across the incoming mesh along its normals; colour follows the height.
// @param amplitude 0.08 0.0 1.0
// @param frequency 6.0 0.5 30.0
// @param speed 2.0 0.0 10.0
void deform(inout Vertex v) {
  float h = sin(v.position.x * frequency + uTime * speed) * cos(v.position.y * frequency * 0.7 + uTime * speed * 0.6);
  v.position += v.normal * h * amplitude;
  v.value = h;
  v.color = vec4(mix(vec3(0.1, 0.3, 0.9), vec3(0.95, 0.9, 0.7), h * 0.5 + 0.5), 1.0);
}
""",
    "Noise Displace": """\
// Noise Displace: fractal noise pushes every vertex out along its normal, like weathered rock.
// @param strength 0.15 0.0 1.0
// @param scale 3.0 0.2 20.0
// @param seed 0.0 0.0 100.0
void deform(inout Vertex v) {
  float h = fbm3(v.position * scale + seed) - 0.5;
  v.position += v.normal * h * strength;
  v.value = h;
  v.color = vec4(mix(vec3(0.35, 0.3, 0.25), vec3(0.85, 0.8, 0.72), clamp(h * 2.0 + 0.5, 0.0, 1.0)), 1.0);
}
""",
    "Mesa": """\
// Mesa: a rocky plateau rising out of a flat grid: soft-edged, roughened by noise, coloured by height,
// with a flat summit (put something on it).
// @param height 1.1 0.0 4.0
// @param radius 2.4 0.5 6.0
// @param rough 0.22 0.0 1.0
void deform(inout Vertex v) {
  vec3 p = v.position;
  float r = length(p.xy) + (fbm3(p * 1.1 + 3.0) - 0.5) * 0.9;
  float mesa = 1.0 - smoothstep(radius * 0.8, radius * 1.1, r);
  float cliff = mesa * height + (fbm3(p * 3.2) - 0.5) * rough * (0.4 + mesa);
  float ground = (fbm3(p * 0.6 + 7.0) - 0.5) * 0.25;
  float top = smoothstep(radius * 0.35, radius * 0.1, length(p.xy));
  v.position.z += mix(cliff, height, top * mesa) + ground;
  v.value = v.position.z;
  float t = clamp(v.position.z / max(height, 1e-3), 0.0, 1.0);
  vec3 rock = mix(vec3(0.30, 0.27, 0.23), vec3(0.66, 0.60, 0.50), t);
  v.color = vec4(mix(vec3(0.20, 0.30, 0.14), rock, smoothstep(0.05, 0.3, t)), 1.0);
}
""",
    "Twist": """\
// Twist: turn the mesh around its Z axis, more the higher it goes.
// @param turns 0.5 -4.0 4.0
// @param height 2.0 0.1 20.0
void deform(inout Vertex v) {
  float a = v.position.z / height * turns * 6.2831853;
  v.position = rotateZ(v.position, a);
  v.normal = rotateZ(v.normal, a);
  v.value = a;
  v.color = vec4(0.5 + 0.5 * cos(a), 0.6, 0.5 + 0.5 * sin(a), 1.0);
}
""",
}
DEFAULT = "Wave"


def check_source(source):
    import re
    if not re.search(r"\bvoid\s+deform\s*\(\s*inout\s+Vertex\s+\w+\s*\)", source):
        raise SdfCodeError("the code must define:  void deform(inout Vertex v) { ... }")
    for bad in ("imageStore", "imageLoad", "gl_GlobalInvocationID"):
        if bad in source:
            raise SdfCodeError(f"'{bad}' isn't allowed here; just change the vertex's fields")


_shaders: dict[str, tuple] = {}


def shader_for(source):
    """(shader, params) for this deform code, compiled once."""
    import gpu
    from . import sampler
    key = hashlib.sha1(source.encode()).hexdigest()
    if key in _shaders:
        return _shaders[key]
    check_source(source)
    params = parse_params(source)
    from .particles import NOISE_PRELUDE
    head = PRELUDE + NOISE_PRELUDE + VERTEX_PRELUDE + param_defines(params)
    code = head + source + "\n" + MAIN
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source("struct CNParams { vec4 v[64]; };")
    info.uniform_buf(0, "CNParams", "cnParams")
    info.sampler(0, 'FLOAT_2D', "cnInP")
    info.sampler(1, 'FLOAT_2D', "cnInN")
    info.image(2, 'RGBA32F', 'FLOAT_2D', "cnOutP", qualifiers={'WRITE'})
    info.image(3, 'RGBA32F', 'FLOAT_2D', "cnOutC", qualifiers={'WRITE'})
    for kind, name in (('FLOAT', "uTime"), ('FLOAT', "uFrame"), ('INT', "cnCount"), ('INT', "cnRow")):
        info.push_constant(kind, name)
    info.local_group_size(GROUP, GROUP, 1)
    info.compute_source(code)
    shader, err, log = sampler._compile_capturing(info)
    if shader is None:
        from . import chain
        raise SdfCodeError("the code didn't compile:\n"
                           + chain.translate_errors(user_errors(log, head.count("\n"), source), source))
    if len(_shaders) > 24:
        _shaders.clear()
    _shaders[key] = (shader, params)
    return shader, params


class MeshState:
    """An incoming mesh on the GPU and the result of running the code on it."""

    def __init__(self, co, normals, tris, key):
        import gpu
        self.key = key
        self.count = len(co)
        self.rows = max(1, math.ceil(self.count / ROW))
        data = np.zeros((self.rows * ROW, 4), np.float32)
        data[:self.count, :3] = co
        self.in_p = gpu.types.GPUTexture((ROW, self.rows), format='RGBA32F',
                                         data=gpu.types.Buffer('FLOAT', data.size, data.ravel()))
        data[:self.count, :3] = normals
        self.in_n = gpu.types.GPUTexture((ROW, self.rows), format='RGBA32F',
                                         data=gpu.types.Buffer('FLOAT', data.size, data.ravel()))
        self.out_p = gpu.types.GPUTexture((ROW, self.rows), format='RGBA32F')
        self.out_c = gpu.types.GPUTexture((ROW, self.rows), format='RGBA32F')
        self.tris = np.ascontiguousarray(tris, np.int32)
        self.batch = None
        self.ran = None                # (code hash, values, time) the outputs hold

    def run(self, source, values, time_s, frame):
        import gpu
        from . import gpu_guard
        if not gpu_guard.allowed():
            return False
        shader, params = shader_for(source)
        ran = (hashlib.sha1(source.encode()).hexdigest(), tuple(sorted((values or {}).items())), time_s)
        if ran == self.ran:
            return True
        slots = np.zeros(256, np.float32)
        for i, prm in enumerate(params):
            slots[i] = float((values or {}).get(prm.name, prm.default))
        self._ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 256, slots.tolist()))
        shader.uniform_block("cnParams", self._ubo)
        shader.uniform_sampler("cnInP", self.in_p)
        try:
            shader.uniform_sampler("cnInN", self.in_n)
        except ValueError:
            pass
        shader.image("cnOutP", self.out_p)
        try:
            shader.image("cnOutC", self.out_c)
        except ValueError:
            pass
        for setter, name, value in ((shader.uniform_float, "uTime", float(time_s)),
                                    (shader.uniform_float, "uFrame", float(frame)),
                                    (shader.uniform_int, "cnCount", self.count),
                                    (shader.uniform_int, "cnRow", ROW)):
            try:
                setter(name, value)
            except ValueError:
                pass
        gpu_guard.dispatch(shader, math.ceil(ROW / GROUP), math.ceil(self.rows / GROUP), 1)
        self.ran = ran
        return True

    def read(self):
        """(positions (n, 3), value (n,), colors (n, 4))."""
        out = []
        for tex in (self.out_p, self.out_c):
            buf = tex.read()
            buf.dimensions = ROW * self.rows * 4
            out.append(np.frombuffer(buf, dtype=np.float32).reshape(-1, 4)[:self.count].copy())
        return out[0][:, :3], out[0][:, 3], out[1]


_states: dict[str, MeshState] = {}


def state_for(name, co, normals, tris, key):
    st = _states.get(name)
    if st is None or st.key != key:
        st = MeshState(co, normals, tris, key)
        _states[name] = st
    return st


def forget(name=None):
    if name is None:
        _states.clear()
    else:
        _states.pop(name, None)


# ---- the draw shader for live display -----------------------------------------------------------

_DRAW_V = """
void main() {
  int i = int(idx);
  ivec2 ij = ivec2(i % CN_ROW, i / CN_ROW);
  vec4 p = texelFetch(cnOutP, ij, 0);
  vec4 c = texelFetch(cnOutC, ij, 0);
  vec4 w = cnModel * vec4(p.xyz, 1.0);
  vWorld = w.xyz;
  vColor = c;
  gl_Position = cnViewProj * w;
}
"""
_DRAW_F = """
void main() {
  vec3 n = normalize(cross(dFdx(vWorld), dFdy(vWorld)));
  float l = 0.35 + 0.65 * max(dot(n, normalize(vec3(0.4, -0.3, 0.85))), 0.0);
  fragColor = vec4(vColor.rgb * l, 1.0);
}
"""
_draw = [None]


def draw_shader():
    import gpu
    if _draw[0] is None:
        iface = gpu.types.GPUStageInterfaceInfo("cn_def_iface")
        iface.smooth('VEC3', "vWorld")
        iface.smooth('VEC4', "vColor")
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'FLOAT', "idx")
        info.vertex_out(iface)
        info.sampler(0, 'FLOAT_2D', "cnOutP")
        info.sampler(1, 'FLOAT_2D', "cnOutC")
        info.push_constant('MAT4', "cnViewProj")
        info.push_constant('MAT4', "cnModel")
        info.define("CN_ROW", str(ROW))
        info.fragment_out(0, 'VEC4', "fragColor")
        info.vertex_source(_DRAW_V)
        info.fragment_source(_DRAW_F)
        _draw[0] = gpu.shader.create_from_info(info)
    return _draw[0]


def draw(state, host_matrix, rv3d):
    import gpu
    from . import gpu_guard
    if state.batch is None:
        fmt = gpu.types.GPUVertFormat()
        fmt.attr_add(id="idx", comp_type='F32', len=1, fetch_mode='FLOAT')
        vbo = gpu.types.GPUVertBuf(fmt, state.count)
        vbo.attr_fill("idx", np.arange(state.count, dtype=np.float32))
        ibo = gpu.types.GPUIndexBuf(type='TRIS', seq=state.tris)
        state.batch = gpu.types.GPUBatch(type='TRIS', buf=vbo, elem=ibo)
    sh = draw_shader()
    gpu_guard.note_draw()
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE')
    sh.bind()
    sh.uniform_sampler("cnOutP", state.out_p)
    sh.uniform_sampler("cnOutC", state.out_c)
    sh.uniform_float("cnViewProj", rv3d.perspective_matrix)
    sh.uniform_float("cnModel", host_matrix)
    state.batch.draw(sh)
    gpu.state.depth_mask_set(False)
    gpu.state.depth_test_set('NONE')
