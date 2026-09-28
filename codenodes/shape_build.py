"""Putting a built Solid into Blender: quads and caps, UVs, and sharp edges.

Kept apart from `shapes/` so that package stays free of bpy and can be tested on its own.
"""

from __future__ import annotations

import math

import numpy as np

from .shapes import ShapeError, parse, solids


def _recalculate_normals(me):
    """Point every face outward.

    The kernel gets the winding right on its own, but this is cheap and makes a wrong
    one impossible — and a solid that is inside out bevels outward and breaks booleans.
    """
    import bmesh
    if not me.polygons:
        return
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()


def set_uvs(layer, coords):
    """Write UVs, whichever spelling this Blender uses ('vector' in 5.x, 'uv' before)."""
    flat = np.ascontiguousarray(coords, np.float32).ravel()
    for holder, attr in ((getattr(layer, "uv", None), "vector"), (layer.data, "uv")):
        if holder is None:
            continue
        try:
            holder.foreach_set(attr, flat)
            return True
        except (AttributeError, TypeError, RuntimeError):
            continue
    return False


def get_uvs(layer, count):
    flat = np.empty(count * 2, np.float32)
    for holder, attr in ((getattr(layer, "uv", None), "vector"), (layer.data, "uv")):
        if holder is None:
            continue
        try:
            holder.foreach_get(attr, flat)
            return flat.reshape(-1, 2)
        except (AttributeError, TypeError, RuntimeError):
            continue
    return np.zeros((count, 2), np.float32)


def fill_mesh(me, solid, smooth=True, uv_name="UVMap"):
    """Replace a mesh datablock's geometry with a Solid, in place."""
    me.clear_geometry()
    if solid.empty:
        me.update()
        return me
    polygons = solid.polygons()
    me.from_pydata([tuple(v) for v in solid.verts.astype(float)], [], polygons)
    me.validate(verbose=False)
    _recalculate_normals(me)

    if solid.uvs is not None and len(solid.uvs) == len(solid.faces):
        layer = me.uv_layers.get(uv_name) or me.uv_layers.new(name=uv_name)
        coords = np.zeros((len(me.loops), 2), np.float32)
        quad_loops = 4 * len(solid.faces)
        coords[:quad_loops] = solid.uvs.reshape(-1, 2)[:quad_loops]
        set_uvs(layer, coords)

    if smooth:
        me.shade_smooth()
    if solid.sharp:
        index = {}
        for edge in me.edges:
            a, b = edge.vertices
            index[(a, b) if a < b else (b, a)] = edge
        for pair in solid.sharp:
            edge = index.get(pair)
            if edge is not None:
                edge.use_edge_sharp = True
    me.update()
    return me


def apply_bevel(me, options):
    """Round the sharp edges with bmesh, baking it into the mesh."""
    import bmesh
    width = float(options.get("width", 0.0))
    if width <= 0:
        return me
    bm = bmesh.new()
    bm.from_mesh(me)
    limit = math.radians(float(options.get("angle", 30.0)))
    edges = [e for e in bm.edges if len(e.link_faces) == 2 and e.calc_face_angle(0.0) >= limit]
    if edges:
        verts = list({v.index: v for e in edges for v in e.verts}.values())
        try:
            bmesh.ops.bevel(bm, geom=edges + verts,
                            offset=width, offset_type='OFFSET',
                            segments=int(options.get("segments", 2)),
                            profile=float(options.get("shape", 0.5)),
                            affect='EDGES', clamp_overlap=True, loop_slide=True)
        except Exception as exc:
            bm.free()
            raise ShapeError(f"the bevel failed ({exc}); try a smaller width") from None
    bm.to_mesh(me)
    bm.free()
    me.update()
    return me


def _temp_object(name, solid, smooth):
    import bpy
    me = bpy.data.meshes.new(f"{name}_tmp")
    fill_mesh(me, solid, smooth)
    obj = bpy.data.objects.new(f"{name}_tmp", me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _measure_mesh(me):
    """(volume, (min corner, max corner)) of a mesh; (0, None) when it's empty."""
    import bmesh
    if not len(me.vertices):
        return 0.0, None
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        volume = abs(bm.calc_volume(signed=False))
    finally:
        bm.free()
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return volume, (co.min(axis=0), co.max(axis=0))


def _measure_solid(solid):
    if not len(solid.verts):
        return 0.0, None
    volume = abs(float(solids._signed_volume(solid))) / 6.0
    verts = np.asarray(solid.verts, np.float64)
    return volume, (verts.min(axis=0), verts.max(axis=0))


def _weld_slivers(me, distance=1e-5):
    """Merge vertices a boolean left a hair apart (where two parts' seams line up), which
    otherwise become zero-length edges that the next boolean trips over."""
    import bmesh
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=distance)
        bm.to_mesh(me)
    finally:
        bm.free()


