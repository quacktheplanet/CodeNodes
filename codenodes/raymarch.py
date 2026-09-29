"""Live SDF surfaces: the same `sdf(p)` code a Code Mesh meshes, raymarched straight into the 3D
viewport instead (Aerie-style), at interactive frame rates and any detail.

Two passes per surface and view: the scene pass traces a ray per pixel at a fraction of the
viewport's size into colour and hit-distance targets; the composite pass scales that up and writes
depth from the hit point, so Blender's own objects sit correctly in front of or behind the surface.
The surface lights itself: a sun (the scene's first Sun lamp, or a default), sky light, soft
shadows, ambient occlusion and distance fog. It is a picture in the viewport: To Geometry turns it
into a real mesh.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

from .sdf_code import PRELUDE, SdfCodeError, check_source, param_defines, parse_params, user_errors

VIEW_STRUCT = """
struct CNView {
  mat4 invViewProj;   // clip -> world
  mat4 toLocal;       // world -> the host object's space (where sdf() lives)
  mat4 toWorld;
  vec4 sun;           // world direction (towards the sun), w = strength
  vec4 sunCol;
  vec4 lo;            // bounds in object space; lo.w = scale of the object (world units per local)
  vec4 hi;            // hi.w = fog density
  vec4 surf;          // colour when the code has no color(p)
  vec4 misc;          // x = uTime, y = uFrame, z = shadows (0/1), w = ambient occlusion (0/1)
  vec4 misc2;         // x = sky background (0/1)
};
struct CNLights {
  vec4 L[32];         // 8 lamps x (position, type), (direction, size), (colour x energy, cone c0), (c1, ...)
  vec4 world;         // uniform world light
  vec4 mat[2];        // (base colour, roughness), (emission, metallic)
};
"""

HEAD = """
#define uTime (cnView.misc.x)
#define uFrame (cnView.misc.y)
"""

MAIN = """
// ---- CodeNodes raymarcher ----
vec3 cnSky(vec3 rd) {
  vec3 sun = normalize(cnView.sun.xyz);
  float e = sun.z;
  vec3 zen = mix(vec3(0.03, 0.04, 0.09), vec3(0.22, 0.42, 0.85), smoothstep(-0.15, 0.35, e));
  vec3 hor = mix(vec3(0.30, 0.16, 0.12), vec3(0.72, 0.80, 0.90), smoothstep(-0.05, 0.30, e));
  hor = mix(hor, vec3(1.0, 0.55, 0.30), (1.0 - smoothstep(0.0, 0.35, e)) * smoothstep(-0.15, 0.05, e) * 0.6);
  vec3 c = mix(hor, zen, pow(clamp(rd.z, 0.0, 1.0), 0.45));
  c = mix(c, hor * 0.35, clamp(-rd.z * 3.0, 0.0, 1.0));            // below the horizon
  float sd = max(dot(rd, sun), 0.0);
  c += cnView.sunCol.rgb * (pow(sd, 900.0) * 12.0 + pow(sd, 12.0) * 0.18);
  return c;
}

vec2 cnBox(vec3 ro, vec3 rd, vec3 lo, vec3 hi) {
  vec3 inv = 1.0 / (rd + vec3(1e-12));
  vec3 t0 = (lo - ro) * inv, t1 = (hi - ro) * inv;
  vec3 tmin = min(t0, t1), tmax = max(t0, t1);
  return vec2(max(max(tmin.x, tmin.y), tmin.z), min(min(tmax.x, tmax.y), tmax.z));
}

vec3 cnNormal(vec3 p, float e) {
  const vec2 k = vec2(1.0, -1.0);
  return normalize(k.xyy * sdf(p + k.xyy * e) + k.yyx * sdf(p + k.yyx * e) +
                   k.yxy * sdf(p + k.yxy * e) + k.xxx * sdf(p + k.xxx * e));
}

float cnShadow(vec3 ro, vec3 rd, float tmin, float tmax, float diag) {
  float res = 1.0, t = tmin;
  for (int i = 0; i < 64; i++) {
    float h = sdf(ro + rd * t);
    res = min(res, 10.0 * h / t);
    t += clamp(h, diag * 0.002, diag * 0.08);
    if (res < 0.002 || t > tmax) break;
  }
  return clamp(res, 0.0, 1.0);
}

