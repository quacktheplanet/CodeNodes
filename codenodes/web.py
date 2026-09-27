"""A scene as an interactive web page: three.js, orbit controls, and real sliders.

Geometry Nodes cannot run in a browser, so every position a slider can reach is built
in Blender first (headless is fine) and exported as glTF. What makes that correct rather
than approximate is working out what each slider actually changes: widen a trail and
the forest that keeps clear of it moves too. So each slider is tried at every value and
any object whose result changes belongs to it. Sliders that change the same objects are
baked together as combinations, so the page never shows a mix that could not exist.
Everything else is exported once.

The page is one self-contained HTML file. Scripts come from jsdelivr; the models are
embedded.
"""

from __future__ import annotations

import base64
import itertools
import json
import math
import os
import shutil
import tempfile

import bpy
from mathutils import Vector

MAX_COMBINATIONS = 64
MAX_BYTES = 14_000_000


class WebError(ValueError):
    pass


def _modifier_with(obj, label):
    for mod in obj.modifiers:
        if mod.type == 'NODES' and mod.node_group is not None:
            for item in mod.node_group.interface.items_tree:
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == label:
                    return mod, item.identifier
    return None, None


def _signature(obj):
    from . import agent
    info = agent.measure(obj)
    return (info["verts"], info["faces"], info["curves"], info["points"], info["instances"],
            tuple(round(v, 3) for v in info["size"]), tuple(round(v, 3) for v in info["centre"]))


def _set(obj, mod, ident, value):
    mod[ident] = value
    obj.update_tag()


def _get(mod, ident):
    """A copy of a modifier input's value: vectors come back as live views that would
    follow later changes (or dangle), which is no use for putting things back."""
    from .gn.serialize import _copy_idprop
    return _copy_idprop(mod[ident])


def _export(objects, path):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    kwargs = dict(filepath=path, export_format='GLB', use_selection=True, export_apply=True,
                  export_cameras=False, export_lights=False)
    names = {p.identifier for p in bpy.ops.export_scene.gltf.get_rna_type().properties}
    if "export_gn_mesh" in names:
        kwargs["export_gn_mesh"] = True
    bpy.ops.export_scene.gltf(**kwargs)
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _to_gltf(v):
    """Blender is Z-up, glTF is Y-up."""
    return [round(v[0], 4), round(v[2], 4), round(-v[1], 4)]


def _palette(objects):
    palette = {}
    mats = set()
    for obj in objects:
        for slot in getattr(obj, "material_slots", []):
            if slot.material:
                mats.add(slot.material)
        for mod in obj.modifiers:
            if mod.type == 'NODES' and mod.node_group:
                for key in mod.keys():
                    value = mod[key]
                    if isinstance(value, bpy.types.Material):
                        mats.add(value)
    for mat in bpy.data.materials:        # instanced assets bring their own
        if mat.users:
            mats.add(mat)
    for mat in mats:
        entry = {"color": list(mat.diffuse_color[:3]), "roughness": 0.6, "metalness": 0.0,
                 "emissive": [0, 0, 0], "emissiveIntensity": 0.0}
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) \
            if mat.node_tree else None
        if bsdf is not None:
            base = bsdf.inputs["Base Color"]
            if not base.is_linked:
                entry["color"] = list(base.default_value[:3])
            entry["roughness"] = float(bsdf.inputs["Roughness"].default_value)
            entry["metalness"] = float(bsdf.inputs["Metallic"].default_value)
            strength = float(bsdf.inputs["Emission Strength"].default_value)
            if strength > 0:
                entry["emissive"] = list(bsdf.inputs["Emission Color"].default_value[:3])
                entry["emissiveIntensity"] = min(strength, 6.0)
        palette[mat.name] = {k: ([round(c, 4) for c in v] if isinstance(v, list) else round(v, 4))
                             for k, v in entry.items()}
    return palette


