"""Starter code for GPU Stage nodes and the particle sources that pair with them.

A stage is a node you write: each defines one or more of
    void born(inout Particle p)              once, when a particle is (re)born
    void behave(inout Particle p, float dt)  every step: change the velocity (the chain moves it)
    vec4 look(Particle p)                    how it's drawn live (colour, alpha)
    vec3 warp(vec3 q)                        where it's shown: bends particles and meshes alike
    void deform(inout Vertex v)              for meshes
and may declare inputs, function inputs/outputs and per-particle attributes (see decl.py).
"""

FIREFLY_SWARM = """\
// Firefly Swarm: fireflies born just above the Emit From surface (or in a ball when there is none).
// Wire stages after it: Wander, Rise, Blink, then Firefly Look.
// @in float height 0.35 0.0 3.0
// @in float radius 2.0 0.1 20.0
// @in float lifetime 7.0 1.0 30.0
void spawn(inout Particle p) {
  bool surface = cnEmitCount > 0;
  vec3 base = surface ? emitPoint(p.seed) : randBall(p.seed) * radius;
  vec3 n = surface ? emitNormal(p.seed) : vec3(0.0, 0.0, 1.0);
  p.position = base + n * (0.03 + rand1(p.seed * 4.7) * height);
  p.velocity = vec3(0.0);
  p.life = lifetime * (0.6 + 0.8 * rand1(p.seed * 1.9));
}
"""

SPARK_BALL = """\
// Spark Ball: particles born in a ball, with a small random kick. A plain source for stages.
// @in float radius 1.0 0.05 20.0
// @in float kick 0.3 0.0 5.0
// @in float lifetime 4.0 0.5 30.0
void spawn(inout Particle p) {
  p.position = randBall(p.seed) * radius;
  p.velocity = (rand3(p.seed * 3.1) * 2.0 - 1.0) * kick;
  p.life = lifetime * (0.5 + rand1(p.seed * 2.3));
}
"""

