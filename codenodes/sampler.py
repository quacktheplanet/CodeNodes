"""Sample user SDF code on a 3D grid with a GPU compute shader.

The grid's z-slices are laid out as tiles of one 2D float texture (reading a 3D
texture back only returns its first slice). Work is dispatched in slabs of
slices; the first slab is timed so an expensive shader is refused before it can
stall the GPU for long. Needs Blender with a window: background mode has no GPU.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time

import numpy as np

from .sdf_code import SdfCodeError, full_source, parse_params, user_errors

MIN_RES, MAX_RES = 8, 512
MAX_GPU_BYTES = 1 << 30            # refuse grids over 1 GiB
GROUP = 8
TIME_BUDGET_S = 30.0               # refuse jobs estimated to take longer than this
SLAB_TARGET_S = 0.25               # aim for GPU submissions about this long

_cache: dict[str, tuple] = {}      # source hash -> (shader, params)


class GpuUnavailable(SdfCodeError):
    pass


def _require_gpu():
    import bpy
    if bpy.app.background:
        raise GpuUnavailable("Code -> Mesh needs the GPU, and Blender has no GPU access in background (-b) mode. "
                             "Run Blender with a window.")


def grid_dims(lo, hi, resolution):
    """Samples per axis: `resolution` along the longest side, the others in proportion."""
    resolution = int(resolution)
    if not MIN_RES <= resolution <= MAX_RES:
        raise SdfCodeError(f"resolution must be between {MIN_RES} and {MAX_RES} (got {resolution})")
    size = np.asarray(hi, float) - np.asarray(lo, float)
    if (size <= 0).any():
        raise SdfCodeError(f"bounds are empty or inverted: min {tuple(lo)}, max {tuple(hi)}")
    longest = size.max()
    return tuple(int(max(2, round(resolution * s / longest))) for s in size)   # (nx, ny, nz)


def _compile_capturing(info):
    """Compile, capturing the driver's log (Blender prints it to the C-level console)."""
    import gpu
    tmp = tempfile.TemporaryFile(mode="w+b")
    saved = [os.dup(1), os.dup(2)]
    try:
        os.dup2(tmp.fileno(), 1)
        os.dup2(tmp.fileno(), 2)
        try:
            shader, err = gpu.shader.create_from_info(info), None
        except Exception as exc:  # compile failure is reported, never raised through
            shader, err = None, exc
    finally:
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in saved:
            os.close(fd)
    tmp.seek(0)
    log = tmp.read().decode("utf-8", "replace")
    tmp.close()
    return shader, err, log


def get_shader(source):
    """(shader, params) for this source, compiled once and cached."""
    _require_gpu()
    key = hashlib.sha1(source.encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    import gpu
    params = parse_params(source)
    code, offset = full_source(source, params)
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source("struct CNParams { vec4 v[64]; };")
    info.uniform_buf(0, "CNParams", "cnParams")
    info.image(0, 'R32F', 'FLOAT_2D', "cnGrid", qualifiers={'WRITE'})
    info.push_constant('VEC3', "cnLo")
    info.push_constant('VEC3', "cnHi")
    info.push_constant('IVEC3', "cnDims")
    info.push_constant('IVEC2', "cnSlab")
    info.push_constant('INT', "cnTiles")
    info.push_constant('FLOAT', "uTime")
    info.push_constant('FLOAT', "uFrame")
    info.local_group_size(GROUP, GROUP, 1)
    info.compute_source(code)
    shader, err, log = _compile_capturing(info)
    if shader is None:
        raise SdfCodeError("the code didn't compile:\n" + user_errors(log, offset, source))
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = (shader, params)
    return shader, params


def sample(source, lo, hi, resolution, time_s=0.0, frame=0.0, values=None, budget_s=TIME_BUDGET_S):
    """Evaluate ``sdf`` on the grid. Returns (vol[z, y, x] float32, stats dict)."""
    import gpu
    shader, params = get_shader(source)
    nx, ny, nz = grid_dims(lo, hi, resolution)
    tiles_x = math.ceil(math.sqrt(nz))
    tiles_y = math.ceil(nz / tiles_x)
    width, height = nx * tiles_x, ny * tiles_y
    max_tex = gpu.capabilities.max_texture_size_get()
    if max(width, height) > max_tex:
        raise SdfCodeError(f"grid {nx}x{ny}x{nz} is too big for this GPU's textures; lower the resolution")
    if width * height * 4 > MAX_GPU_BYTES:
        raise SdfCodeError(f"grid {nx}x{ny}x{nz} would need over 1 GiB; lower the resolution")

    def uniform(setter, name, value):
        # The compiler drops uniforms the code never reads (a constant sdf doesn't
        # read the position), and setting a dropped uniform raises.
        try:
            setter(name, value)
        except ValueError:
            pass

    uniform(shader.uniform_float, "cnLo", tuple(map(float, lo)))
    uniform(shader.uniform_float, "cnHi", tuple(map(float, hi)))
    uniform(shader.uniform_int, "cnDims", (nx, ny, nz))
    uniform(shader.uniform_float, "uTime", float(time_s))
    uniform(shader.uniform_float, "uFrame", float(frame))
    values = values or {}
    slots = np.zeros(256, np.float32)
    for i, prm in enumerate(params):
        slots[i] = float(values.get(prm.name, prm.default))
    ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 256, slots.tolist()))
    shader.uniform_block("cnParams", ubo)

    def run(target, tiles, w, h, z0, z1):
        shader.image("cnGrid", target)
        uniform(shader.uniform_int, "cnTiles", tiles)
        uniform(shader.uniform_int, "cnSlab", (z0, z1))
        gpu.compute.dispatch(shader, math.ceil(w / GROUP), math.ceil(h / GROUP), 1)

    # Time one slice on a one-tile scratch texture (reading it back waits for the GPU).
    t0 = time.perf_counter()
    probe = gpu.types.GPUTexture((nx, ny), format='R32F')
    run(probe, 1, nx, ny, 0, 1)
    probe.read()
    per_slice = max(time.perf_counter() - t0, 1e-5)
    del probe
    estimate = per_slice * nz
    if estimate > budget_s:
        raise SdfCodeError(f"this code is too slow at this resolution (about {estimate:.3g} s estimated, "
                           f"limit {budget_s:g} s); lower the resolution or simplify the code")

    tex = gpu.types.GPUTexture((width, height), format='R32F')
    slab = max(1, int(SLAB_TARGET_S / per_slice))
    for z0 in range(0, nz, slab):
        run(tex, tiles_x, width, height, z0, min(nz, z0 + slab))
    buf = tex.read()
    buf.dimensions = width * height
    atlas = np.frombuffer(buf, dtype=np.float32).reshape(height, width)
    vol = (atlas.reshape(tiles_y, ny, tiles_x, nx).transpose(0, 2, 1, 3)
           .reshape(tiles_y * tiles_x, ny, nx)[:nz].copy())
    stats = {"dims": (nx, ny, nz), "gpu_s": time.perf_counter() - t0, "slab": slab,
             "estimate_s": estimate}
    return vol, stats