def _view():
    s = bpy.context.scene
    cam = s.camera
    view = {}
    if cam is not None:
        bpy.context.view_layer.update()
        forward = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        target = cam.matrix_world.translation + forward * 50.0
        # aim at the ground rather than into the air if the camera looks down
        if forward.z < -0.05:
            target = cam.matrix_world.translation + forward * (cam.matrix_world.translation.z
                                                               / -forward.z)
        fov = 2 * math.degrees(math.atan(cam.data.sensor_width / (2 * cam.data.lens)))
        view = {"position": _to_gltf(cam.matrix_world.translation), "target": _to_gltf(target),
                "fov": round(min(fov * 0.66, 75), 2)}
    sun = next((o for o in s.objects if o.type == 'LIGHT' and o.data.type == 'SUN'), None)
    if sun is not None:
        towards = sun.matrix_world.to_quaternion() @ Vector((0, 0, 1))
        view["sun"] = {"direction": _to_gltf(towards), "color": list(sun.data.color),
                       "strength": round(min(sun.data.energy, 6.0), 3)}
    sky = [0.42, 0.56, 0.78]
    if s.world and s.world.node_tree:
        bg = s.world.node_tree.nodes.get("Background")
        if bg is not None and not bg.inputs[0].is_linked:
            sky = list(bg.inputs[0].default_value[:3])
    view["sky"] = [round(c, 4) for c in sky]
    return view


