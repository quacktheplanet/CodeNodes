"""Maths into models: the expression evaluator, the geometry kernel, the language.

    python tests/test_shapes.py          (no Blender needed)
"""
import importlib.util
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = importlib.util.module_from_spec(importlib.util.spec_from_loader("codenodes", loader=None, is_package=True))
pkg.__path__ = [os.path.join(ROOT, "codenodes")]
sys.modules["codenodes"] = pkg
from codenodes.shapes import expr, language, solids  # noqa: E402

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
    except Exception as exc:
        return text.lower() in str(exc).lower()
    return False


def edge_counts(solid):
    """How many faces use each edge. Every edge of a closed solid should be used twice."""
    counts = {}
    for face in solid.polygons():
        for i in range(len(face)):
            a, b = face[i], face[(i + 1) % len(face)]
            counts[(min(a, b), max(a, b))] = counts.get((min(a, b), max(a, b)), 0) + 1
    return counts


def edges_of(solid):
    return set(edge_counts(solid))


def test_expressions():
    check(expr.number("2 + 3 * 4") == 14, "arithmetic")
    check(abs(expr.number("sin(pi / 2)") - 1.0) < 1e-12, "functions and constants")
    check(abs(expr.number("h * 0.5", {"h": 3.0}) - 1.5) < 1e-12, "names from scope")
    check(expr.number("max(2, 7)") == 7 and expr.number("clamp(5, 0, 1)") == 1, "helpers")
    check(expr.number("3 if 1 < 2 else 9") == 3, "a conditional")
    values = expr.evaluate("t * 2", {"t": np.linspace(0, 1, 5)})
    check(np.allclose(values, [0, 0.5, 1, 1.5, 2]), "a whole array at once")

    # the safety that lets an assistant send these
    for bad, why in [("__import__('os')", "unknown function"),
                     ("(1).__class__", "isn't allowed"),
                     ("open('x')", "unknown function"),
                     ("[1, 2][0]", "isn't allowed"),
                     ("lambda: 1", "isn't allowed"),
                     ("[x for x in (1,)]", "isn't allowed"),
                     ("f'{1}'", "isn't allowed"),
                     ("os.system('x')", "only plain function calls"),
                     ("nope + 1", "unknown name")]:
        check(raises(lambda b=bad: expr.number(b), why), f"{bad!r} is refused ({why})")
    check(raises(lambda: expr.number("1 +"), "not a valid expression"), "a syntax error is explained")
    check(raises(lambda: expr.number("1/0"), "check the maths"), "a non-finite result is caught")
    check("radius" in expr.names_used("radius * 2 + h"), "names_used finds the variables")


def test_profiles_and_solids():
    p = solids.Profile().move(0, 0).line(1, 0).line(1, 1).close()
    pts, sharp = p.finish()
    check(len(pts) == 3 and sharp.all(), f"a closed triangle has three sharp points ({len(pts)})")
    check(abs(solids.Profile().move(0, 0).line(3, 4).length() - 5.0) < 1e-9, "profile length")

    # a revolved straight line is a cylinder: check the radius and that it is watertight
    tube = solids.revolve(solids.Profile().move(1, 0).line(1, 2), segments=32)
    r = np.linalg.norm(tube.verts[:, :2], axis=1)
    check(abs(r.mean() - 1.0) < 1e-6 and r.std() < 1e-6, f"revolve makes an exact radius ({r.mean():.6f})")
    check(tube.verts[:, 2].min() == 0 and abs(tube.verts[:, 2].max() - 2) < 1e-6, "and the right height")
    check(len(tube.faces) == 32 * 1, f"one quad per segment per span ({len(tube.faces)})")
    check(tube.uvs is not None and tube.uvs.shape == (len(tube.faces), 4, 2), "with UVs per corner")
    check(float(tube.uvs[..., 0].max()) > 0.9, "u goes around the sweep")

    # a cone: radius should fall linearly with height
    cone = solids.revolve(solids.Profile().move(1, 0).line(0, 2), segments=24)
    top = cone.verts[np.isclose(cone.verts[:, 2], 2)]
    check(len(top) and np.allclose(np.linalg.norm(top[:, :2], axis=1), 0, atol=1e-6),
          "a cone closes to a point")

    # corners survive as sharp edges, smooth curves do not
    box = solids.revolve(solids.Profile().move(0, 0).line(1, 0).line(1, 1).line(0, 1), segments=16)
    check(len(box.sharp) > 0, f"corners are marked sharp ({len(box.sharp)} edges)")
    curved = solids.Profile()
    curved.curve("cos(t * pi / 2)", "sin(t * pi / 2)", steps=16)
    round_solid = solids.revolve(curved, segments=16)
    check(len(round_solid.sharp) == 2 * 16,
          f"along a smooth curve only the two open ends are sharp ({len(round_solid.sharp)} of "
          f"{len(edges_of(round_solid))} edges)")

    # extrude
    square = solids.Profile().move(-1, -1).line(1, -1).line(1, 1).line(-1, 1).close()
    block = solids.extrude(square, depth=3)
    check(abs(block.verts[:, 2].max() - 3) < 1e-6, "extrude reaches its depth")
    counts = edge_counts(block)
    check(all(c == 2 for c in counts.values()),
          f"a capped extrusion is watertight ({sum(1 for c in counts.values() if c != 2)} bad edges)")
    check(len(block.tris) == 8 and all(len(set(t)) == 3 for t in block.tris),
          f"the caps are real triangles, not quads with a repeated corner ({len(block.tris)})")
    check(all(len(set(f)) == 4 for f in block.faces), "and the sides are real quads")

    tapered = solids.extrude(square, depth=1, taper=0.0)
    check(np.allclose(tapered.verts[np.isclose(tapered.verts[:, 2], 1)][:, :2], 0, atol=1e-6),
          "taper 0 comes to a point")

    # transform and array
    moved = solids.transform(tube, move=(5, 0, 0))
    check(abs(moved.verts[:, 0].mean() - 5) < 1e-5, "transform moves")
    spun = solids.transform(solids.extrude(square, 1), rotate=(0, 0, 90))
    check(abs(spun.verts[:, 0].min() + 1) < 1e-5, "transform rotates")
    row = solids.array(tube, 4, move=(3, 0, 0))
    check(len(row.verts) == 4 * len(tube.verts) and abs(row.verts[:, 0].max() - 10) < 1e-5,
          "a linear array repeats along a direction")
    ring = solids.array(solids.transform(tube, move=(4, 0, 0)), 6, around="z")
    check(len(ring.faces) == 6 * len(tube.faces), "a radial array goes around")

    # limits
    dense = solids.Profile()
    dense.curve("1 + 0.1 * t", "t", steps=2000)
    check(raises(lambda: solids.revolve(dense, segments=2000), "faces"),
          "a profile and sweep that would make millions of faces is refused")
    check(raises(lambda: solids.revolve(solids.Profile().move(-1, 0).line(1, 0)), "negative x"),
          "a revolve profile crossing the axis is refused")
    check(raises(lambda: solids.Profile().line(1, 1), "move"), "a profile must start with move")
    check(raises(lambda: solids.Profile().move(0, 0).arc(0, 0, 1), "somewhere else"),
          "an arc to the same point is refused")
    check(raises(lambda: solids.Profile().move(0, 0).arc(10, 0, 0.1), "too small"),
          "an impossible arc radius is explained")


