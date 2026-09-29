"""GPU Cache: bake a live GPU particle simulation to disk and play it back.

A GPU Cache node sits in a particle stream like a stage:

    [Firefly Swarm] -> [Wander] -> [Rise] -> [GPU Cache] -> [Firefly Look]

Its inputs:
    Mode         Live (simulate every frame on the GPU) or Cached (play the baked frames)
    Start / End  the frames to bake
    ⟳ Bake Now   switch on to bake (switches itself back off, like ✎ Edit Code)
    ✕ Clear      switch on to delete the baked frames

What's cached is the simulation of every pipeline passing through the node (its source and every
born/behave stage, before or after the cache node); looks and warps after it still run live, so you
can restyle a cached simulation without baking again. Cached frames play back instantly (scrub
anywhere), feed To Geometry and Render with CodeNodes, and are kept next to the .blend in
`codenodes_cache/<source>/` (a relative path, so the folder travels with the file). Until the file is
saved they live in Blender's temporary folder.

The file per frame is small on purpose: every particle's position, velocity, age and life and each
per-particle attribute as 16-bit floats, compressed with zlib. Frames outside Start–End show the
nearest baked frame.
"""

from __future__ import annotations

import json
import os
import re
import struct
import time
import zlib

import bpy
import numpy as np

MAGIC = b"CNC1"
# owner source name -> {"mode": 'LIVE' | 'CACHED', "start": int, "end": int, "node": (tree, node)}
STATE: dict[str, dict] = {}
_frames: dict[tuple, dict] = {}        # (dir, frame) -> arrays (a small in-memory window)
_pending: list = []                    # owners to bake on the next tick
BAKE = "⟳ Bake Now"
CLEAR = "✕ Clear"
MODES = ["Live", "Cached"]


def _safe(name):
    return re.sub(r"[^\w\-. ]", "_", name).strip() or "source"


def cache_dir(owner_name):
    """Where an owner's frames live: next to the .blend (relative), else Blender's temp folder."""
    base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else bpy.app.tempdir
    return os.path.join(base, "codenodes_cache", _safe(owner_name))