def check_boolean(operation, before, cutter, after):
    """Whether a boolean result is believable, from volumes and bounding boxes. Returns
    "" or what's wrong. (EXACT booleans on a solid that isn't closed, such as a sweep whose
    two end caps overlap, can return a fragment without complaining.)"""
    (v0, b0), (vc, bc), (v1, b1) = before, cutter, after
    if b1 is None:
        return "the result is empty"
    tol = 0.03 * max(v0, vc, 1e-12)
    size = float(np.max(np.maximum(b0[1], bc[1]) - np.minimum(b0[0], bc[0])))
    slack = 0.02 * size + 1e-6
    if operation == 'UNION':
        if v1 < max(v0, vc) - tol:
            return f"the result ({v1:.4g} m³) is smaller than one of the pieces ({max(v0, vc):.4g} m³)"
        want_lo, want_hi = np.minimum(b0[0], bc[0]), np.maximum(b0[1], bc[1])
        if np.any(b1[0] > want_lo + slack) or np.any(b1[1] < want_hi - slack):
            return "the result doesn't reach as far as its pieces do"
    elif operation == 'DIFFERENCE':
        if v1 < v0 - vc - tol:
            return (f"it removed more ({v0 - v1:.4g} m³) than the cutting part holds ({vc:.4g} m³)")
        if v1 > v0 + tol:
            return "cutting made it bigger"
        if np.any(b1[0] < b0[0] - slack) or np.any(b1[1] > b0[1] + slack):
            return "cutting made it reach further than before"
    elif operation == 'INTERSECT':
        if v1 > min(v0, vc) + tol:
            return "the overlap came out bigger than either piece"
    return ""


def _combine(parts, smooth):
    """Apply the parts' add / subtract / intersect using Blender's boolean solver.

    bmesh has no boolean operator, so this goes through the Boolean modifier on throwaway
    objects and reads the evaluated result back, one operation at a time, and checks each
    result: a boolean that silently returns garbage becomes an error naming the part.
    """
    import bpy
    adds = [p for p in parts if p.mode == "add"]
    others = [p for p in parts if p.mode != "add"]
    if not adds:
        raise ShapeError("there is nothing to subtract from: give the shape a part that adds")
    # The adds are unioned rather than merely put together: parts usually overlap (a
    # thread into its shank), and a later cut through overlapping shells goes wrong.
    base = _temp_object("cn_shape", adds[0].solid, smooth)
    temporary = [base]
    current = _measure_solid(adds[0].solid)
    verbs = {'UNION': "adding", 'DIFFERENCE': "subtracting", 'INTERSECT': "intersecting with"}
    try:
        operations = ([(p, 'UNION') for p in adds[1:]]
                      + [(p, 'DIFFERENCE' if p.mode == "subtract" else 'INTERSECT') for p in others])
        for part, operation in operations:
            cutter = _temp_object(f"cn_{part.name}", part.solid, smooth)
            temporary.append(cutter)
            mod = base.modifiers.new(f"bool_{part.name}", 'BOOLEAN')
            mod.operation = operation
            mod.object = cutter
            # EXACT, not MANIFOLD: Blender 5's Manifold solver refuses these solids
            # ("non-manifold inputs") even when every edge has exactly two faces —
            # the triangle fans at a lathe's poles seem to be enough to put it off.
            mod.solver = 'EXACT'
            # deliberately not hidden: a hidden object is left out of the depsgraph, and
            # then the boolean has nothing to cut with
            depsgraph = bpy.context.evaluated_depsgraph_get()
            depsgraph.update()
            result = bpy.data.meshes.new_from_object(base.evaluated_get(depsgraph))
            _weld_slivers(result)
            base.modifiers.remove(mod)
            old, base.data = base.data, result
            if old.users == 0:
                bpy.data.meshes.remove(old)
            after = _measure_mesh(result)
            problem = check_boolean(operation, current, _measure_solid(part.solid), after)
            if problem:
                raise ShapeError(
                    f"part '{part.name}': {verbs[operation]} it went wrong ({problem}). This usually "
                    "means one of the solids isn't closed, e.g. a profile that crosses itself or "
                    "two parts that only touch along a face")
            current = after
            temporary.remove(cutter)
            data = cutter.data
            bpy.data.objects.remove(cutter, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)
        final = base.data
        base.data = bpy.data.meshes.new("cn_shape_empty")
        return final
    finally:
        for obj in temporary:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)


def build_into(me, source, values=None, smooth=True):
    """The whole pipeline: parse, build the parts, cut them, bevel, into `me`."""
    import bpy
    shape = parse(source)
    parts = shape.build_parts(values or {})
    options = shape.finish_options(values or {})
    if options.get("smooth"):
        smooth = True

    for part in parts:                       # a per-part bevel, before anything is cut
        if part.bevel:
            tmp = bpy.data.meshes.new(f"cn_bevel_{part.name}")
            try:
                fill_mesh(tmp, part.solid, smooth)
                apply_bevel(tmp, part.bevel)
                part.solid = _solid_from_mesh(tmp)
            finally:
                bpy.data.meshes.remove(tmp)

    if any(p.mode != "add" for p in parts):
        combined = _combine(parts, smooth)
        me.clear_geometry()
        _copy_mesh(combined, me)
        bpy.data.meshes.remove(combined)
    else:
        fill_mesh(me, solids.join([p.solid for p in parts]), smooth)

    if options.get("bevel"):
        apply_bevel(me, options["bevel"])
    if smooth:
        me.shade_smooth()
        # A boolean throws away the sharp edges we marked, so a hex head would come back
        # shaded as a blob. Re-mark by angle: smooth along curves, crisp at real corners.
        mark_sharp_by_angle(me, options.get("sharp_angle", 30.0))
    me.update()
    return me


