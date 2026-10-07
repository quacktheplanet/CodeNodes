"""Run a windowed CodeNodes test in background Blender (5.2 or later, which can use the GPU without a window):

    blender -b --factory-startup --python tests/headless.py -- tests/test_blender.py

The windowed tests drive themselves from bpy.app.timers, which never fire in background mode. This
starts the GPU with gpu.init(), stands in for the timer queue, runs the test, then calls its timers
in order (updating the depsgraph between calls, as Blender's event loop does) until the test quits.
Checks that need a real window (viewport drawing, screenshots, key presses) can't run this way.
"""
import heapq
import itertools
import os
import runpy
import sys
import time
import traceback

import bpy
import gpu

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not argv:
    print("FAIL: usage: blender -b --factory-startup --python tests/headless.py -- <test script>", flush=True)
    sys.exit(1)
script = os.path.abspath(argv[0])
if not hasattr(gpu, "init"):
    print(f"FAIL: Blender {bpy.app.version_string} has no GPU in background mode (needs 5.2 or later)", flush=True)
    sys.exit(1)
gpu.init()
print(f"headless: {gpu.platform.renderer_get()} ({gpu.platform.backend_type_get()})", flush=True)

_queue = []                          # (due, order, function)
_order = itertools.count()
_live = set()


def _register(function, first_interval=0.0, persistent=False):
    _live.add(function)
    heapq.heappush(_queue, (time.perf_counter() + first_interval, next(_order), function))


def _unregister(function):
    _live.discard(function)


bpy.app.timers.register = _register
bpy.app.timers.unregister = _unregister
bpy.app.timers.is_registered = lambda function: function in _live
_quit = []


class _Wm:
    """bpy.ops.wm with quit_blender caught (it ends the timer loop; the add-on's own timers never stop)
    and redraw_timer a no-op (there is no window to redraw)."""
    def __init__(self, ops):
        self._ops = ops

    def __getattr__(self, name):
        return getattr(self._ops, name)

    def quit_blender(self, *args, **kwargs):
        _quit.append(True)
        return {'FINISHED'}

    def redraw_timer(self, *args, **kwargs):
        return {'FINISHED'}


bpy.ops.wm = _Wm(bpy.ops.wm)

sys.argv = [script] + argv[1:]
runpy.run_path(script, run_name="__main__")

deadline = time.perf_counter() + float(os.environ.get("CODENODES_HEADLESS_TIMEOUT", "900"))
while _queue and not _quit:
    due, _, function = heapq.heappop(_queue)
    if function not in _live:
        continue
    now = time.perf_counter()
    if now > deadline:
        print("FAIL: headless run timed out", flush=True)
        break
    if due > now:
        time.sleep(due - now)
    try:
        bpy.context.view_layer.update()
        again = function()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        again = None
    if again is None:
        _live.discard(function)
    else:
        heapq.heappush(_queue, (time.perf_counter() + again, next(_order), function))
