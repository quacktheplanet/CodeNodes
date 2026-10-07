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
    check("#define n1_wind_in f0_Wind_Field_wind" in src, "the wired function comes in under its raw name")
    check("vec3 n1_wind(vec3 p, float t) { return n1_wind_in(p, t) * n1_height * 0.5; }" in src,
          "the use line becomes a wrapper: the node's sliders prefixed, the arguments kept")
    check(src.index("vec3 n1_wind(vec3 p") < src.index("void n1_behave"),
          "the wrapper comes before the code that calls it")
    check("n1_wind(p.position, uSceneTime)" in src, "the node's code still calls wind(...)")
    plain = chain.compose_particles(head, [chain.Unit("Plain", PLAIN, funcs={"wind": (wind, "wind")})])
    check("#define n1_wind f0_Wind_Field_wind" in plain.source and "n1_wind_in" not in plain.source,
          "without a use line it's the plain alias, as before (no cost)")
    lone = chain.compose_particles(head, [chain.Unit("Drift", DRIFT)])
    check("vec3 n1_wind_in(vec3 p, float t) { return vec3(0.0); }" in lone.source
          and "return n1_wind_in(p, t) * n1_height * 0.5;" in lone.source,
          "unwired, the use line wraps the stand-in")
    # the same wind, used two ways by two nodes in one chain
    a = chain.Unit("Gentle", DRIFT.replace("* height * 0.5", "* 0.1"), funcs={"wind": (wind, "wind")})
    b = chain.Unit("Lift", DRIFT.replace("wind(p, t) * height * 0.5", "wind(p, t) + vec3(0.0, 0.0, height)"),
                   funcs={"wind": (wind, "wind")})
    two = chain.compose_particles(head, [a, b])
    check(two.source.count("vec3 wind(vec3 p, float t) {") == 0
          and two.source.count("f0_Wind_Field_wind(vec3 p, float t)") == 1,
          "one shared Wind Field is included once")
    check("return n1_wind_in(p, t) * 0.1;" in two.source
          and "return n2_wind_in(p, t) + vec3(0.0, 0.0, n2_height);" in two.source,
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
    check("return n0_wind_in(p) * n0_v_scale;" in mesh.source, "use lines work in mesh chains too")
    check(any("takes vec3 wind(vec3 p)" in w for w in mesh.warnings),
          "and a provider with more arguments than the input is flagged")
    print(f"\nAll {_checks} checks passed.")


main()
