"""CodeNodes on the web: a particle chain packaged to run live in a browser (WebGL2).

    bundle = particle_bundle(head)           # a dict, JSON-ready: the chain's GLSL, slider values, look
    export_page(head, "out/index.html")      # a page that runs it (the runtime, bundle and a canvas)

The chain's own code runs unchanged in the browser: the same composed source (spawn, born, behave,
look, warp, functions, lists, use lines) and the same helpers, with the sliders in the same parameter
buffer. Only the stepper differs (webgl_runtime.js). WebBlend's CodeNodes target calls particle_bundle
and ships webgl_runtime.js with the page.

Particles only for now: GPU Surfaces (raymarched) and GPU Mesh chains aren't packaged yet.
"""

from __future__ import annotations

import base64
import json
import math
import os

RUNTIME = os.path.join(os.path.dirname(__file__), "webgl_runtime.js")
RUNTIME_NAME = "codenodes_webgl.js"
WEB_MAX_COUNT = 4_000_000          # a browser handles fewer than Blender: keep pages light


class NotPackable(Exception):
    pass


def _b64(arr):
    import numpy as np
    return base64.b64encode(np.ascontiguousarray(arr, dtype=np.float32).tobytes()).decode("ascii")


def _view(host, scene, radius):
    """Where the page's camera starts: the scene camera's view of the object if there is one, else a
    three-quarter view sized to the particles. Target, distance, yaw, pitch (radians, Z up) and fov."""
    from mathutils import Vector
    target = host.matrix_world.translation.copy()
    cam = scene.camera
    if cam is not None and cam.type == 'CAMERA':
        eye = cam.matrix_world.translation
        fwd = cam.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))
        # aim at the point on the camera's line closest to the object
        t = max(0.1, (target - eye).dot(fwd))
        target = eye + fwd * t
        d = eye - target
        dist = d.length
        yaw = math.atan2(d.y, d.x)
        pitch = math.asin(max(-1.0, min(1.0, d.z / max(dist, 1e-6))))
        fov = cam.data.angle_y if cam.data.type == 'PERSP' else math.radians(40)
        return {"target": list(target), "distance": dist, "yaw": yaw, "pitch": pitch, "fov": fov}
    dist = max(radius * 3.2, 1.0)
    return {"target": list(target), "distance": dist, "yaw": math.radians(-60), "pitch": math.radians(22),
            "fov": math.radians(40)}


