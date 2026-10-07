// CodeNodes on the web: a particle chain from Blender, running live in WebGL2.
//
//   import { runParticles } from "./codenodes_webgl.js";
//   const view = await runParticles(canvas, bundle);      // bundle: what codenodes/webgl.py exports
//   view.set("n1_speed", 2.0);                            // a slider, by its name in the chain
//
// The chain's own GLSL (spawn, born, behave, look, warp, the noise helpers) runs unchanged. Only the
// stepper differs from Blender's: there it's a compute shader writing images; here a fragment shader
// writes the next state into float textures (ping-pong), one pixel per particle.

const STEP_MAIN = `
layout(location = 0) out vec4 cnOutPos;
layout(location = 1) out vec4 cnOutVel;
#ifdef CN_HAS_X
layout(location = 2) out vec4 cnOutExt;
#endif
void main() {
  ivec2 cnIJ = ivec2(gl_FragCoord.xy);
  int cnIdx = cnIJ.y * cnRow + cnIJ.x;
  vec4 cnA = texelFetch(cnPos, cnIJ, 0);
  vec4 cnB = texelFetch(cnVel, cnIJ, 0);
  if (cnIdx >= cnCount) { cnOutPos = cnA; cnOutVel = cnB;
#ifdef CN_HAS_X
    cnOutExt = vec4(0.0);
#endif
    return; }
  Particle p;
  p.position = cnA.xyz; p.age = cnA.w; p.velocity = cnB.xyz; p.life = cnB.w;
  p.seed = float(cnIdx) + 0.5;
#ifdef CN_HAS_X
  p.cnX = texelFetch(cnExt, cnIJ, 0);
#else
  p.cnX = CN_XDEFAULT;
#endif
  if (cnReset != 0) {
    p.position = vec3(0.0); p.velocity = vec3(0.0); p.age = 0.0; p.life = 5.0; p.cnX = CN_XDEFAULT;
    cnSpawn(p);
    p.age = rand1(p.seed * 2.17) * p.life * cnStagger;
    cnUpdate(p, 0.0);
  } else {
    p.age += cnDt;
    if (p.age >= p.life) { p.age = 0.0; p.velocity = vec3(0.0); p.cnX = CN_XDEFAULT; cnSpawn(p); }
    cnUpdate(p, cnDt);
  }
  cnOutPos = vec4(p.position, p.age);
  cnOutVel = vec4(p.velocity, max(p.life, 1e-4));
#ifdef CN_HAS_X
  cnOutExt = p.cnX;
#endif
}
`;

const DRAW_MAIN = `
out vec4 vColor;
flat out float vSoft;
void main() {
  int cnI = gl_VertexID;
  ivec2 cnIJ = ivec2(cnI % cnRow, cnI / cnRow);
  vec4 cnA = texelFetch(cnPos, cnIJ, 0), cnB = texelFetch(cnVel, cnIJ, 0);
  Particle p;
  p.position = cnA.xyz; p.age = cnA.w; p.velocity = cnB.xyz; p.life = cnB.w; p.seed = float(cnI) + 0.5;
#ifdef CN_HAS_X
  p.cnX = texelFetch(cnExt, cnIJ, 0);
#else
  p.cnX = CN_XDEFAULT;
#endif
  gl_Position = cnMVP * vec4(cnWarp(p.position), 1.0);
  gl_PointSize = cnPointPx;
  vec4 cnC = vec4(1.0);
#ifdef CN_LOOK
  if (cnColorMode == 2) cnC = look(p); else
#endif
  {
    float cnT = cnColorMode == 0 ? clamp(length(p.velocity) / cnSpeedRange, 0.0, 1.0)
                                 : clamp(p.age / max(p.life, 1e-4), 0.0, 1.0);
    cnC = vec4(mix(cnColA, cnColB, cnT), 1.0);
  }
  vColor = vec4(cnC.rgb * cnGain, cnC.a);
  vSoft = cnSoft;
  if (cnI >= cnCount) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
}
`;