def test_language():
    positional, named = language.split_args("0.1, 0.02 radius 0.014 steps 8", language.KEYWORDS["arc"])
    check(positional == ["0.1", "0.02"] and named == {"radius": "0.014", "steps": "8"},
          f"arguments split ({positional}, {named})")
    positional, named = language.split_args("x = r * (1 - t)  y = h * t  steps 24",
                                            language.KEYWORDS["curve"])
    check(named["x"] == "r * (1 - t)" and named["y"] == "h * t" and named["steps"] == "24",
          f"expressions keep their spaces ({named})")
    positional, _ = language.split_args("shade_r * 0.45, height", language.KEYWORDS["move"])
    check(positional == ["shade_r * 0.45", "height"], f"maths is not mistaken for keywords ({positional})")
    _, named = language.split_args("6 around z", language.KEYWORDS["array"])
    check(named == {"around": "z"}, f"a one-word keyword value stops there ({named})")

    shape = language.parse(language.TEMPLATE)
    check([p[0] for p in shape.params] == ["height", "shade_r", "stem_r", "base_r"],
          "the lamp's sliders are read")
    check([n for n, _ in shape.parts] == ["shade", "stem", "base"], "and its three parts")
    solid = shape.build()
    check(len(solid.faces) > 1000, f"the lamp builds ({len(solid.faces):,} faces)")
    check(np.isfinite(solid.verts).all(), "with finite vertices")
    height = solid.verts[:, 2].max()
    check(abs(height - 0.34) < 1e-4, f"it is as tall as the parameter says ({height:.4f} m)")

    taller = shape.build({"height": 0.6})
    check(abs(taller.verts[:, 2].max() - 0.6) < 1e-4, "changing a slider changes the model")
    check(len(taller.faces) == len(solid.faces), "and does not change the topology")

    wall = language.parse("""
param length 4.0
param thickness 0.2
param height 2.5
profile
  move 0, 0
  line length, 0
  line length, thickness
  line 0, thickness
  close
extrude height
""").build()
    size = wall.verts.max(axis=0) - wall.verts.min(axis=0)
    check(np.allclose(size, [4.0, 0.2, 2.5], atol=1e-6), f"a wall comes out the right size ({np.round(size, 3)})")

    # errors name the line
    for source, wanted in [
        ("profile\n  move 0, 0\n  line 1, 0\nrevolv segments 8", "line 4"),
        ("param x 1\nparam x 2\nprofile\n move 0,0\n line 1,0\nrevolve", "line 2"),
        ("profile\n  move 0, 0\n  line nope, 0\nrevolve", "line 3"),
        ("profile\n  move 0, 0\n  arc 1, 1\nrevolve", "line 3"),
        ("revolve segments 8", "needs a profile"),
        ("profile\n  move 0, 0\n  line 1, 0", "never becomes a solid"),
        ("line 1, 0", "inside a `profile`"),
    ]:
        check(raises(lambda s=source: language.parse(s).build(), wanted),
              f"{wanted!r} is reported for a bad description")

    # maths really is maths
    star = language.parse("""
param points 5
param r 1.0
profile
  curve x = r * (0.6 + 0.4 * cos(t * tau * points))  y = t * 2   steps 120
revolve segments 8
""").build()
    check(len(star.faces) > 100, "a profile driven by a wave builds")
    radius = np.linalg.norm(star.verts[:, :2], axis=1)
    check(radius.max() > radius.min() * 1.4, "and the wave really is in the geometry")


def main():
    test_expressions()
    test_profiles_and_solids()
    test_language()
    print(f"\nAll {_checks} checks passed.")


if __name__ == "__main__":
    main()