def particle_bundle(head, host=None, scene=None, count=None):
    """The JSON-ready bundle for a GPU Particles head (a source object in CodeNodes Sources)."""
    import bpy
    import numpy as np
    from . import gn_link, gpu_live, links, live, particles
    from .sdf_code import PRELUDE, param_defines
    scene = scene or bpy.context.scene
    s = head.codenodes
    if links.ekind(head) != 'PARTICLES':
        raise NotPackable(f"'{head.name}' isn't a particle chain (only particles run on the web for now)")
    for _ in range(2):
        gn_link.sync()
        live._flush()
    bpy.context.view_layer.update()             # world matrices of objects just moved
    comp, values = links.composite(head)
    params = particles.parse_params(comp.source)
    names = [p.name for p in params]
    vals = [float(values.get(n, p.default)) for n, p in zip(names, params)]
    n = int(min(count or s.count, WEB_MAX_COUNT))
    row = particles.row_for(n)
    if host is None:
        hosts = gpu_live.hosts.get(head.name) or []
        host = bpy.data.objects.get(hosts[0]) if hosts else None
    emitter = None
    if s.emitter is not None:
        got = gpu_live.emitter_for(head)
        if got is not None and len(got[0]):
            pts, nrm = np.asarray(got[0], np.float32)[:65536], np.asarray(got[1], np.float32)[:65536]
            emitter = {"count": int(len(pts)), "points": _b64(pts), "normals": _b64(nrm)}
    mode = {'SPEED': 0, 'AGE': 1, 'CODE': 2}[s.color_by]
    if comp.has_look and s.color_by != 'AGE':
        mode = 2
    if mode == 2 and not comp.has_look:
        mode = 0
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    obj_matrix = host.matrix_world if host is not None else None
    m = [obj_matrix[r][c] for c in range(4) for r in range(4)] if obj_matrix is not None else \
        [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    radius = 2.0
    for p in params:
        if p.name.endswith(("radius", "size")) and p.name.startswith("n0_"):
            radius = max(radius, float(values.get(p.name, p.default)))
    world = scene.world
    bg = (0.02, 0.022, 0.03, 1.0)
    if world is not None and world.node_tree is not None and world.node_tree.nodes.get("Background"):
        c = world.node_tree.nodes["Background"].inputs["Color"].default_value
        bg = tuple(max(0.0, x) ** (1 / 2.2) for x in c[:3]) + (1.0,)         # shown as sRGB on the page
    return {
        "format": "codenodes-particles", "version": 1,
        "name": head.name.replace("CN · ", ""),
        "count": n, "row": row, "rows": max(1, math.ceil(n / row)),
        "prelude": PRELUDE + particles.PARTICLE_PRELUDE + param_defines(params),
        "source": comp.source, "defines": "",
        "params": vals, "param_names": names,
        "has_x": "#define CN_HAS_X" in comp.source, "has_look": bool(comp.has_look),
        "stagger": float(s.stagger), "substeps": int(s.substeps), "prewarm": float(s.prewarm),
        "fps": float(fps), "scene_time": 0.0,
        "emitter": emitter,
        "look": {"color_mode": mode, "color_a": list(s.color_a), "color_b": list(s.color_b),
                 "point_px": float(s.point_px), "gain": float(s.gain), "speed_range": float(s.speed_range),
                 "additive": s.blend != 'SOLID', "soft": comp.shape in ("glow", "firefly", "streak")},
        "object_matrix": m,
        "view": _view(host, scene, radius) if host is not None else
        {"target": [0, 0, 0], "distance": radius * 3.2, "yaw": -1.05, "pitch": 0.38, "fov": 0.7},
        "background": list(bg),
        "sliders": sliders(head, comp, values),
    }


def sliders(head, comp, values):
    """[{name (in the chain), label, node, min, max, value}] for the page's controls: every slider of every
    node in the chain, labelled the way the node shows it."""
    from . import particles
    out = []
    by_slot = {key: (node, name) for node, name, key in comp.slots}
    for p in particles.parse_params(comp.source):
        if p.name not in by_slot or p.name.endswith("__i"):
            continue
        node, name = by_slot[p.name]
        out.append({"name": p.name, "label": name, "node": node.replace("CN · ", ""),
                    "min": p.min, "max": p.max, "value": float(values.get(p.name, p.default))})
    return out


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  html, body {{ margin: 0; height: 100%; background: {bg}; color: #e8eaec; font: 14px system-ui, sans-serif; }}
  canvas {{ width: 100%; height: 100%; display: block; touch-action: none; }}
  #cn-panel {{ position: fixed; top: 12px; left: 12px; background: #0008; padding: 10px 12px; border-radius: 8px;
              max-height: 80vh; overflow: auto; }}
  #cn-panel label {{ display: grid; grid-template-columns: 9em 9em 3.5em; gap: 6px; align-items: center; }}
  #cn-panel h3 {{ margin: 6px 0 2px; font-size: 12px; color: #9aa1a8; font-weight: 500; }}
  #cn-error {{ position: fixed; inset: 0; display: none; padding: 24px; white-space: pre-wrap; background: #300; }}
</style></head>
<body>
<canvas id="cn"></canvas>
<div id="cn-panel"></div>
<pre id="cn-error"></pre>
<script type="module">
import {{ runParticles }} from "./{runtime}";
const bundles = {bundles};
try {{
  const view = await runParticles(document.getElementById("cn"), bundles);
  window.codenodes = view;
  const panel = document.getElementById("cn-panel");
  // one slider per node and setting, even when a node (a shared Wind Field) is in several chains
  const groups = new Map();
  bundles.forEach((bundle, which) => {{
    for (const s of bundle.sliders) {{
      const key = s.node + "\\u0000" + s.label;
      if (!groups.has(key)) groups.set(key, {{ s, targets: [] }});
      groups.get(key).targets.push([s.name, which]);
    }}
  }});
  let node = null;
  for (const {{ s, targets }} of groups.values()) {{
    if (s.node !== node) {{ node = s.node; const h = document.createElement("h3"); h.textContent = node; panel.append(h); }}
    const row = document.createElement("label");
    const out = document.createElement("span");
    const input = Object.assign(document.createElement("input"), {{ type: "range", min: s.min, max: s.max,
      step: (s.max - s.min) / 200, value: s.value }});
    out.textContent = (+s.value).toFixed(2);
    input.addEventListener("input", () => {{
      for (const [name, which] of targets) view.set(name, +input.value, which);
      out.textContent = (+input.value).toFixed(2);
    }});
    row.append(Object.assign(document.createElement("span"), {{ textContent: s.label }}), input, out);
    panel.append(row);
  }}
  if (!panel.childElementCount) panel.remove();
}} catch (e) {{
  const box = document.getElementById("cn-error"); box.style.display = "block"; box.textContent = e.message;
  window.codenodesError = e.message;
}}
</script>
</body></html>
"""


def export_page(head, path, host=None, scene=None):
    """Write a page that runs `head`'s chain live (index.html + codenodes_webgl.js next to it)."""
    return export_bundles([particle_bundle(head, host, scene)], path)


def export_bundles(bundles, path):
    """Write a page running `bundles` (from particle_bundle / host_bundles) in one canvas."""
    import shutil
    bundle = bundles[0]
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    shutil.copyfile(RUNTIME, os.path.join(folder, RUNTIME_NAME))
    bg = bundle["background"]
    css = f"rgb({int(bg[0] * 255)}, {int(bg[1] * 255)}, {int(bg[2] * 255)})"
    with open(path, "w", encoding="utf-8") as f:
        f.write(PAGE.format(title=" + ".join(b["name"] for b in bundles), bg=css, runtime=RUNTIME_NAME,
                            bundles=json.dumps(bundles).replace("</", "<\\/")))
    return path


def host_bundles(host, scene=None):
    """Bundles for every particle pipeline drawn on `host` (an object whose Geometry Nodes hold code nodes):
    each chain from a source, branches included. Raises NotPackable when it has none."""
    import bpy
    from . import gn_link, gpu_live, links, live
    for _ in range(2):
        gn_link.sync()
        live._flush()
    names = [name for name, hosts in gpu_live.hosts.items() if host.name in hosts]
    out = []
    for name in sorted(names):
        obj = bpy.data.objects.get(name)
        if obj is not None and links.ekind(obj) == 'PARTICLES':
            out.append(particle_bundle(obj, host, scene))
    if not out:
        raise NotPackable(f"'{host.name}' shows no CodeNodes particles (GPU Surfaces and GPU Mesh don't run on the "
                          f"web yet)")
    return out