// glow billboards, firefly wings and streaks: CodeNodes' viewport sprite shader, as is
const SPRITE_MAIN = `
out vec4 vColor;
out vec2 vUV;
flat out float vKind;
uniform vec4 cnMisc, cnProj;
uniform int cnKindV, cnPerV;

// ---- CodeNodes sprites (every local is cn-prefixed: attribute names are macros) ----
void main() {
  int cnV = gl_VertexID;
  int cnPer = cnPerV;                      // vertices per particle: 6 (glow) or 12 (two wings)
  int cnI = cnV / cnPer, cnK = cnV % cnPer;
  int cnRowW = cnRow;
  ivec2 cnIJ = ivec2(cnI % cnRowW, cnI / cnRowW);
  vec4 cnA = texelFetch(cnPos, cnIJ, 0), cnB = texelFetch(cnVel, cnIJ, 0);
  Particle p;
  p.position = cnA.xyz; p.age = cnA.w; p.velocity = cnB.xyz; p.life = cnB.w; p.seed = float(cnI) + 0.5;
#ifdef CN_HAS_X
  p.cnX = texelFetch(cnExt, cnIJ, 0);
#else
  p.cnX = CN_XDEFAULT;
#endif
  vec4 cnCol = look(p);
  vec3 cnC = cnWarp(p.position);
  // the six corners of a quad, as two triangles
  int cnQ = cnK % 6;
  vec2 cnCorner = vec2((cnQ == 1 || cnQ == 2 || cnQ == 4) ? 1.0 : -1.0, (cnQ == 2 || cnQ == 4 || cnQ == 5) ? 1.0 : -1.0);
  vUV = cnCorner;
  float cnSize = cnMisc.x;
  if (cnKindV == 0) {                       // glow: a billboard facing the camera
    vec4 cnClip = cnMVP * vec4(cnC, 1.0);
    // its depth is taken a halo's width towards the camera, so the glow isn't sliced off where it
    // meets the ground (the camera position in this object's space sits in the last parameter slot)
    vec3 cnToCam = cnParams.v[63].xyz - cnC;
    vec4 cnNear = cnMVP * vec4(cnC + normalize(cnToCam + 1e-6) * min(cnSize * 2.5, 0.9 * length(cnToCam)), 1.0);
    cnClip.xy += cnCorner * cnSize * cnProj.xy * cnProj.z;
    cnClip.z = cnNear.z / cnNear.w * cnClip.w;
    gl_Position = cnClip;
    vColor = cnCol;
    vKind = 0.0;
  } else if (cnKindV == 2) {                // streak: a quad from where it was to where it is
    vec3 cnTail = cnWarp(p.position - p.velocity * cnMisc.z);
    vec4 cnH = cnMVP * vec4(cnC, 1.0), cnT = cnMVP * vec4(cnTail, 1.0);
    vec2 cnD = cnH.xy / max(cnH.w, 1e-6) - cnT.xy / max(cnT.w, 1e-6);
    if (dot(cnD, cnD) < 1e-12) cnD = vec2(1.0, 0.0);
    vec2 cnN = normalize(vec2(-cnD.y, cnD.x));
    float cnAlong = cnCorner.y * 0.5 + 0.5;          // 0 at the tail, 1 at the head
    vec4 cnClip = mix(cnT, cnH, cnAlong);
    cnClip.xy += cnN * cnCorner.x * cnSize * (0.35 + 0.65 * cnAlong) * cnProj.xy * cnProj.z;
    gl_Position = cnClip;
    vUV = vec2(cnCorner.x, cnAlong);
    vColor = cnCol;
    vKind = 2.0;
  } else {                                   // wings: two quads flapping about the direction of travel
    vec3 cnAhead = cnWarp(p.position + p.velocity * 0.02) - cnC;
    vec3 cnF = length(cnAhead) > 1e-6 ? normalize(cnAhead) : vec3(1.0, 0.0, 0.0);
    vec3 cnUp = abs(cnF.z) > 0.95 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 0.0, 1.0);
    vec3 cnR = normalize(cross(cnF, cnUp));
    cnUp = cross(cnR, cnF);
    float cnPh = rand1(p.seed * 5.31);
    // a fast beat with a short pause at the top of each stroke: reads as flapping, not shimmering
    float cnBeat = fract(uTime * cnMisc.y + cnPh);
    float cnFlap = 0.95 * (1.0 - pow(abs(sin(cnBeat * 3.14159265)), 0.6)) + 0.1;
    float cnSide = cnK < 6 ? 1.0 : -1.0;
    vec3 cnW = cnR * cnSide * cos(cnFlap) + cnUp * sin(cnFlap);
    float cnSpan = cnSize * cnMisc.z, cnChord = cnSpan * 0.42;
    // the wing root sits on the thorax, a little ahead of the glowing abdomen
    vec3 cnRoot = cnC + cnF * cnSize * 0.9 + cnUp * cnSize * 0.25;
    vec3 cnP = cnRoot + cnW * (0.5 + 0.5 * cnCorner.x) * cnSpan
             + (-cnF * 0.55 + cnF * cnCorner.y * 0.5) * cnChord;
    gl_Position = cnMVP * vec4(cnP, 1.0);
    // lit a little by the insect's own glow near the root
    vColor = vec4(mix(vec3(0.95, 0.85, 0.55), vec3(0.78, 0.9, 1.0), 0.5 + 0.5 * cnCorner.x), 0.6);
    vKind = 1.0;
  }
}
`;
const SPRITE_FRAG = `#version 300 es
precision highp float;
in vec4 vColor;
in vec2 vUV;
flat in float vKind;
out vec4 fragColor;

void main() {
  float r2 = dot(vUV, vUV);
  if (vKind < 0.5) {
    if (r2 > 1.0) discard;
    // a hot core and a wide soft halo: reads as glow even without bloom
    float core = exp(-r2 * 70.0), halo = exp(-r2 * 9.0) * 0.16;
    fragColor = vec4(vColor.rgb * (core * 2.0 + halo), 1.0);
  } else if (vKind < 1.5) {
    // a wing: a teardrop outline (wide near the tip, narrow at the root), a bright rim, and veins
    vec2 e = vec2(vUV.x * 0.5 + 0.5, vUV.y);          // e.x: root 0 -> tip 1, e.y: across (-1..1)
    float width = 0.35 + 0.65 * sin(clamp(e.x, 0.0, 1.0) * 2.6);
    float d = abs(e.y) / max(width, 1e-3);
    if (d > 1.0 || e.x > 0.98) discard;
    float tip = smoothstep(0.98, 0.8, e.x);
    float rim = smoothstep(0.7, 1.0, d) + smoothstep(0.8, 0.98, e.x) * 0.6;
    float vein = 0.0;
    for (int i = 0; i < 3; i++) {
      float off = (float(i) - 1.0) * 0.45 * e.x;
      vein = max(vein, smoothstep(0.035, 0.0, abs(e.y - off)) * smoothstep(0.05, 0.25, e.x));
    }
    float a = vColor.a * (0.28 + 0.55 * rim + 0.35 * vein) * tip;
    fragColor = vec4(vColor.rgb * (0.55 + 0.6 * rim + 0.4 * vein), a);
  } else {
    // a streak: brightest at the head, fading to nothing at the tail and the edges
    float a = (1.0 - vUV.x * vUV.x) * vUV.y * vUV.y;
    fragColor = vec4(vColor.rgb * a, 1.0);
  }
}
`;

