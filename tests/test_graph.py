"""Graph compiler: pure Python, no Blender.

    python tests/test_graph.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# import the two pure modules without running the add-on's register code
import importlib.util  # noqa: E402

pkg = importlib.util.module_from_spec(importlib.util.spec_from_loader("codenodes", loader=None, is_package=True))
pkg.__path__ = [os.path.join(ROOT, "codenodes")]
sys.modules["codenodes"] = pkg
from codenodes import graph, sdf_code  # noqa: E402

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


BALL = "// @param r 1.0 0.1 3.0\nfloat bump(vec3 p) { return 0.0; }\nfloat sdf(vec3 p) { return length(p) - r + bump(p); }"
BOX = "// @param r 0.5\nfloat bump(vec3 p) { return 0.1; }\nfloat sdf(vec3 p) { return sdBox(p, vec3(r)) + bump(p); }"


def main():
    nodes = {
        "Ball": {"kind": "code", "code": BALL, "values": {"r": 0.8}},
        "Box": {"kind": "code", "code": BOX},
        "Blend": {"kind": "smooth_union", "inputs": {"A": "Ball", "B": "Box"}, "values": {"K": 0.3}},
        "Move": {"kind": "transform", "inputs": {"SDF": "Blend"},
                 "values": {"location": (1, 2, 3), "rotation": (0, 0, 1.57), "scale": 2.0}},
    }
    prog = graph.compile_graph(nodes, "Move")
    src = prog.source
    params = sdf_code.parse_params(src)
    names = [p.name for p in params]
    check("float sdf(vec3 p)" in src, "the program defines sdf()")
    check(len([n for n in names if n.endswith("_r")]) == 2, "both nodes' 'r' params exist, separately named")
    check(src.count("float bump(") == 0 and src.count("_bump(vec3 p)") == 2, "same-named helpers in two nodes don't collide")
    ball_r = next(n for n in names if "Ball" in n and n.endswith("_r"))
    check(prog.values[ball_r] == 0.8, "a node's slider value is carried through")
    box_r = next(n for n in names if "Box" in n and n.endswith("_r"))
    check(prog.values[box_r] == 0.5, "an untouched slider uses its default")
    check(any(n.endswith("_K") for n in names) and any(n.endswith("_loc_x") for n in names), "op and transform knobs become params")
    check(len(names) == len(set(names)) <= sdf_code.MAX_PARAMS, "param names are unique")
    sdf_code.check_source(src)
    check(True, "the program passes the sdf source checks")

    # line mapping back to nodes
    lines = src.splitlines()
    ball_line = next(i for i, l in enumerate(lines, 1) if "length(p) -" in l)
    check(prog.locate(ball_line) == ("Ball", 3), f"a line maps back to its node and local line ({prog.locate(ball_line)})")

    # renaming never touches swizzles, and locals keep their names
    swz = "// @param x 0.5\nconst float y = 0.2;\nfloat sdf(vec3 p) {\n  float z = p.x + p.y * y;\n  return length(p.xz) - x - z;\n}"
    s = graph.compile_graph({"S": {"kind": "code", "code": swz}}, "S").source
    check("p.x + p.y" in s and "p.xz" in s and "float z = " in s and "_y = 0.2" in s,
          "swizzles and locals survive renaming; top-level constants are prefixed")

    # the same node used twice is emitted once
    nodes2 = {"Ball": {"kind": "code", "code": BALL},
              "U": {"kind": "union", "inputs": {"A": "Ball", "B": "Ball"}}}
    check(graph.compile_graph(nodes2, "U").source.count("_sdf(vec3 p) { return length") == 1, "a node shared by two inputs is emitted once")

    # errors
    loop = {"A": {"kind": "union", "inputs": {"A": "B"}}, "B": {"kind": "union", "inputs": {"A": "A"}}}
    check(raises(lambda: graph.compile_graph(loop, "A"), "loops back"), "a cycle is reported, not followed forever")
    check(raises(lambda: graph.compile_graph({"X": {"kind": "code", "code": "float f(vec3 p){return 1.0;}"}}, "X"),
                 "node 'X'"), "a bad code node is named in the error")
    check(raises(lambda: graph.compile_graph({}, "Out"), "nothing is connected"), "an empty graph is explained")
    check(raises(lambda: graph.compile_graph({"U": {"kind": "union", "inputs": {"A": "Gone"}}}, "U"), "missing node"),
          "a dangling link is reported")
    half = graph.compile_graph({"U": {"kind": "union", "inputs": {"A": None, "B": None}}}, "U")
    check("1e10" in half.source, "unconnected inputs count as empty space")

    print(f"\nAll {_checks} checks passed.")


if __name__ == "__main__":
    main()
