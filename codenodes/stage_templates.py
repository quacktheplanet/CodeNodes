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
// @in float height 0.35 0.0 3.0  "How high above the surface they're born, in metres"
// @in float radius 2.0 0.1 20.0  "Radius of the swarm when there's no Emit From surface"
// @in float lifetime 7.0 1.0 30.0  "How long each firefly lives, in seconds, before it's reborn"
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
// @in float radius 1.0 0.05 20.0  "Radius of the ball they're born in"
// @in float kick 0.3 0.0 5.0  "Random speed each one starts with"
// @in float lifetime 4.0 0.5 30.0  "How long each particle lives, in seconds, before it's reborn"
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
// @in float strength 0.5 0.0 5.0  "How hard the drifting current pushes (0 = no drift)"
// @in float scale 1.2 0.05 10.0  "Size of the swirls: low = big lazy loops across the scene, high = small twitchy wiggles"
// @in float calm 0.92 0.0 0.999  "How smoothly they turn: higher = lazier, smoother paths; 0 = they snap to the current"
void behave(inout Particle p, float dt) {
  vec3 f = curlNoise(p.position * scale + vec3(0.0, 0.0, uTime * 0.15)) * strength;
  p.velocity = mix(f, p.velocity, calm);
}
""",
    "Rise": """\
// Rise: a gentle buoyancy; particles climb towards a speed and ease off near their ceiling.
// @in float climb 0.12 0.0 3.0  "Upward speed they ease towards (metres per second)"
// @in float ceiling 2.5 0.1 50.0  "Height where the rise fades out, so they hover instead of flying away"
void behave(inout Particle p, float dt) {
  float room = clamp((ceiling - p.position.z) / max(ceiling, 1e-3), 0.0, 1.0);
  p.velocity.z = mix(p.velocity.z, climb * room - (1.0 - room) * climb, 1.0 - exp(-dt * 1.5));
}
""",
    "Gravity": """\
// Gravity: a constant pull (down by default).
// @in float strength 9.8 0.0 50.0  "Pull downwards, in metres per second squared (9.8 = Earth)"
void behave(inout Particle p, float dt) {
  p.velocity.z -= strength * dt;
}
""",
    "Vortex": """\
// Vortex: swirl around the vertical axis through the node's centre.
// @in float spin 1.5 -20.0 20.0  "How fast they circle the vertical axis (negative turns the other way)"
// @in float pull 0.3 -10.0 10.0  "Draws them in towards the axis (negative pushes them out)"
void behave(inout Particle p, float dt) {
  vec3 r = vec3(p.position.xy, 0.0);
  float d = max(length(r), 0.05);
  p.velocity += (cross(vec3(0.0, 0.0, 1.0), r) / d * spin - r / d * pull) * dt;
}
""",
    "Drag": """\
// Drag: slows particles down, like moving through air or water.
// @in float amount 1.0 0.0 20.0  "How quickly they slow down, like moving through air (low) or water (high)"
void behave(inout Particle p, float dt) {
  p.velocity *= exp(-amount * dt);
}
""",
    "Blink": """\
// Blink: each particle pulses on its own rhythm. Adds two per-particle values later nodes can use:
// brightness (0..1) and phase (0..1, fixed per particle).
// @in float rate 0.6 0.05 6.0  "Blinks per second"
// @in float sharpness 6.0 1.0 30.0  "How sudden each blink is: low = gentle pulses, high = quick flashes"
// @out attr brightness 1.0  "Each particle's blink brightness right now (0 to 1), for later nodes"
// @out attr phase 0.0  "Where each particle is in its own blink cycle (0 to 1), for later nodes"
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
// @in func vec3 field(vec3 p)  "The force to push with: wire in a function such as Wind Field's wind"
// @in float amount 1.0 0.0 10.0  "How strongly the field pushes"
void behave(inout Particle p, float dt) {
  p.velocity += field(p.position) * amount * dt;
}
""",
    "Collide with Shape": """\