def mark_sharp_by_angle(me, degrees=30.0):
    """Mark every edge where the surface really turns, so curves stay smooth and
    corners stay crisp."""
    import bmesh
    if not me.polygons:
        return me
    limit = math.radians(float(degrees))
    bm = bmesh.new()
    bm.from_mesh(me)
    for edge in bm.edges:
        if len(edge.link_faces) == 2 and edge.calc_face_angle(0.0) >= limit:
            edge.smooth = False
    bm.to_mesh(me)
    bm.free()
    return me


def _copy_mesh(source, target):
    verts = np.empty(len(source.vertices) * 3, np.float32)
    source.vertices.foreach_get("co", verts)
    polys = [tuple(p.vertices) for p in source.polygons]
    target.from_pydata(verts.reshape(-1, 3).tolist(), [], polys)
    for name in source.uv_layers.keys():
        layer = target.uv_layers.get(name) or target.uv_layers.new(name=name)
        set_uvs(layer, get_uvs(source.uv_layers[name], len(source.loops)))
    sharp_src = np.zeros(len(source.edges), bool)
    source.edges.foreach_get("use_edge_sharp", sharp_src)
    if sharp_src.any() and len(target.edges) == len(source.edges):
        target.edges.foreach_set("use_edge_sharp", sharp_src)
    target.update()
    return target


def _solid_from_mesh(me):
    """A Blender mesh back into a Solid, so a bevelled part can still be cut with."""
    verts = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", verts)
    quads, tris = [], []
    for poly in me.polygons:
        idx = tuple(poly.vertices)
        (quads if len(idx) == 4 else tris).append(idx if len(idx) in (3, 4) else idx[:4])
    sharp = set()
    for edge in me.edges:
        if edge.use_edge_sharp:
            a, b = edge.vertices
            sharp.add((a, b) if a < b else (b, a))
    return solids.Solid(verts.reshape(-1, 3),
                        np.array(quads, np.int32).reshape(-1, 4) if quads else np.zeros((0, 4), np.int32),
                        None, sharp,
                        np.array(tris, np.int32).reshape(-1, 3) if tris else np.zeros((0, 3), np.int32))


def profiles_to_curve(source, values=None, name="Profile"):
    """Every profile in a description as a Blender curve, laid out in the XZ plane.

    A lathe profile drawn as a curve is much easier to judge by eye than a column of
    numbers, and it can be reshaped by hand to work out what the maths should say.
    """
    import bpy
    shape = parse(source)
    scope = shape.values(values or {})
    from .shapes import language
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    found = 0
    for part_name, _mode, ops in shape.parts:
        for word, rest, _line in ops:
            if word != "profile":
                continue
            profile = language._build_profile(rest, scope)
            points, _sharp = profile.finish()
            spline = data.splines.new('POLY')
            spline.points.add(len(points) - 1)
            flat = np.zeros((len(points), 4), np.float32)
            flat[:, 0] = points[:, 0]           # profile x  -> world x (the radius)
            flat[:, 2] = points[:, 1]           # profile y  -> world z (up)
            flat[:, 3] = 1.0
            spline.points.foreach_set("co", flat.ravel())
            spline.use_cyclic_u = profile.closed
            found += 1
    if not found:
        bpy.data.curves.remove(data)
        raise ShapeError("this description has no profile to draw")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj, found


def build(source, values=None):
    """Text -> Solid, adding every part. Booleans and bevels need Blender: see build_into."""
    return parse(source).build(values or {})


def params_of(source):
    """The sliders a shape declares, as (name, default, min, max)."""
    return parse(source).params


def mesh_stats(me):
    """What ended up in Blender, after any booleans and bevels."""
    quads = sum(1 for p in me.polygons if p.loop_total == 4)
    sharp = sum(1 for e in me.edges if e.use_edge_sharp)
    if len(me.vertices):
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        size = (co.max(axis=0) - co.min(axis=0)).tolist()
    else:
        size = [0.0, 0.0, 0.0]
    return {"verts": len(me.vertices), "faces": len(me.polygons), "quads": quads,
            "tris": len(me.polygons) - quads, "sharp_edges": sharp,
            "size": [round(float(v), 4) for v in size]}


def stats(solid):
    size = (solid.verts.max(axis=0) - solid.verts.min(axis=0)) if len(solid.verts) else np.zeros(3)
    return {"verts": len(solid.verts), "quads": len(solid.faces), "tris": len(solid.tris),
            "sharp_edges": len(solid.sharp), "size": [round(float(v), 4) for v in size]}


__all__ = ["fill_mesh", "build", "params_of", "stats", "ShapeError"]
