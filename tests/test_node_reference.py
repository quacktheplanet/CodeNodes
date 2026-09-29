"""docs/NODE_REFERENCE.md must describe every node exactly as the add-on builds it: each shipped
template is built as a real node group and its sockets compared with the document. Background mode:

    blender -b --factory-startup --python tests/test_node_reference.py

Fails (naming the nodes) when a template's sockets, defaults, ranges or code and the document disagree.
Regenerate with:  blender -b --factory-startup --python tools/gen_node_reference.py
"""
import importlib.util
import os
import sys

import bpy  # noqa: F401  (run inside Blender)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location("gen_node_reference",
                                              os.path.join(ROOT, "tools", "gen_node_reference.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

import codenodes  # noqa: E402

checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        print(f"FAIL: {msg}", flush=True)
        sys.exit(1)
    print(f"  ok: {msg}", flush=True)


codenodes.register()
from codenodes import gn_link, stage_templates  # noqa: E402

text = gen.build()
with open(gen.DOC, encoding="utf-8") as fh:
    have = fh.read()
want, got = gen.sections(text), gen.sections(have)
missing = sorted(set(want) - set(got))
check(not missing, f"every node has a section (missing: {missing})")
bad = sorted(k for k in want if got.get(k) != want[k])
check(not bad, f"every section matches the node the add-on builds (differ: {bad}); "
               f"regenerate with tools/gen_node_reference.py")
names = set(want)
for kind in ('PARTICLES', 'MESH', 'DEFORM', 'SHAPE'):
    for key in gn_link.TEMPLATES.get(kind, {}):
        check(key in names, f"{kind.lower()} template '{key}' is documented")
for _title, _icon, keys in stage_templates.SECTIONS:
    for key in keys:
        check(key in names, f"stage '{key}' is documented")
for flow in ("Join Particles", "GPU Cache"):
    check(flow in names, f"{flow} is documented")
check(have == text, "the whole document is up to date")
print(f"ALL {checks} CHECKS PASSED", flush=True)