// Collide with Shape: bounce off a surface wired into 'sdf' (a GPU Surface node's sdf output).
// @in func float sdf(vec3 p) = 1e9  "The surface to bounce off: wire in a GPU Surface node's sdf output"
// @in float bounce 0.4 0.0 1.0  "How much speed is kept after a bounce (0 = stop dead, 1 = perfectly bouncy)"
// @in float radius 0.02 0.0 1.0  "Particle radius used for contact, in metres"
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
// @in color tint 1.0 0.6 0.25  "Colour of the glow"
// @in float intensity 2.0 0.0 20.0  "Brightness of the glow"
// @in float size 0.03 0.001 1.0  "Size of each sprite, in metres"
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
// @in color glow 1.0 0.78 0.22  "Colour of the glowing abdomen"
// @in float intensity 3.0 0.0 40.0  "Brightness of the glow"
// @in float size 0.012 0.001 0.2  "Size of each firefly's body, in metres"
// @in float flap 18.0 0.0 60.0  "Wing beats per second"
// @in float wing 2.4 0.2 6.0  "Wing length compared to the body"
// @out attr brightness 1.0  "Each firefly's glow right now (0 to 1), for later nodes"
// @out attr phase 0.0  "Each firefly's wing-beat phase (0 to 1), for later nodes"
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
// @in color tint 0.55 0.75 1.0  "Colour of the streaks"
// @in float intensity 1.2 0.0 20.0  "Brightness of the streaks"
// @in float size 0.01 0.001 0.5  "Thickness of each streak, in metres"
// @in float trail 0.25 0.0 5.0  "Streak length, in seconds of motion"
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
// @in material mat  "The Blender material whose colours, roughness and metallic the particles take"
// @in func vec3 light(vec3 p, vec3 n, vec3 v, vec3 albedo, float roughness, float metallic) = albedo * 0.8  "How they're lit: wire in Scene Lights' light (unwired: a soft default light)"
// @in float size 0.03 0.001 1.0  "Size of each particle, in metres"
// @in float brightness 1.0 0.0 10.0  "Overall brightness"
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
// @in float angle 1.2 -6.2832 6.2832  "How far to bend, in radians across the span (0 = straight)"
// @in float span 3.0 0.1 50.0  "Length over which the bend happens, in metres"
// @in float stretch 1.0 0.1 5.0  "Stretch along the axis (1 = unchanged)"
// @in float twist 0.0 -12.566 12.566  "Twist around the axis across the span, in radians"
// @in int axis 2 0 2  "Axis to bend along: 0 = X, 1 = Y, 2 = Z"
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
// @in float amount 0.5 -2.0 2.0  "Squeeze (positive) or flare (negative) towards the top"
// @in float span 3.0 0.1 50.0  "Height over which the taper happens, in metres"
vec3 warp(vec3 q) {
  float s = max(1.0 + amount * q.z / max(span, 1e-3), 0.0);
  return vec3(q.xy * s, q.z);
}
""",
    # ---- meshes ---------------------------------------------------------------------------------------
    "Ripple": """\
