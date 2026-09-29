# CodeNodes node reference

Every code node, how it's set up, and what goes in and out. Code nodes run on the GPU by default and
are drawn live in the viewport; **To Geometry** is the one node that turns a GPU result into real
geometry (it says **To Points** after particles and **To Mesh** after a surface or mesh chain).

Part 1 is the language every code node is written in. Part 2 is generated from the add-on itself
(`tools/gen_node_reference.py`): each shipped node with its complete code and every socket it shows.
`tests/test_node_reference.py` fails if the two ever disagree.

## Part 1: writing a code node

A code node is a node you write. Lines starting with `// @` at the top of its code declare the node's
inputs and outputs; the functions it defines decide which streams it takes and gives. Change the code
and the node's sockets follow at once.

### Inputs

| Line | Socket | In the code |
|---|---|---|
| `// @in float speed 1.0 0 4` | a slider (default, then min and max; min/max optional) | `speed` |
| `// @param speed 1.0 0 4` | the same (the older spelling) | `speed` |
| `// @in int count 3 1 10` | a whole number | `count` (an `int`) |
| `// @in color tint 1.0 0.6 0.2` | a colour | `tint` (a `vec3`) |
| `// @in func vec3 wind(vec3 p)` | a function input: wire another node's function output into it | `wind(p)` |
| `// @in func float sdf(vec3 p) = 1e9` | the same, with what it returns when nothing is wired | `sdf(p)` |
| `// @in material mat` | a Material socket | `mat_base` (vec3), `mat_roughness`, `mat_metallic`, `mat_emit` (vec3, colour × strength), `mat_alpha` |
| `// @in hidden lightX 0.0` | none: a value the add-on fills in itself (e.g. Scene Lights' light data) | `lightX` |

A function input with nothing wired returns zero (or the value after `=`), so a node always compiles.

### Outputs

| Line | Socket | Meaning |
|---|---|---|
| `// @out func field` | a function output | your function `field` (any signature) is offered to other nodes |
| `// @out attr brightness 1.0` | a float field output | a per-particle value (default 1.0) any later node reads and writes as `p.brightness`; To Geometry writes it as a point attribute |
| `// @shape firefly` | none | how a look draws each particle: `point`, `glow`, `firefly` (glow and two flapping wings) or `streak` (a trail along the motion) |

A distance function `float sdf(vec3 p)` is always offered as an output too, so particles can collide
with any surface.

### Streams: which functions make which node

| The code defines | The node is | Stream sockets |
|---|---|---|
| `void spawn(inout Particle p)` (and optionally `void update(inout Particle p, float dt)`) | a particle source | Particles out |
| `void born(inout Particle p)`: once when a particle is (re)born | a particle stage | Particles in and out |
| `void behave(inout Particle p, float dt)`: every step; change the velocity, the chain moves the particle | a particle stage | Particles in and out |
| `vec4 look(Particle p)`: its colour (and alpha) when drawn live | a particle look | Particles in and out |
| `vec3 warp(vec3 q)`: where a point is shown (particles are still simulated where they are) | a warp, for particles and meshes alike | Particles and Mesh in and out |
| `void deform(inout Vertex v)`: runs on every vertex | a mesh stage | Mesh in and out |
| `float sdf(vec3 p)` (and optionally `vec3 color(vec3 p)`) | a GPU Surface | Geometry out (empty until To Geometry) |
| only functions it offers (`// @out func ...`) | a function provider | none: wire its function outputs |

`Particle` has `position`, `velocity`, `age`, `life` (seconds) and `seed` (fixed per particle), plus
any declared attributes. `Vertex` has `position`, `normal`, `color` (vec4) and `value` (a float
written as an attribute). Everywhere: `uTime` (seconds) and `uFrame`; in looks, `cnCamera` (the
camera in the object's space). Helpers: `rand1`, `rand3`, `randBall`, `randSphere`, `gnoise`,
`curlNoise`, `emitPoint(seed)` / `emitNormal(seed)` (points on the Emit From surface), and the SDF
helpers (`sdSphere`, `sdBox`, `sdRoundBox`, `sdCylinder`, `sdTorus`, `smin`, `aroundZ`, `rotateX`, ...).

### Chains and graphs

Wire code nodes like any Geometry Nodes: a source, then stages, then (optionally) To Geometry. Every
path from a source to where a stream ends is compiled into **one GPU program**, so splitting work
into small nodes costs no speed.

- **Branches.** Wire one stream output into several stages and each branch goes its own way:
  glowing heads and trails from the same particles, or the same swarm bent two ways. Branches that
  only differ in looks and warps **share one simulation**; a branch with its own behaviour after the
  split runs its own copy (which starts identical).
- **Merging.** Join Particles takes two streams; everything after it applies to both, and a To
  Geometry after it outputs both.
- **Functions fan out.** One function output (a Wind Field, a surface's `sdf`, Scene Lights' `light`)
  can feed any number of nodes, in any chains; each program includes it once.
- **To Geometry anywhere.** Partway along a chain it outputs the stream as it is at that point; the
  rest of the chain still draws live.
- **Order matters**: behaviours run in the order they're wired, then the chain moves each particle by
  its velocity (unless the source has its own `update`). The last look wins.

### Names and errors

Each node's functions, constants and sliders get a per-node prefix inside the program, so two nodes
can both define `strength` or `hash()`. Attributes are shared by name across a chain (at most four
per chain). Names starting with `cn` + capital letter, `gl_`, `uTime` and `uFrame`, GLSL words and
helper names are reserved. A mistake is reported on the node that has it, with its own line:
`node 'Wander', line 4: ...`.

### Baking

- **Blender's Bake node** works after To Geometry like on any geometry: bake a range, scrub it,
  render it, with no add-on needed afterwards.
- **GPU Cache** bakes a live GPU simulation itself (before any To Geometry): Mode Live / Cached,
  Start / End, and ⟳ Bake Now / ✕ Clear toggles that act as buttons. What's cached is the simulation
  of every pipeline passing through it; looks and warps after it stay live. Frames are stored next
  to the .blend in `codenodes_cache/` (16-bit floats, compressed).

### Lighting

**Scene Lights** turns the scene's lamps (sun, point, spot, area, up to 8) and world colour into a
function, `light(p, n, v, albedo, roughness, metallic)`, that lit looks call; the light data update
live as lamps move. **Material Look** brings in a Blender material's Principled BSDF values. A **GPU
Surface** has Lights and Material inputs: wire Scene Lights and a Material Look into them and the
raymarched surface is lit by the scene's lamps with that material. GPU nodes and Blender objects don't
cast shadows on each other.

## Part 2: every node


## Particle sources

### Galaxy

*GPU Particles source*

Galaxy: a million stars on twisted ellipses (a density wave, as in Myriad). Each ring of orbits is turned a little more than the one inside it, so the spiral arms never wind up.

**Example chain:** Galaxy → Bend → (live), or Galaxy → To Points → Instance on Points

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  |
| Count | int | 1000000 | 1 to 16777216 |  |
| Emit From | object |  |  |  |
| size | float | 2.4 | 0.5 to 10 |  |
| twist | float | 2.4 | 0 to 6 |  |
| spin | float | 0.9 | 0 to 3 |  |
| Colour By | menu |  | Code, Speed, Age | Look |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look |
| Glow | toggle | True |  | Look |
| Point Size | float | 1 | 0.5 to 32 | Look |
| Brightness | float | 0.28 | 0 to 8 | Look |
| Speed Range | float | 2 | 0.0001 to 50 | Look |
| Substeps | int | 1 | 1 to 20 | Simulation |
| Pre-warm | float | 0 | 0 to 120 | Simulation |
| Stagger | float | 1 | 0 to 1 | Simulation |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Galaxy: a million stars on twisted ellipses (a density wave, as in Myriad). Each ring of orbits is
// turned a little more than the one inside it, so the spiral arms never wind up.
// @param size 2.4 0.5 10.0
// @param twist 2.4 0.0 6.0
// @param spin 0.9 0.0 3.0

void spawn(inout Particle p) { p.life = 1e9; }        // stars live forever

void update(inout Particle p, float dt) {
  float s = p.seed;
  float bulge = step(rand1(s * 1.7 + 9.0), 0.14);
  float h = rand1(s * 2.3 + 0.5);
  float a = bulge > 0.5 ? pow(h, 1.6) * 0.45 + 0.02 : -log(1.0 - h * 0.99) * 0.6 + 0.05;
  float z = (rand1(s * 3.1 + 3.0) - 0.5) * (bulge > 0.5 ? 0.35 * (1.0 - a) : 0.05 * exp(-a));
  float phase = rand1(s * 4.7 + 1.0) * 6.2831853 + spin / (a + 0.25) * uTime;
  float squash = bulge > 0.5 ? 0.92 : 0.78;
  float turn = a * twist + 0.3;
  vec2 e = vec2(cos(phase) * a, sin(phase) * a * squash);
  float c = cos(turn), sn = sin(turn);
  vec3 q = vec3(c * e.x - sn * e.y, sn * e.x + c * e.y, z);
  if (bulge > 0.5) q.z += sin(phase * 1.7 + rand1(s * 5.3) * 20.0) * 0.08 * (1.0 - a);
  vec3 np = q * size;
  p.velocity = (np - p.position) / max(dt, 1e-4);
  p.position = np;
}

// old yellow stars in the middle, young blue ones out on the arms, a few rosy ones
vec4 look(Particle p) {
  float s = p.seed;
  float bulge = step(rand1(s * 1.7 + 9.0), 0.14);
  float r = length(p.position.xy) / size;
  float rnd = rand1(s * 6.1 + 21.0);
  vec3 old = vec3(1.0, 0.78, 0.5), young = vec3(0.55, 0.7, 1.0), rosy = vec3(1.0, 0.55, 0.75);
  vec3 c = bulge > 0.5 ? old * 0.12 : mix(mix(old, young, smoothstep(0.15, 0.9, r)), rosy, step(0.985, rnd) * 0.9);
  // the middle is where most stars are: dim each one there, so their sum glows instead of clipping
  c *= (0.04 + 0.96 * smoothstep(0.02, 0.85, r)) * (0.6 + 0.8 * rnd);
  return vec4(c, 1.0);
}
```


### Flow

*GPU Particles source*

Flow: particles ride a curl-noise current (as in Myriad): crisp filaments that never clump.

**Example chain:** Flow → Glow Look → (live)

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  |
| Count | int | 1000000 | 1 to 16777216 |  |
| Emit From | object |  |  |  |
| scale | float | 0.55 | 0.1 to 3 |  |
| speed | float | 1.9 | 0.1 to 6 |  |
| radius | float | 2.2 | 0.5 to 8 |  |
| Colour By | menu |  | Code, Speed, Age | Look |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look |
| Glow | toggle | True |  | Look |
| Point Size | float | 1 | 0.5 to 32 | Look |
| Brightness | float | 0.3 | 0 to 8 | Look |
| Speed Range | float | 2 | 0.0001 to 50 | Look |
| Substeps | int | 1 | 1 to 20 | Simulation |
| Pre-warm | float | 3 | 0 to 120 | Simulation |
| Stagger | float | 1 | 0 to 1 | Simulation |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Flow: particles ride a curl-noise current (as in Myriad): crisp filaments that never clump.
// @param scale 0.55 0.1 3.0
// @param speed 1.9 0.1 6.0
// @param radius 2.2 0.5 8.0

void spawn(inout Particle p) {
  p.position = randSphere(p.seed + uTime * 13.7) * radius * (0.9 + 0.2 * rand1(p.seed * 3.7));
  p.life = 4.0 + rand1(p.seed * 1.9 + uTime) * 5.0;
}

void update(inout Particle p, float dt) {
  vec3 q = p.position;
  vec3 flow = curlNoise(q * scale + vec3(0.0, 0.0, uTime * 0.05)) * 0.55
            - q * 0.04 * max(length(q) - radius * 1.1, 0.0);
  p.velocity = flow * speed;
  p.position += p.velocity * dt;
  if (length(p.position) > radius * 2.7) p.age = p.life;     // wandered off: respawn
}

vec4 look(Particle p) {
  float t = clamp(length(p.velocity) * 1.1, 0.0, 1.0);
  vec3 a = vec3(0.15, 0.25, 0.9), b = vec3(0.3, 0.95, 0.85), c = vec3(1.0, 0.95, 0.7);
  vec3 col = t < 0.5 ? mix(a, b, t * 2.0) : mix(b, c, t * 2.0 - 1.0);
  return vec4(col * smoothstep(0.0, 1.0, p.life - p.age), 1.0);
}
```


### Attractor

*GPU Particles source*

Attractor: the Aizawa strange attractor, integrated on the GPU (as in Myriad).

**Example chain:** Attractor → (live)

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  |
| Count | int | 600000 | 1 to 16777216 |  |
| Emit From | object |  |  |  |
| size | float | 2.1 | 0.5 to 8 |  |
| rate | float | 0.45 | 0.05 to 2 |  |
| Colour By | menu |  | Code, Speed, Age | Look |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look |
| Glow | toggle | True |  | Look |
| Point Size | float | 1 | 0.5 to 32 | Look |
| Brightness | float | 0.3 | 0 to 8 | Look |
| Speed Range | float | 2 | 0.0001 to 50 | Look |
| Substeps | int | 1 | 1 to 20 | Simulation |
| Pre-warm | float | 4 | 0 to 120 | Simulation |
| Stagger | float | 1 | 0 to 1 | Simulation |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Attractor: the Aizawa strange attractor, integrated on the GPU (as in Myriad).
// @param size 2.1 0.5 8.0
// @param rate 0.45 0.05 2.0

vec3 toWorld(vec3 q) { return (q - vec3(0.0, 0.0, 0.6)) * size; }
vec3 toAizawa(vec3 w) { return w / size + vec3(0.0, 0.0, 0.6); }

void spawn(inout Particle p) {
  p.position = toWorld((rand3(p.seed + uTime * 7.3) - 0.5) * 0.4 + vec3(0.1, 0.0, 0.0));
  p.life = 8.0 + rand1(p.seed * 2.9 + uTime) * 20.0;
}

void update(inout Particle p, float dt) {
  vec3 q = toAizawa(p.position);
  const float a = 0.95, b = 0.7, c = 0.6, d = 3.5, e = 0.25, f = 0.1;
  for (int k = 0; k < 3; k++) {
    vec3 dq = vec3((q.z - b) * q.x - d * q.y, d * q.x + (q.z - b) * q.y,
                   c + a * q.z - q.z * q.z * q.z / 3.0 - (q.x * q.x + q.y * q.y) * (1.0 + e * q.z) + f * q.z * q.x * q.x * q.x);
    q += dq * dt * rate;
  }
  vec3 np = toWorld(q);
  p.velocity = (np - p.position) / max(dt, 1e-4);
  p.position = np;
  if (length(q) > 4.0) p.age = p.life;
}

vec4 look(Particle p) {
  float t = clamp(length(p.velocity) / size * 0.35, 0.0, 1.0);
  vec3 a = vec3(0.6, 0.12, 0.45), b = vec3(1.0, 0.45, 0.25), c = vec3(1.0, 0.95, 0.8);
  return vec4(t < 0.5 ? mix(a, b, t * 2.0) : mix(b, c, t * 2.0 - 1.0), 1.0);
}
```


### Swirl

*GPU Particles source*

Particles. spawn() places one; update() moves it, called once per step. Particle: position, velocity, age, life, seed. Helpers: rand1 rand3 randBall noise3 fbm3, plus uTime. Sliders are declared below.

**Example chain:** Swirl → Wander → Glow Look → (live)

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  |
| Count | int | 200000 | 1 to 16777216 |  |
| Emit From | object |  |  |  |
| speed | float | 1 | 0 to 4 |  |
| swirl | float | 1.6 | 0.1 to 5 |  |
| drag | float | 0.9 | 0.5 to 1 |  |
| Colour By | menu |  | Code, Speed, Age | Look |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look |
| Glow | toggle | True |  | Look |
| Point Size | float | 1.5 | 0.5 to 32 | Look |
| Brightness | float | 0.4 | 0 to 8 | Look |
| Speed Range | float | 2 | 0.0001 to 50 | Look |
| Substeps | int | 1 | 1 to 20 | Simulation |
| Pre-warm | float | 0 | 0 to 120 | Simulation |
| Stagger | float | 1 | 0 to 1 | Simulation |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Particles. spawn() places one; update() moves it, called once per step.
// Particle: position, velocity, age, life, seed.
// Helpers: rand1 rand3 randBall noise3 fbm3, plus uTime. Sliders are declared below.
// @param speed 1.0 0.0 4.0
// @param swirl 1.6 0.1 5.0
// @param drag 0.9 0.5 1.0

vec3 curl(vec3 p) {                       // a divergence-free field: particles swirl, never pile up
  float e = 0.35;
  float n1 = noise3(p * swirl + vec3(0.0, 0.0, uTime * 0.2));
  float n2 = noise3((p + vec3(0.0, e, 0.0)) * swirl + vec3(0.0, 0.0, uTime * 0.2));
  float n3 = noise3((p + vec3(0.0, 0.0, e)) * swirl + vec3(5.2, 1.3, uTime * 0.2));
  float n4 = noise3((p + vec3(e, 0.0, 0.0)) * swirl + vec3(5.2, 1.3, uTime * 0.2));
  return vec3(n2 - n1, n3 - n4, n1 - n3) / e;
}

void spawn(inout Particle p) {
  p.position = randBall(p.seed) * 1.8;
  p.life = 4.0 + rand1(p.seed * 3.3) * 3.0;
}

void update(inout Particle p, float dt) {
  p.velocity = mix(curl(p.position) * speed, p.velocity, drag);
  p.position += p.velocity * dt;
}
```


### Firefly Swarm

*GPU Particles source*

Firefly Swarm: fireflies born just above the Emit From surface (or in a ball when there is none). Wire stages after it: Wander, Rise, Blink, then Firefly Look.

**Example chain:** Firefly Swarm (Emit From a ground mesh) → Wander → Rise → Blink → Firefly Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  |
| Count | int | 2500 | 1 to 16777216 |  |
| Emit From | object |  |  |  |
| height | float | 0.35 | 0 to 3 |  |
| radius | float | 2 | 0.1 to 20 |  |
| lifetime | float | 7 | 1 to 30 |  |
| Colour By | menu |  | Code, Speed, Age | Look |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look |
| Glow | toggle | True |  | Look |
| Point Size | float | 3 | 0.5 to 32 | Look |
| Brightness | float | 1 | 0 to 8 | Look |
| Speed Range | float | 2 | 0.0001 to 50 | Look |
| Substeps | int | 1 | 1 to 20 | Simulation |
| Pre-warm | float | 0 | 0 to 120 | Simulation |
| Stagger | float | 1 | 0 to 1 | Simulation |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
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
```


### Spark Ball

*GPU Particles source*

Spark Ball: particles born in a ball, with a small random kick. A plain source for stages.

**Example chain:** Spark Ball → Gravity → Drag → Streak Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  |
| Count | int | 100000 | 1 to 16777216 |  |
| Emit From | object |  |  |  |
| radius | float | 1 | 0.05 to 20 |  |
| kick | float | 0.3 | 0 to 5 |  |
| lifetime | float | 4 | 0.5 to 30 |  |
| Colour By | menu |  | Code, Speed, Age | Look |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look |
| Glow | toggle | True |  | Look |
| Point Size | float | 1.5 | 0.5 to 32 | Look |
| Brightness | float | 0.5 | 0 to 8 | Look |
| Speed Range | float | 2 | 0.0001 to 50 | Look |
| Substeps | int | 1 | 1 to 20 | Simulation |
| Pre-warm | float | 0 | 0 to 120 | Simulation |
| Stagger | float | 1 | 0 to 1 | Simulation |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Spark Ball: particles born in a ball, with a small random kick. A plain source for stages.
// @in float radius 1.0 0.05 20.0
// @in float kick 0.3 0.0 5.0
// @in float lifetime 4.0 0.5 30.0
void spawn(inout Particle p) {
  p.position = randBall(p.seed) * radius;
  p.velocity = (rand3(p.seed * 3.1) * 2.0 - 1.0) * kick;
  p.life = lifetime * (0.5 + rand1(p.seed * 2.3));
}
```


### Fountain

*GPU Particles source*

A fountain: particles shoot up from the origin and fall back under gravity.

**Example chain:** Fountain → Collide with Shape (sdf from a GPU Surface) → Glow Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  |
| Count | int | 100000 | 1 to 16777216 |  |
| Emit From | object |  |  |  |
| power | float | 4 | 0.5 to 10 |  |
| gravity | float | 9.8 | 0 to 20 |  |
| Colour By | menu |  | Code, Speed, Age | Look |
| Slow / Young | colour | (0.6, 0.85, 1, 1) |  | Look |
| Fast / Old | colour | (0.1, 0.3, 0.9, 1) |  | Look |
| Glow | toggle | False |  | Look |
| Point Size | float | 3 | 0.5 to 32 | Look |
| Brightness | float | 1 | 0 to 8 | Look |
| Speed Range | float | 2 | 0.0001 to 50 | Look |
| Substeps | int | 1 | 1 to 20 | Simulation |
| Pre-warm | float | 0 | 0 to 120 | Simulation |
| Stagger | float | 1 | 0 to 1 | Simulation |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// A fountain: particles shoot up from the origin and fall back under gravity.
// @param power 4.0 0.5 10.0
// @param gravity 9.8 0.0 20.0
void spawn(inout Particle p) {
  vec3 r = rand3(p.seed);
  p.position = vec3(0.0);
  p.velocity = vec3((r.x - 0.5) * 1.2, (r.y - 0.5) * 1.2, power * (0.8 + 0.4 * r.z));
  p.life = 1.5 + rand1(p.seed * 7.1);
}
void update(inout Particle p, float dt) {
  p.velocity.z -= gravity * dt;
  p.position += p.velocity * dt;
}
```


## Particle stages

### Wander

*GPU Stage*

Wander: lazy curl-noise drifting, like insects on a summer evening.

**Example chain:** Firefly Swarm → **Wander** → Rise → Firefly Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| strength | float | 0.5 | 0 to 5 |  |
| scale | float | 1.2 | 0.05 to 10 |  |
| calm | float | 0.92 | 0 to 0.999 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Wander: lazy curl-noise drifting, like insects on a summer evening.
// @in float strength 0.5 0.0 5.0
// @in float scale 1.2 0.05 10.0
// @in float calm 0.92 0.0 0.999
void behave(inout Particle p, float dt) {
  vec3 f = curlNoise(p.position * scale + vec3(0.0, 0.0, uTime * 0.15)) * strength;
  p.velocity = mix(f, p.velocity, calm);
}
```


### Rise

*GPU Stage*

Rise: a gentle buoyancy; particles climb towards a speed and ease off near their ceiling.

**Example chain:** Firefly Swarm → Wander → **Rise** → Firefly Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| climb | float | 0.12 | 0 to 3 |  |
| ceiling | float | 2.5 | 0.1 to 50 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Rise: a gentle buoyancy; particles climb towards a speed and ease off near their ceiling.
// @in float climb 0.12 0.0 3.0
// @in float ceiling 2.5 0.1 50.0
void behave(inout Particle p, float dt) {
  float room = clamp((ceiling - p.position.z) / max(ceiling, 1e-3), 0.0, 1.0);
  p.velocity.z = mix(p.velocity.z, climb * room - (1.0 - room) * climb, 1.0 - exp(-dt * 1.5));
}
```


### Gravity

*GPU Stage*

Gravity: a constant pull (down by default).

**Example chain:** Spark Ball → **Gravity** → Collide with Shape → Glow Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| strength | float | 9.8 | 0 to 50 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Gravity: a constant pull (down by default).
// @in float strength 9.8 0.0 50.0
void behave(inout Particle p, float dt) {
  p.velocity.z -= strength * dt;
}
```


### Vortex

*GPU Stage*

Vortex: swirl around the vertical axis through the node's centre.

**Example chain:** Spark Ball → **Vortex** → Drag → Streak Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| spin | float | 1.5 | -20 to 20 |  |
| pull | float | 0.3 | -10 to 10 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Vortex: swirl around the vertical axis through the node's centre.
// @in float spin 1.5 -20.0 20.0
// @in float pull 0.3 -10.0 10.0
void behave(inout Particle p, float dt) {
  vec3 r = vec3(p.position.xy, 0.0);
  float d = max(length(r), 0.05);
  p.velocity += (cross(vec3(0.0, 0.0, 1.0), r) / d * spin - r / d * pull) * dt;
}
```


### Drag

*GPU Stage*

Drag: slows particles down, like moving through air or water.

**Example chain:** Spark Ball → Gravity → **Drag** → Glow Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| amount | float | 1 | 0 to 20 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Drag: slows particles down, like moving through air or water.
// @in float amount 1.0 0.0 20.0
void behave(inout Particle p, float dt) {
  p.velocity *= exp(-amount * dt);
}
```


### Blink

*GPU Stage*

Blink: each particle pulses on its own rhythm. Adds two per-particle values later nodes can use: brightness (0..1) and phase (0..1, fixed per particle).

**Example chain:** Firefly Swarm → Wander → **Blink** → Firefly Look (reads brightness and phase)

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| rate | float | 0.6 | 0.05 to 6 |  |
| sharpness | float | 6 | 1 to 30 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |
| brightness | float |
| phase | float |

**Code**

```glsl
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
```


### Push by Field

*GPU Stage*

Push by Field: a force from a function wired into 'field' (e.g. Wind Field).

**Example chain:** Wind Field.wind → field; Swirl → **Push by Field** → Glow Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| amount | float | 1 | 0 to 10 |  |
| field | function |  |  |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
// Push by Field: a force from a function wired into 'field' (e.g. Wind Field).
// @in func vec3 field(vec3 p)
// @in float amount 1.0 0.0 10.0
void behave(inout Particle p, float dt) {
  p.velocity += field(p.position) * amount * dt;
}
```


### Collide with Shape

*GPU Stage*

Collide with Shape: bounce off a surface wired into 'sdf' (a GPU Surface node's sdf output).

**Example chain:** GPU Surface.sdf → sdf; Fountain → **Collide with Shape** → Glow Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| bounce | float | 0.4 | 0 to 1 |  |
| radius | float | 0.02 | 0 to 1 |  |
| sdf | function |  |  |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |
| sdf | function |

**Code**

```glsl
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
```


## Particle looks

### Glow Look

*GPU Stage*

Glow Look: soft glowing sprites that fade in and out over each particle's life.

**Example chain:** Swirl → Wander → **Glow Look**

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| tint | colour | (1, 0.6, 0.25, 1) |  |  |
| intensity | float | 2 | 0 to 20 |  |
| size | float | 0.03 | 0.001 to 1 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
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
```


### Firefly Look

*GPU Stage*

Firefly Look: a glowing abdomen with a soft halo, and two little wings that flap. Reads brightness and phase (from Blink, or their defaults without it).

**Example chain:** Firefly Swarm → Wander → Rise → Blink → **Firefly Look**

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| glow | colour | (1, 0.78, 0.22, 1) |  |  |
| intensity | float | 3 | 0 to 40 |  |
| size | float | 0.012 | 0.001 to 0.2 |  |
| flap | float | 18 | 0 to 60 |  |
| wing | float | 2.4 | 0.2 to 6 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |
| brightness | float |
| phase | float |

**Code**

```glsl
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
```


### Streak Look

*GPU Stage*

Streak Look: each particle drawn as a glowing streak along its motion (a trail). Wire it next to another look from the same stream to get heads and trails from the same particles.

**Example chain:** Blink → Firefly Look and Blink → **Streak Look** (heads and trails, one simulation)

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| tint | colour | (0.55, 0.75, 1, 1) |  |  |
| intensity | float | 1.2 | 0 to 20 |  |
| size | float | 0.01 | 0.001 to 0.5 |  |
| trail | float | 0.25 | 0 to 5 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

**Code**

```glsl
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
```


### Material Look

*GPU Stage*

Material Look: particles in a Blender material's colours (its Principled BSDF: base colour, roughness, metallic, emission), lit by the scene when a Scene Lights node is wired into 'light'. Wire its 'material' output into a GPU Surface's Material input to shade a surface with it. Each particle is shaded as a small sphere facing the camera.

**Example chain:** Scene Lights.light → light; Swirl → **Material Look**; its material → a GPU Surface's Material

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| size | float | 0.03 | 0.001 to 1 |  |
| brightness | float | 1 | 0 to 10 |  |
| mat | material |  |  |  |
| light | function |  |  |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |
| material | function |

**Code**

```glsl
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
```


## Warps (particles and meshes)

### Bend

*GPU Stage*

Bend: bends, stretches and twists whatever comes in (particles or a mesh) along an axis, without changing it: the particles still move as before, they're just shown bent. axis: 0 = X, 1 = Y, 2 = Z (the length being bent). A flat galaxy curls when bent along X.

**Example chain:** Galaxy → **Bend** → (live); or a mesh → **Bend** → To Mesh

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| angle | float | 1.2 | -6.2832 to 6.2832 |  |
| span | float | 3 | 0.1 to 50 |  |
| stretch | float | 1 | 0.1 to 5 |  |
| twist | float | 0 | -12.566 to 12.566 |  |
| axis | int | 2 | 0 to 2 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |
| Mesh | geometry |

**Code**

```glsl
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
```


### Taper

*GPU Stage*

Taper: squeezes or flares whatever comes in along Z.

**Example chain:** a mesh → **Taper** → To Mesh

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Particles | particles |  |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| amount | float | 0.5 | -2 to 2 |  |
| span | float | 3 | 0.1 to 50 |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |
| Mesh | geometry |

**Code**

```glsl
// Taper: squeezes or flares whatever comes in along Z.
// @in float amount 0.5 -2.0 2.0
// @in float span 3.0 0.1 50.0
vec3 warp(vec3 q) {
  float s = max(1.0 + amount * q.z / max(span, 1e-3), 0.0);
  return vec3(q.xy * s, q.z);
}
```


## Mesh stages

### Ripple

*GPU Stage*

Ripple: rings travel out from the centre along the normals; colour follows the height.

**Example chain:** GPU Mesh → **Ripple** → To Mesh

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| amplitude | float | 0.05 | 0 to 1 |  |
| wavelength | float | 0.4 | 0.02 to 5 |  |
| speed | float | 1 | 0 to 10 |  |

**Outputs**

| Socket | Type |
|---|---|
| Mesh | geometry |

**Code**

```glsl
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
```


### Sway by Field

*GPU Stage*

Sway by Field: bends a mesh (grass, cloth, hair cards) with a force function wired into 'field' (e.g. Wind Field): the higher a vertex, the further it's pushed. Colour darkens where it bends most.

**Example chain:** Wind Field.wind → field; a grass mesh → **Sway by Field** → (live) or To Mesh

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| amount | float | 0.15 | 0 to 5 |  |
| height | float | 0.6 | 0.01 to 10 |  |
| field | function |  |  |  |

**Outputs**

| Socket | Type |
|---|---|
| Mesh | geometry |

**Code**

```glsl
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
```


## Functions

### Wind Field

*GPU Stage*

Wind Field: a gusty breeze as a function other nodes can call (wire 'wind' into Push by Field).

**Example chain:** **Wind Field**.wind → Push by Field.field and Sway by Field.field

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| strength | float | 0.4 | 0 to 10 |  |
| gusts | float | 0.7 | 0 to 5 |  |
| turbulence | float | 0.3 | 0 to 5 |  |

**Outputs**

| Socket | Type |
|---|---|
| wind | function |

**Code**

```glsl
// Wind Field: a gusty breeze as a function other nodes can call (wire 'wind' into Push by Field).
// @in float strength 0.4 0.0 10.0
// @in float gusts 0.7 0.0 5.0
// @in float turbulence 0.3 0.0 5.0
// @out func wind
vec3 wind(vec3 q) {
  float g = 0.6 + 0.4 * sin(uTime * gusts + q.y * 0.5);
  return vec3(strength * g, 0.0, 0.0) + curlNoise(q * 0.4 + vec3(uTime * 0.1)) * turbulence;
}
```


## Lighting

### Scene Lights

*GPU Stage*

Scene Lights: the scene's lamps (sun, point, spot, area) and world colour as a function other nodes call: wire 'light' into a lit look (Material Look) or a GPU Surface's Lights input. CodeNodes keeps the light data (hidden inputs) in step with the scene, up to 8 lamps. No shadows are cast between GPU nodes and Blender objects.

**Example chain:** **Scene Lights**.light → Material Look.light and a GPU Surface's Lights

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  |
| intensity | float | 1 | 0 to 10 |  |
| world | float | 1 | 0 to 10 |  |

**Outputs**

| Socket | Type |
|---|---|
| light | function |

**Code**

```glsl
// Scene Lights: the scene's lamps (sun, point, spot, area) and world colour as a function
// other nodes call: wire 'light' into a lit look (Material Look) or a GPU Surface's Lights input.
// CodeNodes keeps the light data (hidden inputs) in step with the scene, up to 8 lamps.
// No shadows are cast between GPU nodes and Blender objects.
// @in float intensity 1.0 0.0 10.0
// @in float world 1.0 0.0 10.0
// @out func light
// @in hidden w_r 0.05
// @in hidden w_g 0.05
// @in hidden w_b 0.05
// @in hidden l0_type -1.0
// @in hidden l0_px 0.0
// @in hidden l0_py 0.0
// @in hidden l0_pz 0.0
// @in hidden l0_dx 0.0
// @in hidden l0_dy 0.0
// @in hidden l0_dz 0.0
// @in hidden l0_r 0.0
// @in hidden l0_g 0.0
// @in hidden l0_b 0.0
// @in hidden l0_size 0.0
// @in hidden l0_c0 0.0
// @in hidden l0_c1 0.0
// @in hidden l1_type -1.0
// @in hidden l1_px 0.0
// @in hidden l1_py 0.0
// @in hidden l1_pz 0.0
// @in hidden l1_dx 0.0
// @in hidden l1_dy 0.0
// @in hidden l1_dz 0.0
// @in hidden l1_r 0.0
// @in hidden l1_g 0.0
// @in hidden l1_b 0.0
// @in hidden l1_size 0.0
// @in hidden l1_c0 0.0
// @in hidden l1_c1 0.0
// @in hidden l2_type -1.0
// @in hidden l2_px 0.0
// @in hidden l2_py 0.0
// @in hidden l2_pz 0.0
// @in hidden l2_dx 0.0
// @in hidden l2_dy 0.0
// @in hidden l2_dz 0.0
// @in hidden l2_r 0.0
// @in hidden l2_g 0.0
// @in hidden l2_b 0.0
// @in hidden l2_size 0.0
// @in hidden l2_c0 0.0
// @in hidden l2_c1 0.0
// @in hidden l3_type -1.0
// @in hidden l3_px 0.0
// @in hidden l3_py 0.0
// @in hidden l3_pz 0.0
// @in hidden l3_dx 0.0
// @in hidden l3_dy 0.0
// @in hidden l3_dz 0.0
// @in hidden l3_r 0.0
// @in hidden l3_g 0.0
// @in hidden l3_b 0.0
// @in hidden l3_size 0.0
// @in hidden l3_c0 0.0
// @in hidden l3_c1 0.0
// @in hidden l4_type -1.0
// @in hidden l4_px 0.0
// @in hidden l4_py 0.0
// @in hidden l4_pz 0.0
// @in hidden l4_dx 0.0
// @in hidden l4_dy 0.0
// @in hidden l4_dz 0.0
// @in hidden l4_r 0.0
// @in hidden l4_g 0.0
// @in hidden l4_b 0.0
// @in hidden l4_size 0.0
// @in hidden l4_c0 0.0
// @in hidden l4_c1 0.0
// @in hidden l5_type -1.0
// @in hidden l5_px 0.0
// @in hidden l5_py 0.0
// @in hidden l5_pz 0.0
// @in hidden l5_dx 0.0
// @in hidden l5_dy 0.0
// @in hidden l5_dz 0.0
// @in hidden l5_r 0.0
// @in hidden l5_g 0.0
// @in hidden l5_b 0.0
// @in hidden l5_size 0.0
// @in hidden l5_c0 0.0
// @in hidden l5_c1 0.0
// @in hidden l6_type -1.0
// @in hidden l6_px 0.0
// @in hidden l6_py 0.0
// @in hidden l6_pz 0.0
// @in hidden l6_dx 0.0
// @in hidden l6_dy 0.0
// @in hidden l6_dz 0.0
// @in hidden l6_r 0.0
// @in hidden l6_g 0.0
// @in hidden l6_b 0.0
// @in hidden l6_size 0.0
// @in hidden l6_c0 0.0
// @in hidden l6_c1 0.0
// @in hidden l7_type -1.0
// @in hidden l7_px 0.0
// @in hidden l7_py 0.0
// @in hidden l7_pz 0.0
// @in hidden l7_dx 0.0
// @in hidden l7_dy 0.0
// @in hidden l7_dz 0.0
// @in hidden l7_r 0.0
// @in hidden l7_g 0.0
// @in hidden l7_b 0.0
// @in hidden l7_size 0.0
// @in hidden l7_c0 0.0
// @in hidden l7_c1 0.0

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
  if (l0_type > -0.5) { lamp(p, l0_type, vec3(l0_px, l0_py, l0_pz), vec3(l0_dx, l0_dy, l0_dz), vec3(l0_r, l0_g, l0_b), l0_size, l0_c0, l0_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  if (l1_type > -0.5) { lamp(p, l1_type, vec3(l1_px, l1_py, l1_pz), vec3(l1_dx, l1_dy, l1_dz), vec3(l1_r, l1_g, l1_b), l1_size, l1_c0, l1_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  if (l2_type > -0.5) { lamp(p, l2_type, vec3(l2_px, l2_py, l2_pz), vec3(l2_dx, l2_dy, l2_dz), vec3(l2_r, l2_g, l2_b), l2_size, l2_c0, l2_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  if (l3_type > -0.5) { lamp(p, l3_type, vec3(l3_px, l3_py, l3_pz), vec3(l3_dx, l3_dy, l3_dz), vec3(l3_r, l3_g, l3_b), l3_size, l3_c0, l3_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  if (l4_type > -0.5) { lamp(p, l4_type, vec3(l4_px, l4_py, l4_pz), vec3(l4_dx, l4_dy, l4_dz), vec3(l4_r, l4_g, l4_b), l4_size, l4_c0, l4_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  if (l5_type > -0.5) { lamp(p, l5_type, vec3(l5_px, l5_py, l5_pz), vec3(l5_dx, l5_dy, l5_dz), vec3(l5_r, l5_g, l5_b), l5_size, l5_c0, l5_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  if (l6_type > -0.5) { lamp(p, l6_type, vec3(l6_px, l6_py, l6_pz), vec3(l6_dx, l6_dy, l6_dz), vec3(l6_r, l6_g, l6_b), l6_size, l6_c0, l6_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  if (l7_type > -0.5) { lamp(p, l7_type, vec3(l7_px, l7_py, l7_pz), vec3(l7_dx, l7_dy, l7_dz), vec3(l7_r, l7_g, l7_b), l7_size, l7_c0, l7_c1, l, rad, wd); c += brdf(n, v, l, albedo, roughness, metallic, wd) * rad * intensity; }
  return c;
}
```


## GPU Surfaces

### Castle

*GPU Surface (SDF)*

The Aerie citadel as a GPU surface: a ring wall with towers, a keep and roofs. Units: the citadel is about 25 across; `scale` is metres per unit.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  |
| towers | float | 6 | 3 to 12 |  |
| radius | float | 10 | 6 to 12.5 |  |
| wallHeight | float | 4.5 | 2 to 9 |  |
| keepHeight | float | 14 | 6 to 26 |  |
| scale | float | 0.1 | 0.02 to 1 |  |
| Lights | function |  |  |  |
| Material | function |  |  |  |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look |
| Shadows | toggle | True |  | Look |
| Ambient Occlusion | toggle | True |  | Look |
| Fog | float | 0 | 0 to 1 | Look |
| Sky | toggle | False |  | Look |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look |
| Bounds Min | vector | (-1.6, -1.6, -0.15) |  | Bounds |
| Bounds Max | vector | (1.6, 1.6, 2.35) |  | Bounds |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |
| sdf | function |

**Code**

```glsl
// The Aerie citadel as a GPU surface: a ring wall with towers, a keep and roofs.
// Units: the citadel is about 25 across; `scale` is metres per unit.
// @param towers 6 3 12
// @param radius 10 6 12.5
// @param wallHeight 4.5 2 9
// @param keepHeight 14 6 26
// @param scale 0.1 0.02 1.0

float citadel(vec3 p) {
  float ring = abs(length(p.xy) - radius) - 0.7;
  float wall = max(ring, max(-p.z - 1.0, p.z - wallHeight));
  vec3 m = aroundZ(p, floor(towers) * 7.0);
  wall = min(wall, sdBox(m - vec3(radius, 0.0, wallHeight + 0.45), vec3(0.7, 0.42, 0.45)));
  float gate = min(sdBox(p - vec3(0.0, -radius, 1.0), vec3(1.4, 2.0, 2.0)),
                   sdCylinder(rotateX(p - vec3(0.0, -radius, 3.0), 1.5708), 1.4, 2.0));
  wall = max(wall, -gate);
  vec3 t = aroundZ(p, floor(towers));
  float towerH = wallHeight + 3.5;
  float tower = sdCylinder(t - vec3(radius, 0.0, towerH * 0.5), 2.0, towerH * 0.5 + 0.5);
  vec3 s = aroundZ(t - vec3(radius, 0.0, 0.0), 4.0);
  tower = max(tower, -sdBox(s - vec3(2.0, 0.0, towerH * 0.62), vec3(0.5, 0.11, 0.6)));
  float keep = sdRoundBox(p - vec3(0.0, 0.0, keepHeight * 0.5), vec3(3.6, 3.6, keepHeight * 0.5), 0.25);
  vec3 k = aroundZ(p, 4.0);
  float arcade = min(sdBox(k - vec3(3.6, 0.0, 1.1), vec3(0.8, 1.05, 1.1)),
                     sdCylinder(rotateY(k - vec3(3.6, 0.0, 2.2), 1.5708), 1.05, 0.8));
  float windows = sdBox(k - vec3(3.6, 0.0, keepHeight * 0.72), vec3(0.8, 0.35, 0.9));
  keep = max(keep, -min(arcade, windows));
  return min(min(wall, tower), keep);
}

float roofs(vec3 p) {
  vec3 t = aroundZ(p, floor(towers));
  float towerH = wallHeight + 3.5;
  float caps = sdCone(t - vec3(radius, 0.0, towerH + 2.4), 2.4, 2.55, 0.05);
  float spire = sdCone(p - vec3(0.0, 0.0, keepHeight + 3.4), 3.4, 4.0, 0.08);
  return min(caps, spire);
}

float sdf(vec3 p) {
  vec3 q = p / scale;
  return min(citadel(q), roofs(q)) * scale;
}

// colour for the live view: terracotta roofs, weathered stone
vec3 color(vec3 p) {
  vec3 q = p / scale;
  if (roofs(q) < citadel(q)) return vec3(0.72, 0.30, 0.18);
  return vec3(0.80, 0.74, 0.64) * (0.82 + 0.18 * noise3(q * 1.7));
}
```


### Planet

*GPU Surface (SDF)*

A small planet, Tellus-style: continents from fractal noise, shallow seas, snow on the peaks and at the poles.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  |
| radius | float | 1 | 0.3 to 3 |  |
| relief | float | 0.05 | 0 to 0.25 |  |
| sea | float | 0.5 | 0.3 to 0.7 |  |
| seed | float | 3 | 0 to 100 |  |
| Lights | function |  |  |  |
| Material | function |  |  |  |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look |
| Shadows | toggle | True |  | Look |
| Ambient Occlusion | toggle | True |  | Look |
| Fog | float | 0 | 0 to 1 | Look |
| Sky | toggle | False |  | Look |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look |
| Bounds Min | vector | (-1.45, -1.45, -1.45) |  | Bounds |
| Bounds Max | vector | (1.45, 1.45, 1.45) |  | Bounds |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |
| sdf | function |

**Code**

```glsl
// A small planet, Tellus-style: continents from fractal noise, shallow seas, snow on the peaks
// and at the poles.
// @param radius 1.0 0.3 3.0
// @param relief 0.05 0.0 0.25
// @param sea 0.5 0.3 0.7
// @param seed 3.0 0.0 100.0

float land(vec3 n) { return fbm3(n * 2.3 + seed); }

float sdf(vec3 p) {
  vec3 n = normalize(p);
  float h = max(land(n), sea);
  return (length(p) - radius - (h - sea) * relief * 2.0) * 0.8;
}

vec3 color(vec3 p) {
  vec3 n = normalize(p);
  float h = land(n);
  if (h < sea) return mix(vec3(0.02, 0.07, 0.22), vec3(0.06, 0.32, 0.45), smoothstep(sea - 0.07, sea, h));
  float e = (h - sea) / (1.0 - sea);
  vec3 c = mix(vec3(0.16, 0.40, 0.12), vec3(0.50, 0.42, 0.28), smoothstep(0.05, 0.35, e));
  return mix(c, vec3(0.95), smoothstep(0.55, 0.75, e + abs(n.z) * 0.55));
}
```


### Saturn

*GPU Surface (SDF)*

A ringed planet: a banded sphere and a tilted ring, one surface.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  |
| radius | float | 0.9 | 0.1 to 2 |  |
| ring | float | 1.55 | 0.5 to 3 |  |
| width | float | 0.35 | 0.02 to 1 |  |
| tilt | float | 0.45 | 0 to 1.5 |  |
| Lights | function |  |  |  |
| Material | function |  |  |  |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look |
| Shadows | toggle | True |  | Look |
| Ambient Occlusion | toggle | True |  | Look |
| Fog | float | 0 | 0 to 1 | Look |
| Sky | toggle | False |  | Look |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look |
| Bounds Min | vector | (-2.2, -2.2, -1.2) |  | Bounds |
| Bounds Max | vector | (2.2, 2.2, 1.2) |  | Bounds |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |
| sdf | function |

**Code**

```glsl
// A ringed planet: a banded sphere and a tilted ring, one surface.
// @param radius 0.9 0.1 2.0
// @param ring 1.55 0.5 3.0
// @param width 0.35 0.02 1.0
// @param tilt 0.45 0.0 1.5

float planet(vec3 p) { return sdSphere(p, radius) + 0.012 * sin(p.z * 18.0 + uTime * 3.0); }
float band(vec3 p) {
  vec3 q = rotateX(p, tilt);
  vec2 r = vec2(length(q.xy) - ring, q.z);
  return sdBox(vec3(r, 0.0), vec3(width, 0.02, 1.0));
}
float sdf(vec3 p) { return min(planet(p), band(p)); }

vec3 color(vec3 p) {
  if (band(p) < planet(p)) {
    float r = length(rotateX(p, tilt).xy);
    return vec3(0.85, 0.78, 0.62) * (0.7 + 0.3 * sin(r * 60.0));
  }
  return mix(vec3(0.85, 0.66, 0.42), vec3(0.95, 0.88, 0.7), 0.5 + 0.5 * sin(p.z * 18.0 + uTime * 3.0));
}
```


### Donut

*GPU Surface (SDF)*

A donut. sdf(p) is the distance to the surface: negative inside, positive outside (metres). Each @param line below becomes an input on the node.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  |
| major | float | 0.8 | 0.2 to 2 |  |
| minor | float | 0.3 | 0.05 to 1 |  |
| Lights | function |  |  |  |
| Material | function |  |  |  |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look |
| Shadows | toggle | True |  | Look |
| Ambient Occlusion | toggle | True |  | Look |
| Fog | float | 0 | 0 to 1 | Look |
| Sky | toggle | False |  | Look |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds |
| Bounds Max | vector | (2, 2, 2) |  | Bounds |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |
| sdf | function |

**Code**

```glsl
// A donut. sdf(p) is the distance to the surface: negative inside, positive outside (metres).
// Each @param line below becomes an input on the node.
// @param major 0.8 0.2 2.0
// @param minor 0.3 0.05 1.0
float sdf(vec3 p) {
  return sdTorus(p, major, minor);
}
```


### Rounded Box

*GPU Surface (SDF)*

A box with rounded edges.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  |
| size | float | 0.8 | 0.1 to 2 |  |
| roundness | float | 0.15 | 0 to 0.5 |  |
| Lights | function |  |  |  |
| Material | function |  |  |  |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look |
| Shadows | toggle | True |  | Look |
| Ambient Occlusion | toggle | True |  | Look |
| Fog | float | 0 | 0 to 1 | Look |
| Sky | toggle | False |  | Look |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds |
| Bounds Max | vector | (2, 2, 2) |  | Bounds |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |
| sdf | function |

**Code**

```glsl
// A box with rounded edges.
// @param size 0.8 0.1 2.0
// @param roundness 0.15 0.0 0.5
float sdf(vec3 p) {
  return sdRoundBox(p, vec3(size), min(roundness, size));
}
```


### Gyroid Ball

*GPU Surface (SDF)*

A sphere carved into a gyroid lattice.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  |
| radius | float | 1 | 0.2 to 2 |  |
| cells | float | 6 | 1 to 16 |  |
| thickness | float | 0.05 | 0.01 to 0.3 |  |
| Lights | function |  |  |  |
| Material | function |  |  |  |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look |
| Shadows | toggle | True |  | Look |
| Ambient Occlusion | toggle | True |  | Look |
| Fog | float | 0 | 0 to 1 | Look |
| Sky | toggle | False |  | Look |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds |
| Bounds Max | vector | (2, 2, 2) |  | Bounds |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |
| sdf | function |

**Code**

```glsl
// A sphere carved into a gyroid lattice.
// @param radius 1.0 0.2 2.0
// @param cells 6.0 1.0 16.0
// @param thickness 0.05 0.01 0.3
float sdf(vec3 p) {
  vec3 q = p * cells;
  float g = abs(dot(sin(q), cos(q.yzx))) / cells - thickness;
  return max(sdSphere(p, radius), g);
}
```


### Blob

*GPU Surface (SDF)*

Code -> Mesh starter: five spheres melting together into a moving blob. Replace it with your own. Define sdf(p): the distance to the surface, negative inside, positive outside, in Blender units. Helpers: sdSphere sdBox sdRoundBox sdTorus sdCapsule sdCylinder smin smax rotateX/Y/Z noise3 fbm3 Time: uTime (seconds), uFrame. Sliders: declare them like the lines below.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  |
| blend | float | 0.35 | 0 to 1 |  |
| wobble | float | 0.08 | 0 to 0.3 |  |
| Lights | function |  |  |  |
| Material | function |  |  |  |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look |
| Shadows | toggle | True |  | Look |
| Ambient Occlusion | toggle | True |  | Look |
| Fog | float | 0 | 0 to 1 | Look |
| Sky | toggle | False |  | Look |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds |
| Bounds Max | vector | (2, 2, 2) |  | Bounds |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |
| sdf | function |

**Code**

```glsl
// Code -> Mesh starter: five spheres melting together into a moving blob. Replace it with your own.
// Define sdf(p): the distance to the surface, negative inside, positive outside, in Blender units.
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
```


## GPU Mesh

### Wave

*GPU Mesh*

Wave: ripples travel across the incoming mesh along its normals; colour follows the height.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  |
| amplitude | float | 0.08 | 0 to 1 |  |
| frequency | float | 6 | 0.5 to 30 |  |
| speed | float | 2 | 0 to 10 |  |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

**Code**

```glsl
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
```


### Noise Displace

*GPU Mesh*

Noise Displace: fractal noise pushes every vertex out along its normal, like weathered rock.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  |
| strength | float | 0.15 | 0 to 1 |  |
| scale | float | 3 | 0.2 to 20 |  |
| seed | float | 0 | 0 to 100 |  |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

**Code**

```glsl
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
```


### Mesa

*GPU Mesh*

Mesa: a rocky plateau rising out of a flat grid: soft-edged, roughened by noise, coloured by height, with a flat summit (put something on it).

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  |
| height | float | 1.1 | 0 to 4 |  |
| radius | float | 2.4 | 0.5 to 6 |  |
| rough | float | 0.22 | 0 to 1 |  |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

**Code**

```glsl
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
```


### Twist

*GPU Mesh*

Twist: turn the mesh around its Z axis, more the higher it goes.

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Mesh | geometry |  |  |  |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  |
| turns | float | 0.5 | -4 to 4 |  |
| height | float | 2 | 0.1 to 20 |  |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

**Code**

```glsl
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
```


## Code Shapes

### Desk Lamp

*Code Shape*

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Desk Lamp, Vase, Custom |  |
| height | float | 0.34 | 0.1 to 0.8 |  |
| shade_r | float | 0.14 | 0.03 to 0.4 |  |
| stem_r | float | 0.01 | 0.003 to 0.05 |  |
| base_r | float | 0.095 | 0.03 to 0.3 |  |
| Smooth | toggle | True |  | Look |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

**Code**

```text
# A desk lamp. Every number can be maths, so the whole thing is one formula.
param height   0.34   0.10 0.80
param shade_r  0.14   0.03 0.40
param stem_r   0.010  0.003 0.05
param base_r   0.095  0.03 0.30

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
```


### Vase

*Code Shape*

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  |
| Template | menu |  | Desk Lamp, Vase, Custom |  |
| height | float | 0.4 | 0.1 to 1 |  |
| belly | float | 0.12 | 0.03 to 0.4 |  |
| neck | float | 0.05 | 0.02 to 0.2 |  |
| Smooth | toggle | True |  | Look |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

**Code**

```text
# A vase: one curved profile, spun around the vertical axis.
param height  0.40  0.10 1.00
param belly   0.12  0.03 0.40
param neck    0.05  0.02 0.20

part body
  profile
    move 0, 0
    line belly * 0.7, 0
    curve x = belly * 0.7 + (neck - belly * 0.7) * t + belly * 0.5 * sin(t * pi)   y = height * t   steps 24
    line 0, height
    close
  revolve segments 64
```


## Flow nodes

### To Geometry, after particles (To Points)

*Flow node*

CodeNodes: turns the GPU node (or chain) before it into real geometry: points with every per-particle attribute, or a mesh. Later nodes and renders can use it, like after Realize Instances

**Example chain:** any GPU node or chain → **To Geometry** → Instance on Points / Set Material / ...

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| Particles | particles |  |  |  |
| When | menu |  | Automatic, Every Frame, When Changed, Only for Render |  |
| Max Points | int | 0 | 0 to 16777216 |  |
| Keep Velocity | toggle | True |  |  |
| Keep Age | toggle | True |  |  |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

### To Geometry, after a surface (To Mesh)

*Flow node*

CodeNodes: turns the GPU node (or chain) before it into real geometry: points with every per-particle attribute, or a mesh. Later nodes and renders can use it, like after Realize Instances

**Example chain:** any GPU node or chain → **To Geometry** → Instance on Points / Set Material / ...

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| Geometry | geometry |  |  |  |
| When | menu |  | Automatic, Every Frame, When Changed, Only for Render |  |
| Resolution | int | 128 | 8 to 512 |  |

**Outputs**

| Socket | Type |
|---|---|
| Geometry | geometry |

### Join Particles

*Flow node*

CodeNodes: merges two particle streams. Everything wired after it applies to both, and a To Geometry after it outputs both

**Example chain:** Swirl and Fountain → **Join Particles** → Glow Look → To Points

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| Particles A | particles |  |  |  |
| Particles B | particles |  |  |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |

### GPU Cache

*Flow node*

CodeNodes: bakes the GPU simulation of the particles passing through it and plays it back. Mode: Live or Cached. Switch on ⟳ Bake Now to bake Start–End

**Example chain:** Firefly Swarm → Wander → Rise → **GPU Cache** → Firefly Look

**Inputs**

| Socket | Type | Default | Range | Panel |
|---|---|---|---|---|
| Particles | particles |  |  |  |
| Mode | menu |  | Live, Cached |  |
| Start | int | 1 | -100000 to 100000 |  |
| End | int | 120 | -100000 to 100000 |  |
| ⟳ Bake Now | toggle | False |  |  |
| ✕ Clear | toggle | False |  |  |

**Outputs**

| Socket | Type |
|---|---|
| Particles | particles |
