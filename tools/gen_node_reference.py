"""Write docs/NODE_REFERENCE.md: how every code node is set up.

The first part (the declaration syntax) is written by hand below. The per-node sections are
generated: every shipped template is built as a real node group in (background) Blender and its
sockets are read back from the group's interface, so the reference can't drift from the code.

    blender -b --factory-startup --python tools/gen_node_reference.py            # write the file
    blender -b --factory-startup --python tools/gen_node_reference.py -- --check # compare only

tests/test_node_reference.py runs the --check and fails if a template's sockets and the doc disagree.
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DOC = os.path.join(ROOT, "docs", "NODE_REFERENCE.md")

INTRO = r"""# CodeNodes node reference

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
| `// @in func vec3 wind(vec3 p) use: wind(p) * 0.4` | the same, with a **use line** (see below) | `wind(p)` gives the use line's value |
| `// @in func vec3 bodyPos(int i, float t)` | any signature: ints, several arguments, or none (`int bodyCount()`) | `bodyPos(i, uSceneTime)` |
| `// @in material mat` | a Material socket | `mat_base` (vec3), `mat_roughness`, `mat_metallic`, `mat_emit` (vec3, colour × strength), `mat_alpha` |
| `// @in hidden lightX 0.0` | none: a value the add-on fills in itself (e.g. Scene Lights' light data) | `lightX` |

A function input with nothing wired returns zero (or the value after `=`), so a node always compiles.

**Use lines: one shared function, used differently by each node.** Every function input shows a text
input right under it, `wind · use`. It holds one expression saying how *this* node applies what's
wired in: `wind(p) * 0.4` for a gentle drift, `vec3(wind(p).xy * height, 0)` for grass that only sways
sideways, `wind(p) + vec3(0, 0, rise)` for smoke. The node's code keeps calling `wind(p)` and gets the
use line's value. The expression can use the function's arguments, the node's own inputs and the
helpers. Typing on the node writes it into the code as `use: …` at the end of the declaration line,
and editing the code updates the node. The plain call (`wind(p)`) is the default and costs nothing. The
shared function's header says how many nodes use it (`Wind Field · used by 3`), and wiring in a
function whose signature doesn't match the input shows a ⚠ on the node that takes it.

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
written as an attribute). Everywhere: `uTime` (seconds) and `uFrame`, and `uSceneTime`: the
timeline's time in seconds, which (unlike `uTime`) stays on the timeline's frame while the viewport
previews a paused scene, so use it for anything that must line up with real geometry (Orbits' planets
and the asteroids that orbit them). In looks, `cnCamera` (the camera in the object's space). Helpers: `rand1`, `rand3`, `randBall`, `randSphere`, `gnoise`,
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
per chain). Names starting with `cn` + capital letter, `gl_`, `uTime`, `uFrame` and `uSceneTime`, GLSL words and
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