// Ripple: rings travel out from the centre along the normals; colour follows the height.
// @in float amplitude 0.05 0.0 1.0  "Height of the rings, in metres"
// @in float wavelength 0.4 0.02 5.0  "Distance between rings, in metres"
// @in float speed 1.0 0.0 10.0  "How fast the rings travel outwards"
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
// @in func vec3 field(vec3 p)  "The force to sway with: wire in a function such as Wind Field's wind"
// @in float amount 0.15 0.0 5.0  "How far it sways"
// @in float height 0.6 0.01 10.0  "Height at which the sway is full; below it the mesh bends less (roots stay put)"
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
// @in float strength 0.4 0.0 10.0  "Average wind speed"
// @in float gusts 0.7 0.0 5.0  "How much the wind rises and falls over time"
// @in float turbulence 0.3 0.0 5.0  "Small-scale swirling on top of the breeze"
// @out func wind
vec3 wind(vec3 q) {
  float g = 0.6 + 0.4 * sin(uTime * gusts + q.y * 0.5);
  return vec3(strength * g, 0.0, 0.0) + curlNoise(q * 0.4 + vec3(uTime * 0.1)) * turbulence;
}
""",
}



# ---- space: sources ----------------------------------------------------------------------------------

POINTS_FROM_FUNCTION = """\
// Points from Function: one point per index, placed every step by a function wired into 'place' (e.g.
// Orbits' planetPos) at the timeline's time. Put To Points after it to instance things on the points.
// @in func vec3 place(int i, float t)  "Where point i is at time t: wire in a function such as Orbits' planetPos"
// @in func float size(int i) = 1.0  "The size of point i, written as its bodySize attribute (e.g. Orbits' planetRadius)"
// @in func int count() = 1000000  "How many points to use (e.g. Orbits' planetCount); points past it get size 0"
// @out attr bodySize 1.0  "The size 'size' gave each point: use it to scale what you instance there"
void spawn(inout Particle p) { p.life = 1e9; }
void update(inout Particle p, float dt) {
  int i = int(p.seed);
  vec3 q = place(i, uSceneTime);
  if (dt > 0.0) p.velocity = (q - p.position) / dt;
  p.position = q;
  p.bodySize = i < count() ? size(i) : 0.0;
}
"""

BELT = """\
// Belt: a ring of particles round the origin, each set off on a circular orbit: asteroid belts, debris
// discs. Add Gravity to Bodies (with the same star mass) to keep them going round.
// @in float inner 9.5 0.1 500.0  "Inner radius of the belt, in metres"
// @in float outer 12.0 0.1 500.0  "Outer radius of the belt, in metres"
// @in float thickness 0.4 0.0 50.0  "How thick the belt is, top to bottom"
// @in float starMass 30.0 0.0 1000.0  "Pull of the star they orbit: sets their speed (use Gravity to Bodies' star mass)"
void spawn(inout Particle p) {
  float s = p.seed;
  float r = mix(inner, outer, rand1(s * 1.31));
  float a = rand1(s * 2.17) * 6.2831853;
  p.position = vec3(cos(a) * r, sin(a) * r, (rand1(s * 3.71) - 0.5) * thickness);
  float v = sqrt(starMass / max(r, 1e-3)) * (0.97 + 0.06 * rand1(s * 5.31));
  p.velocity = vec3(-sin(a), cos(a), 0.0) * v;
  p.life = 1e9;
}
"""

RING = """\
// Ring: a flat, thin ring of dust going round its centre, the inner edge faster than the outer (Kepler).
// Put Follow Body after it to wrap it round a planet.
// @in float inner 1.1 0.01 100.0  "Inner radius of the ring, in metres"
// @in float outer 1.9 0.01 100.0  "Outer radius of the ring, in metres"
// @in float thickness 0.01 0.0 5.0  "How thick the ring is"
// @in float spin 0.8 0.0 10.0  "How fast it goes round"
// @in float gaps 0.5 0.0 1.0  "How clear the gaps between bands are (0 = even dust)"
void spawn(inout Particle p) { p.life = 1e9; }
void update(inout Particle p, float dt) {
  float s = p.seed;
  float u = rand1(s * 1.37);
  float band = 0.5 + 0.5 * sin(u * 37.0);
  u = mix(u, u - (band - 0.5) * 0.03, gaps);        // pull dust away from the gaps into the bands
  float r = mix(inner, outer, clamp(u, 0.0, 1.0));
  float a = rand1(s * 2.91) * 6.2831853 + spin * uTime / (r * sqrt(r));
  vec3 q = vec3(cos(a) * r, sin(a) * r, (rand1(s * 4.13) - 0.5) * thickness);
  if (dt > 0.0) p.velocity = (q - p.position) / dt;
  p.position = q;
}
"""

NEBULA = """\
// Nebula: glowing clouds of gas made of soft sprites, shaped and coloured by fractal noise.
// @shape glow
// @in float radius 30.0 1.0 1000.0  "Size of the cloud, in metres"
// @in float wisps 1.6 0.2 8.0  "Size of the wisps: higher = more, smaller ones"
// @in float thickness 0.35 0.0 1.0  "How flat the cloud is (0 = a flat sheet, 1 = a ball)"
// @in color colorA 0.95 0.25 0.55  "Colour of the thin, outer gas"
// @in color colorB 0.25 0.5 1.0  "Colour of the dense gas"
// @in float intensity 0.015 0.0 5.0  "Brightness of each sprite (many overlap, so keep it low)"
// @in float size 2.0 0.01 50.0  "Size of each sprite, in metres"
void spawn(inout Particle p) {
  float s = p.seed;
  vec3 q = vec3(0.0);
  for (int k = 0; k < 16; k++) {                    // keep only places where the gas is thick
    q = randBall(s * (1.0 + float(k) * 0.1731) + float(k) * 0.37) * radius;
    q.z *= thickness;
    float g = fbm3(q / radius * wisps * 2.0 + 3.0);
    if (g > 0.5 + 0.25 * length(q) / radius) break;
  }
  p.position = q;
  p.velocity = vec3(0.0);
  p.life = 1e9;
}
vec4 look(Particle p) {
  float t = fbm3(p.position / radius * wisps + 9.0);
  vec3 c = mix(colorA, colorB, smoothstep(0.38, 0.62, t));
  float fade = 1.0 - smoothstep(0.55, 1.0, length(p.position) / radius);
  return vec4(c * intensity * fade, 1.0);
}
"""

_ORBIT_HEAD = """\
// @in int planets 3 1 8  "How many planets"
// @in float first 3.4 0.5 100.0  "Radius of the innermost orbit, in metres"
// @in float gap 2.4 0.1 100.0  "Distance from one orbit to the next, in metres"
// @in float speed 0.6 0.0 10.0  "How fast the innermost planet goes round (outer ones are slower, by Kepler's third law)"
// @in float size 0.6 0.05 10.0  "Radius of an average planet, in metres"
// @in float incline 0.06 0.0 1.0  "How far the orbits tilt out of the plane, in radians"
// @in float density 4.0 0.0 100.0  "How strongly planets pull (mass per cubic metre)"
// @out func planetPos  "Where planet i is at time t: planetPos(int i, float t)"
// @out func planetRadius  "The radius of planet i: planetRadius(int i)"
// @out func planetMass  "The mass of planet i, for gravity: planetMass(int i)"
// @out func planetCount  "How many planets there are: planetCount()"
float orbitRadius(int i) { return first + gap * float(i); }
float orbitAngle(int i, float t) {
  float r = orbitRadius(i);
  return speed * pow(first / r, 1.5) * t + float(i) * 2.39996;     // each starts a golden angle further on
}
// planet sizes vary smoothly with i (no float hash: the same on every GPU, and in Python)
float planetRadius(int i) { return size * (0.75 + 0.35 * sin(float(i) * 2.3 + 0.7)); }
float planetMass(int i) { float r = planetRadius(i); return density * r * r * r; }
int planetCount() { return planets; }
"""

ORBITS = """\
// Orbits: where each planet is at any time, as functions any number of nodes can share: planetPos(i, t),
// planetRadius(i), planetMass(i), planetCount(). Change this node's code and everything using it follows.
""" + _ORBIT_HEAD + """\
vec3 planetPos(int i, float t) {
  float r = orbitRadius(i), a = orbitAngle(i, t);
  float tilt = incline * sin(float(i) * 1.7 + 0.4);
  return vec3(cos(a) * r, sin(a) * r * cos(tilt), sin(a) * r * sin(tilt));
}
"""

FIGURE_EIGHTS = """\
// Figure Eights: the same functions as Orbits (planetPos, planetRadius, planetMass, planetCount), but the
// planets trace figures of eight. Pick it on an Orbits node and everything wired to it follows.
""" + _ORBIT_HEAD + """\
vec3 planetPos(int i, float t) {
  float r = orbitRadius(i), a = orbitAngle(i, t);
  float s = sin(a), c = cos(a);
  vec2 q = vec2(c, s * c) / (1.0 + s * s) * r * 1.35;          // a lemniscate of Bernoulli
  vec3 v = vec3(q, sin(a * 2.0) * r * incline);
  return rotateZ(v, float(i) * 1.9);
}
"""

SPACE_STAGES = {
    "Gravity to Bodies": """\
