"""The JavaScript shape language builds exactly what the Python one does.

    python tests/test_shape_js.py          (needs numpy and node; no Blender)

Every command, at the defaults and at other slider values: same vertices (to float32
precision), same faces in the same order. And a mistake is reported on the same line.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.modules.setdefault("bpy", None)          # the shapes package never needs it
from codenodes.shapes import language  # noqa: E402

SOURCES = {
    "lamp (the template)": (language.TEMPLATE, [{}, {"height": 0.6, "shade_r": 0.25}, {"base_r": 0.2}]),
    "bottle, touching the axis": ("""
param h 0.3 0.1 1
param neck 0.02 0.01 0.05
part body
  profile
    move 0, 0
    line 0.05, 0
    arc 0.06, 0.01 radius 0.012
    curve x = 0.06 - (0.06 - neck) * smoothstep(0.5, 0.9, t)  y = 0.01 + (h - 0.01) * t  steps 30
    line 0, h
  revolve segments 40
""", [{}, {"h": 0.8, "neck": 0.045}]),
    "shell and part turn": ("""
param open 270 30 359
part cup
  profile
    move 0.02, 0
    curve x = 0.02 + 0.03 * t ** 0.5  y = 0.06 * t  steps 16
  shell 0.003
  revolve segments 36 degrees open
""", [{}, {"open": 90}]),
    "extrude with taper, on x": ("""
param w 1 0.2 3
part plate
  profile
    move 0, 0
    line w, 0
    line w, 0.5
    arc 0, 0.5 radius w * 0.6
    close
  extrude 0.2 steps 3 taper 0.8 axis x
""", [{}, {"w": 2.2}]),
    "sweep along a helix, twisted": ("""
param turns 4 1 10
part spring
  profile
    move 0.004, 0
    arc -0.004, 0 radius 0.004 steps 8
    arc 0.004, 0 radius 0.004 steps 8
    close
  path
    helix radius 0.03 pitch 0.012 turns turns steps 120
  sweep twist 90 scale 0.5
""", [{}, {"turns": 7}]),
    "sweep along a maths path": ("""
part handle
  profile
    move -0.01, -0.01
    line 0.01, -0.01
    line 0.01, 0.01
    line -0.01, 0.01
    close
  path
    move 0, 0, 0
    curve x = 0.1 * sin(t * pi)  y = 0  z = 0.2 * t  steps 24
    line 0, 0, 0.3 steps 4
  sweep
""", [{}]),
    "loft, closed profiles": ("""
param top 0.5 0.1 2
part vase
  profile
    move 1, 0
    line 0, 1
    line -1, 0
    line 0, -1
    close
  profile
    move top, top
    line -top, top
    line -top, -top
    line top, -top
    close
  loft steps 4 axis y
""", [{}, {"top": 1.5}]),
    "arrays, turns and a mirror": ("""
param n 6 1 12
part spokes
  profile
    move 0, 0
    line 0.5, 0
    line 0.5, 0.05
    line 0, 0.05
    close
  extrude 0.02
  translate 0.1, 0, 0
  array n around z
part row subtract
  profile
    move 0, 0
    line 0.1, 0
    line 0.1, 0.1
    close
  revolve segments 12
  rotate 30, 0, 45
  scale -1, 1, 1
  array 3 x 0.3
""", [{}, {"n": 11}]),
    "named components (rotate x, translate z, scale z)": ("""
param size 1 0.5 3
part rod
  profile
    move 0, 0
    line 0.1, 0
    line 0.1, size
    line 0, size
    close
  revolve segments 16
  rotate x 90
  translate z 1
part cap
  profile
    move 0, 0
    line 0.2, 0
    line 0.2, 0.1
    line 0, 0.1
    close
  revolve segments 12
  scale z 2
  translate y 1 z 2
  array 3 x 0.5
part knob
  profile
    move 0, 0
    line 0.05, 0
    line 0.05, 0.05
    line 0, 0.05
    close
  revolve segments 8
  scale 2
""", [{}, {"size": 2}]),
    "ring on a closed helix, and a twisted ring": ("""
param r 0.5 0.2 1
part ring
  profile
    move -0.05, -0.05
    line 0.05, -0.05
    line 0.05, 0.05
    line -0.05, 0.05
    close
  path
    helix radius r pitch 0 turns 1
  sweep