def export_page(path, objects=None, sliders=(), static=(), overrides=None, title="Level",
                subtitle=""):
    """Write the page. sliders: [{"object", "input", "values", "label"?, "unit"?}].

    static: objects to leave out of change detection (they are exported once, as they
    are). overrides: {object: {input: value}} used only while exporting — a lower
    terrain resolution, say, to keep the page small.
    """
    if not str(path).lower().endswith(".html"):
        raise WebError("the page has to be written to a .html file")
    scene_objs = list(bpy.context.view_layer.objects)
    if objects:
        missing = [n for n in objects if n not in bpy.data.objects]
        if missing:
            raise WebError(f"no object called {', '.join(missing)}")
        chosen = [bpy.data.objects[n] for n in objects]
    else:
        chosen = [o for o in scene_objs if o.type in ('MESH', 'CURVE', 'CURVES')
                  and o.visible_get()]
    restore = []
    selected = [o for o in scene_objs if o.select_get()]
    folder = tempfile.mkdtemp(prefix="codenodes_web_")
    try:
        for name, values in (overrides or {}).items():
            obj = bpy.data.objects.get(name)
            if obj is None:
                raise WebError(f"override for '{name}', which does not exist")
            for label, value in values.items():
                mod, ident = _modifier_with(obj, label)
                if mod is None:
                    raise WebError(f"'{name}' has no input '{label}'")
                restore.append((obj, mod, ident, _get(mod, ident)))
                _set(obj, mod, ident, value)

        specs = []
        for spec in sliders:
            obj = bpy.data.objects.get(spec.get("object", ""))
            if obj is None:
                raise WebError(f"slider on '{spec.get('object')}', which does not exist")
            mod, ident = _modifier_with(obj, spec.get("input", ""))
            if mod is None:
                raise WebError(f"'{obj.name}' has no input '{spec.get('input')}'")
            values = list(spec.get("values") or [])
            if len(values) < 2:
                raise WebError(f"slider '{spec.get('input')}' needs at least two values")
            current = _get(mod, ident)
            if not all(isinstance(v, (int, float)) for v in values + [current]):
                raise WebError(f"slider '{spec['input']}' has to be a number or on/off input, "
                               "with number values")
            restore.append((obj, mod, ident, current))
            # Blender keeps floats as float32: 3.2 comes back as 3.2000000476837
            near = [i for i, v in enumerate(values) if v == current or (
                isinstance(v, (int, float)) and isinstance(current, (int, float))
                and abs(v - current) < 1e-4)]
            if near:
                start = near[0]
            else:
                values = sorted(values + [current]) if all(
                    isinstance(v, (int, float)) for v in values) else [current] + values
                start = values.index(current)
            specs.append({"obj": obj, "mod": mod, "ident": ident, "values": values,
                          "base": start,
                          "label": spec.get("label") or spec["input"],
                          "unit": spec.get("unit", ""), "input": spec["input"]})

        # what does each slider really change?
        frozen = {bpy.data.objects[n] for n in static if n in bpy.data.objects}
        base = {o: _signature(o) for o in chosen}
        for spec in specs:
            touched = set()
            for i, value in enumerate(spec["values"]):
                if i == spec["base"]:
                    continue
                _set(spec["obj"], spec["mod"], spec["ident"], value)
                touched |= {o for o in chosen if o not in frozen and _signature(o) != base[o]}
            _set(spec["obj"], spec["mod"], spec["ident"], spec["values"][spec["base"]])
            if spec["obj"] in chosen:
                touched.add(spec["obj"])
            spec["touches"] = touched

        # sliders that change the same things are baked together
        clusters = []
        for spec in specs:
            joined = [c for c in clusters if c["objects"] & spec["touches"]]
            merged = {"specs": [spec], "objects": set(spec["touches"])}
            for c in joined:
                merged["specs"] = c["specs"] + merged["specs"]
                merged["objects"] |= c["objects"]
                clusters.remove(c)
            clusters.append(merged)

        moving = set().union(*(c["objects"] for c in clusters)) if clusters else set()
        still = [o for o in chosen if o not in moving]
        data = {"title": title, "subtitle": subtitle, "view": _view(),
                "palette": _palette(chosen), "static": None, "groups": []}
        if still:
            data["static"] = _export(still, os.path.join(folder, "static.glb"))
        total = len(data["static"] or "")
        for n, cluster in enumerate(clusters):
            combos = list(itertools.product(*(range(len(s["values"])) for s in cluster["specs"])))
            if len(combos) > MAX_COMBINATIONS:
                raise WebError("sliders " + ", ".join(s["label"] for s in cluster["specs"])
                               + f" change the same objects, so every combination is baked: "
                                 f"{len(combos)} is too many. Use fewer values, or list an "
                                 f"object in static")
            variants = {}
            for combo in combos:
                for spec, i in zip(cluster["specs"], combo):
                    _set(spec["obj"], spec["mod"], spec["ident"], spec["values"][i])
                variants[",".join(map(str, combo))] = _export(
                    sorted(cluster["objects"], key=lambda o: o.name),
                    os.path.join(folder, f"g{n}_{'_'.join(map(str, combo))}.glb"))
                total += len(variants[",".join(map(str, combo))])
                if total > MAX_BYTES:
                    raise WebError("the page would be too big; use fewer slider values or a "
                                   "lower resolution through overrides")
            for spec in cluster["specs"]:
                _set(spec["obj"], spec["mod"], spec["ident"], spec["values"][spec["base"]])
            data["groups"].append({
                "objects": sorted(o.name for o in cluster["objects"]),
                "sliders": [{"label": s["label"], "unit": s["unit"], "values": s["values"],
                             "start": s["base"], "object": s["obj"].name, "input": s["input"],
                             "moves": sorted(o.name for o in s["touches"] if o != s["obj"])}
                            for s in cluster["specs"]],
                "variants": variants})
    finally:
        # everything back as it was, however far the export got
        for obj, mod, ident, value in reversed(restore):
            try:
                _set(obj, mod, ident, value)
            except Exception:
                pass
        for o in bpy.context.view_layer.objects:
            o.select_set(o in selected)
        shutil.rmtree(folder, ignore_errors=True)

    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("__TITLE__", _escape(title)).replace("__DATA__", payload)
    target = os.path.abspath(path)
    if not os.path.isdir(os.path.dirname(target)):
        raise WebError(f"the folder {os.path.dirname(target)} does not exist")
    partial = target + ".part"
    with open(partial, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(partial, target)       # never leave a half-written page behind
    return {"ok": True, "path": os.path.abspath(path), "bytes": len(html),
            "static": [o.name for o in still],
            "groups": [{"sliders": [s["label"] for s in g["sliders"]], "objects": g["objects"],
                        "variants": len(g["variants"])} for g in data["groups"]]}


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


TEMPLATE = r"""<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {
  --paper: #F2F4EF; --ink: #1D2822; --muted: #5E6B63; --line: #D5DBD3;
  --accent: #2E6A9E; --track: #C9D1CA; --glass: rgba(242, 244, 239, 0.92);
  --display: "Barlow Semi Condensed", "Arial Narrow", "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #141A17; --ink: #E4EBE6; --muted: #98A69D; --line: #2A332E;
    --accent: #86B6DF; --track: #34403A; --glass: rgba(20, 26, 23, 0.9); color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --paper: #141A17; --ink: #E4EBE6; --muted: #98A69D; --line: #2A332E;
  --accent: #86B6DF; --track: #34403A; --glass: rgba(20, 26, 23, 0.9); color-scheme: dark;
}
html, body { height: 100%; }
body { background: var(--paper); color: var(--ink); font-family: var(--display); overflow: hidden; }
#stage { position: fixed; inset: 0; }
#stage canvas { display: block; width: 100%; height: 100%; touch-action: none; }
.panel {
  position: fixed; top: calc(16px + env(safe-area-inset-top, 0px)); left: 16px;
  width: min(330px, calc(100% - 32px)); max-height: calc(100% - 32px); overflow: auto;
  background: var(--glass); backdrop-filter: blur(10px); border: 1px solid var(--line);
  border-radius: 10px; padding: 16px 18px; display: grid; gap: 14px;
}
.panel h1 { margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.01em; line-height: 1.05; text-wrap: balance; }
.panel .sub { margin: 4px 0 0; color: var(--muted); font-size: 15px; line-height: 1.35; }
.controls { display: grid; gap: 14px; }
.ctl { display: grid; gap: 6px; }
.ctl .row { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.ctl label { font-size: 13px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }
.ctl output { font-family: var(--mono); font-size: 14px; font-variant-numeric: tabular-nums; color: var(--accent); }
.ctl .note { font-size: 13px; color: var(--muted); }
input[type=range] { width: 100%; accent-color: var(--accent); margin: 0; height: 24px; }
input[type=range]:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 4px; }
.ticks { display: flex; justify-content: space-between; font-family: var(--mono); font-size: 11px; color: var(--muted); padding-inline: 2px; }
.foot { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; font-size: 13px; color: var(--muted); border-top: 1px solid var(--line); padding-top: 10px; }
.foot button { font: inherit; font-weight: 600; color: var(--accent); background: none; border: 1px solid var(--line); border-radius: 6px; padding: 4px 10px; cursor: pointer; }
.foot button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
#status { position: fixed; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom, 0px)); font-family: var(--mono); font-size: 12px; color: var(--ink); background: var(--glass); border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; }
@media (max-width: 640px) {
  .panel { top: auto; bottom: calc(12px + env(safe-area-inset-bottom, 0px)); left: 12px; width: calc(100% - 24px); max-height: 46%; padding: 12px 14px; gap: 10px; }
  .panel h1 { font-size: 21px; }
  .panel .sub { display: none; }
  #status { top: calc(12px + env(safe-area-inset-top, 0px)); bottom: auto; right: 12px; }
}
</style>
<div id="stage"></div>
<section class="panel" aria-label="Scene controls">
  <div>
    <h1 id="title"></h1>
    <p class="sub" id="subtitle"></p>
  </div>
  <div class="controls" id="controls"></div>
  <div class="foot"><span>Drag to orbit · scroll or pinch to zoom</span><button type="button" id="reset">Reset view</button></div>
</section>
<div id="status" role="status">Loading scene…</div>
<script type="application/json" id="scene-data">__DATA__</script>
<script>
// Say what went wrong on the page itself; a silent "Loading…" helps nobody.
window.addEventListener("error", function (e) { var s = document.getElementById("status"); if (s) { s.hidden = false; s.textContent = "Error: " + (e.message || e); } });
window.addEventListener("unhandledrejection", function (e) { var s = document.getElementById("status"); if (s) { s.hidden = false; s.textContent = "Error: " + ((e.reason && e.reason.message) || e.reason); } });
</script>
<!-- Classic scripts rather than ES modules and an import map: a host that adds its own
     module script first makes the browser ignore a later import map. -->
<script src="https://cdn.jsdelivr.net/npm/three@0.147.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.147.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.147.0/examples/js/loaders/GLTFLoader.js"></script>
<script>
(function () {
const status = document.getElementById("status");
if (!window.THREE || !THREE.GLTFLoader || !THREE.OrbitControls) {
  status.textContent = "Could not load three.js from cdn.jsdelivr.net.";
  return;
}
const OrbitControls = THREE.OrbitControls, GLTFLoader = THREE.GLTFLoader;
const data = JSON.parse(document.getElementById("scene-data").textContent);
document.getElementById("title").textContent = data.title;
document.getElementById("subtitle").textContent = data.subtitle || "";

const stage = document.getElementById("stage");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.9;
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.physicallyCorrectLights = true;
stage.appendChild(renderer.domElement);

// three 0.147 treats colours as linear already, which is what Blender hands over
const lin = (c) => new THREE.Color(c[0], c[1], c[2]);
const scene = new THREE.Scene();
const sky = lin(data.view.sky);
scene.background = sky;
const view = data.view;
const camera = new THREE.PerspectiveCamera(view.fov || 45, 1, 0.1, 2000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.49;

scene.add(new THREE.HemisphereLight(sky, lin([0.16, 0.14, 0.1]), 1.1));
const sun = new THREE.DirectionalLight(view.sun ? lin(view.sun.color) : 0xffffff, view.sun ? view.sun.strength * 0.75 : 3);
const sunDir = new THREE.Vector3(...(view.sun ? view.sun.direction : [0.4, 1, 0.3])).normalize();
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.03;
scene.add(sun, sun.target);

const materials = {};
function paint(root) {
  root.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = true; o.receiveShadow = true;
    const swap = (m) => {
      const p = data.palette[m.name];
      if (!p) return m;
      if (!materials[m.name]) {
        materials[m.name] = new THREE.MeshStandardMaterial({
          color: lin(p.color), roughness: p.roughness, metalness: p.metalness,
          emissive: lin(p.emissive), emissiveIntensity: p.emissiveIntensity, name: m.name });
      }
      return materials[m.name];
    };
    o.material = Array.isArray(o.material) ? o.material.map(swap) : swap(o.material);
  });
  return root;
}

const loader = new GLTFLoader();
function parse(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Promise((resolve, reject) => loader.parse(bytes.buffer, "", (g) => resolve(paint(g.scene)), reject));
}

let framed = false;
function frame() {
  const box = new THREE.Box3().setFromObject(scene);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3()), centre = box.getCenter(new THREE.Vector3());
  const radius = size.length() / 2;
  sun.position.copy(centre).addScaledVector(sunDir, radius * 2);
  sun.target.position.copy(centre);
  const cam = sun.shadow.camera;
  cam.left = cam.bottom = -radius; cam.right = cam.top = radius;
  cam.near = 0.5; cam.far = radius * 4; cam.updateProjectionMatrix();
  scene.fog = new THREE.Fog(sky, radius * 1.6, radius * 4);
  if (!framed) { resetView(centre, radius); framed = true; }
}
let home = null;
function resetView(centre, radius) {
  if (!home) {
    home = view.position
      ? { pos: new THREE.Vector3(...view.position), target: new THREE.Vector3(...view.target) }
      : { pos: centre.clone().add(new THREE.Vector3(radius, radius * 0.8, radius)), target: centre.clone() };
  }
  camera.position.copy(home.pos);
  controls.target.copy(home.target);
  controls.update();
}
document.getElementById("reset").addEventListener("click", () => resetView());

const fmt = (v, unit) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(v < 10 ? 2 : 1).replace(/\.?0+$/, "")) : String(v)) + (unit ? " " + unit : "");

async function main() {
  if (data.static) scene.add(await parse(data.static));
  const controlsEl = document.getElementById("controls");
  for (const [g, group] of data.groups.entries()) {
    const holder = new THREE.Group();
    scene.add(holder);
    const cache = {};
    const picks = group.sliders.map((s) => s.start);
    let token = 0;
    async function show() {
      const key = picks.join(",");
      const mine = ++token;
      status.textContent = "Building…"; status.hidden = false;
      if (!cache[key]) cache[key] = parse(group.variants[key]);
      const model = await cache[key];
      if (mine !== token) return;
      holder.clear(); holder.add(model);
      frame();
      status.hidden = true;
    }
    group.sliders.forEach((s, i) => {
      const id = `slider-${g}-${i}`;
      const wrap = document.createElement("div");
      wrap.className = "ctl";
      const moves = s.moves.length ? `Also moves ${s.moves.join(", ")}` : "";
      wrap.innerHTML = `<div class="row"><label for="${id}"></label><output for="${id}"></output></div>
        <input type="range" id="${id}" min="0" max="${s.values.length - 1}" step="1" value="${s.start}">
        <div class="ticks"><span></span><span></span></div>${moves ? '<div class="note"></div>' : ""}`;
      wrap.querySelector("label").textContent = s.label;
      const out = wrap.querySelector("output");
      const ticks = wrap.querySelectorAll(".ticks span");
      ticks[0].textContent = fmt(s.values[0], s.unit);
      ticks[1].textContent = fmt(s.values[s.values.length - 1], s.unit);
      if (moves) wrap.querySelector(".note").textContent = moves;
      const input = wrap.querySelector("input");
      const update = () => { out.textContent = fmt(s.values[input.value], s.unit); };
      input.addEventListener("input", () => { picks[i] = Number(input.value); update(); show(); });
      update();
      controlsEl.appendChild(wrap);
    });
    await show();
  }
  frame();
  status.hidden = true;
}

function resize() {
  const w = stage.clientWidth, h = stage.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
main().catch((e) => { status.textContent = "Could not load the scene: " + e.message; status.hidden = false; });
})();
</script>
"""


# ---- live shape pages ----------------------------------------------------------------------
# A shape description is our own small language, so the browser can build it itself: the
# page carries the text and a JavaScript copy of the builder (shapes/shapes.js, checked
# against the Python by tests/test_shape_js.py) and rebuilds the mesh as a slider moves,
# at any value rather than a few baked ones.

def shape_page(path, source, title="Shape", subtitle="", colors=None, color=(0.72, 0.7, 0.66),
               roughness=0.45, metalness=0.0, values=None):
    """Write a page that rebuilds a shape in the browser as its sliders move.

    values: slider values to start from (what was tuned in Blender), by name.
    colors: {part name: [r, g, b]} (linear, 0-1); other parts get `color`. Parts that
    cut another part (subtract, intersect) need Blender's boolean solver, so a shape
    with them is refused; bevels are left off and the page says so.
    """
    from . import shapes as shape_lang
    if not str(path).lower().endswith(".html"):
        raise WebError("the page has to be written to a .html file")
    try:
        shape = shape_lang.parse(source)
        parts = shape.build_parts()
    except shape_lang.ExprError as exc:
        raise WebError(f"the shape does not build: {exc}") from None
    cutting = [p.name for p in parts if p.mode != "add"]
    if cutting:
        raise WebError("a live page cannot cut one part with another yet (" + ", ".join(
            f"'{n}'" for n in cutting) + "); bake it with web_page instead")
    notes = []
    if any(p.bevel for p in parts) or shape.finish_options().get("bevel"):
        notes.append("Bevels are left off here; Blender adds them.")
    data = {"title": title, "subtitle": subtitle, "source": source,
            "params": [{"name": n, "default": min(max(float((values or {}).get(n, d)), lo), hi),
                        "min": lo, "max": hi} for n, d, lo, hi in shape.params],
            "color": [float(c) for c in color],
            "colors": {k: [float(c) for c in v] for k, v in (colors or {}).items()},
            "roughness": float(roughness), "metalness": float(metalness), "notes": notes}
    with open(os.path.join(os.path.dirname(__file__), "shapes", "shapes.js"), encoding="utf-8") as fh:
        engine = fh.read()
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = (SHAPE_TEMPLATE.replace("__STYLE__", _STYLE).replace("__TITLE__", _escape(title))
            .replace("__ENGINE__", engine.replace("</", "<\\/")).replace("__DATA__", payload))
    target = os.path.abspath(path)
    if not os.path.isdir(os.path.dirname(target)):
        raise WebError(f"the folder {os.path.dirname(target)} does not exist")
    partial = target + ".part"
    with open(partial, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(partial, target)
    solid = shape.build()
    return {"ok": True, "path": target, "bytes": len(html), "sliders": [p[0] for p in shape.params],
            "faces": len(solid.faces) + len(solid.tris), "notes": notes}


_STYLE = TEMPLATE[TEMPLATE.index("<style>"):TEMPLATE.index("</style>") + len("</style>")]

SHAPE_TEMPLATE = r"""<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
__STYLE__
<style>
details.src summary { cursor: pointer; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
details.src pre { margin: 8px 0 0; max-height: 220px; overflow: auto; font-family: var(--mono); font-size: 11.5px; line-height: 1.45; background: var(--paper); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; }
.notes { font-size: 13px; color: var(--muted); }
.notes:empty { display: none; }
</style>
<div id="stage"></div>
<section class="panel" aria-label="Shape controls">
  <div>
    <h1 id="title"></h1>
    <p class="sub" id="subtitle"></p>
  </div>
  <div class="controls" id="controls"></div>
  <div class="notes" id="notes"></div>
  <details class="src"><summary>The whole model</summary><pre id="source"></pre></details>
  <div class="foot"><span>Built live in your browser · drag to orbit</span><button type="button" id="reset">Reset</button></div>
</section>
<div id="status" role="status">Loading…</div>
<script type="application/json" id="shape-data">__DATA__</script>
<script>
window.addEventListener("error", function (e) { var s = document.getElementById("status"); if (s) { s.hidden = false; s.textContent = "Error: " + (e.message || e); } });
</script>
<script src="https://cdn.jsdelivr.net/npm/three@0.147.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.147.0/examples/js/controls/OrbitControls.js"></script>
<script>
__ENGINE__
</script>
<script>
(function () {
const status = document.getElementById("status");
if (!window.THREE || !THREE.OrbitControls) { status.textContent = "Could not load three.js from cdn.jsdelivr.net."; return; }
const data = JSON.parse(document.getElementById("shape-data").textContent);
document.getElementById("title").textContent = data.title;
document.getElementById("subtitle").textContent = data.subtitle || "";
document.getElementById("source").textContent = data.source.trim();
document.getElementById("notes").textContent = data.notes.join(" ");
const shape = CodeShapes.parse(data.source);

const stage = document.getElementById("stage");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.physicallyCorrectLights = true;
stage.appendChild(renderer.domElement);
const theme = document.documentElement.dataset.theme;
const dark = theme === "dark" || (theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
const scene = new THREE.Scene();
scene.background = new THREE.Color(dark ? 0x121815 : 0xe9ede7);
const camera = new THREE.PerspectiveCamera(35, 1, 0.001, 1000);
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x3a3228, 1.4));
const sun = new THREE.DirectionalLight(0xfff4e6, 3.2);
sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0002; sun.shadow.normalBias = 0.02;
scene.add(sun, sun.target);
const fill = new THREE.DirectionalLight(0xcfe0ff, 0.8);
scene.add(fill);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShadowMaterial({ opacity: dark ? 0.45 : 0.22 }));
ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true;
scene.add(ground);
const holder = new THREE.Group();
scene.add(holder);
const lin = (c) => new THREE.Color(c[0], c[1], c[2]);
const materials = {};
function material(name) {
  if (!materials[name]) materials[name] = new THREE.MeshStandardMaterial({
    color: lin(data.colors[name] || data.color), roughness: data.roughness, metalness: data.metalness, side: THREE.DoubleSide });
  return materials[name];
}

// Z up to Y up, and normals that stay smooth across curves but break at real corners
const CREASE = Math.cos(25 * Math.PI / 180);
function geometry(solid) {
  const v = solid.verts, polys = solid.polygons(), nv = v.length / 3;
  const P = (i) => [v[3 * i], v[3 * i + 2], -v[3 * i + 1]];
  const fn = polys.map((f) => {
    let x = 0, y = 0, z = 0;
    for (let k = 0; k < f.length; k++) {
      const a = P(f[k]), b = P(f[(k + 1) % f.length]);
      x += (a[1] - b[1]) * (a[2] + b[2]); y += (a[2] - b[2]) * (a[0] + b[0]); z += (a[0] - b[0]) * (a[1] + b[1]);
    }
    const len = Math.hypot(x, y, z) || 1;
    return [x / len, y / len, z / len, len];
  });
  const around = Array.from({ length: nv }, () => []);
  polys.forEach((f, p) => f.forEach((i) => around[i].push(p)));
  const pos = [], nor = [];
  polys.forEach((f, p) => {
    const corner = f.map((i) => {
      let x = 0, y = 0, z = 0;
      for (const q of around[i]) {
        const a = fn[p], b = fn[q];
        if (a[0] * b[0] + a[1] * b[1] + a[2] * b[2] >= CREASE) { x += b[0] * b[3]; y += b[1] * b[3]; z += b[2] * b[3]; }
      }
      const len = Math.hypot(x, y, z) || 1;
      return [x / len, y / len, z / len];
    });
    for (let k = 1; k < f.length - 1; k++) for (const c of [0, k, k + 1]) {
      const q = P(f[c]);
      pos.push(q[0], q[1], q[2]); nor.push(corner[c][0], corner[c][1], corner[c][2]);
    }
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
  return g;
}

const values = {};
data.params.forEach((p) => { values[p.name] = p.default; });
let framed = false, pending = false, homeView = null;
function home(centre, radius) {
  if (centre) homeView = { target: centre.clone(), pos: centre.clone().add(new THREE.Vector3(radius * 1.9, radius * 1.1, radius * 2.3)) };
  if (!homeView) return;
  camera.position.copy(homeView.pos); controls.target.copy(homeView.target); controls.update();
}
function build() {
  pending = false;
  const t0 = performance.now();
  let parts;
  try { parts = shape.buildParts(values); }
  catch (e) { status.hidden = false; status.textContent = e.message; window.shapeStats = { error: e.message }; return; }
  holder.children.forEach((m) => m.geometry.dispose());
  holder.clear();
  let faces = 0;
  for (const part of parts) {
    const mesh = new THREE.Mesh(geometry(part.solid), material(part.name));
    mesh.castShadow = true; mesh.receiveShadow = true; mesh.name = part.name;
    holder.add(mesh);
    faces += part.solid.quads.length / 4 + part.solid.tris.length / 3;
  }
  const box = new THREE.Box3().setFromObject(holder);
  if (!box.isEmpty()) {
    const size = box.getSize(new THREE.Vector3()), centre = box.getCenter(new THREE.Vector3()), radius = size.length() / 2 || 1;
    ground.position.set(centre.x, box.min.y, centre.z); ground.scale.setScalar(radius * 12);
    sun.position.set(centre.x + radius * 2, centre.y + radius * 3.5, centre.z + radius * 1.6); sun.target.position.copy(centre);
    fill.position.set(centre.x - radius * 3, centre.y + radius, centre.z - radius * 2);
    const cam = sun.shadow.camera; cam.left = cam.bottom = -radius * 2; cam.right = cam.top = radius * 2;
    cam.near = radius * 0.1; cam.far = radius * 10; cam.updateProjectionMatrix();
    if (!framed) { home(centre, radius); framed = true; }
  }
  const ms = performance.now() - t0;
  window.shapeStats = { faces, ms, parts: parts.length, size: box.isEmpty() ? null : box.getSize(new THREE.Vector3()).toArray() };
  status.hidden = false;
  status.textContent = faces.toLocaleString() + " faces · built in " + ms.toFixed(1) + " ms";
}
const request = () => { if (!pending) { pending = true; requestAnimationFrame(build); } };

const controlsEl = document.getElementById("controls");
const nice = (x) => { const e = Math.pow(10, Math.floor(Math.log10(x))); return [1, 2, 5, 10].map((m) => m * e).find((s) => s >= x); };
const inputs = [];
data.params.forEach((p, i) => {
  const whole = [p.default, p.min, p.max].every(Number.isInteger) && p.max - p.min >= 2;
  const step = whole ? 1 : nice((p.max - p.min) / 400 || 0.001);
  const digits = whole ? 0 : Math.max(0, -Math.floor(Math.log10(step)));
  const id = "param-" + i, wrap = document.createElement("div");
  wrap.className = "ctl";
  wrap.innerHTML = '<div class="row"><label for="' + id + '"></label><output for="' + id + '"></output></div>' +
    '<input type="range" id="' + id + '" min="' + p.min + '" max="' + p.max + '" step="' + step + '" value="' + p.default + '">' +
    '<div class="ticks"><span></span><span></span></div>';
  wrap.querySelector("label").textContent = p.name.replace(/_/g, " ");
  const out = wrap.querySelector("output"), input = wrap.querySelector("input"), ticks = wrap.querySelectorAll(".ticks span");
  ticks[0].textContent = (+p.min).toFixed(digits); ticks[1].textContent = (+p.max).toFixed(digits);
  const show = () => { out.textContent = (+values[p.name]).toFixed(digits); };
  input.addEventListener("input", () => { values[p.name] = Number(input.value); show(); request(); });
  show();
  inputs.push([input, p, show]);
  controlsEl.appendChild(wrap);
});
document.getElementById("reset").addEventListener("click", () => {
  inputs.forEach(([input, p, show]) => { input.value = p.default; values[p.name] = p.default; show(); });
  build(); home();
});

function resize() {
  const w = stage.clientWidth, h = stage.clientHeight;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();
build();
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
})();
</script>
"""