float cnAO(vec3 p, vec3 n, float diag) {
  float occ = 0.0, w = 1.0;
  for (int i = 1; i <= 5; i++) {
    float h = diag * 0.012 * float(i);
    occ += (h - sdf(p + n * h)) * w;
    w *= 0.7;
  }
  return clamp(1.0 - occ * 18.0 / diag, 0.0, 1.0);
}

vec3 cnAces(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }

#ifdef CN_SCENE_LIGHTS
const float CN_PI = 3.14159265;
// `widen`: a lamp's angular size (radius / distance): its highlight spreads like a rougher surface's,
// with the energy kept (Karis' normalisation), as EEVEE's soft lamps do
vec3 cnBrdf(vec3 n, vec3 v, vec3 l, vec3 albedo, float rough, float metal, float widen) {
  float nl = max(dot(n, l), 0.0);
  if (nl <= 0.0) return vec3(0.0);
  vec3 h = normalize(l + v);
  float nv = max(dot(n, v), 1e-4), nh = max(dot(n, h), 0.0), vh = max(dot(v, h), 0.0);
  float a0 = max(rough * rough, 0.002);
  float a = clamp(a0 + widen * 0.5, 0.002, 1.0), a2 = a * a;
  float dn = nh * nh * (a2 - 1.0) + 1.0;
  float d = a2 / (CN_PI * dn * dn);                       // GGX stays normalised as it widens
  float k = (rough + 1.0) * (rough + 1.0) / 8.0;
  float g = (nv / (nv * (1.0 - k) + k)) * (nl / (nl * (1.0 - k) + k));
  vec3 f0 = mix(vec3(0.04), albedo, metal);
  vec3 fr = f0 + (1.0 - f0) * pow(1.0 - vh, 5.0);
  vec3 spec = d * g * fr / (4.0 * nv * nl + 1e-4);
  return ((1.0 - fr) * (1.0 - metal) * albedo / CN_PI + spec) * nl;
}
// shade a hit with the scene's lamps (world space); soft self-shadows for the two brightest lamps
vec3 cnSceneLit(vec3 p, vec3 n, vec3 pW, vec3 nW, vec3 vW, vec3 albedo, float rough, float metal, float diag,
                float ao) {
  vec3 c = albedo * cnLights.world.rgb * ao * (1.0 - 0.5 * metal);
  for (int i = 0; i < 8; i++) {
    vec4 a = cnLights.L[i * 4], b = cnLights.L[i * 4 + 1], cc = cnLights.L[i * 4 + 2], e = cnLights.L[i * 4 + 3];
    float type = a.w;
    if (type < -0.5) continue;
    vec3 l, rad;
    float widen = 0.0;
    if (type < 0.5) { l = -b.xyz; rad = cc.rgb; widen = 0.0047; }     // the sun's 0.27 degree radius
    else {
      vec3 d = a.xyz - pW;
      float r2 = max(dot(d, d), b.w * b.w * 0.25 + 1e-4);
      widen = b.w * 0.5 * inversesqrt(r2);
      l = d * inversesqrt(dot(d, d) + 1e-12);
      rad = cc.rgb / (4.0 * CN_PI * r2);
      if (type > 1.5 && type < 2.5) rad *= smoothstep(cc.w, e.x, dot(-l, b.xyz));
      if (type > 2.5) rad *= 4.0 * max(dot(-l, b.xyz), 0.0);
    }
    float sh = 1.0;
    if (i < 2 && cnView.misc.z > 0.5 && dot(nW, l) > 0.0) {
      vec3 lL = normalize((cnView.toLocal * vec4(l, 0.0)).xyz);
      sh = cnShadow(p + n * diag * 0.002, lL, diag * 0.004, diag, diag);
    }
    c += cnBrdf(nW, vW, l, albedo, rough, metal, widen) * rad * sh;
  }
  return c;
}
#endif

