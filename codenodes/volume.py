"""Volume output: density code on the GPU -> OpenVDB -> a Blender Volume object.

Write ``float density(vec3 p)`` (0 is empty, 1 is thick) and get smoke, cloud or
nebula geometry that EEVEE and Cycles render natively. Volumes are file-backed, so
this always writes a .vdb; an animation is a numbered sequence that Blender plays
by itself, with no add-on and no GPU at render time.
"""

from __future__ import annotations

import os

import numpy as np

from . import cache, sampler
from .sdf_code import DENSITY, SdfCodeError

GRID_NAME = "density"

TEMPLATE = """\
// Volume: return how thick the smoke is at p. 0 is empty, ~1 is solid.
// Keep it at 0 near the edge of the bounds, or you get a visible box.
// Helpers: noise3 fbm3 sdSphere sdBox length smoothstep. Time: uTime.
// @param radius 1.5 0.3 3.0
// @param scale 1.9 0.2 6.0
// @param wisp 0.9 0.0 1.5

float density(vec3 p) {
  float fade = smoothstep(radius, radius * 0.25, length(p));  // 1 in the middle, 0 by `radius`
  float swirl = fbm3(p * scale + vec3(0.0, 0.0, uTime * 0.25));
  float wisps = smoothstep(0.62 - 0.3 * wisp, 0.66, swirl);   // contrast: holes and filaments
  return clamp(fade * wisps, 0.0, 1.0);
}
"""


def _openvdb():
    try:
        import openvdb
        return openvdb
    except ImportError as exc:                      # pragma: no cover - depends on the build
        raise SdfCodeError("this Blender has no bundled 'openvdb' module, so volumes can't be "
                           "written here") from exc


def write_vdb(path, grid_values, lo, hi, name=GRID_NAME, fog=True):
    """Write a [z, y, x] numpy array as an OpenVDB grid placed between lo and hi."""
    vdb = _openvdb()
    values = np.ascontiguousarray(np.nan_to_num(grid_values, nan=0.0, posinf=0.0, neginf=0.0),
                                  dtype=np.float32)
    nz, ny, nx = values.shape
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    voxel = (hi - lo) / np.maximum(np.array([nx, ny, nz]) - 1, 1)
    grid = vdb.FloatGrid(background=0.0)
    grid.copyFromArray(np.ascontiguousarray(values.transpose(2, 1, 0)))   # OpenVDB indexes [x, y, z]
    # rows scale, last row translates (OpenVDB multiplies row vectors)
    grid.transform = vdb.createLinearTransform(matrix=[
        [voxel[0], 0.0, 0.0, 0.0],
        [0.0, voxel[1], 0.0, 0.0],
        [0.0, 0.0, voxel[2], 0.0],
        [lo[0], lo[1], lo[2], 1.0],
    ])
    grid.name = name
    try:
        grid.gridClass = vdb.GridClass.FOG_VOLUME if fog else vdb.GridClass.LEVEL_SET
    except Exception:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    vdb.write(path, grids=[grid])
    return os.path.getsize(path)


def compute(source, lo, hi, resolution, time_s=0.0, frame=0.0, values=None):
    """(density grid [z, y, x], stats) from density code, on the GPU."""
    return sampler.sample(source, lo, hi, resolution, time_s, frame, values, kind=DENSITY)


def smoke_material(name="CodeNodes Smoke"):
    """A Principled Volume material, so the volume shows up without extra setup.

    Emission is left at zero on purpose. Principled Volume does **not** multiply
    emission by the density grid, so a constant Emission Strength makes the whole
    bounding box glow as a solid cube. To make a volume glow, feed an Attribute
    node reading "density" into Emission Strength.
    """
    import bpy
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    vol = tree.nodes.new("ShaderNodeVolumePrincipled")
    vol.location = (0, 0)
    vol.inputs["Density"].default_value = 3.0
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    tree.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def ensure_object(name, filepath, lo, hi):
    """Create or update the Volume object that shows a .vdb file."""
    import bpy
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != 'VOLUME':
        raise SdfCodeError(f"an object named '{name}' exists and isn't a volume")
    if obj is None:
        data = bpy.data.volumes.new(name)
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(smoke_material())
    obj.data.filepath = bpy.path.relpath(filepath) if bpy.data.filepath else filepath
    obj.data.is_sequence = False
    try:                       # grids are read lazily; load now so the file is shown straight away
        obj.data.grids.load()
    except Exception:
        pass
    return obj


def build(name, source, lo=(-2, -2, -2), hi=(2, 2, 2), resolution=96, time_s=0.0, frame=0,
          values=None):
    """One frame: sample the density, write a .vdb, point a Volume object at it."""
    grid, stats = compute(source, lo, hi, resolution, time_s, frame, values)
    directory = cache.cache_dir(name)
    path = f"{directory}/{cache._slug(name)}_{int(frame):04d}.vdb"
    stats["bytes"] = write_vdb(path, grid, lo, hi)
    stats["filepath"] = path
    stats["max_density"] = float(grid.max())
    obj = ensure_object(name, path, lo, hi)
    return obj, stats


def bake_sequence(name, source, lo, hi, resolution, start, end, values=None, progress=None):
    """Write a numbered .vdb per frame and let Blender play the sequence natively."""
    import bpy
    scene = bpy.context.scene
    start, end = int(start), int(end)
    if end < start:
        raise SdfCodeError(f"the frame range is empty ({start} to {end})")
    directory = cache.cache_dir(name)
    for old in os.listdir(directory):
        if old.endswith(".vdb"):
            os.remove(os.path.join(directory, old))
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    here = scene.frame_current
    total = 0
    obj = None
    try:
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            grid, _ = compute(source, lo, hi, resolution, (frame - scene.frame_start) / fps, frame, values)
            path = f"{directory}/{cache._slug(name)}_{frame:04d}.vdb"
            total += write_vdb(path, grid, lo, hi)
            if progress is not None:
                progress(frame - start + 1, end - start + 1)
    finally:
        scene.frame_set(here)
    obj = ensure_object(name, f"{directory}/{cache._slug(name)}_{start:04d}.vdb", lo, hi)
    data = obj.data
    data.is_sequence = True
    data.frame_start = start
    data.frame_offset = start - 1
    data.frame_duration = end - start + 1
    data.sequence_mode = 'EXTEND'
    return obj, {"dir": directory, "frames": end - start + 1, "bytes": total, "start": start, "end": end}