STAGES = {
    # ---- particles: what they do -----------------------------------------------------------------
    "Wander": """\
// Wander: lazy curl-noise drifting, like insects on a summer evening.
// @in float strength 0.5 0.0 5.0
// @in float scale 1.2 0.05 10.0
// @in float calm 0.92 0.0 0.999
void behave(inout Particle p, float dt) {
  vec3 f = curlNoise(p.position * scale + vec3(0.0, 0.0, uTime * 0.15)) * strength;
  p.velocity = mix(f, p.velocity, calm);
}
""",
    "Rise": """\
// Rise: a gentle buoyancy; particles climb towards a speed and ease off near their ceiling.
// @in float climb 0.12 0.0 3.0
// @in float ceiling 2.5 0.1 50.0
void behave(inout Particle p, float dt) {
  float room = clamp((ceiling - p.position.z) / max(ceiling, 1e-3), 0.0, 1.0);
  p.velocity.z = mix(p.velocity.z, climb * room - (1.0 - room) * climb, 1.0 - exp(-dt * 1.5));
}
""",
    "Gravity": """\
// Gravity: a constant pull (down by default).
// @in float strength 9.8 0.0 50.0
void behave(inout Particle p, float dt) {
  p.velocity.z -= strength * dt;
}
""",
    "Vortex": """\
// Vortex: swirl around the vertical axis through the node's centre.
// @in float spin 1.5 -20.0 20.0
// @in float pull 0.3 -10.0 10.0
void behave(inout Particle p, float dt) {
  vec3 r = vec3(p.position.xy, 0.0);
  float d = max(length(r), 0.05);
  p.velocity += (cross(vec3(0.0, 0.0, 1.0), r) / d * spin - r / d * pull) * dt;
}
""",
    "Drag": """\
// Drag: slows particles down, like moving through air or water.
// @in float amount 1.0 0.0 20.0
void behave(inout Particle p, float dt) {
  p.velocity *= exp(-amount * dt);
}
""",
    "Blink": """\
// Blink: each particle pulses on its own rhythm. Adds two per-particle values later nodes can use:
// brightness (0..1) and phase (0..1, fixed per particle).
// @in float rate 0.6 0.05 6.0
// @in float sharpness 6.0 1.0 30.0
// @out attr brightness 1.0
// @out attr phase 0.0
void born(inout Particle p) {
  p.phase = rand1(p.seed * 9.13);
}
void behave(inout Particle p, float dt) {
  float w = 0.5 + 0.5 * sin((uTime * rate * (0.7 + 0.6 * p.phase) + p.phase) * 6.2831853);
  p.brightness = pow(w, sharpness);
}
""",
    "Push by Field": """\
// Push by Field: a force from a function wired into 'field' (e.g. Wind Field).
// @in func vec3 field(vec3 p)
// @in float amount 1.0 0.0 10.0
void behave(inout Particle p, float dt) {
  p.velocity += field(p.position) * amount * dt;
}
""",
    "Collide with Shape": """\
// Collide with Shape: bounce off a surface wired into 'sdf' (a GPU Surface node's sdf output).
// @in func float sdf(vec3 p) = 1e9
// @in float bounce 0.4 0.0 1.0
// @in float radius 0.02 0.0 1.0
void behave(inout Particle p, float dt) {
  float d = sdf(p.position);
  if (d < radius) {
    const float e = 0.002;
    vec3 n = normalize(vec3(sdf(p.position + vec3(e, 0, 0)) - sdf(p.position - vec3(e, 0, 0)),
                            sdf(p.position + vec3(0, e, 0)) - sdf(p.position - vec3(0, e, 0)),
                            sdf(p.position + vec3(0, 0, e)) - sdf(p.position - vec3(0, 0, e))) + 1e-9);
    p.position += n * (radius - d);
    float vn = dot(p.velocity, n);
    if (vn < 0.0) p.velocity -= (1.0 + bounce) * vn * n;
  }
}
""",
    # ---- particles: how they look ----------------------------------------------------------------
    "Glow Look": """\
// Glow Look: soft glowing sprites that fade in and out over each particle's life.
// @shape glow
// @in color tint 1.0 0.6 0.25
// @in float intensity 2.0 0.0 20.0
// @in float size 0.03 0.001 1.0
vec4 look(Particle p) {
  float k = clamp(p.age / max(p.life, 1e-4), 0.0, 1.0);
  float fade = smoothstep(0.0, 0.1, k) * smoothstep(1.0, 0.85, k);
  return vec4(tint * intensity * fade, 1.0);
}
""",
    "Firefly Look": """\
// Firefly Look: a glowing abdomen with a soft halo, and two little wings that flap.
// Reads brightness and phase (from Blink, or their defaults without it).
// @shape firefly
// @in color glow 1.0 0.78 0.22
// @in float intensity 3.0 0.0 40.0
// @in float size 0.012 0.001 0.2
// @in float flap 18.0 0.0 60.0
// @in float wing 2.4 0.2 6.0
// @out attr brightness 1.0
// @out attr phase 0.0
vec4 look(Particle p) {
  float k = clamp(p.age / max(p.life, 1e-4), 0.0, 1.0);
  float fade = smoothstep(0.0, 0.08, k) * smoothstep(1.0, 0.9, k);
  return vec4(glow * intensity * (0.08 + 0.92 * p.brightness) * fade, 1.0);
}
""",
    "Streak Look": """\
// Streak Look: each particle drawn as a glowing streak along its motion (a trail). Wire it next to
// another look from the same stream to get heads and trails from the same particles.
// @shape streak
// @in color tint 0.55 0.75 1.0
// @in float intensity 1.2 0.0 20.0
// @in float size 0.01 0.001 0.5
// @in float trail 0.25 0.0 5.0
vec4 look(Particle p) {
  float k = clamp(p.age / max(p.life, 1e-4), 0.0, 1.0);
  float fade = smoothstep(0.0, 0.1, k) * smoothstep(1.0, 0.85, k);
  return vec4(tint * intensity * fade, 1.0);
}
""",
    "Material Look": """\
// Material Look: particles in a Blender material's colours (its Principled BSDF: base colour,
// roughness, metallic, emission), lit by the scene when a Scene Lights node is wired into 'light'.
// Wire its 'material' output into a GPU Surface's Material input to shade a surface with it.
// Each particle is shaded as a small sphere facing the camera.
// @shape glow
// @in material mat
// @in func vec3 light(vec3 p, vec3 n, vec3 v, vec3 albedo, float roughness, float metallic) = albedo * 0.8
// @in float size 0.03 0.001 1.0
// @in float brightness 1.0 0.0 10.0
// @out func material
vec4 material(vec3 q) { return vec4(mat_base, mat_roughness); }
vec4 look(Particle p) {
  vec3 v = normalize(cnCamera - p.position);
  vec3 n = normalize(v + vec3(0.0, 0.0, 0.35));
  vec3 c = light(p.position, n, v, mat_base, mat_roughness, mat_metallic) + mat_emit;
  return vec4(c * brightness, mat_alpha);
}
""",
    # ---- both: space warps ------------------------------------------------------------------------
    "Bend": """\
// Bend: bends, stretches and twists whatever comes in (particles or a mesh) along an axis, without
// changing it: the particles still move as before, they're just shown bent.
// axis: 0 = X, 1 = Y, 2 = Z (the length being bent). A flat galaxy curls when bent along X.
// @in float angle 1.2 -6.2832 6.2832
// @in float span 3.0 0.1 50.0
// @in float stretch 1.0 0.1 5.0
// @in float twist 0.0 -12.566 12.566
// @in int axis 2 0 2
vec3 toAxis(vec3 q) { return axis == 0 ? q.yzx : (axis == 1 ? q.zxy : q); }
vec3 fromAxis(vec3 q) { return axis == 0 ? q.zxy : (axis == 1 ? q.yzx : q); }
vec3 warp(vec3 q) {
  q = toAxis(q);
  q.z *= stretch;
  float a = twist * q.z / max(span, 1e-3);
  q.xy = vec2(cos(a) * q.x - sin(a) * q.y, sin(a) * q.x + cos(a) * q.y);
  if (abs(angle) > 1e-4) {
    float r = span / angle;
    float th = q.z / r;
    q = vec3(r - (r - q.x) * cos(th), q.y, (r - q.x) * sin(th));
  }
  return fromAxis(q);
}
""",
    "Taper": """\
// Taper: squeezes or flares whatever comes in along Z.
// @in float amount 0.5 -2.0 2.0
// @in float span 3.0 0.1 50.0
vec3 warp(vec3 q) {
  float s = max(1.0 + amount * q.z / max(span, 1e-3), 0.0);
  return vec3(q.xy * s, q.z);
}
""",
    # ---- meshes ---------------------------------------------------------------------------------------
    "Ripple": """\
// Ripple: rings travel out from the centre along the normals; colour follows the height.
// @in float amplitude 0.05 0.0 1.0
// @in float wavelength 0.4 0.02 5.0
// @in float speed 1.0 0.0 10.0
void deform(inout Vertex v) {
  float h = sin(length(v.position.xy) / wavelength * 6.2831853 - uTime * speed * 6.2831853);
  v.position += v.normal * h * amplitude;
  v.value = h;
  v.color = vec4(mix(vec3(0.15, 0.35, 0.8), vec3(0.9, 0.95, 1.0), h * 0.5 + 0.5), 1.0);
}
""",
    "Sway by Field": """\
// Sway by Field: bends a mesh (grass, cloth, hair cards) with a force function wired into 'field'
// (e.g. Wind Field): the higher a vertex, the further it's pushed. Colour darkens where it bends most.
// @in func vec3 field(vec3 p)
// @in float amount 0.15 0.0 5.0
// @in float height 0.6 0.01 10.0
void deform(inout Vertex v) {
  float k = clamp(v.position.z / height, 0.0, 1.0);
  vec3 push = field(v.position) * amount * k * k;
  v.position += vec3(push.xy, -0.25 * length(push.xy) * k);
  v.value = length(push);
  v.color = vec4(mix(vec3(0.12, 0.32, 0.08), vec3(0.45, 0.62, 0.2), k), 1.0);
}
""",
    # ---- functions for other nodes --------------------------------------------------------------------
    "Wind Field": """\
// Wind Field: a gusty breeze as a function other nodes can call (wire 'wind' into Push by Field).
// @in float strength 0.4 0.0 10.0
// @in float gusts 0.7 0.0 5.0
// @in float turbulence 0.3 0.0 5.0
// @out func wind
vec3 wind(vec3 q) {
  float g = 0.6 + 0.4 * sin(uTime * gusts + q.y * 0.5);
  return vec3(strength * g, 0.0, 0.0) + curlNoise(q * 0.4 + vec3(uTime * 0.1)) * turbulence;
}
""",
}