const DRAW_FRAG = `#version 300 es
precision highp float;
in vec4 vColor;
flat in float vSoft;
out vec4 fragColor;
void main() {
  vec2 q = gl_PointCoord * 2.0 - 1.0;
  float r2 = dot(q, q);
  if (r2 > 1.0) discard;
  float a = vSoft > 0.5 ? exp(-r2 * 3.0) : 1.0;     // glows fade out from the middle
  fragColor = vec4(vColor.rgb * a, vColor.a * a);
}
`;

const QUAD_VS = `#version 300 es
void main() {
  vec2 v = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(v * 2.0 - 1.0, 0.0, 1.0);
}
`;

function header(b, draw) {
  return `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
uniform CNParams { vec4 v[64]; } cnParams;
uniform sampler2D cnPos, cnVel, cnExt, cnEmitP, cnEmitN;
uniform float cnDt, uTime, uFrame, cnStagger;
uniform int cnCount, cnRow, cnReset, cnEmitCount;
${draw ? `uniform mat4 cnMVP;
uniform vec3 cnColA, cnColB;
uniform float cnPointPx, cnGain, cnSpeedRange, cnSoft;
uniform int cnColorMode;
${b.has_look ? "#define CN_LOOK\n" : ""}` : ""}`;
}

function compile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(s);
    throw new Error("CodeNodes: a shader didn't compile:\n" + log);
  }
  return s;
}