"""

EXAMPLES = {
    "Galaxy": "Galaxy → Bend → (live), or Galaxy → To Points → Instance on Points",
    "Flow": "Flow → Glow Look → (live)",
    "Attractor": "Attractor → (live)",
    "Swirl": "Swirl → Wander → Glow Look → (live)",
    "Firefly Swarm": "Firefly Swarm (Emit From a ground mesh) → Wander → Rise → Blink → Firefly Look",
    "Spark Ball": "Spark Ball → Gravity → Drag → Streak Look",
    "Fountain": "Fountain → Collide with Shape (sdf from a GPU Surface) → Glow Look",
    "Wander": "Firefly Swarm → **Wander** → Rise → Firefly Look",
    "Rise": "Firefly Swarm → Wander → **Rise** → Firefly Look",
    "Gravity": "Spark Ball → **Gravity** → Collide with Shape → Glow Look",
    "Vortex": "Spark Ball → **Vortex** → Drag → Streak Look",
    "Drag": "Spark Ball → Gravity → **Drag** → Glow Look",
    "Blink": "Firefly Swarm → Wander → **Blink** → Firefly Look (reads brightness and phase)",
    "Push by Field": "Wind Field.wind → field; Swirl → **Push by Field** → Glow Look",
    "Collide with Shape": "GPU Surface.sdf → sdf; Fountain → **Collide with Shape** → Glow Look",
    "Glow Look": "Swirl → Wander → **Glow Look**",
    "Firefly Look": "Firefly Swarm → Wander → Rise → Blink → **Firefly Look**",
    "Streak Look": "Blink → Firefly Look and Blink → **Streak Look** (heads and trails, one simulation)",
    "Material Look": "Scene Lights.light → light; Swirl → **Material Look**; its material → a GPU Surface's Material",
    "Bend": "Galaxy → **Bend** → (live); or a mesh → **Bend** → To Mesh",
    "Taper": "a mesh → **Taper** → To Mesh",
    "Ripple": "GPU Mesh → **Ripple** → To Mesh",
    "Sway by Field": "Wind Field.wind → field; a grass mesh → **Sway by Field** → (live) or To Mesh",
    "Wind Field": "**Wind Field**.wind → Push by Field.field and Sway by Field.field",
    "Scene Lights": "**Scene Lights**.light → Material Look.light and a GPU Surface's Lights",
}

SECTION_ORDER = [
    ("Particle sources", 'PARTICLES', None),
    ("Particle stages", 'STAGE', "Particle Stages"),
    ("Particle looks", 'STAGE', "Particle Looks"),
    ("Warps (particles and meshes)", 'STAGE', "Warps (particles and meshes)"),
    ("Mesh stages", 'STAGE', "Mesh Stages"),
    ("Functions", 'STAGE', "Functions"),
    ("Lighting", 'STAGE', "Lighting"),
    ("GPU Surfaces", 'MESH', None),
    ("GPU Mesh", 'DEFORM', None),
    ("Code Shapes", 'SHAPE', None),
]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (tuple, list)):
        return "(" + ", ".join(_fmt(x) for x in v) + ")"
    return str(v)


TYPE_NAMES = {"NodeSocketFloat": "float", "NodeSocketInt": "int", "NodeSocketBool": "toggle",
              "NodeSocketColor": "colour", "NodeSocketMenu": "menu", "NodeSocketGeometry": "geometry",
              "NodeSocketBundle": "particles", "NodeSocketClosure": "function", "NodeSocketString": "text",
              "NodeSocketObject": "object", "NodeSocketMaterial": "material", "NodeSocketVector": "vector"}


def sockets(group):
    """([(panel, name, type, default, range)], [(name, type)]) read from a node group's interface."""
    ins, outs = [], []
    for item in group.interface.items_tree:
        if item.item_type != 'SOCKET':
            continue
        t = TYPE_NAMES.get(item.socket_type, item.socket_type)
        if item.in_out == 'INPUT':
            default = getattr(item, "default_value", None)
            try:
                if hasattr(default, "__len__") and not isinstance(default, str):
                    default = tuple(round(float(x), 4) for x in default)
                elif isinstance(default, float):
                    default = round(default, 4)
            except TypeError:
                default = None
            rng = ""
            if hasattr(item, "min_value") and item.socket_type in ("NodeSocketFloat", "NodeSocketInt"):
                lo, hi = item.min_value, item.max_value
                if abs(lo) < 1e8 and abs(hi) < 1e8:
                    rng = f"{_fmt(round(lo, 4))} to {_fmt(round(hi, 4))}"
            panel = item.parent.name if item.parent is not None and item.parent.name else ""
            if item.socket_type in ("NodeSocketGeometry", "NodeSocketBundle", "NodeSocketClosure",
                                    "NodeSocketMaterial", "NodeSocketObject", "NodeSocketMenu"):
                default = default if item.socket_type == "NodeSocketMenu" else None
            ins.append((panel, item.name, t, default, rng, _tip(item)))
        else:
            outs.append((item.name, t, _tip(item)))
    return ins, outs


def _tip(item):
    return (getattr(item, "description", "") or "").replace("|", "/").replace(chr(10), " ")


def _menu_items(group, name):
    ms = group.nodes.get(f"Menu · {name}")
    return [i.name for i in ms.enum_items] if ms is not None else []


def describe(code):
    lines = []
    for line in code.splitlines():
        t = line.strip()
        if not t.startswith("//"):
            break
        t = t[2:].strip()
        if t.startswith("@"):
            continue
        lines.append(t)
    return " ".join(lines)