def manifest(owner_name):
    path = os.path.join(cache_dir(owner_name), "manifest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---- the file format ----------------------------------------------------------------------------

FIELDS = (("position", 3), ("velocity", 3), ("age", 1), ("life", 1))


def write_frame(path, state, attrs=()):
    """One frame: MAGIC, header length, JSON header, zlib(float16 fields in order)."""
    fields = list(FIELDS) + [(a, 1) for a in attrs]
    n = len(state["position"])
    parts = []
    for name, comps in fields:
        arr = np.asarray(state[name], np.float32).reshape(n, comps) if comps > 1 else \
            np.asarray(state[name], np.float32).reshape(n)
        parts.append(arr.astype(np.float16).tobytes())
    payload = zlib.compress(b"".join(parts), 1)
    header = json.dumps({"count": n, "fields": fields, "dtype": "f16"}).encode()
    with open(path, "wb") as fh:
        fh.write(MAGIC + struct.pack("<I", len(header)) + header + payload)
    return 8 + len(header) + len(payload)


def read_frame(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != MAGIC:
        raise ValueError(f"not a CodeNodes cache frame: {path}")
    hlen = struct.unpack("<I", data[4:8])[0]
    header = json.loads(data[8:8 + hlen])
    raw = np.frombuffer(zlib.decompress(data[8 + hlen:]), np.float16)
    n = header["count"]
    out, at = {}, 0
    for name, comps in header["fields"]:
        k = n * comps
        arr = raw[at:at + k].astype(np.float32)
        out[name] = arr.reshape(n, comps) if comps > 1 else arr
        at += k
    out["speed"] = np.linalg.norm(out["velocity"], axis=1).astype(np.float32)
    return out


def frame_path(owner_name, frame):
    return os.path.join(cache_dir(owner_name), f"{int(frame):05d}.cnc")


def load(owner_name, frame):
    """The baked state at `frame` (clamped to the baked range), or None."""
    m = manifest(owner_name)
    if not m:
        return None
    f = min(max(int(frame), m["start"]), m["end"])
    d = cache_dir(owner_name)
    key = (d, f, m.get("baked_at"))
    got = _frames.get(key)
    if got is None:
        path = frame_path(owner_name, f)
        if not os.path.exists(path):
            return None
        got = read_frame(path)
        if len(_frames) > 6:
            _frames.clear()
        _frames[key] = got
    return got


# ---- the node ---------------------------------------------------------------------------------------

def is_cache(group):
    from . import gn_link
    return gn_link.is_cache(group)


def build_group():
    """A GPU Cache node group."""
    from . import gn_link, gn_sockets
    group = bpy.data.node_groups.new(gn_link._unique("GPU Cache", bpy.data.node_groups), "GeometryNodeTree")
    group[gn_link.CACHE] = True
    group.description = ("CodeNodes: bakes the GPU simulation of the particles passing through it and plays it "
                         "back. Mode: Live or Cached. Switch on ⟳ Bake Now to bake Start–End")
    if hasattr(group, "color_tag"):
        try:
            group.color_tag = 'GEOMETRY'
        except TypeError:
            pass
    iface = group.interface
    iface.new_socket("Particles", in_out="INPUT", socket_type="NodeSocketBundle")
    iface.new_socket("Mode", in_out="INPUT", socket_type="NodeSocketMenu")
    for name, default, lo, hi in (("Start", 1, -100000, 100000), ("End", 120, -100000, 100000)):
        item = iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketInt")
        item.default_value, item.min_value, item.max_value = default, lo, hi
    for name in (BAKE, CLEAR):
        iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Particles", in_out="OUTPUT", socket_type="NodeSocketBundle")
    gin, gout = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-400, 0), (250, 0)
    gn_sockets.ensure_menu_switch(group, "Mode", MODES)
    gn_sockets._hook_menus(group)
    return group


def _value(tree, node, name):
    from . import gn_link
    sock = node.inputs.get(name)
    return gn_link.resolve(tree, sock) if sock is not None else None


def sync(tree, node, owners):
    """Read one GPU Cache node: its mode and range for every simulation (owner) passing through it,
    and its Bake Now / Clear toggles. `owners` are source objects (a head or a branch with its own
    simulation)."""
    mode = _value(tree, node, "Mode") or "Live"
    start = int(_value(tree, node, "Start") or 1)
    end = int(_value(tree, node, "End") or start)
    if end < start:
        start, end = end, start
    for o in owners:
        STATE[o.name] = {"mode": 'CACHED' if mode == "Cached" else 'LIVE', "start": start, "end": end,
                         "node": (tree.name, node.name)}
    for name, action in ((BAKE, "bake"), (CLEAR, "clear")):
        sock = node.inputs.get(name)
        if sock is not None and not sock.is_linked and sock.default_value:
            sock.default_value = False            # behaves like a button
            for o in owners:
                _pending.append((action, o.name))
            if not bpy.app.timers.is_registered(_run_pending):
                bpy.app.timers.register(_run_pending, first_interval=0.05)
    label = status(owners, mode, start, end)
    if node.label != label:
        node.label = label


def status(owners, mode, start, end):
    """What the node's header says: "Cached 1–120 · 180 MB", "Live · baked 1–120", "Live"."""
    total, rng = 0, None
    for o in owners:
        m = manifest(o.name)
        if m:
            total += m.get("bytes", 0)
            rng = (m["start"], m["end"])
    size = f"{total / 2 ** 20:.1f} MB" if total < 100 * 2 ** 20 else f"{total / 2 ** 20:.0f} MB"
    if mode == "Cached":
        if rng is None:
            return "GPU Cache · nothing baked: switch on ⟳ Bake Now"
        return f"Cached {rng[0]}–{rng[1]} · {size}"
    if rng is not None:
        return f"GPU Cache · Live · baked {rng[0]}–{rng[1]} · {size}"
    return "GPU Cache · Live"


def forget_missing(keep_names):
    for name in [n for n in STATE if n not in keep_names]:
        STATE.pop(name, None)


def _run_pending():
    from . import live
    if live._rendering():
        return 0.5
    todo = list(_pending)
    _pending.clear()
    for action, name in todo:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        try:
            if action == "bake":
                bake(obj)
            else:
                clear(obj)
        except Exception as exc:  # report on the source, never take Blender down
            import traceback
            _report_exc()
            obj.codenodes.last_error = f"cache: {exc}"
    from . import gn_link, gpu_live
    gn_link._dirty[0] = True
    gpu_live.redraw()
    return None


def clear(obj):
    d = cache_dir(obj.name)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".cnc") or f == "manifest.json":
                os.remove(os.path.join(d, f))
    _frames.clear()