function program(gl, vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error("CodeNodes: " + gl.getProgramInfoLog(p));
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) {
    const info = gl.getActiveUniform(p, i);
    u[info.name.replace(/\[0\]$/, "")] = gl.getUniformLocation(p, info.name);
  }
  const block = gl.getUniformBlockIndex(p, "CNParams");
  if (block !== gl.INVALID_INDEX) gl.uniformBlockBinding(p, block, 0);
  return { p, u };
}

function floatTexture(gl, w, h, data = null) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, w, h, 0, gl.RGBA, gl.FLOAT, data);
  for (const k of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER]) gl.texParameteri(gl.TEXTURE_2D, k, gl.NEAREST);
  for (const k of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T]) gl.texParameteri(gl.TEXTURE_2D, k, gl.CLAMP_TO_EDGE);
  return t;
}

function decode(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

// column-major 4x4 helpers
function perspective(fovY, aspect, near, far) {
  const f = 1 / Math.tan(fovY / 2), nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function lookAt(eye, target, up) {
  const z = norm(sub(eye, target)), x = norm(cross(up, z)), y = cross(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
    -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}
function mul(a, b) {
  const o = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) o[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
  return o;
}
function invertAffine(m) {      // object -> world matrices are affine: invert the 3x3 and the translation
  const a = m[0], b = m[1], c = m[2], d = m[4], e = m[5], f = m[6], g = m[8], h = m[9], i = m[10];
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  const inv = [A / det, -(b * i - c * h) / det, (b * f - c * e) / det, 0,
    B / det, (a * i - c * g) / det, -(a * f - c * d) / det, 0,
    C / det, -(a * h - b * g) / det, (a * e - b * d) / det, 0, 0, 0, 0, 1];
  const t = [m[12], m[13], m[14]];
  for (let r = 0; r < 3; r++) inv[12 + r] = -(inv[r] * t[0] + inv[4 + r] * t[1] + inv[8 + r] * t[2]);
  return inv;
}
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

// One particle system in a WebGL2 context: its state, stepper and drawing.
function makeSystem(gl, b) {
  const step = program(gl, QUAD_VS, header(b, false) + b.defines + b.prelude + b.source + STEP_MAIN);
  const draw = program(gl, header(b, true) + b.defines + b.prelude + b.source + DRAW_MAIN, DRAW_FRAG);
  const sp = b.sprite && b.has_look ? b.sprite : null;
  const sprite = sp ? program(gl, header(b, true) + b.defines + b.prelude + b.source + SPRITE_MAIN, SPRITE_FRAG) : null;
  const W = b.row, H = b.rows;
  const state = [0, 1].map(() => ({
    pos: floatTexture(gl, W, H), vel: floatTexture(gl, W, H), ext: b.has_x ? floatTexture(gl, W, H) : null,
    fb: gl.createFramebuffer(),
  }));
  for (const s of state) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, s.fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, s.pos, 0);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, s.vel, 0);
    if (s.ext) gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT2, gl.TEXTURE_2D, s.ext, 0);
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  const emitN = b.emitter ? b.emitter.count : 0;
  const emitRows = Math.max(1, Math.ceil(emitN / 256));
  const emitP = floatTexture(gl, 256, emitRows, b.emitter ? padRGBA(decode(b.emitter.points), 256 * emitRows) : null);
  const emitNr = floatTexture(gl, 256, emitRows, b.emitter ? padRGBA(decode(b.emitter.normals), 256 * emitRows) : null);
  const params = new Float32Array(256);
  b.params.forEach((v, i) => { params[i] = v; });
  const index = Object.fromEntries(b.param_names.map((n, i) => [n, i]));
  const ubo = gl.createBuffer();
  gl.bindBuffer(gl.UNIFORM_BUFFER, ubo);
  gl.bufferData(gl.UNIFORM_BUFFER, params.byteLength, gl.DYNAMIC_DRAW);
  const vao = gl.createVertexArray();
  const sys = { b, cur: 0, time: 0, frame: 0, params, index, steps: 0 };
  const dt = 1 / (b.fps * b.substeps);

  function bindCommon(prog, st) {
    const u = prog.u;
    [[st.pos, "cnPos"], [st.vel, "cnVel"], [st.ext || st.pos, "cnExt"], [emitP, "cnEmitP"], [emitNr, "cnEmitN"]]
      .forEach(([t, name], i) => { gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, t); if (u[name]) gl.uniform1i(u[name], i); });
    const set1f = (n, v) => { if (u[n]) gl.uniform1f(u[n], v); };
    const set1i = (n, v) => { if (u[n]) gl.uniform1i(u[n], v); };
    set1f("uTime", sys.time); set1f("uFrame", sys.frame); set1f("cnStagger", b.stagger);
    set1i("cnCount", b.count); set1i("cnRow", W); set1i("cnEmitCount", emitN);
  }
  function upload(camObj) {
    params[252] = camObj[0]; params[253] = camObj[1]; params[254] = camObj[2];
    params[255] = (b.scene_time || 0) + sys.time;          // uSceneTime
    gl.bindBuffer(gl.UNIFORM_BUFFER, ubo);
    gl.bufferSubData(gl.UNIFORM_BUFFER, 0, params);
    gl.bindBufferBase(gl.UNIFORM_BUFFER, 0, ubo);
  }
  function stepOnce(h, reset) {
    const src = state[sys.cur], dst = state[1 - sys.cur];
    gl.useProgram(step.p);
    bindCommon(step, src);
    gl.uniform1f(step.u.cnDt, h);
    gl.uniform1i(step.u.cnReset, reset ? 1 : 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fb);
    gl.drawBuffers(b.has_x ? [gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1, gl.COLOR_ATTACHMENT2]
      : [gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
    gl.viewport(0, 0, W, H);
    gl.disable(gl.BLEND);
    gl.disable(gl.DEPTH_TEST);
    gl.bindVertexArray(vao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    sys.cur = 1 - sys.cur;
    sys.steps++;
  }
  sys.start = () => {
    upload([0, 0, 0]);
    stepOnce(0, true);
    for (let k = 0; k < Math.round(b.prewarm * b.fps) * b.substeps; k++) { sys.time += dt; stepOnce(dt, false); }
  };
  sys.advance = () => {                                   // one frame of the scene
    upload([0, 0, 0]);
    for (let k = 0; k < b.substeps; k++) { sys.time += dt; stepOnce(dt, false); }
    sys.frame++;
  };
  sys.draw = (viewProj, eye, proj) => {
    const mvp = mul(viewProj, b.object_matrix);
    const inv = invertAffine(b.object_matrix);
    upload([0, 1, 2].map((r) => inv[r] * eye[0] + inv[4 + r] * eye[1] + inv[8 + r] * eye[2] + inv[12 + r]));
    if (sprite) { drawSprites(mvp, proj); return; }
    gl.useProgram(draw.p);
    bindCommon(draw, state[sys.cur]);
    const u = draw.u, L = b.look;
    gl.uniformMatrix4fv(u.cnMVP, false, mvp);
    gl.uniform3fv(u.cnColA, L.color_a);
    gl.uniform3fv(u.cnColB, L.color_b);
    gl.uniform1f(u.cnPointPx, L.point_px * devicePixelRatio * (L.soft ? 3.0 : 1.0));
    gl.uniform1f(u.cnGain, L.gain);
    gl.uniform1f(u.cnSpeedRange, L.speed_range);
    gl.uniform1f(u.cnSoft, L.soft ? 1 : 0);
    gl.uniform1i(u.cnColorMode, L.color_mode);
    gl.enable(gl.BLEND);
    if (L.additive) gl.blendFunc(gl.SRC_ALPHA, gl.ONE); else gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(false);     // hidden behind surfaces
    gl.bindVertexArray(vao);
    gl.drawArrays(gl.POINTS, 0, b.count);
    gl.depthMask(true);
  };
  function drawSprites(mvp, proj) {
    const m = b.object_matrix;
    const scale = Math.max(Math.hypot(m[0], m[1], m[2]), Math.hypot(m[4], m[5], m[6]), Math.hypot(m[8], m[9], m[10]));
    const passes = sp.shape === "streak" ? [[2, 6, "add"]] : sp.shape === "firefly" ? [[0, 6, "add"], [1, 12, "alpha"]] : [[0, 6, "add"]];
    gl.useProgram(sprite.p);
    bindCommon(sprite, state[sys.cur]);
    const u = sprite.u;
    gl.uniformMatrix4fv(u.cnMVP, false, mvp);
    gl.uniform4f(u.cnProj, proj[0], proj[5], scale, 0);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(false);
    gl.enable(gl.BLEND);
    gl.bindVertexArray(vao);
    for (const [kind, per, blend] of passes) {
      if (blend === "add") gl.blendFunc(gl.ONE, gl.ONE); else gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      const live = (n, d) => { const k = (sp.prefix || "") + n; return k in index ? params[index[k]] : d; };
      gl.uniform4f(u.cnMisc, live("size", sp.size) * (kind ? 1 : 3), live("flap", sp.flap),
        kind === 2 ? live("trail", sp.trail) : live("wing", sp.wing), sys.time);
      gl.uniform1i(u.cnKindV, kind);
      gl.uniform1i(u.cnPerV, per);
      gl.drawArrays(gl.TRIANGLES, 0, b.count * per);
    }
    gl.depthMask(true);
  }
  return sys;
}

// ---- GPU Surfaces: the raymarcher, one pass, depth written from the hit ----
const SURFACE_VS = `#version 300 es
out vec2 vUv;
void main() {
  vec2 v = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  vUv = v;
  gl_Position = vec4(v * 2.0 - 1.0, 0.0, 1.0);
}
`;

function surfaceHeader(b) {
  return `#version 300 es
precision highp float;
precision highp int;
${b.head}
uniform CNParams { vec4 v[64]; } cnParams;
layout(std140) uniform CNViewB { CNView cnView; };
layout(std140) uniform CNLightsB { CNLights cnLights; };
uniform mat4 cnViewProj;
in vec2 vUv;
layout(location = 0) out vec4 fragColor;
vec4 hitDist;
`;
}

function invert4(m) {
  const inv = new Array(16);
  inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
  inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
  inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
  inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
  inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
  inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
  inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
  inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
  inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
  inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
  inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
  inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
  inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
  inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
  inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
  inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];
  const det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
  return inv.map((x) => x / (det || 1));
}

function makeSurface(gl, b) {
  const prog = program(gl, SURFACE_VS, surfaceHeader(b) + b.prelude + b.source + b.main);
  const blocks = [["CNParams", 0], ["CNViewB", 1], ["CNLightsB", 2]];
  for (const [name, slot] of blocks) {
    const i = gl.getUniformBlockIndex(prog.p, name);
    if (i !== gl.INVALID_INDEX) gl.uniformBlockBinding(prog.p, i, slot);
  }
  const params = new Float32Array(256);
  b.params.forEach((v, i) => { params[i] = v; });
  const index = Object.fromEntries(b.param_names.map((n, i) => [n, i]));
  const ubo = [gl.createBuffer(), gl.createBuffer(), gl.createBuffer()];
  const lights = new Float32Array(b.lights);
  gl.bindBuffer(gl.UNIFORM_BUFFER, ubo[2]);
  gl.bufferData(gl.UNIFORM_BUFFER, lights, gl.STATIC_DRAW);
  const toWorld = b.object_matrix, toLocal = invert4(toWorld);
  const vao = gl.createVertexArray();
  const sys = { b, params, index, time: 0, frame: 0, steps: 0 };
  sys.start = () => {};
  sys.advance = () => { sys.time += 1 / b.fps; sys.frame++; };
  sys.draw = (viewProj, eye) => {
    params[255] = sys.time;                                     // uSceneTime
    const view = new Float32Array(16 * 3 + 4 * 7);
    view.set(invert4(viewProj), 0); view.set(toLocal, 16); view.set(toWorld, 32);
    view.set([...b.sun], 48); view.set(b.sun_color, 52);
    view.set([...b.bounds[0], b.scale], 56); view.set([...b.bounds[1], b.fog], 60);
    view.set([...b.surface_color.slice(0, 3), 1], 64);
    view.set([sys.time, sys.frame, b.shadows ? 1 : 0, b.ao ? 1 : 0], 68);
    view.set([b.sky ? 1 : 0, 0, 0, 0], 72);
    gl.bindBuffer(gl.UNIFORM_BUFFER, ubo[0]); gl.bufferData(gl.UNIFORM_BUFFER, params, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.UNIFORM_BUFFER, ubo[1]); gl.bufferData(gl.UNIFORM_BUFFER, view, gl.DYNAMIC_DRAW);
    ubo.forEach((u, i) => gl.bindBufferBase(gl.UNIFORM_BUFFER, i, u));
    gl.useProgram(prog.p);
    gl.uniformMatrix4fv(prog.u.cnViewProj, false, viewProj);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LESS); gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.bindVertexArray(vao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  return sys;
}

// ---- Mesh chains: deform(v) in the vertex shader, flat normals from screen derivatives ----
const MESH_VS_MAIN = `
in vec3 cnInPos;
in vec3 cnInNrm;
uniform mat4 cnViewProj, cnModel;
out vec3 vWorld;
out vec4 vColor;
void main() {
  Vertex v;
  v.position = cnInPos; v.normal = cnInNrm; v.color = vec4(1.0); v.value = 0.0; v.index = float(gl_VertexID);
  deform(v);
  vec4 w = cnModel * vec4(v.position, 1.0);
  vWorld = w.xyz;
  vColor = v.color;
  gl_Position = cnViewProj * w;
}
`;
const MESH_FS = `#version 300 es
precision highp float;
in vec3 vWorld;
in vec4 vColor;
out vec4 fragColor;
void main() {
  vec3 n = normalize(cross(dFdx(vWorld), dFdy(vWorld)));
  float l = 0.35 + 0.65 * max(dot(n, normalize(vec3(0.4, -0.3, 0.85))), 0.0);
  fragColor = vec4(pow(vColor.rgb * l, vec3(1.0 / 2.2)), 1.0);
}
`;

function makeMesh(gl, b) {
  const vs = `#version 300 es
precision highp float;
precision highp int;
uniform CNParams { vec4 v[64]; } cnParams;
uniform float uTime, uFrame;
` + b.prelude + b.source + MESH_VS_MAIN;
  const prog = program(gl, vs, MESH_FS);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  for (const [name, data] of [["cnInPos", b.positions], ["cnInNrm", b.normals]]) {
    const loc = gl.getAttribLocation(prog.p, name);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, decode(data), gl.STATIC_DRAW);
    if (loc >= 0) { gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0); }
  }
  const ibuf = gl.createBuffer();
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibuf);
  const bin = atob(b.indices), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array(bytes.buffer), gl.STATIC_DRAW);
  gl.bindVertexArray(null);
  const params = new Float32Array(256);
  b.params.forEach((v, i) => { params[i] = v; });
  const index = Object.fromEntries(b.param_names.map((n, i) => [n, i]));
  const ubo = gl.createBuffer();
  const sys = { b, params, index, time: 0, frame: 0, steps: 0 };
  sys.start = () => {};
  sys.advance = () => { sys.time += 1 / b.fps; sys.frame++; };
  sys.draw = (viewProj) => {
    params[255] = sys.time;
    gl.bindBuffer(gl.UNIFORM_BUFFER, ubo); gl.bufferData(gl.UNIFORM_BUFFER, params, gl.DYNAMIC_DRAW);
    gl.bindBufferBase(gl.UNIFORM_BUFFER, 0, ubo);
    gl.useProgram(prog.p);
    gl.uniformMatrix4fv(prog.u.cnViewProj, false, viewProj);
    gl.uniformMatrix4fv(prog.u.cnModel, false, b.object_matrix);
    if (prog.u.uTime) gl.uniform1f(prog.u.uTime, sys.time);
    if (prog.u.uFrame) gl.uniform1f(prog.u.uFrame, sys.frame);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LESS); gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.bindVertexArray(vao);
    gl.drawElements(gl.TRIANGLES, b.triangles * 3, gl.UNSIGNED_INT, 0);
    gl.bindVertexArray(null);
  };
  return sys;
}