void main() {
  vec2 uv = vUv * 2.0 - 1.0;
  vec4 wn = cnView.invViewProj * vec4(uv, -1.0, 1.0);
  vec4 wf = cnView.invViewProj * vec4(uv, 1.0, 1.0);
  vec3 nearW = wn.xyz / wn.w, farW = wf.xyz / wf.w;
  vec3 rdW = normalize(farW - nearW);
  vec3 ro = (cnView.toLocal * vec4(nearW, 1.0)).xyz;
  vec3 rdL = (cnView.toLocal * vec4(rdW, 0.0)).xyz;
  float sc = length(rdL);                  // local units per world unit along this ray
  vec3 rd = rdL / sc;
  vec3 lo = cnView.lo.xyz, hi = cnView.hi.xyz;
  float diag = length(hi - lo);
  vec2 tb = cnBox(ro, rd, lo, hi);
  float tFar = min(tb.y, length(farW - nearW) * sc);
  float t = max(tb.x, 0.0);
  bool hit = false;
  if (tb.x <= tb.y && tb.y > 0.0) {
    for (int i = 0; i < CN_STEPS; i++) {
      float d = sdf(ro + rd * t);
      if (d < max(diag * 0.00015, t * 0.0006)) { hit = true; break; }
      t += d * 0.9;
      if (t > tFar) break;
    }
  }
  vec3 skyC = cnSky(rdW);
  if (!hit) {
    if (cnView.misc2.x > 0.5) { fragColor = vec4(skyC, 1.0); hitDist = vec4(-2.0); }
    else { fragColor = vec4(0.0); hitDist = vec4(-1.0); }
    return;
  }
  vec3 p = ro + rd * t;
  vec3 n = cnNormal(p, max(diag * 0.0004, t * 0.0008));
  vec3 sunL = normalize((cnView.toLocal * vec4(cnView.sun.xyz, 0.0)).xyz);
  vec3 nW = normalize((transpose(cnView.toLocal) * vec4(n, 0.0)).xyz);
#ifdef CN_MATERIAL
  vec3 albedo = clamp(cnLights.mat[0].rgb, 0.0, 1.0);
  float rough = cnLights.mat[0].w, metal = cnLights.mat[1].w;
#elif defined(CN_HAS_COLOR)
  vec3 albedo = clamp(color(p), 0.0, 1.0);
  float rough = 0.5, metal = 0.0;
#else
  vec3 albedo = cnView.surf.rgb;
  float rough = 0.5, metal = 0.0;
#endif
  float dif = max(dot(n, sunL), 0.0);
  float sh = (cnView.misc.z > 0.5 && dif > 0.0) ? cnShadow(p + n * diag * 0.002, sunL, diag * 0.004, diag, diag) : 1.0;
  float ao = cnView.misc.w > 0.5 ? cnAO(p, n, diag) : 1.0;
  vec3 sunC = cnView.sunCol.rgb * cnView.sun.w;
  vec3 skyAmb = cnSky(normalize(nW + vec3(0.0, 0.0, 0.6))) * 0.9;
  vec3 h = normalize(sunL - rd);
  float spec = pow(max(dot(n, h), 0.0), 48.0) * 0.18 * sh;
#ifdef CN_SCENE_LIGHTS
  vec3 pW = (cnView.toWorld * vec4(p, 1.0)).xyz;
  vec3 col = cnSceneLit(p, n, pW, nW, -rdW, albedo, rough, metal, diag, ao);
#else
  vec3 col = albedo * (sunC * dif * sh + skyAmb * ao * (0.55 + 0.45 * nW.z)
                       + vec3(0.25, 0.2, 0.15) * ao * max(-nW.z, 0.0) * 0.3) + sunC * spec;
#endif
#ifdef CN_MATERIAL
  col += cnLights.mat[1].rgb;
#endif
  float tW = t / sc;
  float fogK = 1.0 - exp(-cnView.hi.w * tW);
  col = mix(col, skyC, fogK);
  fragColor = vec4(col, 1.0);   // scene-linear: Blender's view transform does the rest
  hitDist = vec4(tW);
}
"""

VERT = "void main() { vUv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }"

COMPOSITE = """
// The surface is marched at a lower resolution (Live Resolution) and scaled up here. Filtering the
// hit distance across the surface's outline would blend a real distance with "no hit" (-1) into a
// small positive one: a dark fringe placed right in front of the camera, over whatever is behind
// (a thin black jagged line where the surface meets a mesh). So the distance is read unfiltered
// from the nearest texel, and the colour is only filtered among texels that hit.
void main() {
  ivec2 size = textureSize(depthTex, 0);
  vec2 fp = vUv * vec2(size) - 0.5;
  ivec2 near = clamp(ivec2(floor(fp + 0.5)), ivec2(0), size - 1);
  float d = texelFetch(depthTex, near, 0).r;
  if (d < -1.5) { gl_FragDepth = 0.999999; fragColor = texelFetch(colTex, near, 0); return; }   // sky
  if (d < 0.0) discard;
  ivec2 i0 = clamp(ivec2(floor(fp)), ivec2(0), size - 1);
  ivec2 i1 = min(i0 + 1, size - 1);
  vec2 f = fract(fp);
  vec4 acc = vec4(0.0);
  float wsum = 0.0;
  for (int k = 0; k < 4; k++) {
    ivec2 ij = ivec2(k & 1, k >> 1);
    ivec2 at = ivec2(ij.x == 1 ? i1.x : i0.x, ij.y == 1 ? i1.y : i0.y);
    float w = (ij.x == 1 ? f.x : 1.0 - f.x) * (ij.y == 1 ? f.y : 1.0 - f.y);
    float dk = texelFetch(depthTex, at, 0).r;
    if (dk >= 0.0 && abs(dk - d) < max(0.05 * d, 0.02)) { acc += texelFetch(colTex, at, 0) * w; wsum += w; }
  }
  vec4 c = wsum > 0.0 ? acc / wsum : texelFetch(colTex, near, 0);
  if (c.a <= 0.0) discard;
  vec2 uv = vUv * 2.0 - 1.0;
  vec4 wn = invViewProj * vec4(uv, -1.0, 1.0);
  vec4 wf = invViewProj * vec4(uv, 1.0, 1.0);
  vec3 nearW = wn.xyz / wn.w;
  vec3 rdW = normalize(wf.xyz / wf.w - nearW);
  vec4 clip = viewProj * vec4(nearW + rdW * d, 1.0);
  gl_FragDepth = clamp(clip.z / clip.w * 0.5 + 0.5, 0.0, 0.999998);
  fragColor = vec4(c.rgb, 1.0);
}
"""

_COLOR_RE = re.compile(r"\bvec3\s+color\s*\(\s*vec3\s+\w+\s*\)")


def has_color(source):
    return _COLOR_RE.search(source) is not None


def _iface(gpu):
    i = gpu.types.GPUStageInterfaceInfo("cn_rm_iface")
    i.smooth('VEC2', "vUv")
    return i


_scene_cache: dict[str, tuple] = {}
_comp = [None]


def scene_shader(source, steps=192, lit=False, material=False):
    """(shader, params) for this SDF code, compiled once. Raises SdfCodeError with the user's lines.
    `lit`: shade with the scene's lamps (Scene Lights); `material`: take a Material Look's values."""
    import gpu
    from . import sampler
    key = hashlib.sha1(f"{steps}\0{lit}\0{material}\0{source}".encode()).hexdigest()
    if key in _scene_cache:
        return _scene_cache[key]
    check_source(source)
    params = parse_params(source)
    head = HEAD + PRELUDE + param_defines(params)
    if has_color(source):
        head = "#define CN_HAS_COLOR\n" + head
    if lit:
        head = "#define CN_SCENE_LIGHTS\n" + head
    if material:
        head = "#define CN_MATERIAL\n" + head
    code = head + source + "\n" + MAIN
    offset = head.count("\n")
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source("struct CNParams { vec4 v[64]; };" + VIEW_STRUCT)
    info.uniform_buf(0, "CNParams", "cnParams")
    info.uniform_buf(1, "CNView", "cnView")
    info.uniform_buf(2, "CNLights", "cnLights")
    info.define("CN_STEPS", str(int(steps)))
    info.vertex_in(0, 'VEC2', "pos")
    info.vertex_out(_iface(gpu))
    info.fragment_out(0, 'VEC4', "fragColor")
    info.fragment_out(1, 'VEC4', "hitDist")
    info.vertex_source(VERT)
    info.fragment_source(code)
    shader, err, log = sampler._compile_capturing(info)
    if shader is None:
        raise SdfCodeError("the code didn't compile:\n" + user_errors(log, offset, source))
    if len(_scene_cache) > 24:
        _scene_cache.clear()
    _scene_cache[key] = (shader, params)
    return shader, params


def comp_shader():
    import gpu
    if _comp[0] is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_out(_iface(gpu))
        info.push_constant('MAT4', "invViewProj")
        info.push_constant('MAT4', "viewProj")
        info.sampler(0, 'FLOAT_2D', "colTex")
        info.sampler(1, 'FLOAT_2D', "depthTex")
        info.fragment_out(0, 'VEC4', "fragColor")
        info.depth_write('ANY')
        info.vertex_source(VERT)
        info.fragment_source(COMPOSITE)
        _comp[0] = gpu.shader.create_from_info(info)
    return _comp[0]


_quads: dict[int, object] = {}


def _quad(shader):
    from gpu_extras.batch import batch_for_shader
    key = id(shader)
    if key not in _quads:
        _quads[key] = batch_for_shader(shader, 'TRI_STRIP', {"pos": [(-1, -1), (1, -1), (-1, 1), (1, 1)]})
    return _quads[key]


_targets: dict[tuple, tuple] = {}


def _target(key, w, h):
    import gpu
    tg = _targets.get(key)
    if tg is None or tg[0] != (w, h):
        c = gpu.types.GPUTexture((w, h), format='RGBA16F')
        d = gpu.types.GPUTexture((w, h), format='R32F')
        tg = ((w, h), c, d, gpu.types.GPUFrameBuffer(color_slots=[c, d]))
        _targets[key] = tg
        if len(_targets) > 16:
            _targets.pop(next(iter(_targets)))
    return tg


def scene_sun(scene):
    """(world direction towards the sun, colour, strength) from the scene's first Sun lamp."""
    for obj in scene.objects:
        if obj.type == 'LIGHT' and obj.data.type == 'SUN' and obj.visible_get():
            d = obj.matrix_world.to_3x3() @ __import__("mathutils").Vector((0.0, 0.0, 1.0))
            d.normalize()
            strength = min(4.0, max(0.2, obj.data.energy / 3.0))
            return tuple(d), tuple(obj.data.color), strength
    return (0.45, -0.35, 0.82), (1.0, 0.93, 0.82), 1.1


