"""Declarations and chains: pure Python, no Blender.

    python tests/test_decl.py

Covers use lines: `// @in func vec3 wind(vec3 p) use: wind(p) * 0.4`, how each node that shares one
function decides what it means to that node.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import importlib.util  # noqa: E402

pkg = importlib.util.module_from_spec(importlib.util.spec_from_loader("codenodes", loader=None, is_package=True))
pkg.__path__ = [os.path.join(ROOT, "codenodes")]
sys.modules["codenodes"] = pkg
from codenodes import chain, decl, sdf_code  # noqa: E402

_checks = 0


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        raise SystemExit(1)
    print(f"  ok: {msg}")


def raises(fn, text):
    try:
        fn()
    except sdf_code.SdfCodeError as exc:
        return text in str(exc)
    return False


WIND = """// Wind
// @in float strength 1.4 0 4
// @out func wind
vec3 wind(vec3 p, float t) { return vec3(strength * sin(p.y + t), 0.0, 0.0); }
"""

SWARM = """// @param speed 1.0
void spawn(inout Particle p) { p.position = randBall(p.seed); }
"""

DRIFT = """// Drift
// @in float height 2.0 0 10  "How tall"
// @in func vec3 wind(vec3 p, float t) use: wind(p, t) * height * 0.5  "The wind"
void behave(inout Particle p, float dt) { p.velocity += wind(p.position, uSceneTime) * dt; }
"""

PLAIN = """// @in func vec3 wind(vec3 p, float t)
void behave(inout Particle p, float dt) { p.velocity += wind(p.position, uSceneTime) * dt; }
"""


def main():
    # ---- parsing -------------------------------------------------------------------------------
    d = decl.parse(DRIFT)
    f = d.func_ins[0]
    check(f.use == "wind(p, t) * height * 0.5" and f.wraps(), "a use line is read from the declaration")
    check(d.descriptions.get("wind") == "The wind", "the tooltip after a use line still reads")
    check(f.arg_names() == ["p", "t"] and f.plain_use() == "wind(p, t)", "its arguments and plain call")
    p = decl.parse(PLAIN).func_ins[0]
    check(p.use is None and not p.wraps() and p.use_line() == "wind(p, t)",
          "without a use line the node uses the function as it is")
    same = decl.parse(PLAIN.replace("float t)", "float t) use: wind( p,t )")).func_ins[0]
    check(not same.wraps(), "a use line that only calls the function changes nothing")
    dflt = decl.parse("// @in func float sdf(vec3 p) = 1e9 use: sdf(p) - 0.1\n").func_ins[0]
    check(dflt.default == "1e9" and dflt.use == "sdf(p) - 0.1", "a default and a use line together")
    check(raises(lambda: decl.parse("// @in func float f(vec3 p) use: f(p)) + 1\n"), "brackets"),
          "a use line with unmatched brackets is refused, naming the problem")
    check(raises(lambda: decl.parse("// @in func float f(vec3 p) use: f(p); discard\n"), "one expression"),
          "a use line is one expression")

    # ---- writing it back -----------------------------------------------------------------------
    new = decl.set_use(PLAIN, "wind", "wind(p, t) * 0.4")
    check("// @in func vec3 wind(vec3 p, float t) use: wind(p, t) * 0.4" in new
          and decl.parse(new).func_ins[0].use == "wind(p, t) * 0.4", "set_use writes the use line into the code")
    back = decl.set_use(new, "wind", "wind(p, t)")
    check(back == PLAIN, "setting it back to the plain call gives the original code")
    kept = decl.set_use(DRIFT, "wind", "-wind(p, t)")
    check('use: -wind(p, t)  "The wind"' in kept and decl.parse(kept).descriptions["wind"] == "The wind",
          "rewriting keeps the tooltip")
    check(decl.set_use(dflt_src := "// @in func float sdf(vec3 p) = 1e9\n", "sdf", "sdf(p) * 2.0")
          == "// @in func float sdf(vec3 p) = 1e9 use: sdf(p) * 2.0\n", "and keeps the unwired default")
    check(raises(lambda: decl.set_use(PLAIN, "gust", "gust(p)"), "no function input called 'gust'"),
          "set_use on a missing input says so")
    check(raises(lambda: decl.set_use(PLAIN, "wind", "wind(p, t"), "brackets"),
          "set_use refuses a broken expression and leaves the code alone")
    del dflt_src

    # ---- compiling -----------------------------------------------------------------------------
    wind = chain.Unit("Wind Field", WIND)
    head = chain.Unit("Swarm", SWARM)
    drift = chain.Unit("Drift", DRIFT, funcs={"wind": (wind, "wind")})
    comp = chain.compose_particles(head, [drift])
    src = comp.source
    check("#define n1_wired_wind f0_Wind_Field_wind" in src, "the wired function comes in under its raw name")
    check("vec3 n1_wind(vec3 p, float t) { return n1_wired_wind(p, t) * n1_height * 0.5; }" in src,
          "the use line becomes a wrapper: the node's sliders prefixed, the arguments kept")
    check(src.index("vec3 n1_wind(vec3 p") < src.index("void n1_behave"),
          "the wrapper comes before the code that calls it")
    check("n1_wind(p.position, uSceneTime)" in src, "the node's code still calls wind(...)")
    plain = chain.compose_particles(head, [chain.Unit("Plain", PLAIN, funcs={"wind": (wind, "wind")})])
    check("#define n1_wind f0_Wind_Field_wind" in plain.source and "n1_wired_wind" not in plain.source,
          "without a use line it's the plain alias, as before (no cost)")
    lone = chain.compose_particles(head, [chain.Unit("Drift", DRIFT)])
    check("vec3 n1_wired_wind(vec3 p, float t) { return vec3(0.0); }" in lone.source
          and "return n1_wired_wind(p, t) * n1_height * 0.5;" in lone.source,
          "unwired, the use line wraps the stand-in")
    # the same wind, used two ways by two nodes in one chain
    a = chain.Unit("Gentle", DRIFT.replace("* height * 0.5", "* 0.1"), funcs={"wind": (wind, "wind")})
    b = chain.Unit("Lift", DRIFT.replace("wind(p, t) * height * 0.5", "wind(p, t) + vec3(0.0, 0.0, height)"),
                   funcs={"wind": (wind, "wind")})
    two = chain.compose_particles(head, [a, b])
    check(two.source.count("vec3 wind(vec3 p, float t) {") == 0
          and two.source.count("f0_Wind_Field_wind(vec3 p, float t)") == 1,
          "one shared Wind Field is included once")
    check("return n1_wired_wind(p, t) * 0.1;" in two.source
          and "return n2_wired_wind(p, t) + vec3(0.0, 0.0, n2_height);" in two.source,
          "and each node wraps it its own way")
    # errors in a use line point at the declaration line in the node's own code
    line = next(i for i, l in enumerate(src.splitlines(), 1) if l.startswith("vec3 n1_wind(vec3 p"))
    msg = chain.translate_errors(f"line {line}: 'height' : undeclared identifier", src)
    check(msg.startswith("node 'Drift', line 3:") and "use: wind(p, t)" in msg,
          f"a compile error in a use line names the node and its declaration line ({msg.splitlines()[0]})")
    # a provider whose signature doesn't match: a warning, not a silent mismatch
    orbit = chain.Unit("Orbits", "// @out func pos\nvec3 pos(int i, float t) { return vec3(0.0); }\n")
    odd = chain.compose_particles(head, [chain.Unit("Drift", DRIFT, funcs={"wind": (orbit, "pos")})])
    check(any("takes vec3 wind(vec3 p, float t)" in w and "gives vec3 pos(int i, float t)" in w
              for w in odd.warnings), f"a signature mismatch is reported ({odd.warnings})")
    mesh = chain.compose_mesh([chain.Unit("Sway", "// @in func vec3 wind(vec3 p) use: wind(p) * v_scale\n"
                                          "// @in float v_scale 1.0\n"
                                          "void deform(inout Vertex v) { v.position += wind(v.position); }\n",
                                          funcs={"wind": (wind, "wind")})])
    check("return n0_wired_wind(p) * n0_v_scale;" in mesh.source, "use lines work in mesh chains too")
    check(any("takes vec3 wind(vec3 p)" in w for w in mesh.warnings),
          "and a provider with more arguments than the input is flagged")
    lists()
    print(f"\nAll {_checks} checks passed.")


BODIES = """// Bodies: the planets
// @list
name    mass  radius  color:3
Rocky   1.0   0.6     0.55 0.45 0.35
Ocean   2.4   0.9     0.10 0.30 0.70
Ice     0.7   0.5     0.90 0.95 1.00
"""

ORBITS = """// @in list Body bodies  "The planets"
// @in float speed 1.0
struct Body { float mass; float radius; vec3 color; };
vec3 tint(int i) { Body b = bodies(i); return b.color * b.mass; }
void behave(inout Particle p, float dt) {
  for (int i = 0; i < bodies_count(); i++) p.velocity += tint(i) * dt * speed;
}
"""


def lists():
    # ---- a List node's table -------------------------------------------------------------------
    d = decl.parse(BODIES)
    t = d.table
    check(t is not None and len(t) == 3 and t.label_name == "name" and t.labels == ["Rocky", "Ocean", "Ice"],
          "a List node's table: three rows, the name column kept as labels")
    check([(c.name, c.size) for c in t.columns] == [("mass", 1), ("radius", 1), ("color", 3)]
          and t.column("color").values[1] == (0.1, 0.3, 0.7), "number columns, color:3 taking three numbers")
    check(decl.strip(BODIES).strip() == "" and not d.roles, "a List has no GPU code of its own")
    commas = decl.parse("// @list\nx, y\n1, 2\n3, 4\n").table
    check(commas.column("y").values == [2.0, 4.0], "cells can be separated by commas too")
    check(raises(lambda: decl.parse("// @list\na b\n1 2 3\n"), "3 cells, but the columns need 2"),
          "a row with the wrong number of cells names its line")
    check(raises(lambda: decl.parse("// @list\na b:3\n1 2 x 4\n"), "isn't a number"),
          "a word in a number column is refused")
    again = decl.parse(decl.set_table(BODIES, t)).table
    check(again.labels == t.labels and all(a.values == b.values for a, b in zip(again.columns, t.columns)),
          "writing a table back and reading it again gives the same data")
    check(decl.set_table(BODIES, t).startswith("// Bodies: the planets\n// @list\nname"),
          "and keeps the comment above it")

    # ---- list inputs ---------------------------------------------------------------------------
    o = decl.parse(ORBITS)
    check(o.lists[0].name == "bodies" and o.lists[0].type == "Body" and o.lists[0].is_record()
          and o.descriptions["bodies"] == "The planets", "a list input of records, with its tooltip")
    check(decl.struct_fields(ORBITS, "Body") == [("float", "mass"), ("float", "radius"), ("vec3", "color")],
          "the record's fields come from the struct in the code")
    check(raises(lambda: decl.parse("// @in list Planet ps\n"), "isn't a list type"),
          "a list of an undefined struct is refused")

    head = chain.Unit("Swarm", SWARM)
    bodies = chain.Unit("Bodies", BODIES)
    orb = chain.Unit("Orbits", ORBITS, funcs={"bodies": (bodies, "List")})
    src = chain.compose_particles(head, [orb]).source
    check("struct n1_Body { float mass; float radius; vec3 color; };" in src
          and src.index("struct n1_Body") < src.index("n1_bodies(int i)"),
          "the struct gets the node's prefix and comes before the list that returns it")
    check("int n1_bodies_count() { return 3; }" in src, "the list's length is a constant")
    check("const vec3 n1_bodies_2[3] = vec3[3](vec3(0.55, 0.45, 0.35), vec3(0.1, 0.3, 0.7), vec3(0.9, 0.95, 1.0));"
          in src, "each column is a constant array")
    check("n1_Body n1_bodies(int i) { int k = clamp(i, 0, 2); return n1_Body(n1_bodies_0[k], n1_bodies_1[k], "
          "n1_bodies_2[k]); }" in src, "bodies(i) builds the record from the columns")
    check("Body b = bodies(i)" not in src and "n1_Body b = n1_bodies(i);" in src
          and "i < n1_bodies_count()" in src, "the code's own names follow the prefix")
    lone = chain.compose_particles(head, [chain.Unit("Orbits", ORBITS)]).source
    check("int n1_bodies_count() { return 0; }" in lone
          and "n1_Body n1_bodies(int i) { return n1_Body(0.0, 0.0, vec3(0.0)); }" in lone,
          "unwired, a list is empty (and still compiles)")
    one = "// @in list vec3 cols\nvoid behave(inout Particle p, float dt) { p.velocity += cols(0); }\n"
    col = chain.compose_particles(head, [chain.Unit("C", one, funcs={"cols": (bodies, "color")})]).source
    check("vec3 n1_cols(int i) { int k = clamp(i, 0, 2); return n1_cols_0[k]; }" in col,
          "a single-type list wired from one column")
    check(raises(lambda: chain.compose_particles(head, [chain.Unit("C", one, funcs={"cols": (bodies, "List")})]),
                 "wire one of 'Bodies''s columns (mass, radius, color)"),
          "a single-type list wired to a whole table asks for a column")
    check(raises(lambda: chain.compose_particles(head, [chain.Unit("C", one, funcs={"cols": (bodies, "mass")})]),
                 "has 1 number per row, but cols is a vec3"), "a column of the wrong size is refused")
    ints = chain.compose_particles(head, [chain.Unit(
        "I", "// @in list int ns\nvoid behave(inout Particle p, float dt) { p.velocity.x += float(ns(1)); }\n",
        funcs={"ns": (chain.Unit("N", "// @list\nn\n3\n4.4\n"), "n")})]).source
    check("const int n1_ns_0[2] = int[2](3, 4);" in ints, "int lists round to whole numbers")
    short = BODIES.replace("color:3", "tint:3")
    warn = chain.compose_particles(head, [chain.Unit("Orbits", ORBITS, funcs={
        "bodies": (chain.Unit("Bodies", short), "List")})])
    check(any("no column 'color'" in w for w in warn.warnings) and "vec3(0.0))" in warn.source,
          "a record field with no column is zero, with a warning")


main()