// Run one or more CodeNodes systems (bundles: particles, surfaces, mesh chains) in a canvas, with one camera.
export async function runParticles(canvas, bundles, opts = {}) {
  const list = Array.isArray(bundles) ? bundles : [bundles];
  const gl = canvas.getContext("webgl2", { antialias: true, premultipliedAlpha: false, alpha: true, depth: true });
  if (!gl) throw new Error("CodeNodes: this browser has no WebGL2");
  if (!gl.getExtension("EXT_color_buffer_float")) throw new Error("CodeNodes: float render targets unsupported");
  const make = { surface: makeSurface, mesh: makeMesh };
  const systems = list.map((b) => (make[b.kind] || makeSystem)(gl, b));
  const first = list[0];
  const fps = (list.find((b) => b.kind === "particles") || first).fps;

  // the camera: Blender's, as exported, orbiting its target (drag to turn, wheel to zoom)
  const v = opts.view || first.view;
  const cam = { target: v.target, dist: v.distance, yaw: v.yaw, pitch: v.pitch, fov: v.fov };
  if (opts.interactive !== false) {
    let dragging = null;
    canvas.addEventListener("pointerdown", (e) => { dragging = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointerup", () => { dragging = null; });
    canvas.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      cam.yaw -= (e.clientX - dragging[0]) * 0.008;
      cam.pitch = Math.max(-1.5, Math.min(1.5, cam.pitch + (e.clientY - dragging[1]) * 0.008));
      dragging = [e.clientX, e.clientY];
    });
    canvas.addEventListener("wheel", (e) => { e.preventDefault(); cam.dist *= Math.exp(e.deltaY * 0.001); }, { passive: false });
  }
  const bg = opts.background || first.background;

  function render() {
    const w = canvas.clientWidth * devicePixelRatio | 0, h = canvas.clientHeight * devicePixelRatio | 0;
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    const eye = [cam.target[0] + cam.dist * Math.cos(cam.pitch) * Math.cos(cam.yaw),
      cam.target[1] + cam.dist * Math.cos(cam.pitch) * Math.sin(cam.yaw),
      cam.target[2] + cam.dist * Math.sin(cam.pitch)];
    const proj = perspective(cam.fov, w / Math.max(h, 1), Math.max(0.01, cam.dist * 0.01), cam.dist * 100);
    const viewProj = mul(proj, lookAt(eye, cam.target, [0, 0, 1]));
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, w, h);
    gl.clearColor(bg[0], bg[1], bg[2], bg[3]);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    for (const s of systems) s.draw(viewProj, eye, proj);
  }

  for (const s of systems) s.start();
  let last = performance.now(), acc = 0, running = true;
  const stats = { frames: 0, get steps() { return systems.reduce((n, s) => n + (s.steps || 0), 0); } };
  function tick(now) {
    if (!running) return;
    acc += Math.min(0.1, (now - last) / 1000) * (opts.speed ?? 1);
    last = now;
    while (acc >= 1 / fps) { for (const s of systems) s.advance(); acc -= 1 / fps; }
    render();
    stats.frames++;
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
  const find = (name) => systems.find((s) => name in s.index);
  return {
    stats, gl, systems,
    set(name, value, which) { const s = which != null ? systems[which] : find(name); if (s && name in s.index) s.params[s.index[name]] = value; },
    get(name, which) { const s = which != null ? systems[which] : find(name); return s ? s.params[s.index[name]] : undefined; },
    stop() { running = false; },
    time: () => systems[0].time,
  };
}

function padRGBA(xyz, n) {
  const out = new Float32Array(n * 4);
  for (let i = 0; i < xyz.length / 3; i++) { out[i * 4] = xyz[i * 3]; out[i * 4 + 1] = xyz[i * 3 + 1]; out[i * 4 + 2] = xyz[i * 3 + 2]; out[i * 4 + 3] = 1; }
  return out;
}