part band
  profile
    move -0.02, -0.08
    line 0.02, -0.08
    line 0.02, 0.08
    line -0.02, 0.08
    close
  path
    helix radius r * 1.5 pitch 0 turns 1 steps 48
  sweep twist 360
""", [{}, {"r": 0.8}]),
    "if-else and ranges": ("""
param bumps 3 1 8
part wavy
  profile
    move 0, 0
    curve x = 0.5 + (0.1 if t > 0.5 else 0.05) * sin(t * tau * bumps)  y = t  steps 64
    line 0, 1
  revolve segments 24 axis y cap no
""", [{}, {"bumps": 7}]),
}

ERRORS = {
    "unknown name": "part a\n  profile\n    move 0, 0\n    line wide, 1\n  revolve",
    "bad command": "part a\n  wobble 3",
    "radius too small": "part a\n  profile\n    move 0, 0\n    arc 1, 0 radius 0.1\n  revolve",
    "no solid": "part a\n  profile\n    move 0, 0\n    line 1, 1",
}

_checks = 0
_failed = False


def check(cond, msg):
    global _checks, _failed
    _checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        _failed = True


NODE_SCRIPT = r"""
const fs = require("fs");
const CodeShapes = require(process.argv[2]);
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
for (const [key, c] of Object.entries(cases)) {
  try {
    const t0 = process.hrtime.bigint();
    const s = CodeShapes.parse(c.source).build(c.values);
    out[key] = { verts: Array.from(s.verts), polys: s.polygons(), ms: Number(process.hrtime.bigint() - t0) / 1e6 };
  } catch (e) { out[key] = { error: String(e.message), kind: e.constructor.name }; }
}
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def main():
    node = shutil.which("node")
    check(node is not None, f"node is available ({node})")
    if node is None:
        print("\nFAIL")
        return
    cases, expected = {}, {}
    for name, (source, values_list) in SOURCES.items():
        shape = language.parse(source)
        for values in values_list:
            key = f"{name} {json.dumps(values)}"
            solid = shape.build(values)
            cases[key] = {"source": source, "values": values}
            expected[key] = solid
    for name, source in ERRORS.items():
        cases[f"error: {name}"] = {"source": source, "values": {}}
        try:
            language.parse(source).build()
            expected[f"error: {name}"] = "no error"
        except language.ShapeError as exc:
            expected[f"error: {name}"] = str(exc)

    work = tempfile.mkdtemp(prefix="codenodes_shapejs_")
    try:
        script = os.path.join(work, "run.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(NODE_SCRIPT)
        with open(os.path.join(work, "cases.json"), "w", encoding="utf-8") as fh:
            json.dump(cases, fh)
        subprocess.run([node, script, os.path.join(ROOT, "codenodes", "shapes", "shapes.js"),
                        os.path.join(work, "cases.json"), os.path.join(work, "out.json")], check=True)
        with open(os.path.join(work, "out.json"), encoding="utf-8") as fh:
            got = json.load(fh)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for key, want in expected.items():
        mine = got[key]
        if isinstance(want, str):
            line = want.split(":")[0]
            check("error" in mine and mine["error"].split(":")[0] == line and mine["kind"] == "ShapeError",
                  f"{key}: reported on the same line ({line!r}; js said {mine.get('error', 'nothing')[:60]!r})")
            continue
        if "error" in mine:
            check(False, f"{key}: js failed: {mine['error']}")
            continue
        verts = np.asarray(mine["verts"], np.float64).reshape(-1, 3)
        py_polys = want.polygons()
        same_faces = [tuple(p) for p in mine["polys"]] == py_polys
        scale = max(float(np.abs(want.verts).max()), 1e-6)
        err = float(np.abs(verts - want.verts.astype(np.float64)).max()) if len(verts) == len(want.verts) else float("inf")
        check(len(verts) == len(want.verts) and same_faces and err <= 2e-6 * scale,
              f"{key}: {len(verts)} verts, {len(py_polys)} faces, same order; largest difference "
              f"{err:.1e} ({mine['ms']:.1f} ms in js)")
    print(("\nFAIL" if _failed else f"\nALL {_checks} CHECKS PASSED"), flush=True)


main()
