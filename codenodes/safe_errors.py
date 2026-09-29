"""Printing errors without crashing Blender.

Python 3.12+ adds "Did you mean …?" to an AttributeError by calling dir() on the object the attribute
was looked up on. When that object is a Blender struct that has since been freed (a node socket
removed when a group's interface was rebuilt, say), dir() reads freed memory and takes Blender down
with an access violation (seen in the wild: RNA_property_type ← pyrna_struct_dir). So before anything
prints a traceback, the Blender structs are taken off the exception chain.

`report()` is used instead of traceback.print_exc() everywhere in CodeNodes, and while CodeNodes is
enabled `sys.excepthook` is wrapped the same way, which covers errors Blender itself prints (from
handlers, timers, draw functions and property updates).
"""
import sys
import traceback

try:
    import bpy
    _STRUCT = (bpy.types.bpy_struct,)
except Exception:                     # outside Blender (unit tests)
    _STRUCT = ()


def _is_struct(value):
    return bool(_STRUCT) and isinstance(value, _STRUCT)


def scrub(exc):
    """Drop Blender structs from `exc` and everything chained to it, so no suggestion code touches them."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, AttributeError):
            try:
                if _is_struct(getattr(exc, "obj", None)):
                    exc.obj = None
            except Exception:
                pass
        for sub in getattr(exc, "exceptions", ()) or ():   # exception groups
            scrub(sub)
        exc = exc.__cause__ or exc.__context__
    return exc


def report(exc=None, prefix="CodeNodes"):
    """Print the current (or given) exception safely."""
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        return
    scrub(exc)
    try:
        print(f"{prefix}: an error was caught (details below)", file=sys.stderr)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    except Exception:
        try:
            print(f"{prefix}: {type(exc).__name__}: {exc}", file=sys.stderr)
        except Exception:
            pass


_previous = [None]


def _hook(etype, value, tb):
    scrub(value)
    (_previous[0] or sys.__excepthook__)(etype, value, tb)


def register():
    if sys.excepthook is not _hook:
        _previous[0] = sys.excepthook
        sys.excepthook = _hook


def unregister():
    if sys.excepthook is _hook:
        sys.excepthook = _previous[0] or sys.__excepthook__
    _previous[0] = None
