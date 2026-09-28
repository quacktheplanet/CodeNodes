"""The one door GPU work goes through, so it can be refused while a render runs.

Running GPU code while Blender's renderer works crashes Blender, so every compute dispatch and
every live draw asks `allowed()` first. The counters let tests prove that nothing ran during a
render: `stats()['during_render']` must stay 0.
"""

from __future__ import annotations

_stats = {"dispatches": 0, "draws": 0, "refused": 0, "during_render": 0}


def rendering():
    from . import live
    return live._rendering()


def allowed():
    """False while any render runs (the work is skipped and counted as refused)."""
    if rendering():
        _stats["refused"] += 1
        return False
    return True


def dispatch(shader, x, y, z=1):
    import gpu
    if rendering():                    # belt and braces: callers should have asked allowed()
        _stats["during_render"] += 1
        return
    _stats["dispatches"] += 1
    gpu.compute.dispatch(shader, x, y, z)


def note_draw():
    if rendering():
        _stats["during_render"] += 1
    else:
        _stats["draws"] += 1


def stats():
    return dict(_stats)


def reset_stats():
    for k in _stats:
        _stats[k] = 0