def draw(key, settings, source, values, host_matrix, region, rv3d, scene, time_s, frame, lights=None,
         material=None):
    """Raymarch one live surface into the current view. Raises SdfCodeError on bad code.
    `lights` / `material`: from lights.surface_uniforms (None: the built-in sun and sky)."""
    import gpu
    from . import gpu_guard
    shader, params = scene_shader(source, lit=lights is not None, material=material is not None)
    q = max(0.15, min(1.0, float(settings.quality)))
    w, h = max(32, int(region.width * q)), max(24, int(region.height * q))
    tg = _target((key, region.as_pointer()), w, h)
    vp = rv3d.perspective_matrix
    inv = vp.inverted()
    to_local = host_matrix.inverted_safe()
    sun_d, sun_c, sun_s = scene_sun(scene)
    lo, hi = tuple(settings.bounds_min), tuple(settings.bounds_max)
    scale = sum(v.length for v in host_matrix.to_3x3().col) / 3.0

    def mat(m):
        return [m[r][c] for c in range(4) for r in range(4)]       # column-major for GLSL

    view = (mat(inv) + mat(to_local) + mat(host_matrix)
            + [*sun_d, sun_s] + [*sun_c, 1.0]
            + [*lo, scale] + [*hi, float(settings.fog)]
            + [*settings.surface_color, 1.0]
            + [float(time_s), float(frame), 1.0 if settings.shadows else 0.0, 1.0 if settings.ao else 0.0]
            + [1.0 if settings.sky else 0.0, 0.0, 0.0, 0.0])
    slots = np.zeros(256, np.float32)
    for i, prm in enumerate(params):
        slots[i] = float((values or {}).get(prm.name, prm.default))
    pbuf = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 256, slots.tolist()))
    vbuf = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', len(view), view))
    lvals = list(lights) if lights is not None else [-1.0, 0, 0, 0] * 32 + [0.05, 0.05, 0.05, 1.0]
    lvals += list(material) if material is not None else [0.8, 0.8, 0.8, 0.5, 0.0, 0.0, 0.0, 0.0]
    lbuf = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', len(lvals), [float(v) for v in lvals]))
    gpu_guard.note_draw()
    with tg[3].bind():
        with gpu.matrix.push_pop():
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set('NONE')
            tg[3].clear(color=(0.0, 0.0, 0.0, 0.0))
            shader.bind()
            shader.uniform_block("cnParams", pbuf)
            shader.uniform_block("cnView", vbuf)
            shader.uniform_block("cnLights", lbuf)
            _quad(shader).draw(shader)
    cs = comp_shader()
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE')
    cs.bind()
    cs.uniform_float("invViewProj", inv)
    cs.uniform_float("viewProj", vp)
    cs.uniform_sampler("colTex", tg[1])
    cs.uniform_sampler("depthTex", tg[2])
    _quad(cs).draw(cs)
    gpu.state.depth_test_set('NONE')
    gpu.state.depth_mask_set(False)
