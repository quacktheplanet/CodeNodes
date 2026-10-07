"""Which code nodes are wired into which: the pipelines the Geometry Nodes sync finds, and the
composed program for each.

Code nodes form a graph, not just a line. A stream (particles or a mesh) can split: one output wired
into several stages, each branch going its own way; streams can merge (Join Particles); and one
function output can feed any number of nodes. The sync turns that graph into *pipelines*: every
path from a source (the "head") to where a stream ends (a node nothing continues from, or a To
Geometry node). Each pipeline is compiled into one GPU program:

    CHAINS[pipeline name] = {"source": head source name, "stages": [stage source names in order],
                             "kind": 'PARTICLES' | 'DEFORM',
                             "funcs": {(consumer name, input name): (provider name, export name)}}

The first pipeline of a head is named after the head itself. Every further branch gets a hidden
"branch" object in the CodeNodes Sources collection (BASE[branch] = head), which mirrors the head's
settings, so everything that works per source (drawing, To Geometry, caches) works per branch.
Branches whose simulation part is the same as their head's (the same born/behave stages) share the
head's GPU simulation and only add their own drawing (Composite.sim_key).

A GPU Stage node that sits first in a mesh chain (a Bend wired straight to ordinary geometry) is a
head itself: it gets a tap like a GPU Mesh node and is drawn and made real the same way.
"""

from __future__ import annotations

import hashlib

import bpy

from . import chain

CHAINS: dict[str, dict] = {}
MESH_HEADS: set[str] = set()          # GPU Stage sources that head a mesh chain
STAGE_HEADS: dict[str, list] = {}     # stage source name -> pipeline names it belongs to
BASE: dict[str, str] = {}             # branch pipeline name -> its head source name
BRANCHES: dict[str, list] = {}        # head source name -> its branch pipeline names
_cache: dict[str, tuple] = {}         # head name -> (key, Composite)
GPU_KINDS = ('MESH', 'PARTICLES', 'DEFORM')


def ekind(obj):
    """The kind a source acts as: a GPU Stage heading a mesh chain acts as a GPU Mesh."""
    s = getattr(obj, "codenodes", None)
    if s is None:
        return None
    name = BASE.get(obj.name, obj.name)          # a branch acts as its head does
    if s.kind == 'STAGE' and name in MESH_HEADS:
        return 'DEFORM'
    return s.kind


def is_gpu(obj):
    return ekind(obj) in GPU_KINDS


def heads_of(obj):
    return STAGE_HEADS.get(obj.name, [])


def users_of(provider):
    """The nodes a function provider's outputs are wired into (each node once)."""
    out = []
    for info in CHAINS.values():
        for (consumer, _inp), (prov, _exp) in info.get("funcs", {}).items():
            if prov == provider and consumer not in out:
                out.append(consumer)
    return out


def base_name(obj_or_name):
    """The head source a pipeline starts from: itself for a head, the head for a branch."""
    name = obj_or_name if isinstance(obj_or_name, str) else obj_or_name.name
    return BASE.get(name, name)


def base_obj(obj):
    """The head source object of a pipeline (the object itself unless it's a branch)."""
    name = BASE.get(obj.name)
    if name is None:
        return obj
    return bpy.data.objects.get(name) or obj


def is_branch(obj):
    return obj.name in BASE


def pipelines_of(head_name):
    """Every pipeline starting from a head: its own name first, then its branches."""
    return [head_name] + list(BRANCHES.get(head_name, []))


def _text(obj):
    table = obj.get("cn_list_table")         # a native list wired into a code node (gn_link list taps)
    if table is not None:
        return table
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

    names = [info.get("source", head.name)] + list(info.get("stages", []))
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
    h.update(repr((ekind(head), info.get("source"), info.get("stages"),
                   sorted(info.get("funcs", {}).items()))).encode())
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
    """Called by the Geometry Nodes sync. Returns the pipeline names whose chain changed."""
    changed = {n for n in set(chains) | set(CHAINS) if chains.get(n) != CHAINS.get(n)}
    changed |= (set(mesh_heads) ^ MESH_HEADS)
    CHAINS.clear()
    CHAINS.update(chains)
    MESH_HEADS.clear()
    MESH_HEADS.update(mesh_heads)
    BASE.clear()
    BRANCHES.clear()
    for name, info in chains.items():
        src = info.get("source", name)
        if src != name:
            BASE[name] = src
            BRANCHES.setdefault(src, []).append(name)
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