// Gravity to Bodies: Newton's pull towards a star at the centre and towards every body wired in (e.g. the
// planets from Orbits). Particles orbit, swing past planets and get flung.
// @in func vec3 bodyPos(int i, float t)  "Where body i is at time t: wire in Orbits' planetPos"
// @in func float bodyMass(int i) = 0.0  "The mass of body i: wire in Orbits' planetMass"
// @in func int bodyCount() = 0  "How many bodies there are: wire in Orbits' planetCount"
// @in float starMass 30.0 0.0 1000.0  "Pull of the star at the centre (0 = no star)"
// @in float soften 0.25 0.01 10.0  "Stops the pull growing without limit very close to a body, in metres"
void behave(inout Particle p, float dt) {
  float r2 = dot(p.position, p.position) + soften * soften;
  vec3 acc = -p.position * starMass / (r2 * sqrt(r2));
  int n = bodyCount();
  for (int i = 0; i < 8; i++) {
    if (i >= n) break;
    vec3 d = bodyPos(i, uSceneTime) - p.position;
    float b2 = dot(d, d) + soften * soften;
    acc += d * bodyMass(i) / (b2 * sqrt(b2));
  }
  p.velocity += acc * dt;
}
""",
    "Collide with Bodies": """\
// Collide with Bodies: particles bounce off the star at the centre and off every body wired in (spheres at
// Orbits' planetPos with planetRadius), and never end up inside one. Put it after the other behaviours.
// @in func vec3 bodyPos(int i, float t)  "Where body i is at time t: wire in Orbits' planetPos"
// @in func float bodyRadius(int i) = 0.0  "The radius of body i: wire in Orbits' planetRadius"
// @in func int bodyCount() = 0  "How many bodies there are: wire in Orbits' planetCount"
// @in float starRadius 1.0 0.0 100.0  "Radius of the star at the centre (0 = no star)"
// @in float margin 0.12 0.0 1.0  "Extra room for mountains, as a fraction of each body's radius"
// @in float bounce 0.5 0.0 1.0  "Speed kept after a bounce (0 = stop dead, 1 = perfectly bouncy)"
void bounceOff(inout Particle p, vec3 c, float R, float dt) {
  vec3 d = p.position + p.velocity * dt - c;           // where the particle is about to be
  float dist = length(d);
  if (dist < R) {
    vec3 n = dist > 1e-6 ? d / dist : vec3(0.0, 0.0, 1.0);
    float vn = dot(p.velocity, n);
    if (vn < 0.0) p.velocity -= (1.0 + bounce) * vn * n;
    p.position = c + n * R - p.velocity * dt;          // so this step's move lands it on the surface
  }
}
void behave(inout Particle p, float dt) {
  if (starRadius > 0.0) bounceOff(p, vec3(0.0), starRadius, dt);
  int n = bodyCount();
  for (int i = 0; i < 8; i++) {
    if (i >= n) break;
    bounceOff(p, bodyPos(i, uSceneTime), bodyRadius(i) * (1.0 + margin), dt);
  }
}
""",
    "Star Colours": """\
