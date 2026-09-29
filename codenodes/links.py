"""Which code nodes are wired into which: the chains the Geometry Nodes sync finds, and the composed
program for each chain's first node (its "head").

    CHAINS[head source name] = {"stages": [stage source names], "kind": 'PARTICLES' | 'DEFORM',
                                "funcs": {(consumer name, input name): (provider name, export name)}}

A GPU Stage node that sits first in a mesh chain (a Bend wired straight to ordinary geometry) is a
head itself: it gets a tap like a GPU Mesh node and is drawn and made real the same way.
"""

from __future__ import annotations

import hashlib

import bpy

from . import chain

CHAINS: dict[str, dict] = {}
MESH_HEADS: set[str] = set()          # GPU Stage sources that head a mesh chain
STAGE_HEADS: dict[str, list] = {}     # stage source name -> head source names it belongs to
_cache: dict[str, tuple] = {}         # head name -> (key, Composite)
GPU_KINDS = ('MESH', 'PARTICLES', 'DEFORM')


def ekind(obj):
    """The kind a source acts as: a GPU Stage heading a mesh chain acts as a GPU Mesh."""
    s = getattr(obj, "codenodes", None)
    if s is None:
        return None
    if s.kind == 'STAGE' and obj.name in MESH_HEADS:
        return 'DEFORM'
    return s.kind


def is_gpu(obj):
    return ekind(obj) in GPU_KINDS


def heads_of(obj):
    return STAGE_HEADS.get(obj.name, [])


def _text(obj):
    t = obj.codenodes.text
    return t.as_string() if t is not None else ""


def _values(obj):
    return {p.name: p.value for p in obj.codenodes.params}


def _units(head, info):
    """chain.Unit for the head, each stage and each function provider (one Unit per object)."""
    pool = {}

    def unit(obj):
        u = pool.get(obj.name)
        if u is None:
            u = chain.Unit(obj.name, _text(obj), _values(obj))
            pool[obj.name] = u
        return u

    names = [head.name] + list(info.get("stages", []))
    objs = [bpy.data.objects.get(n) for n in names]
    if any(o is None for o in objs):
        objs = [o for o in objs if o is not None]
    units = [unit(o) for o in objs]
    for (consumer, inp), (provider, export) in info.get("funcs", {}).items():
        c = pool.get(consumer)
        pobj = bpy.data.objects.get(provider)
        if pobj is None:
            continue
        if c is None:                       # a provider feeding another provider
            cobj = bpy.data.objects.get(consumer)
            if cobj is None:
                continue
            c = unit(cobj)
        c.funcs[inp] = (unit(pobj), export)
    return units, pool


def _key(head, info, pool):
    h = hashlib.sha1()
    h.update(repr((ekind(head), info.get("stages"), sorted(info.get("funcs", {}).items()))).encode())
    for name in sorted(pool):
        h.update(name.encode() + b"\0" + pool[name].code.encode() + b"\0")
    return h.hexdigest()


def composite(head):
    """(Composite, values) for a head source: its chain composed into one program. Raises SdfCodeError."""
    info = CHAINS.get(head.name, {"stages": [], "funcs": {}})
    units, pool = _units(head, info)
    key = _key(head, info, pool)
    got = _cache.get(head.name)
    if got is None or got[0] != key:
        if ekind(head) == 'PARTICLES':
            comp = chain.compose_particles(units[0], units[1:])
        else:
            comp = chain.compose_mesh(units)
        got = (key, comp)
        _cache[head.name] = got
    comp = got[1]

    def lookup(node, name):
        u = pool.get(node)
        return u.values.get(name) if u is not None else None

    return comp, comp.values_from(lookup)


def check_compile(head):
    """Compose and compile a head's chain on the GPU now. "" or the error (node and line)."""
    from . import deform, gpu_guard, particles
    from .sdf_code import SdfCodeError
    try:
        comp, _values = composite(head)
        if not gpu_guard.allowed():
            return ""
        if ekind(head) == 'PARTICLES':
            particles.get_sim(head.name, comp.source, head.codenodes.count)
        elif ekind(head) == 'DEFORM':
            deform.shader_for(comp.source)
        return ""
    except SdfCodeError as exc:
        return str(exc)


def forget(name=None):
    if name is None:
        _cache.clear()
    else:
        _cache.pop(name, None)


def set_chains(chains, mesh_heads):
    """Called by the Geometry Nodes sync. Returns the head names whose chain changed."""
    changed = {n for n in set(chains) | set(CHAINS) if chains.get(n) != CHAINS.get(n)}
    changed |= (set(mesh_heads) ^ MESH_HEADS)
    CHAINS.clear()
    CHAINS.update(chains)
    MESH_HEADS.clear()
    MESH_HEADS.update(mesh_heads)
    STAGE_HEADS.clear()
    for head, info in chains.items():
        for st in info.get("stages", []):
            STAGE_HEADS.setdefault(st, []).append(head)
        for (consumer, _inp), (provider, _exp) in info.get("funcs", {}).items():
            STAGE_HEADS.setdefault(provider, [])
            if head not in STAGE_HEADS[provider]:
                STAGE_HEADS[provider].append(head)
    for name in changed:
        forget(name)
    return changed