def _scene_lights_code(slots=8):
    """Scene Lights: the scene's lights and world as a function other nodes call. The light data are
    hidden inputs CodeNodes keeps up to date (move a lamp and the light moves), in the space of the
    object whose tree holds the node. Up to `slots` lights; the code itself never changes."""
    head = ["// Scene Lights: the scene's lamps (sun, point, spot, area) and world colour as a function",
            "// other nodes call: wire 'light' into a lit look (Material Look) or a GPU Surface's Lights input.",
            "// CodeNodes keeps the light data (hidden inputs) in step with the scene, up to 8 lamps.",
            "// No shadows are cast between GPU nodes and Blender objects.",
            "// @in float intensity 1.0 0.0 10.0",
            "// @in float world 1.0 0.0 10.0",
            "// @out func light",
            "// @in hidden w_r 0.05", "// @in hidden w_g 0.05", "// @in hidden w_b 0.05"]
    fields = ("type", "px", "py", "pz", "dx", "dy", "dz", "r", "g", "b", "size", "c0", "c1")
    for i in range(slots):
        for f in fields:
            head.append(f"// @in hidden l{i}_{f} {-1.0 if f == 'type' else 0.0}")
    body = """
const float PI_L = 3.14159265;
// GGX specular + Lambert diffuse, the same model as Principled BSDF's main lobes. `widen`: the lamp's
// angular size, which spreads its highlight like a rougher surface (as EEVEE's soft lamps do)
vec3 brdf(vec3 n, vec3 v, vec3 l, vec3 albedo, float rough, float metal, float widen) {
  float nl = max(dot(n, l), 0.0);
  if (nl <= 0.0) return vec3(0.0);
  vec3 h = normalize(l + v);
  float nv = max(dot(n, v), 1e-4), nh = max(dot(n, h), 0.0), vh = max(dot(v, h), 0.0);
  float a = clamp(max(rough * rough, 0.002) + widen * 0.5, 0.002, 1.0), a2 = a * a;
  float dn = nh * nh * (a2 - 1.0) + 1.0;
  float d = a2 / (PI_L * dn * dn);
  float k = (rough + 1.0) * (rough + 1.0) / 8.0;
  float g = (nv / (nv * (1.0 - k) + k)) * (nl / (nl * (1.0 - k) + k));
  vec3 f0 = mix(vec3(0.04), albedo, metal);
  vec3 fr = f0 + (1.0 - f0) * pow(1.0 - vh, 5.0);
  vec3 spec = d * g * fr / (4.0 * nv * nl + 1e-4);
  vec3 kd = (1.0 - fr) * (1.0 - metal);
  return (kd * albedo / PI_L + spec) * nl;
}
// one lamp: the direction towards it (l) and the irradiance it delivers (rad)
void lamp(vec3 p, float type, vec3 pos, vec3 dir, vec3 col, float size, float c0, float c1,
          out vec3 l, out vec3 rad, out float widen) {
  if (type < 0.5) { l = -dir; rad = col; widen = 0.0047; return; }  // sun: colour x strength (W/m2)
  vec3 d = pos - p;
  float r2 = max(dot(d, d), size * size * 0.25 + 1e-4);
  widen = size * 0.5 * inversesqrt(r2);
  l = d * inversesqrt(dot(d, d) + 1e-12);
  rad = col / (4.0 * PI_L * r2);                                   // point: colour x power (W)
  if (type > 1.5 && type < 2.5) rad *= smoothstep(c0, c1, dot(-l, dir));        // spot cone
  if (type > 2.5) rad *= 4.0 * max(dot(-l, dir), 0.0);                           // area: one side
}
vec3 light(vec3 p, vec3 n, vec3 v, vec3 albedo, float roughness, float metallic) {
  vec3 c = albedo * vec3(w_r, w_g, w_b) * world * (1.0 - 0.5 * metallic);
  vec3 l, rad;
  float wd;
"""
    for i in range(slots):
        body += (f"  if (l{i}_type > -0.5) {{ lamp(p, l{i}_type, vec3(l{i}_px, l{i}_py, l{i}_pz), "
                 f"vec3(l{i}_dx, l{i}_dy, l{i}_dz), vec3(l{i}_r, l{i}_g, l{i}_b), l{i}_size, l{i}_c0, l{i}_c1, "
                 f"l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }}\n")
    body += "  return c;\n}\n"
    return "\n".join(head) + "\n" + body


STAGES["Scene Lights"] = _scene_lights_code()
LIGHT_SLOTS = 8

# the order the Add menu shows them in, by section
SECTIONS = [
    ("Particle Stages", 'FORCE_TURBULENCE', ["Wander", "Rise", "Gravity", "Vortex", "Drag", "Blink",
                                             "Push by Field", "Collide with Shape"]),
    ("Particle Looks", 'LIGHT_POINT', ["Glow Look", "Firefly Look", "Streak Look", "Material Look"]),
    ("Warps (particles and meshes)", 'MOD_SIMPLEDEFORM', ["Bend", "Taper"]),
    ("Mesh Stages", 'MOD_WAVE', ["Ripple", "Sway by Field"]),
    ("Functions", 'FORCE_WIND', ["Wind Field"]),
    ("Lighting", 'LIGHT_SUN', ["Scene Lights"]),
]