// Star Colours: gives every star a temperature (cool orange in the core, hot blue-white out on the arms)
// and draws it in its blackbody colour. To Points writes 'temp' (thousands of kelvin) for materials.
// @in float reach 2.4 0.1 1000.0  "Radius of the galaxy, in metres (the source's size)"
// @in float core 3.5 1.0 40.0  "Temperature of stars in the core, in thousands of kelvin"
// @in float arms 11.0 1.0 40.0  "Temperature of young stars out on the arms, in thousands of kelvin"
// @in float spread 0.35 0.0 1.0  "How much neighbouring stars differ"
// @in float brightness 0.9 0.0 10.0  "Overall brightness"
// @out attr temp 6.0  "Each star's temperature in thousands of kelvin (for a Blackbody node in a material)"
vec3 blackbody(float kk) {                 // an RGB fit of Planck's law, kk in thousands of kelvin
  float t = kk * 10.0;
  float r = t <= 66.0 ? 1.0 : 1.292936 * pow(t - 60.0, -0.1332047);
  float g = t <= 66.0 ? 0.3900815 * log(t) - 0.6318414 : 1.129890 * pow(t - 60.0, -0.0755148);
  float b = t >= 66.0 ? 1.0 : (t <= 19.0 ? 0.0 : 0.5432068 * log(t - 10.0) - 1.1962541);
  return clamp(vec3(r, g, b), 0.0, 1.0);
}
void behave(inout Particle p, float dt) {
  float r = length(p.position.xy) / reach;
  float rnd = rand1(p.seed * 6.1 + 21.0);
  p.temp = mix(core, arms, smoothstep(0.1, 0.8, r)) * (1.0 + (rnd - 0.5) * spread * 1.6);
}
vec4 look(Particle p) {
  float r = length(p.position.xy) / reach;
  float rnd = rand1(p.seed * 6.1 + 21.0);
  vec3 c = blackbody(p.temp);
  // the middle is where most stars are: dim each there, so their sum glows instead of clipping
  c *= (0.05 + 0.95 * smoothstep(0.02, 0.85, r)) * (0.5 + rnd) * brightness;
  return vec4(c, 1.0);
}
""",
    "Follow Body": """\
