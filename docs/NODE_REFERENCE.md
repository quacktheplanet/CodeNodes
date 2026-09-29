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

**Tooltips.** End any declaration line with a sentence in double quotes and it becomes that socket's
hover tooltip in the node editor (and its "What it does" entry below):

```glsl
// @in float calm 0.92 0.0 0.999  "How smoothly they turn: higher = lazier"
// @out attr heat 0.0  "How hot each particle is (0 to 1)"
```

The first comment line of the code (one that isn't a declaration) becomes the node's own description.

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| Count | int | 1000000 | 1 to 16777216 |  | How many particles to simulate. Drawing them live handles millions; To Geometry copies them into Blender, which costs more |
| Emit From | object |  |  |  | An object whose surface the particles are born on (optional). Without one, the code decides where they start |
| size | float | 2.4 | 0.5 to 10 |  | Radius of the galaxy |
| twist | float | 2.4 | 0 to 6 |  | How strongly the orbits twist into spiral arms |
| spin | float | 0.9 | 0 to 3 |  | How fast it turns |
| Colour By | menu |  | Code, Speed, Age | Look | What colours the particles when no Look node is wired: the code's own colour, their speed or their age |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look | Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age) |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look | Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age) |
| Glow | toggle | True |  | Look | Draw additively, so overlapping particles add up to a glow. Off draws them solid |
| Point Size | float | 1 | 0.5 to 32 | Look | Size of each particle on screen, in pixels, when no Look node sets a size |
| Brightness | float | 0.28 | 0 to 8 | Look | Overall brightness of the live particles |
| Speed Range | float | 2 | 0.0001 to 50 | Look | The speed (metres per second) that counts as fully fast for Colour By: Speed |
| Substeps | int | 1 | 1 to 20 | Simulation | Simulation steps per frame. More is steadier for fast or stiff motion, and costs more |
| Pre-warm | float | 0 | 0 to 120 | Simulation | Seconds simulated before the first frame, so the particles open already in shape |
| Stagger | float | 1 | 0 to 1 | Simulation | How spread out the births are: 0 = all at once, 1 = evenly over their lifetime |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Galaxy: a million stars on twisted ellipses (a density wave, as in Myriad). Each ring of orbits is
// turned a little more than the one inside it, so the spiral arms never wind up.
// @param size 2.4 0.5 10.0  "Radius of the galaxy"
// @param twist 2.4 0.0 6.0  "How strongly the orbits twist into spiral arms"
// @param spin 0.9 0.0 3.0  "How fast it turns"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| Count | int | 1000000 | 1 to 16777216 |  | How many particles to simulate. Drawing them live handles millions; To Geometry copies them into Blender, which costs more |
| Emit From | object |  |  |  | An object whose surface the particles are born on (optional). Without one, the code decides where they start |
| scale | float | 0.55 | 0.1 to 3 |  | Size of the current's swirls |
| speed | float | 1.9 | 0.1 to 6 |  | How fast particles ride the current |
| radius | float | 2.2 | 0.5 to 8 |  | Size of the region they live in |
| Colour By | menu |  | Code, Speed, Age | Look | What colours the particles when no Look node is wired: the code's own colour, their speed or their age |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look | Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age) |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look | Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age) |
| Glow | toggle | True |  | Look | Draw additively, so overlapping particles add up to a glow. Off draws them solid |
| Point Size | float | 1 | 0.5 to 32 | Look | Size of each particle on screen, in pixels, when no Look node sets a size |
| Brightness | float | 0.3 | 0 to 8 | Look | Overall brightness of the live particles |
| Speed Range | float | 2 | 0.0001 to 50 | Look | The speed (metres per second) that counts as fully fast for Colour By: Speed |
| Substeps | int | 1 | 1 to 20 | Simulation | Simulation steps per frame. More is steadier for fast or stiff motion, and costs more |
| Pre-warm | float | 3 | 0 to 120 | Simulation | Seconds simulated before the first frame, so the particles open already in shape |
| Stagger | float | 1 | 0 to 1 | Simulation | How spread out the births are: 0 = all at once, 1 = evenly over their lifetime |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Flow: particles ride a curl-noise current (as in Myriad): crisp filaments that never clump.
// @param scale 0.55 0.1 3.0  "Size of the current's swirls"
// @param speed 1.9 0.1 6.0  "How fast particles ride the current"
// @param radius 2.2 0.5 8.0  "Size of the region they live in"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| Count | int | 600000 | 1 to 16777216 |  | How many particles to simulate. Drawing them live handles millions; To Geometry copies them into Blender, which costs more |
| Emit From | object |  |  |  | An object whose surface the particles are born on (optional). Without one, the code decides where they start |
| size | float | 2.1 | 0.5 to 8 |  | Size of the attractor |
| rate | float | 0.45 | 0.05 to 2 |  | How fast particles move along it |
| Colour By | menu |  | Code, Speed, Age | Look | What colours the particles when no Look node is wired: the code's own colour, their speed or their age |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look | Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age) |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look | Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age) |
| Glow | toggle | True |  | Look | Draw additively, so overlapping particles add up to a glow. Off draws them solid |
| Point Size | float | 1 | 0.5 to 32 | Look | Size of each particle on screen, in pixels, when no Look node sets a size |
| Brightness | float | 0.3 | 0 to 8 | Look | Overall brightness of the live particles |
| Speed Range | float | 2 | 0.0001 to 50 | Look | The speed (metres per second) that counts as fully fast for Colour By: Speed |
| Substeps | int | 1 | 1 to 20 | Simulation | Simulation steps per frame. More is steadier for fast or stiff motion, and costs more |
| Pre-warm | float | 4 | 0 to 120 | Simulation | Seconds simulated before the first frame, so the particles open already in shape |
| Stagger | float | 1 | 0 to 1 | Simulation | How spread out the births are: 0 = all at once, 1 = evenly over their lifetime |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Attractor: the Aizawa strange attractor, integrated on the GPU (as in Myriad).
// @param size 2.1 0.5 8.0  "Size of the attractor"
// @param rate 0.45 0.05 2.0  "How fast particles move along it"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| Count | int | 200000 | 1 to 16777216 |  | How many particles to simulate. Drawing them live handles millions; To Geometry copies them into Blender, which costs more |
| Emit From | object |  |  |  | An object whose surface the particles are born on (optional). Without one, the code decides where they start |
| speed | float | 1 | 0 to 4 |  | How fast particles swirl |
| swirl | float | 1.6 | 0.1 to 5 |  | Size of the swirling pattern (higher = tighter) |
| drag | float | 0.9 | 0.5 to 1 |  | How much speed they keep each step (1 = none lost) |
| Colour By | menu |  | Code, Speed, Age | Look | What colours the particles when no Look node is wired: the code's own colour, their speed or their age |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look | Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age) |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look | Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age) |
| Glow | toggle | True |  | Look | Draw additively, so overlapping particles add up to a glow. Off draws them solid |
| Point Size | float | 1.5 | 0.5 to 32 | Look | Size of each particle on screen, in pixels, when no Look node sets a size |
| Brightness | float | 0.4 | 0 to 8 | Look | Overall brightness of the live particles |
| Speed Range | float | 2 | 0.0001 to 50 | Look | The speed (metres per second) that counts as fully fast for Colour By: Speed |
| Substeps | int | 1 | 1 to 20 | Simulation | Simulation steps per frame. More is steadier for fast or stiff motion, and costs more |
| Pre-warm | float | 0 | 0 to 120 | Simulation | Seconds simulated before the first frame, so the particles open already in shape |
| Stagger | float | 1 | 0 to 1 | Simulation | How spread out the births are: 0 = all at once, 1 = evenly over their lifetime |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Particles. spawn() places one; update() moves it, called once per step.
// Particle: position, velocity, age, life, seed.
// Helpers: rand1 rand3 randBall noise3 fbm3, plus uTime. Sliders are declared below.
// @param speed 1.0 0.0 4.0  "How fast particles swirl"
// @param swirl 1.6 0.1 5.0  "Size of the swirling pattern (higher = tighter)"
// @param drag 0.9 0.5 1.0  "How much speed they keep each step (1 = none lost)"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| Count | int | 2500 | 1 to 16777216 |  | How many particles to simulate. Drawing them live handles millions; To Geometry copies them into Blender, which costs more |
| Emit From | object |  |  |  | An object whose surface the particles are born on (optional). Without one, the code decides where they start |
| height | float | 0.35 | 0 to 3 |  | How high above the surface they're born, in metres |
| radius | float | 2 | 0.1 to 20 |  | Radius of the swarm when there's no Emit From surface |
| lifetime | float | 7 | 1 to 30 |  | How long each firefly lives, in seconds, before it's reborn |
| Colour By | menu |  | Code, Speed, Age | Look | What colours the particles when no Look node is wired: the code's own colour, their speed or their age |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look | Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age) |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look | Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age) |
| Glow | toggle | True |  | Look | Draw additively, so overlapping particles add up to a glow. Off draws them solid |
| Point Size | float | 3 | 0.5 to 32 | Look | Size of each particle on screen, in pixels, when no Look node sets a size |
| Brightness | float | 1 | 0 to 8 | Look | Overall brightness of the live particles |
| Speed Range | float | 2 | 0.0001 to 50 | Look | The speed (metres per second) that counts as fully fast for Colour By: Speed |
| Substeps | int | 1 | 1 to 20 | Simulation | Simulation steps per frame. More is steadier for fast or stiff motion, and costs more |
| Pre-warm | float | 0 | 0 to 120 | Simulation | Seconds simulated before the first frame, so the particles open already in shape |
| Stagger | float | 1 | 0 to 1 | Simulation | How spread out the births are: 0 = all at once, 1 = evenly over their lifetime |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
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
```


### Spark Ball

*GPU Particles source*

Spark Ball: particles born in a ball, with a small random kick. A plain source for stages.

**Example chain:** Spark Ball → Gravity → Drag → Streak Look

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| Count | int | 100000 | 1 to 16777216 |  | How many particles to simulate. Drawing them live handles millions; To Geometry copies them into Blender, which costs more |
| Emit From | object |  |  |  | An object whose surface the particles are born on (optional). Without one, the code decides where they start |
| radius | float | 1 | 0.05 to 20 |  | Radius of the ball they're born in |
| kick | float | 0.3 | 0 to 5 |  | Random speed each one starts with |
| lifetime | float | 4 | 0.5 to 30 |  | How long each particle lives, in seconds, before it's reborn |
| Colour By | menu |  | Code, Speed, Age | Look | What colours the particles when no Look node is wired: the code's own colour, their speed or their age |
| Slow / Young | colour | (0.15, 0.3, 1, 1) |  | Look | Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age) |
| Fast / Old | colour | (1, 0.55, 0.2, 1) |  | Look | Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age) |
| Glow | toggle | True |  | Look | Draw additively, so overlapping particles add up to a glow. Off draws them solid |
| Point Size | float | 1.5 | 0.5 to 32 | Look | Size of each particle on screen, in pixels, when no Look node sets a size |
| Brightness | float | 0.5 | 0 to 8 | Look | Overall brightness of the live particles |
| Speed Range | float | 2 | 0.0001 to 50 | Look | The speed (metres per second) that counts as fully fast for Colour By: Speed |
| Substeps | int | 1 | 1 to 20 | Simulation | Simulation steps per frame. More is steadier for fast or stiff motion, and costs more |
| Pre-warm | float | 0 | 0 to 120 | Simulation | Seconds simulated before the first frame, so the particles open already in shape |
| Stagger | float | 1 | 0 to 1 | Simulation | How spread out the births are: 0 = all at once, 1 = evenly over their lifetime |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Spark Ball: particles born in a ball, with a small random kick. A plain source for stages.
// @in float radius 1.0 0.05 20.0  "Radius of the ball they're born in"
// @in float kick 0.3 0.0 5.0  "Random speed each one starts with"
// @in float lifetime 4.0 0.5 30.0  "How long each particle lives, in seconds, before it's reborn"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Galaxy, Flow, Attractor, Swirl, Firefly Swarm, Spark Ball, Fountain, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| Count | int | 100000 | 1 to 16777216 |  | How many particles to simulate. Drawing them live handles millions; To Geometry copies them into Blender, which costs more |
| Emit From | object |  |  |  | An object whose surface the particles are born on (optional). Without one, the code decides where they start |
| power | float | 4 | 0.5 to 10 |  | Launch speed upwards |
| gravity | float | 9.8 | 0 to 20 |  | Pull back down (9.8 = Earth) |
| Colour By | menu |  | Code, Speed, Age | Look | What colours the particles when no Look node is wired: the code's own colour, their speed or their age |
| Slow / Young | colour | (0.6, 0.85, 1, 1) |  | Look | Colour of slow particles (Colour By: Speed) or young ones (Colour By: Age) |
| Fast / Old | colour | (0.1, 0.3, 0.9, 1) |  | Look | Colour of fast particles (Colour By: Speed) or old ones (Colour By: Age) |
| Glow | toggle | False |  | Look | Draw additively, so overlapping particles add up to a glow. Off draws them solid |
| Point Size | float | 3 | 0.5 to 32 | Look | Size of each particle on screen, in pixels, when no Look node sets a size |
| Brightness | float | 1 | 0 to 8 | Look | Overall brightness of the live particles |
| Speed Range | float | 2 | 0.0001 to 50 | Look | The speed (metres per second) that counts as fully fast for Colour By: Speed |
| Substeps | int | 1 | 1 to 20 | Simulation | Simulation steps per frame. More is steadier for fast or stiff motion, and costs more |
| Pre-warm | float | 0 | 0 to 120 | Simulation | Seconds simulated before the first frame, so the particles open already in shape |
| Stagger | float | 1 | 0 to 1 | Simulation | How spread out the births are: 0 = all at once, 1 = evenly over their lifetime |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// A fountain: particles shoot up from the origin and fall back under gravity.
// @param power 4.0 0.5 10.0  "Launch speed upwards"
// @param gravity 9.8 0.0 20.0  "Pull back down (9.8 = Earth)"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| strength | float | 0.5 | 0 to 5 |  | How hard the drifting current pushes (0 = no drift) |
| scale | float | 1.2 | 0.05 to 10 |  | Size of the swirls: low = big lazy loops across the scene, high = small twitchy wiggles |
| calm | float | 0.92 | 0 to 0.999 |  | How smoothly they turn: higher = lazier, smoother paths; 0 = they snap to the current |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Wander: lazy curl-noise drifting, like insects on a summer evening.
// @in float strength 0.5 0.0 5.0  "How hard the drifting current pushes (0 = no drift)"
// @in float scale 1.2 0.05 10.0  "Size of the swirls: low = big lazy loops across the scene, high = small twitchy wiggles"
// @in float calm 0.92 0.0 0.999  "How smoothly they turn: higher = lazier, smoother paths; 0 = they snap to the current"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| climb | float | 0.12 | 0 to 3 |  | Upward speed they ease towards (metres per second) |
| ceiling | float | 2.5 | 0.1 to 50 |  | Height where the rise fades out, so they hover instead of flying away |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Rise: a gentle buoyancy; particles climb towards a speed and ease off near their ceiling.
// @in float climb 0.12 0.0 3.0  "Upward speed they ease towards (metres per second)"
// @in float ceiling 2.5 0.1 50.0  "Height where the rise fades out, so they hover instead of flying away"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| strength | float | 9.8 | 0 to 50 |  | Pull downwards, in metres per second squared (9.8 = Earth) |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Gravity: a constant pull (down by default).
// @in float strength 9.8 0.0 50.0  "Pull downwards, in metres per second squared (9.8 = Earth)"
void behave(inout Particle p, float dt) {
  p.velocity.z -= strength * dt;
}
```


### Vortex

*GPU Stage*

Vortex: swirl around the vertical axis through the node's centre.

**Example chain:** Spark Ball → **Vortex** → Drag → Streak Look

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| spin | float | 1.5 | -20 to 20 |  | How fast they circle the vertical axis (negative turns the other way) |
| pull | float | 0.3 | -10 to 10 |  | Draws them in towards the axis (negative pushes them out) |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Vortex: swirl around the vertical axis through the node's centre.
// @in float spin 1.5 -20.0 20.0  "How fast they circle the vertical axis (negative turns the other way)"
// @in float pull 0.3 -10.0 10.0  "Draws them in towards the axis (negative pushes them out)"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| amount | float | 1 | 0 to 20 |  | How quickly they slow down, like moving through air (low) or water (high) |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Drag: slows particles down, like moving through air or water.
// @in float amount 1.0 0.0 20.0  "How quickly they slow down, like moving through air (low) or water (high)"
void behave(inout Particle p, float dt) {
  p.velocity *= exp(-amount * dt);
}
```


### Blink

*GPU Stage*

Blink: each particle pulses on its own rhythm. Adds two per-particle values later nodes can use: brightness (0..1) and phase (0..1, fixed per particle).

**Example chain:** Firefly Swarm → Wander → **Blink** → Firefly Look (reads brightness and phase)

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| rate | float | 0.6 | 0.05 to 6 |  | Blinks per second |
| sharpness | float | 6 | 1 to 30 |  | How sudden each blink is: low = gentle pulses, high = quick flashes |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |
| brightness | float | Each particle's blink brightness right now (0 to 1), for later nodes |
| phase | float | Where each particle is in its own blink cycle (0 to 1), for later nodes |

**Code**

```glsl
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
```


### Push by Field

*GPU Stage*

Push by Field: a force from a function wired into 'field' (e.g. Wind Field).

**Example chain:** Wind Field.wind → field; Swirl → **Push by Field** → Glow Look

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| amount | float | 1 | 0 to 10 |  | How strongly the field pushes |
| field | function |  |  |  | The force to push with: wire in a function such as Wind Field's wind |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
// Push by Field: a force from a function wired into 'field' (e.g. Wind Field).
// @in func vec3 field(vec3 p)  "The force to push with: wire in a function such as Wind Field's wind"
// @in float amount 1.0 0.0 10.0  "How strongly the field pushes"
void behave(inout Particle p, float dt) {
  p.velocity += field(p.position) * amount * dt;
}
```


### Collide with Shape

*GPU Stage*

Collide with Shape: bounce off a surface wired into 'sdf' (a GPU Surface node's sdf output).

**Example chain:** GPU Surface.sdf → sdf; Fountain → **Collide with Shape** → Glow Look

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| bounce | float | 0.4 | 0 to 1 |  | How much speed is kept after a bounce (0 = stop dead, 1 = perfectly bouncy) |
| radius | float | 0.02 | 0 to 1 |  | Particle radius used for contact, in metres |
| sdf | function |  |  |  | The surface to bounce off: wire in a GPU Surface node's sdf output |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |
| sdf | function | The surface to bounce off: wire in a GPU Surface node's sdf output |

**Code**

```glsl
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
```


## Particle looks

### Glow Look

*GPU Stage*

Glow Look: soft glowing sprites that fade in and out over each particle's life.

**Example chain:** Swirl → Wander → **Glow Look**

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| tint | colour | (1, 0.6, 0.25, 1) |  |  | Colour of the glow |
| intensity | float | 2 | 0 to 20 |  | Brightness of the glow |
| size | float | 0.03 | 0.001 to 1 |  | Size of each sprite, in metres |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
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
```


### Firefly Look

*GPU Stage*

Firefly Look: a glowing abdomen with a soft halo, and two little wings that flap. Reads brightness and phase (from Blink, or their defaults without it).

**Example chain:** Firefly Swarm → Wander → Rise → Blink → **Firefly Look**

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| glow | colour | (1, 0.78, 0.22, 1) |  |  | Colour of the glowing abdomen |
| intensity | float | 3 | 0 to 40 |  | Brightness of the glow |
| size | float | 0.012 | 0.001 to 0.2 |  | Size of each firefly's body, in metres |
| flap | float | 18 | 0 to 60 |  | Wing beats per second |
| wing | float | 2.4 | 0.2 to 6 |  | Wing length compared to the body |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |
| brightness | float | Each firefly's glow right now (0 to 1), for later nodes |
| phase | float | Each firefly's wing-beat phase (0 to 1), for later nodes |

**Code**

```glsl
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
```


### Streak Look

*GPU Stage*

Streak Look: each particle drawn as a glowing streak along its motion (a trail). Wire it next to another look from the same stream to get heads and trails from the same particles.

**Example chain:** Blink → Firefly Look and Blink → **Streak Look** (heads and trails, one simulation)

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| tint | colour | (0.55, 0.75, 1, 1) |  |  | Colour of the streaks |
| intensity | float | 1.2 | 0 to 20 |  | Brightness of the streaks |
| size | float | 0.01 | 0.001 to 0.5 |  | Thickness of each streak, in metres |
| trail | float | 0.25 | 0 to 5 |  | Streak length, in seconds of motion |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |

**Code**

```glsl
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
```


### Material Look

*GPU Stage*

Material Look: particles in a Blender material's colours (its Principled BSDF: base colour, roughness, metallic, emission), lit by the scene when a Scene Lights node is wired into 'light'. Wire its 'material' output into a GPU Surface's Material input to shade a surface with it. Each particle is shaded as a small sphere facing the camera.

**Example chain:** Scene Lights.light → light; Swirl → **Material Look**; its material → a GPU Surface's Material

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| size | float | 0.03 | 0.001 to 1 |  | Size of each particle, in metres |
| brightness | float | 1 | 0 to 10 |  | Overall brightness |
| mat | material |  |  |  | The Blender material whose colours, roughness and metallic the particles take |
| light | function |  |  |  | How they're lit: wire in Scene Lights' light (unwired: a soft default light) |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |
| material | function | The function 'material': wire it into a function input of another node |

**Code**

```glsl
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
```


## Warps (particles and meshes)

### Bend

*GPU Stage*

Bend: bends, stretches and twists whatever comes in (particles or a mesh) along an axis, without changing it: the particles still move as before, they're just shown bent. axis: 0 = X, 1 = Y, 2 = Z (the length being bent). A flat galaxy curls when bent along X.

**Example chain:** Galaxy → **Bend** → (live); or a mesh → **Bend** → To Mesh

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| angle | float | 1.2 | -6.2832 to 6.2832 |  | How far to bend, in radians across the span (0 = straight) |
| span | float | 3 | 0.1 to 50 |  | Length over which the bend happens, in metres |
| stretch | float | 1 | 0.1 to 5 |  | Stretch along the axis (1 = unchanged) |
| twist | float | 0 | -12.566 to 12.566 |  | Twist around the axis across the span, in radians |
| axis | int | 2 | 0 to 2 |  | Axis to bend along: 0 = X, 1 = Y, 2 = Z |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |
| Mesh | geometry | The mesh after this node's code. Add To Geometry (To Mesh) to use it in regular nodes or renders |

**Code**

```glsl
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
```


### Taper

*GPU Stage*

Taper: squeezes or flares whatever comes in along Z.

**Example chain:** a mesh → **Taper** → To Mesh

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Particles | particles |  |  |  | The particle stream to work on: wire in the Particles output of the node before |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| amount | float | 0.5 | -2 to 2 |  | Squeeze (positive) or flare (negative) towards the top |
| span | float | 3 | 0.1 to 50 |  | Height over which the taper happens, in metres |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The particle stream after this node: wire it into the next stage, a Look, or To Geometry |
| Mesh | geometry | The mesh after this node's code. Add To Geometry (To Mesh) to use it in regular nodes or renders |

**Code**

```glsl
// Taper: squeezes or flares whatever comes in along Z.
// @in float amount 0.5 -2.0 2.0  "Squeeze (positive) or flare (negative) towards the top"
// @in float span 3.0 0.1 50.0  "Height over which the taper happens, in metres"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| amplitude | float | 0.05 | 0 to 1 |  | Height of the rings, in metres |
| wavelength | float | 0.4 | 0.02 to 5 |  | Distance between rings, in metres |
| speed | float | 1 | 0 to 10 |  | How fast the rings travel outwards |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Mesh | geometry | The mesh after this node's code. Add To Geometry (To Mesh) to use it in regular nodes or renders |

**Code**

```glsl
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
```


### Sway by Field

*GPU Stage*

Sway by Field: bends a mesh (grass, cloth, hair cards) with a force function wired into 'field' (e.g. Wind Field): the higher a vertex, the further it's pushed. Colour darkens where it bends most.

**Example chain:** Wind Field.wind → field; a grass mesh → **Sway by Field** → (live) or To Mesh

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| amount | float | 0.15 | 0 to 5 |  | How far it sways |
| height | float | 0.6 | 0.01 to 10 |  | Height at which the sway is full; below it the mesh bends less (roots stay put) |
| field | function |  |  |  | The force to sway with: wire in a function such as Wind Field's wind |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Mesh | geometry | The mesh after this node's code. Add To Geometry (To Mesh) to use it in regular nodes or renders |

**Code**

```glsl
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
```


## Functions

### Wind Field

*GPU Stage*

Wind Field: a gusty breeze as a function other nodes can call (wire 'wind' into Push by Field).

**Example chain:** **Wind Field**.wind → Push by Field.field and Sway by Field.field

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| strength | float | 0.4 | 0 to 10 |  | Average wind speed |
| gusts | float | 0.7 | 0 to 5 |  | How much the wind rises and falls over time |
| turbulence | float | 0.3 | 0 to 5 |  | Small-scale swirling on top of the breeze |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| wind | function | The function 'wind': wire it into a function input of another node |

**Code**

```glsl
// Wind Field: a gusty breeze as a function other nodes can call (wire 'wind' into Push by Field).
// @in float strength 0.4 0.0 10.0  "Average wind speed"
// @in float gusts 0.7 0.0 5.0  "How much the wind rises and falls over time"
// @in float turbulence 0.3 0.0 5.0  "Small-scale swirling on top of the breeze"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Wander, Rise, Gravity, Vortex, Drag, Blink, Push by Field, Collide with Shape, Glow Look, Firefly Look, Streak Look, Material Look, Bend, Taper, Ripple, Sway by Field, Wind Field, Scene Lights, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| intensity | float | 1 | 0 to 10 |  | Multiplies the brightness of the scene's lamps |
| world | float | 1 | 0 to 10 |  | Multiplies the world (sky) light |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| light | function | The scene's lighting as a function: wire it into a Look's or Surface's light input |

**Code**

```glsl
// Scene Lights: the scene's lamps (sun, point, spot, area) and world colour as a function
// other nodes call: wire 'light' into a lit look (Material Look) or a GPU Surface's Lights input.
// CodeNodes keeps the light data (hidden inputs) in step with the scene, up to 8 lamps.
// No shadows are cast between GPU nodes and Blender objects.
// @in float intensity 1.0 0.0 10.0  "Multiplies the brightness of the scene's lamps"
// @in float world 1.0 0.0 10.0  "Multiplies the world (sky) light"
// @out func light  "The scene's lighting as a function: wire it into a Look's or Surface's light input"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| towers | float | 6 | 3 to 12 |  | Number of towers on the ring wall |
| radius | float | 10 | 6 to 12.5 |  | Radius of the ring wall |
| wallHeight | float | 4.5 | 2 to 9 |  | Height of the wall |
| keepHeight | float | 14 | 6 to 26 |  | Height of the central keep |
| scale | float | 0.1 | 0.02 to 1 |  | Overall size (the castle is modelled in tens of metres) |
| Lights | function |  |  |  | Wire a Scene Lights node's light output here to light this surface with the scene's lamps and world |
| Material | function |  |  |  | Wire a Material Look node's material output here to use a Blender material's colours and roughness |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look | Base colour of the live surface (when no Material is wired) |
| Shadows | toggle | True |  | Look | Soft shadows the surface casts on itself in the live view |
| Ambient Occlusion | toggle | True |  | Look | Darkens creases and corners in the live view |
| Fog | float | 0 | 0 to 1 | Look | Distance haze in the live view (0 = none) |
| Sky | toggle | False |  | Look | Draws a sky behind the surface in the live view |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look | Resolution of the live view (1 = full). Lower is faster while you work |
| Bounds Min | vector | (-1.6, -1.6, -0.15) |  | Bounds | One corner of the box the surface lives in. To Geometry builds the mesh inside it |
| Bounds Max | vector | (1.6, 1.6, 2.35) |  | Bounds | The opposite corner of the box the surface lives in |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |
| sdf | function | The function 'sdf': wire it into a function input of another node |

**Code**

```glsl
// The Aerie citadel as a GPU surface: a ring wall with towers, a keep and roofs.
// Units: the citadel is about 25 across; `scale` is metres per unit.
// @param towers 6 3 12  "Number of towers on the ring wall"
// @param radius 10 6 12.5  "Radius of the ring wall"
// @param wallHeight 4.5 2 9  "Height of the wall"
// @param keepHeight 14 6 26  "Height of the central keep"
// @param scale 0.1 0.02 1.0  "Overall size (the castle is modelled in tens of metres)"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| radius | float | 1 | 0.3 to 3 |  | Radius of the planet, in metres |
| relief | float | 0.05 | 0 to 0.25 |  | Height of the mountains compared to the radius |
| sea | float | 0.5 | 0.3 to 0.7 |  | Sea level: higher floods more land |
| seed | float | 3 | 0 to 100 |  | Changes the continents |
| Lights | function |  |  |  | Wire a Scene Lights node's light output here to light this surface with the scene's lamps and world |
| Material | function |  |  |  | Wire a Material Look node's material output here to use a Blender material's colours and roughness |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look | Base colour of the live surface (when no Material is wired) |
| Shadows | toggle | True |  | Look | Soft shadows the surface casts on itself in the live view |
| Ambient Occlusion | toggle | True |  | Look | Darkens creases and corners in the live view |
| Fog | float | 0 | 0 to 1 | Look | Distance haze in the live view (0 = none) |
| Sky | toggle | False |  | Look | Draws a sky behind the surface in the live view |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look | Resolution of the live view (1 = full). Lower is faster while you work |
| Bounds Min | vector | (-1.45, -1.45, -1.45) |  | Bounds | One corner of the box the surface lives in. To Geometry builds the mesh inside it |
| Bounds Max | vector | (1.45, 1.45, 1.45) |  | Bounds | The opposite corner of the box the surface lives in |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |
| sdf | function | The function 'sdf': wire it into a function input of another node |

**Code**

```glsl
// A small planet, Tellus-style: continents from fractal noise, shallow seas, snow on the peaks
// and at the poles.
// @param radius 1.0 0.3 3.0  "Radius of the planet, in metres"
// @param relief 0.05 0.0 0.25  "Height of the mountains compared to the radius"
// @param sea 0.5 0.3 0.7  "Sea level: higher floods more land"
// @param seed 3.0 0.0 100.0  "Changes the continents"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| radius | float | 0.9 | 0.1 to 2 |  | Radius of the planet |
| ring | float | 1.55 | 0.5 to 3 |  | Radius of the ring |
| width | float | 0.35 | 0.02 to 1 |  | Width of the ring |
| tilt | float | 0.45 | 0 to 1.5 |  | Tilt of the ring, in radians |
| Lights | function |  |  |  | Wire a Scene Lights node's light output here to light this surface with the scene's lamps and world |
| Material | function |  |  |  | Wire a Material Look node's material output here to use a Blender material's colours and roughness |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look | Base colour of the live surface (when no Material is wired) |
| Shadows | toggle | True |  | Look | Soft shadows the surface casts on itself in the live view |
| Ambient Occlusion | toggle | True |  | Look | Darkens creases and corners in the live view |
| Fog | float | 0 | 0 to 1 | Look | Distance haze in the live view (0 = none) |
| Sky | toggle | False |  | Look | Draws a sky behind the surface in the live view |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look | Resolution of the live view (1 = full). Lower is faster while you work |
| Bounds Min | vector | (-2.2, -2.2, -1.2) |  | Bounds | One corner of the box the surface lives in. To Geometry builds the mesh inside it |
| Bounds Max | vector | (2.2, 2.2, 1.2) |  | Bounds | The opposite corner of the box the surface lives in |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |
| sdf | function | The function 'sdf': wire it into a function input of another node |

**Code**

```glsl
// A ringed planet: a banded sphere and a tilted ring, one surface.
// @param radius 0.9 0.1 2.0  "Radius of the planet"
// @param ring 1.55 0.5 3.0  "Radius of the ring"
// @param width 0.35 0.02 1.0  "Width of the ring"
// @param tilt 0.45 0.0 1.5  "Tilt of the ring, in radians"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| major | float | 0.8 | 0.2 to 2 |  | Radius from the centre to the middle of the tube |
| minor | float | 0.3 | 0.05 to 1 |  | Radius of the tube |
| Lights | function |  |  |  | Wire a Scene Lights node's light output here to light this surface with the scene's lamps and world |
| Material | function |  |  |  | Wire a Material Look node's material output here to use a Blender material's colours and roughness |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look | Base colour of the live surface (when no Material is wired) |
| Shadows | toggle | True |  | Look | Soft shadows the surface casts on itself in the live view |
| Ambient Occlusion | toggle | True |  | Look | Darkens creases and corners in the live view |
| Fog | float | 0 | 0 to 1 | Look | Distance haze in the live view (0 = none) |
| Sky | toggle | False |  | Look | Draws a sky behind the surface in the live view |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look | Resolution of the live view (1 = full). Lower is faster while you work |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds | One corner of the box the surface lives in. To Geometry builds the mesh inside it |
| Bounds Max | vector | (2, 2, 2) |  | Bounds | The opposite corner of the box the surface lives in |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |
| sdf | function | The function 'sdf': wire it into a function input of another node |

**Code**

```glsl
// A donut. sdf(p) is the distance to the surface: negative inside, positive outside (metres).
// Each @param line below becomes an input on the node.
// @param major 0.8 0.2 2.0  "Radius from the centre to the middle of the tube"
// @param minor 0.3 0.05 1.0  "Radius of the tube"
float sdf(vec3 p) {
  return sdTorus(p, major, minor);
}
```


### Rounded Box

*GPU Surface (SDF)*

A box with rounded edges.

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| size | float | 0.8 | 0.1 to 2 |  | Half the box's width |
| roundness | float | 0.15 | 0 to 0.5 |  | Radius of the rounded edges |
| Lights | function |  |  |  | Wire a Scene Lights node's light output here to light this surface with the scene's lamps and world |
| Material | function |  |  |  | Wire a Material Look node's material output here to use a Blender material's colours and roughness |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look | Base colour of the live surface (when no Material is wired) |
| Shadows | toggle | True |  | Look | Soft shadows the surface casts on itself in the live view |
| Ambient Occlusion | toggle | True |  | Look | Darkens creases and corners in the live view |
| Fog | float | 0 | 0 to 1 | Look | Distance haze in the live view (0 = none) |
| Sky | toggle | False |  | Look | Draws a sky behind the surface in the live view |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look | Resolution of the live view (1 = full). Lower is faster while you work |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds | One corner of the box the surface lives in. To Geometry builds the mesh inside it |
| Bounds Max | vector | (2, 2, 2) |  | Bounds | The opposite corner of the box the surface lives in |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |
| sdf | function | The function 'sdf': wire it into a function input of another node |

**Code**

```glsl
// A box with rounded edges.
// @param size 0.8 0.1 2.0  "Half the box's width"
// @param roundness 0.15 0.0 0.5  "Radius of the rounded edges"
float sdf(vec3 p) {
  return sdRoundBox(p, vec3(size), min(roundness, size));
}
```


### Gyroid Ball

*GPU Surface (SDF)*

A sphere carved into a gyroid lattice.

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| radius | float | 1 | 0.2 to 2 |  | Radius of the ball |
| cells | float | 6 | 1 to 16 |  | Number of lattice cells across it |
| thickness | float | 0.05 | 0.01 to 0.3 |  | Thickness of the lattice walls |
| Lights | function |  |  |  | Wire a Scene Lights node's light output here to light this surface with the scene's lamps and world |
| Material | function |  |  |  | Wire a Material Look node's material output here to use a Blender material's colours and roughness |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look | Base colour of the live surface (when no Material is wired) |
| Shadows | toggle | True |  | Look | Soft shadows the surface casts on itself in the live view |
| Ambient Occlusion | toggle | True |  | Look | Darkens creases and corners in the live view |
| Fog | float | 0 | 0 to 1 | Look | Distance haze in the live view (0 = none) |
| Sky | toggle | False |  | Look | Draws a sky behind the surface in the live view |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look | Resolution of the live view (1 = full). Lower is faster while you work |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds | One corner of the box the surface lives in. To Geometry builds the mesh inside it |
| Bounds Max | vector | (2, 2, 2) |  | Bounds | The opposite corner of the box the surface lives in |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |
| sdf | function | The function 'sdf': wire it into a function input of another node |

**Code**

```glsl
// A sphere carved into a gyroid lattice.
// @param radius 1.0 0.2 2.0  "Radius of the ball"
// @param cells 6.0 1.0 16.0  "Number of lattice cells across it"
// @param thickness 0.05 0.01 0.3  "Thickness of the lattice walls"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Castle, Planet, Saturn, Donut, Rounded Box, Gyroid Ball, Blob, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| blend | float | 0.35 | 0 to 1 |  | How smoothly the spheres melt into each other |
| wobble | float | 0.08 | 0 to 0.3 |  | How much the blob wobbles over time |
| Lights | function |  |  |  | Wire a Scene Lights node's light output here to light this surface with the scene's lamps and world |
| Material | function |  |  |  | Wire a Material Look node's material output here to use a Blender material's colours and roughness |
| Colour | colour | (0.72, 0.66, 0.58, 1) |  | Look | Base colour of the live surface (when no Material is wired) |
| Shadows | toggle | True |  | Look | Soft shadows the surface casts on itself in the live view |
| Ambient Occlusion | toggle | True |  | Look | Darkens creases and corners in the live view |
| Fog | float | 0 | 0 to 1 | Look | Distance haze in the live view (0 = none) |
| Sky | toggle | False |  | Look | Draws a sky behind the surface in the live view |
| Live Resolution | float | 0.6 | 0.15 to 1 | Look | Resolution of the live view (1 = full). Lower is faster while you work |
| Bounds Min | vector | (-2, -2, -2) |  | Bounds | One corner of the box the surface lives in. To Geometry builds the mesh inside it |
| Bounds Max | vector | (2, 2, 2) |  | Bounds | The opposite corner of the box the surface lives in |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |
| sdf | function | The function 'sdf': wire it into a function input of another node |

**Code**

```glsl
// Code -> Mesh starter: five spheres melting together into a moving blob. Replace it with your own.
// Define sdf(p): the distance to the surface, negative inside, positive outside, in Blender units.
// Helpers: sdSphere sdBox sdRoundBox sdTorus sdCapsule sdCylinder smin smax rotateX/Y/Z noise3 fbm3
// Time: uTime (seconds), uFrame. Sliders: declare them like the lines below.
// @param blend 0.35 0.0 1.0  "How smoothly the spheres melt into each other"
// @param wobble 0.08 0.0 0.3  "How much the blob wobbles over time"

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| amplitude | float | 0.08 | 0 to 1 |  | Height of the waves, in metres |
| frequency | float | 6 | 0.5 to 30 |  | Number of waves across the mesh |
| speed | float | 2 | 0 to 10 |  | How fast the waves travel |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |

**Code**

```glsl
// Wave: ripples travel across the incoming mesh along its normals; colour follows the height.
// @param amplitude 0.08 0.0 1.0  "Height of the waves, in metres"
// @param frequency 6.0 0.5 30.0  "Number of waves across the mesh"
// @param speed 2.0 0.0 10.0  "How fast the waves travel"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| strength | float | 0.15 | 0 to 1 |  | How far the noise pushes vertices out, in metres |
| scale | float | 3 | 0.2 to 20 |  | Size of the noise: higher = finer detail |
| seed | float | 0 | 0 to 100 |  | Changes the noise pattern |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |

**Code**

```glsl
// Noise Displace: fractal noise pushes every vertex out along its normal, like weathered rock.
// @param strength 0.15 0.0 1.0  "How far the noise pushes vertices out, in metres"
// @param scale 3.0 0.2 20.0  "Size of the noise: higher = finer detail"
// @param seed 0.0 0.0 100.0  "Changes the noise pattern"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| height | float | 1.1 | 0 to 4 |  | Height of the plateau, in metres |
| radius | float | 2.4 | 0.5 to 6 |  | Radius of the plateau top, in metres |
| rough | float | 0.22 | 0 to 1 |  | How rough and rocky the sides are |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |

**Code**

```glsl
// Mesa: a rocky plateau rising out of a flat grid: soft-edged, roughened by noise, coloured by height,
// with a flat summit (put something on it).
// @param height 1.1 0.0 4.0  "Height of the plateau, in metres"
// @param radius 2.4 0.5 6.0  "Radius of the plateau top, in metres"
// @param rough 0.22 0.0 1.0  "How rough and rocky the sides are"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Mesh | geometry |  |  |  | The mesh to work on, from anything in Geometry Nodes. This node's code runs on every vertex |
| Template | menu |  | Wave, Noise Displace, Mesa, Twist, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| turns | float | 0.5 | -4 to 4 |  | Full turns from bottom to top (negative twists the other way) |
| height | float | 2 | 0.1 to 20 |  | Height over which the twist happens, in metres |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |

**Code**

```glsl
// Twist: turn the mesh around its Z axis, more the higher it goes.
// @param turns 0.5 -4.0 4.0  "Full turns from bottom to top (negative twists the other way)"
// @param height 2.0 0.1 20.0  "Height over which the twist happens, in metres"
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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Desk Lamp, Vase, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| height | float | 0.34 | 0.1 to 0.8 |  |  |
| shade_r | float | 0.14 | 0.03 to 0.4 |  |  |
| stem_r | float | 0.01 | 0.003 to 0.05 |  |  |
| base_r | float | 0.095 | 0.03 to 0.3 |  |  |
| Smooth | toggle | True |  | Look | Smooth shading on the built model |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| ✎ Edit Code | toggle | False |  |  | Opens this node's code in a pop-up Text Editor, then switches itself back off like a button. Double-click the node or press Ctrl+E for the same |
| Template | menu |  | Desk Lamp, Vase, Custom |  | Load one of the ready-made codes for this kind of node. If you had edited the code, your version is kept in a text named '… (before …)' |
| height | float | 0.4 | 0.1 to 1 |  |  |
| belly | float | 0.12 | 0.03 to 0.4 |  |  |
| neck | float | 0.05 | 0.02 to 0.2 |  |  |
| Smooth | toggle | True |  | Look | Smooth shading on the built model |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | Stays empty: GPU nodes draw live in the viewport. Add To Geometry after this node to get real geometry |

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

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| Particles | particles |  |  |  | The particle chain to make real: the particles become points with all their attributes |
| When | menu |  | Automatic, Every Frame, When Changed, Only for Render |  | When to rebuild the real geometry: Automatic (every frame for particles and animated code, otherwise when something changes), Every Frame, When Changed, or Only for Render |
| Max Points | int | 0 | 0 to 16777216 |  | At most this many particles become points (0 = all of them) |
| Keep Velocity | toggle | True |  |  | Write each point's velocity and speed (for motion blur, or colouring by speed) |
| Keep Age | toggle | True |  |  | Write each point's age and life |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | The real geometry: use it like the output of any Geometry Nodes node |

### To Geometry, after a surface (To Mesh)

*Flow node*

CodeNodes: turns the GPU node (or chain) before it into real geometry: points with every per-particle attribute, or a mesh. Later nodes and renders can use it, like after Realize Instances

**Example chain:** any GPU node or chain → **To Geometry** → Instance on Points / Set Material / ...

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| Geometry | geometry |  |  |  | The GPU node or chain to make real: particles become points, surfaces and mesh code become a mesh |
| When | menu |  | Automatic, Every Frame, When Changed, Only for Render |  | When to rebuild the real geometry: Automatic (every frame for particles and animated code, otherwise when something changes), Every Frame, When Changed, or Only for Render |
| Resolution | int | 128 | 8 to 512 |  | Detail of the mesh built from a surface: cells along each side of its bounds (higher is finer and slower) |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Geometry | geometry | The real geometry: use it like the output of any Geometry Nodes node |

### Join Particles

*Flow node*

CodeNodes: merges two particle streams. Everything wired after it applies to both, and a To Geometry after it outputs both

**Example chain:** Swirl and Fountain → **Join Particles** → Glow Look → To Points

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| Particles A | particles |  |  |  | The first particle stream |
| Particles B | particles |  |  |  | The second particle stream |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | Both streams together: everything wired after this applies to both |

### GPU Cache

*Flow node*

CodeNodes: bakes the GPU simulation of the particles passing through it and plays it back. Mode: Live or Cached. Switch on ⟳ Bake Now to bake Start–End

**Example chain:** Firefly Swarm → Wander → Rise → **GPU Cache** → Firefly Look

**Inputs**

| Socket | Type | Default | Range | Panel | What it does |
|---|---|---|---|---|---|
| Particles | particles |  |  |  | The particle chain to record |
| Mode | menu |  | Live, Cached |  | Live runs the simulation as you work. Cached plays back the recorded frames (instant scrubbing; renders read them too) |
| Start | int | 1 | -100000 to 100000 |  | First frame to record |
| End | int | 120 | -100000 to 100000 |  | Last frame to record |
| ⟳ Bake Now | toggle | False |  |  | Switch on to record Start–End now (it switches itself back off) |
| ✕ Clear | toggle | False |  |  | Switch on to delete the recording (it switches itself back off) |

**Outputs**

| Socket | Type | What it gives |
|---|---|---|
| Particles | particles | The same particles, live or played back from the recording |