def bake(obj, start=None, end=None, progress=True):
    """Simulate `obj` (a source or branch with its own simulation) from Start to End on the GPU and
    write every frame. Returns the manifest."""
    from . import gpu_live, links, particles
    st = STATE.get(obj.name, {})
    start = int(st.get("start", 1) if start is None else start)
    end = int(st.get("end", start) if end is None else end)
    s = obj.codenodes
    comp, values = links.composite(obj)
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    emitter = gpu_live.emitter_for(obj) if s.emitter is not None else None
    d = cache_dir(obj.name)
    os.makedirs(d, exist_ok=True)
    clear(obj)
    attrs = tuple(name for name, _d in comp.attrs)
    wm = bpy.context.window_manager
    t0 = time.perf_counter()
    total = 0
    particles.forget(obj.name)
    if progress:
        wm.progress_begin(0, max(1, end - start))
    try:
        for f in range(start, end + 1):
            sim, _steps = particles.advance(obj.name, comp.source, s.count, f, start, fps, values, s.substeps,
                                            s.stagger, s.prewarm, emitter)
            n = sim.count
            pos = sim._read_tex(sim.pos, n)
            vel = sim._read_tex(sim.vel, n)
            state = {"position": pos[:, :3], "age": pos[:, 3], "velocity": vel[:, :3], "life": vel[:, 3]}
            if sim.ext is not None and attrs:
                ext = sim._read_tex(sim.ext, n)
                for i, a in enumerate(attrs[:4]):
                    state[a] = ext[:, i]
            total += write_frame(frame_path(obj.name, f), state, attrs[:4] if sim.ext is not None else ())
            if progress:
                wm.progress_update(f - start)
    finally:
        if progress:
            wm.progress_end()
    m = {"start": start, "end": end, "count": s.count, "attrs": list(attrs[:4]), "bytes": total,
         "sim_key": comp.sim_key, "seconds": round(time.perf_counter() - t0, 3), "baked_at": time.time()}
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    particles.forget(obj.name)             # the live simulation restarts cleanly after a bake
    _frames.clear()
    s.stats = (f"baked {start}–{end}: {end - start + 1} frames · {total / 2 ** 20:.1f} MB · "
               f"{m['seconds']:.1f} s")
    return m


def active(owner):
    """The cache settings if `owner` plays from a bake right now, else None."""
    st = STATE.get(owner.name)
    if not st or st["mode"] != 'CACHED':
        return None
    m = manifest(owner.name)
    if not m:
        return None
    return m


def _upload(sim, state):
    """Put a baked frame into a Sim's GPU textures (so it's drawn and read like a live one)."""
    import gpu
    n, size = sim.count, sim.row * sim.rows

    def tex(a4):
        buf = np.zeros((size, 4), np.float32)
        m = min(n, len(a4))
        buf[:m] = a4[:m]
        return gpu.types.GPUTexture((sim.row, sim.rows), format='RGBA32F',
                                    data=gpu.types.Buffer('FLOAT', size * 4, buf.ravel()))

    sim.pos = tex(np.c_[state["position"], state["age"]])
    sim.vel = tex(np.c_[state["velocity"], state["life"]])
    if sim.ext is not None:
        attrs = [a for a in state if a not in ("position", "velocity", "age", "life", "speed")]
        ext = np.zeros((len(state["position"]), 4), np.float32)
        for i, a in enumerate(attrs[:4]):
            ext[:, i] = state[a]
        sim.ext = tex(ext)
    sim.frame = None                        # going back to Live restarts the simulation


def playback(owner, comp, scene):
    """A Sim holding the baked frame for the scene's frame if `owner` is cached, else None."""
    from . import gpu_guard, particles
    if not STATE or owner.name not in STATE:
        return None
    m = active(owner)
    if m is None or not gpu_guard.allowed():
        return None
    state = load(owner.name, scene.frame_current)
    if state is None:
        return None
    sim = particles.get_sim(owner.name, comp.source, owner.codenodes.count)
    key = (m.get("baked_at"), min(max(scene.frame_current, m["start"]), m["end"]))
    if getattr(sim, "cache_key", None) != key:
        _upload(sim, state)
        sim.cache_key = key
    return sim


def _report_exc():
    """Print the current error without letting Python touch freed Blender structs (see safe_errors)."""
    from .safe_errors import report
    report()