// Follow Body: carries particles or a mesh along with a moving body (e.g. a planet from Orbits), tilted,
// so rings and moons go where it goes. A warp: it changes where things are shown, not how they move.
// @in func vec3 bodyPos(int i, float t)  "Where body i is at time t: wire in Orbits' planetPos"
// @in int body 1 0 7  "Which body to follow (0 = the first)"
// @in float tilt 0.45 -3.2 3.2  "Tilt of the ring's plane, in radians"
vec3 warp(vec3 q) { return rotateX(q, tilt) + bodyPos(body, uSceneTime); }
""",
    "Colour by Height": """\
// Colour by Height: paints a mesh by the height the node before it stored in v.value: deep and shallow sea
// below 0, then beaches, lowland, highland and snow up to 1 (Planet Terrain stores it this way).
// @in color deep 0.01 0.04 0.16  "Colour of deep water"
// @in color shallow 0.03 0.28 0.46  "Colour of shallow water"
// @in color lowland 0.15 0.36 0.1  "Colour of low ground"
// @in color highland 0.42 0.33 0.24  "Colour of high ground"
// @in color snow 0.94 0.95 1.0  "Colour of snow and ice"
// @in float snowline 0.7 0.0 1.5  "Height above which the land is white (1 = the highest peaks)"
// @in float ice 0.0 0.0 1.0  "Polar caps: how far the ice reaches from the poles towards the equator"
void deform(inout Vertex v) {
  float h = v.value;
  vec3 c;
  if (h < 0.0) {
    c = mix(shallow, deep, clamp(-h, 0.0, 1.0));
  } else {
    c = mix(lowland, highland, smoothstep(0.1, 0.55, h));
    c = mix(vec3(0.74, 0.68, 0.5), c, smoothstep(0.0, 0.05, h));      // beaches
    c = mix(c, snow, smoothstep(snowline - 0.08, snowline + 0.04, h));
  }
  float lat = abs(normalize(v.position).z);
  if (ice > 0.0) c = mix(c, snow, smoothstep(1.0 - ice, 1.0 - ice + 0.06, lat));
  v.color = vec4(c, 1.0);
}
""",
}

SPACE_FUNCTIONS = {"Orbits": ORBITS, "Figure Eights": FIGURE_EIGHTS}

STAGES.update(SPACE_STAGES)
STAGES.update(SPACE_FUNCTIONS)


def _scene_lights_code(slots=8):
    """Scene Lights: the scene's lights and world as a function other nodes call. The light data are
    hidden inputs CodeNodes keeps up to date (move a lamp and the light moves), in the space of the
    object whose tree holds the node. Up to `slots` lights; the code itself never changes."""
    head = ["// Scene Lights: the scene's lamps (sun, point, spot, area) and world colour as a function",
            "// other nodes call: wire 'light' into a lit look (Material Look) or a GPU Surface's Lights input.",
            "// CodeNodes keeps the light data (hidden inputs) in step with the scene, up to 8 lamps.",
            "// No shadows are cast between GPU nodes and Blender objects.",
            "// @in float intensity 1.0 0.0 10.0  \"Multiplies the brightness of the scene's lamps\"",
            "// @in float world 1.0 0.0 10.0  \"Multiplies the world (sky) light\"",
            "// @out func light  \"The scene's lighting as a function: wire it into a Look's or Surface's light input\"",
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

# ---- lists: data you edit as a table, and stages that read it -------------------------------------------

STAGES["Attractors"] = """\
// Attractors: points that pull particles in, as a table (Tab into the node, or ✎ Edit Code, to edit it).
// Wire the List output into Attract to List. Add a row and there's another attractor.
// @list
name    position:3       strength  radius
Left    -1.5 0.0 0.5     2.0       0.4
Right    1.5 0.0 0.5     2.0       0.4
Top      0.0 0.0 2.0     1.0       0.3
"""

STAGES["Attract to List"] = """\
// Attract to List: particles are pulled towards every point in a list (e.g. an Attractors List node).
// @in list Attractor targets  "The points that pull: wire in a List node whose columns are position, strength and radius"
// @in float amount 1.0 0.0 10.0  "How strongly all of them pull"
struct Attractor { vec3 position; float strength; float radius; };
void behave(inout Particle p, float dt) {
  for (int i = 0; i < targets_count(); i++) {
    Attractor a = targets(i);
    vec3 d = a.position - p.position;
    float r2 = dot(d, d) + a.radius * a.radius;
    p.velocity += d * (a.strength * amount / (r2 * sqrt(r2))) * dt;
  }
}
"""

# the order the Add menu shows them in, by section
SECTIONS = [
    ("Particle Stages", 'FORCE_TURBULENCE', ["Wander", "Rise", "Gravity", "Vortex", "Drag", "Blink",
                                             "Push by Field", "Collide with Shape", "Gravity to Bodies",
                                             "Collide with Bodies"]),
    ("Particle Looks", 'LIGHT_POINT', ["Glow Look", "Firefly Look", "Streak Look", "Material Look",
                                       "Star Colours"]),
    ("Warps (particles and meshes)", 'MOD_SIMPLEDEFORM', ["Bend", "Taper", "Follow Body"]),
    ("Mesh Stages", 'MOD_WAVE', ["Ripple", "Sway by Field", "Colour by Height"]),
    ("Functions", 'FORCE_WIND', ["Wind Field", "Orbits", "Figure Eights"]),
    ("Lists", 'SPREADSHEET', ["Attractors", "Attract to List"]),
    ("Lighting", 'LIGHT_SUN', ["Scene Lights"]),
]