def node_section(title, kind_label, code, group, example):
    ins, outs = sockets(group)
    out = [f"### {title}", "", f"*{kind_label}*", ""]
    d = describe(code) if code else (group.description or "")
    if d:
        out += [d, ""]
    if example:
        out += [f"**Example chain:** {example}", ""]
    out += ["**Inputs**", "", "| Socket | Type | Default | Range | Panel | What it does |",
            "|---|---|---|---|---|---|"]
    for panel, name, t, default, rng, tip in ins:
        if t == "menu":
            items = _menu_items(group, name)
            rng = ", ".join(items)
        out.append(f"| {name} | {t} | {_fmt(default)} | {rng} | {panel} | {tip} |")
    out += ["", "**Outputs**", ""]
    if outs:
        out += ["| Socket | Type | What it gives |", "|---|---|---|"] + [f"| {n} | {t} | {tip} |" for n, t, tip in outs]
    else:
        out.append("(none)")
    out.append("")
    if code:
        lang = "glsl" if kind_label != "Code Shape" else "text"
        out += ["**Code**", "", f"```{lang}", code.rstrip("\n"), "```", ""]
    return out


def build():
    """The whole document as a string (builds throwaway node groups, then removes them)."""
    import codenodes
    from codenodes import gn_link, gpu_cache, stage_templates
    made = []
    parts = [INTRO]
    try:
        for title, kind, section in SECTION_ORDER:
            if section is not None:
                keys = next(k for t, _i, k in stage_templates.SECTIONS if t == section)
            else:
                keys = list(gn_link.TEMPLATES.get(kind, {}).keys())
            if not keys:
                continue
            parts.append(f"## {title}\n")
            for key in keys:
                group, err = gn_link.create(kind, key)
                made.append(group)
                obj = gn_link.source_of(group)
                code = obj.codenodes.text.as_string() if obj is not None and obj.codenodes.text else ""
                label = {"PARTICLES": "GPU Particles source", "STAGE": "GPU Stage", "MESH": "GPU Surface (SDF)",
                         "DEFORM": "GPU Mesh", "SHAPE": "Code Shape"}[kind]
                parts += node_section(key, label, code, group, EXAMPLES.get(key))
                parts.append("")
        parts.append("## Flow nodes\n")
        tg = gn_link.build_make_real()
        made.append(tg)
        for kind, what in (('PARTICLES', "after particles (To Points)"), ('MESH', "after a surface (To Mesh)")):
            gn_link.sync_real_interface(tg, kind, None)
            parts += node_section(f"To Geometry, {what}", "Flow node", "", tg,
                                  "any GPU node or chain → **To Geometry** → Instance on Points / Set Material / ...")
        join = gn_link.build_join()
        made.append(join)
        parts += node_section("Join Particles", "Flow node", "", join,
                              "Swirl and Fountain → **Join Particles** → Glow Look → To Points")
        cache = gpu_cache.build_group()
        made.append(cache)
        parts += node_section("GPU Cache", "Flow node", "", cache,
                              "Firefly Swarm → Wander → Rise → **GPU Cache** → Firefly Look")
    finally:
        for g in made:
            obj = gn_link.source_of(g) if gn_link.is_code_group(g) else None
            try:
                bpy.data.node_groups.remove(g)
            except (ReferenceError, RuntimeError):
                pass
            if obj is not None:
                txt = obj.codenodes.text
                bpy.data.objects.remove(obj)
                if txt is not None and txt.users == 0:
                    bpy.data.texts.remove(txt)
    return "\n".join(parts).rstrip("\n") + "\n"


def sections(text):
    """{heading: body} for every '### ' section (to report which nodes disagree)."""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        if line.startswith("### "):
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = line[4:], []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def main():
    import codenodes
    codenodes.register()
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    text = build()
    if "--check" in args:
        try:
            with open(DOC, encoding="utf-8") as fh:
                have = fh.read()
        except OSError:
            have = ""
        if have == text:
            print("NODE_REFERENCE OK: the reference matches every node", flush=True)
        else:
            a, b = sections(have), sections(text)
            bad = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            print("NODE_REFERENCE MISMATCH: " + (", ".join(bad) if bad else "the introduction"), flush=True)
    else:
        with open(DOC, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {DOC} ({len(text.splitlines())} lines)", flush=True)


if __name__ == "__main__":
    main()
