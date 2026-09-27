"""Ready-made, tested Geometry Nodes capabilities for building worlds.

Each one is an ordinary node group — open it in the node editor and it is all there —
with its controls as group inputs, so they show up as sliders on the modifier. An
assistant composes these rather than writing every setup from nothing: they are tested,
seeded (the same seed gives the same result) and documented through their inputs.

    terrain          rolling ground from noise, with an optional winding canyon
    scatter          trees, rocks, props over a surface, kept off steep slopes
    wall             a solid wall along a curve, with doorways and posts
    path_bridge      a walkway along a curve that hugs the ground and becomes a
                     bridge — railings, posts, pillars — wherever the ground drops away
    along_curve      things beside a curve at a steady spacing: lamps, benches, fences
    wall_network     walls along every spline of a curve, joined at corners and T's
    rooms            a floor plan of closed outlines -> floors, walls, doorways, windows
    roof             flat, gable or hip, on top of whatever the object already has
    stairs           stairs or a ramp along a curve between its two end heights
    water            a water surface along a curve (a river carved with terrain's Carve)

Add one by writing a function that returns a Graph and decorating it with
@capability("name"); tests/test_gn_library.py builds every one and checks it.
"""

from __future__ import annotations

from .builder import Graph

CAPABILITIES = {}
DOWN = (0.0, 0.0, -1.0)
UP = (0.0, 0.0, 1.0)


def capability(key):
    def register(fn):
        CAPABILITIES[key] = fn
        return fn
    return register


def graph(key):
    if key not in CAPABILITIES:
        raise KeyError(f"no capability called {key!r}. There is: {', '.join(sorted(CAPABILITIES))}")
    return CAPABILITIES[key]()


def manifest():
    return [dict(graph(key).manifest(), key=key) for key in sorted(CAPABILITIES)]


TAG = "codenodes_capability"


def build(key, name=None, warnings=None, refresh=True):
    """Make the node group for a capability, or rebuild it (refresh). Returns the tree.

    With refresh=False an existing group is used as it is, so changes someone made to it
    with nodes_edit survive. A group of that name that the library did not make is never
    touched.
    """
    from . import serialize
    g = graph(key)
    name = name or g.name
    import bpy
    existing = bpy.data.node_groups.get(name)
    if existing is not None:
        if existing.get(TAG) not in (key, None) or (existing.get(TAG) is None and existing.nodes):
            raise serialize.BuildError(
                f"there is already a node group called '{name}' that is not the {key} "
                "capability; pass another name")
        if not refresh and existing.get(TAG) == key:
            return existing
    tree = serialize.write(g.data(), name=name, warnings=warnings)
    tree[TAG] = key
    if hasattr(tree, "description"):
        tree.description = g.about
    return tree


# ---- pieces several capabilities share ---------------------------------------------------

def _pick_source(g, collection, stand_in, seed, offset):
    """A collection's objects if it has any, otherwise the stand-in; and a random pick."""
    instances = g.node("GeometryNodeCollectionInfo",
                       {"Collection": collection, "Separate Children": True,
                        "Reset Children": True})["Instances"]
    count = g.node("GeometryNodeAttributeDomainSize", {"Geometry": instances},
                   component="INSTANCES")["Instance Count"]
    has = g.compare("GREATER_THAN", count, 0, "INT")
    source = g.switch("GEOMETRY", has, stand_in, instances)
    pick = g.random("INT", 0, 100000, g.math("ADD", seed, offset))
    return source, has, pick


def _lift(g, geometry, z):
    return g.node("GeometryNodeTransform", {"Geometry": geometry,
                                            "Translation": g.xyz(0, 0, z)})["Geometry"]


def _material(g, geometry, material, selection=None):
    inputs = {"Geometry": geometry, "Material": material}
    if selection is not None:
        inputs["Selection"] = selection
    return g.node("GeometryNodeSetMaterial", inputs)["Geometry"]


def _upright(g, curve):
    """Curve normals pointing up, so profiles swept along it stay level."""
    return g.node("GeometryNodeSetCurveNormal", {"Curve": curve, "Mode": "Z Up"})["Curve"]


def _right_of(g, tangent):
    """The horizontal direction to the right of travel along a curve."""
    return g.vmath("NORMALIZE", g.vmath("CROSS_PRODUCT", tangent, UP))


def _ground_below(g, ground, position):
    """Cast down onto the ground from high above, so a curve drawn a little below the
    surface still finds it. Returns (is hit, ground height)."""
    ray = g.node("GeometryNodeRaycast", {"Target Geometry": ground,
                                         "Source Position": g.vmath("ADD", position,
                                                                    (0.0, 0.0, 1000.0)),
                                         "Ray Direction": DOWN, "Ray Length": 100000.0})
    return ray["Is Hit"], g.separate(ray["Hit Position"])["Z"]


def _walls(g, curve, thick, height, footing, ground_obj=None, extend=True):
    """Walls swept along every spline of a curve and unioned into one solid, so walls that
    meet at a corner, a T or a crossing join instead of overlapping.

    Returns (solid, path, base): the path is the curve laid flat at z = 0 with each
    point's base height captured as `base`, for placing doorways and posts on it.
    Open ends reach half a thickness past the drawn points, so two walls drawn to the
    same corner close it instead of leaving a notch.
    """
    # corners inside one spline: see `wall` for why they are rounded by half the thickness
    rounded = g.node("GeometryNodeFilletCurve",
                     {"Curve": curve, "Mode": "Poly", "Count": 4, "Limit Radius": True,
                      "Radius": g.math("MULTIPLY", thick, 0.5)})["Curve"]
    pos = g.position()
    here = g.separate(pos)
    base_z = here["Z"]
    dense = rounded
    if ground_obj is not None:
        ground = g.node("GeometryNodeObjectInfo", {"Object": ground_obj},
                        transform_space="RELATIVE")["Geometry"]
        faces = g.node("GeometryNodeAttributeDomainSize", {"Geometry": ground},
                       component="MESH")["Face Count"]
        grounded = g.compare("GREATER_THAN", faces, 0, "INT")
        dense = g.node("GeometryNodeSubdivideCurve",
                       {"Curve": rounded, "Cuts": g.switch("INT", grounded, 0, 12)})["Curve"]
        hit, ground_z = _ground_below(g, ground, pos)
        base_z = g.switch("FLOAT", g.logic("AND", grounded, hit), here["Z"], ground_z)
    based = g.node("GeometryNodeCaptureAttribute", {"Geometry": dense, "Base": base_z},
                   items={"capture_items": [{"name": "Base", "data_type": "FLOAT"}]})
    base = based["Base"]
    flat = g.node("GeometryNodeSetPosition",
                  {"Geometry": based["Geometry"],
                   "Position": g.xyz(here["X"], here["Y"], 0.0)})["Geometry"]
    if extend:
        start = g.node("GeometryNodeCurveEndpointSelection",
                       {"Start Size": 1, "End Size": 0})["Selection"]
        either = g.node("GeometryNodeCurveEndpointSelection",
                        {"Start Size": 1, "End Size": 1})["Selection"]
        open_end = g.logic("AND", either,
                           g.logic("NOT", g.node("GeometryNodeInputSplineCyclic")["Cyclic"]))
        reach = g.math("MULTIPLY", g.switch("FLOAT", start, 0.5, -0.5), thick)
        flat = g.node("GeometryNodeSetPosition",
                      {"Geometry": flat, "Selection": open_end,
                       "Offset": g.vmath("SCALE", g.node("GeometryNodeInputTangent")["Tangent"],
                                         scale=reach)})["Geometry"]
    path = _upright(g, flat)
    rect = g.node("GeometryNodeCurvePrimitiveQuadrilateral",
                  {"Width": thick, "Height": g.math("ADD", height, footing)})["Curve"]
    profile = g.node("GeometryNodeTransform",     # profile Y points down: see `wall`
                     {"Geometry": rect,
                      "Translation": g.xyz(0, g.math("MULTIPLY",
                                                     g.math("SUBTRACT", height, footing),
                                                     -0.5), 0)})["Geometry"]
    swept = g.node("GeometryNodeCurveToMesh", {"Curve": path, "Profile Curve": profile,
                                               "Fill Caps": True})["Mesh"]
    lifted = g.node("GeometryNodeSetPosition",
                    {"Geometry": swept, "Offset": g.xyz(0, 0, base)})["Geometry"]
    # EXACT is the solver that really unions overlapping pieces of one mesh; Manifold and
    # Float hand them back untouched (measured: the volume stays the sum of the parts)
    solid = g.node("GeometryNodeMeshBoolean", {"Mesh 2": lifted},
                   operation="UNION", solver="EXACT")["Mesh"]
    return solid, path, base


def _cutters(g, spots, rotation, size, lift):
    """Boxes of `size` on points (already at the wall's base), raised by `lift`, turned."""
    box = _lift(g, g.node("GeometryNodeMeshCube", {"Size": size})["Mesh"], lift)
    placed = g.node("GeometryNodeInstanceOnPoints", {"Points": spots, "Instance": box,
                                                     "Rotation": rotation})["Instances"]
    return g.node("GeometryNodeRealizeInstances", {"Geometry": placed})["Geometry"]


def _cut(g, solid, cutters):
    # Manifold for the difference: exact was seen to collapse a flat wall with doorways
    return g.node("GeometryNodeMeshBoolean", {"Mesh 1": solid, "Mesh 2": cutters},
                  operation="DIFFERENCE", solver="MANIFOLD")["Mesh"]


def _flat_shaded(g, mesh):
    return g.node("GeometryNodeSetShadeSmooth", {"Mesh": mesh, "Shade Smooth": False})["Mesh"]


# ---- the capabilities --------------------------------------------------------------------

@capability("terrain")
def terrain():
    g = Graph("CN Terrain", "Rolling ground from layered noise, with an optional canyon that "
                            "winds along the X axis. Seeded: the same seed gives the same land.")
    g.panel("Shape")
    size = g.input("Size", "float", 80.0, 1.0, 5000.0, "width and depth, in metres",
                   subtype="DISTANCE")
    res = g.input("Resolution", "int", 160, 2, 2000, "vertices along each side: more is "
                                                     "smoother and slower")
    height = g.input("Height", "float", 4.0, 0.0, 1000.0, "roughly how far hilltops rise above "
                                                          "hollows", subtype="DISTANCE")
    feature = g.input("Feature Size", "float", 22.0, 0.5, 5000.0, "roughly how far apart the "
                                                                  "hills are", subtype="DISTANCE")
    rough = g.input("Roughness", "float", 4.0, 0.0, 15.0, "small bumps on top of the big shapes")
    seed = g.input("Seed", "int", 0, 0, 1000000, "change it for different land")
    g.panel("Canyon")
    depth = g.input("Canyon Depth", "float", 0.0, 0.0, 1000.0, "0 for no canyon",
                    subtype="DISTANCE")
    width = g.input("Canyon Width", "float", 7.0, 0.1, 1000.0, subtype="DISTANCE")
    wander = g.input("Canyon Wander", "float", 5.0, 0.0, 1000.0, "how far it winds from side "
                                                                 "to side", subtype="DISTANCE")
    steep = g.input("Canyon Steepness", "float", 6.0, 2.0, 20.0,
                    "2 is a soft valley; higher gives sheer walls and a flat floor")
    g.panel("Surface")
    material = g.input("Material", "material")
    cliff_mat = g.input("Cliff Material", "material",
                        about="for ground steeper than Cliff Angle — rock on canyon walls")
    cliff_angle = g.input("Cliff Angle", "float", 0.65, 0.0, 1.5708, subtype="ANGLE")
    g.panel("Carve", "a river bed or a road cut along a curve", closed=True)
    carve_obj = g.input("Carve Along", "object",
                        about="a curve: the ground is carved to its height minus Carve Depth, "
                              "and eased back up the banks")
    carve_w = g.input("Carve Width", "float", 4.0, 0.0, 1000.0, "the flat bed or road",
                      subtype="DISTANCE")
    carve_d = g.input("Carve Depth", "float", 1.0, -100.0, 100.0,
                      "how far below the curve the bed lies: 0 for a road at the curve's height",
                      subtype="DISTANCE")
    bank = g.input("Bank Width", "float", 3.0, 0.01, 1000.0,
                   "how far out the ground eases back to its own shape", subtype="DISTANCE")
    carve_mat = g.input("Carve Material", "material", about="for the bed or road surface")
    g.panel(None)

    grid = g.node("GeometryNodeMeshGrid", {"Size X": size, "Size Y": size,
                                           "Vertices X": res, "Vertices Y": res})["Mesh"]
    pos = g.position()
    noise = g.node("ShaderNodeTexNoise", {"Vector": pos, "W": g.math("MULTIPLY", seed, 7.31),
                                          "Scale": g.math("DIVIDE", 1.0, feature),
                                          "Detail": rough}, noise_dimensions="4D")
    hills = g.node("ShaderNodeMapRange", {"Value": noise["Fac"], "From Min": 0.3, "From Max": 0.7,
                                          "To Min": g.math("MULTIPLY", height, -0.5),
                                          "To Max": g.math("MULTIPLY", height, 0.5)},
                   clamp=False)["Result"]
    # the canyon: a steep-sided trench around a centre line that wanders with 1D noise
    p = g.separate(pos)
    meander = g.node("ShaderNodeTexNoise", {"W": g.math("ADD", g.math("DIVIDE", p["X"], 30.0),
                                                        g.math("MULTIPLY", seed, 3.1)),
                                            "Scale": 1.0, "Detail": 1.0},
                     noise_dimensions="1D")["Fac"]
    centre = g.math("MULTIPLY", g.math("SUBTRACT", meander, 0.5), g.math("MULTIPLY", wander, 2.0))
    across = g.math("ABSOLUTE", g.math("DIVIDE", g.math("SUBTRACT", p["Y"], centre), width))
    # depth * e^-(|y|^steepness): a gaussian at 2, a flat-floored trench as it rises
    trench = g.math("MULTIPLY", g.math("EXPONENT", g.math("MULTIPLY",
                                                          g.math("POWER", across, steep), -1.0)),
                    depth)
    lifted = g.node("GeometryNodeSetPosition", {"Geometry": grid,
                                                "Offset": g.xyz(0, 0, g.math("SUBTRACT", hills,
                                                                             trench))})["Geometry"]
    carved, in_bed = _carve(g, lifted, carve_obj, carve_w, carve_d, bank)
    smooth = g.node("GeometryNodeSetShadeSmooth", {"Mesh": carved})["Mesh"]
    face_up = g.separate(g.node("GeometryNodeInputNormal")["Normal"])["Z"]
    cliffs = g.compare("LESS_THAN", face_up, g.math("COSINE", cliff_angle))
    ground = _material(g, _material(g, smooth, material), cliff_mat, cliffs)
    g.output("Geometry", "geometry", _material(g, ground, carve_mat, in_bed))
    return g


def _carve(g, mesh, curve_obj, width, depth, bank):
    """Carve a mesh along a curve object: every vertex within Width/2 of the curve (in
    plan) goes to the curve's height there minus Depth, easing back to where it was over
    Bank. Returns (mesh, in-the-bed field). With no curve, nothing changes."""
    source = g.node("GeometryNodeObjectInfo", {"Object": curve_obj},
                    transform_space="RELATIVE")["Geometry"]
    points = g.node("GeometryNodeAttributeDomainSize", {"Geometry": source},
                    component="CURVE")["Point Count"]
    active = g.compare("GREATER_THAN", points, 1, "INT")
    fine = g.node("GeometryNodeResampleCurve", {"Curve": source, "Mode": "Length",
                                                "Length": 0.5})["Curve"]
    cp = g.separate(g.position())
    heights = g.node("GeometryNodeCaptureAttribute", {"Geometry": fine, "Height": cp["Z"]},
                     items={"capture_items": [{"name": "Height", "data_type": "FLOAT"}]})
    flat = g.node("GeometryNodeSetPosition", {"Geometry": heights["Geometry"],
                                              "Position": g.xyz(cp["X"], cp["Y"], 0.0)})["Geometry"]
    wire = g.node("GeometryNodeCurveToMesh", {"Curve": flat})["Mesh"]
    mp = g.separate(g.position())
    in_plan = g.xyz(mp["X"], mp["Y"], 0.0)
    away = g.node("GeometryNodeProximity", {"Geometry": wire, "Sample Position": in_plan},
                  target_element="EDGES")["Distance"]
    nearest = g.node("GeometryNodeSampleNearest", {"Geometry": wire, "Sample Position": in_plan},
                     domain="POINT")["Index"]
    there = g.node("GeometryNodeSampleIndex", {"Geometry": wire, "Value": heights["Height"],
                                               "Index": nearest},
                   data_type="FLOAT", domain="POINT")["Value"]
    half = g.math("MULTIPLY", width, 0.5)
    pull = g.node("ShaderNodeMapRange", {"Value": away, "From Min": half,
                                         "From Max": g.math("ADD", half, bank),
                                         "To Min": 1.0, "To Max": 0.0},
                  interpolation_type="SMOOTHSTEP", clamp=True)["Result"]
    pull = g.switch("FLOAT", active, 0.0, pull)
    marked = g.node("GeometryNodeCaptureAttribute",
                    # the flat bed only: the grid's faces would draw the bank's edge as steps
                    {"Geometry": mesh, "Bed": g.compare("GREATER_THAN", pull, 0.9)},
                    items={"capture_items": [{"name": "Bed", "data_type": "BOOLEAN"}]})
    target = g.math("SUBTRACT", there, depth)
    z = g.math("MULTIPLY_ADD", pull, g.math("SUBTRACT", target, mp["Z"]), mp["Z"])
    carved = g.node("GeometryNodeSetPosition",
                    {"Geometry": marked["Geometry"], "Position": g.xyz(mp["X"], mp["Y"], z)})["Geometry"]
    return carved, marked["Bed"]


@capability("scatter")
def scatter():
    g = Graph("CN Scatter", "Scatters things over a surface — trees, rocks, props — evenly "
                            "spaced, kept off slopes steeper than Max Slope, each with a "
                            "random size and turn. Uses a collection's objects if one is "
                            "given, otherwise stand-in cones. Seeded.")
    surface = g.input("Geometry", "geometry")
    surface_obj = g.input("Surface Object", "object",
                          about="scatter over this object instead of the modifier's own "
                                "geometry — so a forest can sit on its own object and keep "
                                "clear of things that follow the ground, with no loop")
    g.panel("Placement")
    density = g.input("Density", "float", 0.08, 0.0, 1000.0, "things per square metre, at most")
    spacing = g.input("Spacing", "float", 2.0, 0.0, 1000.0, "the closest two things can be",
                      subtype="DISTANCE")
    slope = g.input("Max Slope", "float", 0.52, 0.0, 1.5708, "the steepest ground to place on",
                    subtype="ANGLE")
    clear_of = g.input("Keep Clear Of", "collection",
                       about="nothing is placed within Clear Distance of these objects — "
                             "paths, walls, buildings")
    clear = g.input("Clear Distance", "float", 2.0, 0.0, 1000.0, subtype="DISTANCE")
    seed = g.input("Seed", "int", 0, 0, 1000000)
    g.panel("Variation")
    smin = g.input("Scale Min", "float", 0.7, 0.01, 100.0)
    smax = g.input("Scale Max", "float", 1.3, 0.01, 100.0)
    turn = g.input("Random Turn", "bool", True, about="turn each one randomly about its up axis")
    align = g.input("Align to Surface", "bool", False,
                    about="tilt with the ground instead of standing upright")
    g.panel("Output")
    things = g.input("Instances", "collection",
                     about="objects to scatter; one is picked at random for each spot")
    keep = g.input("Keep Surface", "bool", True,
                   about="output the surface too, not only what is scattered on it")
    realize = g.input("Realize", "bool", False,
                      about="make the instances real mesh: heavier, only when something needs it")
    g.panel(None)

    other = g.node("GeometryNodeObjectInfo", {"Object": surface_obj},
                   transform_space="RELATIVE")["Geometry"]
    other_faces = g.node("GeometryNodeAttributeDomainSize", {"Geometry": other},
                         component="MESH")["Face Count"]
    ground = g.switch("GEOMETRY", g.compare("GREATER_THAN", other_faces, 0, "INT"), surface, other)
    up = g.separate(g.node("GeometryNodeInputNormal")["Normal"])["Z"]
    level = g.compare("GREATER_EQUAL", up, g.math("COSINE", slope))
    spots = g.node("GeometryNodeDistributePointsOnFaces",
                   {"Mesh": ground, "Selection": level, "Distance Min": spacing,
                    "Density Max": density, "Seed": seed}, distribute_method="POISSON")
    # clear of paths and buildings: those objects as they are placed, measured from each spot
    avoid = g.node("GeometryNodeRealizeInstances",
                   {"Geometry": g.node("GeometryNodeCollectionInfo", {"Collection": clear_of},
                                       transform_space="RELATIVE")["Instances"]})["Geometry"]
    anything = g.node("GeometryNodeAttributeDomainSize", {"Geometry": avoid},
                      component="MESH")["Point Count"]
    near = g.node("GeometryNodeProximity", {"Geometry": avoid,
                                            "Sample Position": g.position()},
                  target_element="FACES")["Distance"]
    crowded = g.logic("AND", g.compare("GREATER_THAN", anything, 0, "INT"),
                      g.compare("LESS_THAN", near, clear))
    kept = g.node("GeometryNodeDeleteGeometry", {"Geometry": spots["Points"],
                                                 "Selection": crowded},
                  domain="POINT")["Geometry"]
    base = g.switch("ROTATION", align, None, spots["Rotation"])
    yaw = g.random("FLOAT", 0.0, 6.2832, g.math("ADD", seed, 1))
    spun = g.node("FunctionNodeRotateRotation",
                  {"Rotation": base,
                   "Rotate By": g.node("FunctionNodeEulerToRotation",
                                       {"Euler": g.xyz(0, 0, yaw)})["Rotation"]},
                  rotation_space="LOCAL")["Rotation"]
    rotation = g.switch("ROTATION", turn, base, spun)
    scale = g.random("FLOAT", smin, smax, g.math("ADD", seed, 2))
    cone = g.node("GeometryNodeMeshCone", {"Vertices": 10, "Radius Bottom": 0.9,
                                           "Depth": 3.2})["Mesh"]
    source, has, pick = _pick_source(g, things, _lift(g, cone, 1.6), seed, 3)
    placed = g.node("GeometryNodeInstanceOnPoints",
                    {"Points": kept, "Instance": source, "Pick Instance": has,
                     "Instance Index": pick, "Rotation": rotation, "Scale": scale})["Instances"]
    real = g.node("GeometryNodeRealizeInstances", {"Geometry": placed})["Geometry"]
    result = g.switch("GEOMETRY", realize, placed, real)
    g.output("Geometry", "geometry", g.switch("GEOMETRY", keep, result, g.join(surface, result)))
    return g


@capability("wall")
def wall():
    g = Graph("CN Wall Along Curve", "A solid wall that follows a curve, with doorways cut "
                                     "through it at even spacing and posts along it. Draw or "
                                     "edit the curve and the wall follows. Open or closed curves.")
    curve = g.input("Curve", "geometry")
    ground_obj = g.input("Ground", "object",
                         about="if set, the wall rises and falls with this ground")
    g.panel("Wall")
    height = g.input("Height", "float", 3.0, 0.05, 200.0, subtype="DISTANCE")
    thick = g.input("Thickness", "float", 0.4, 0.01, 50.0, subtype="DISTANCE")
    footing = g.input("Footing", "float", 0.5, 0.0, 50.0,
                      "how far the wall reaches below its base, so it never floats on a slope",
                      subtype="DISTANCE")
    wall_mat = g.input("Wall Material", "material")
    g.panel("Doorways")
    doors = g.input("Doorways", "int", 1, 0, 500, "how many, spaced evenly along the wall")
    door_w = g.input("Doorway Width", "float", 1.4, 0.05, 100.0, subtype="DISTANCE")
    door_h = g.input("Doorway Height", "float", 2.3, 0.05, 200.0, subtype="DISTANCE")
    shift = g.input("Doorway Shift", "float", 0.0, -0.5, 0.5,
                    "slide every doorway along, as a fraction of the gap between them")
    g.panel("Posts")
    posts = g.input("Posts", "bool", True)
    spacing = g.input("Post Spacing", "float", 4.0, 0.2, 500.0, subtype="DISTANCE")
    post_size = g.input("Post Size", "float", 0.6, 0.02, 20.0, subtype="DISTANCE")
    rise = g.input("Post Rise", "float", 0.3, 0.0, 20.0, "how far posts stand above the wall",
                   subtype="DISTANCE")
    post_mat = g.input("Post Material", "material")
    g.panel(None)

    # A profile swept round a sharp corner turns to bisect it, which pinches the wall to
    # 70% of its thickness at a right angle. Rounding each corner by half the thickness
    # keeps it constant: crisp inside, slightly rounded outside. This has to happen
    # before the extra points below, or every nearly-straight one gets a degenerate
    # little fillet that flips the profile.
    rounded = g.node("GeometryNodeFilletCurve",
                     {"Curve": curve, "Mode": "Poly", "Count": 4, "Limit Radius": True,
                      "Radius": g.math("MULTIPLY", thick, 0.5)})["Curve"]
    # on the ground: extra points along each side (Subdivide keeps the corners, where
    # Resample would cut them), each dropped onto the ground below it
    ground = g.node("GeometryNodeObjectInfo", {"Object": ground_obj},
                    transform_space="RELATIVE")["Geometry"]
    faces = g.node("GeometryNodeAttributeDomainSize", {"Geometry": ground},
                   component="MESH")["Face Count"]
    grounded = g.compare("GREATER_THAN", faces, 0, "INT")
    dense = g.node("GeometryNodeSubdivideCurve",
                   {"Curve": rounded, "Cuts": g.switch("INT", grounded, 0, 12)})["Curve"]
    pos = g.position()
    hit, ground_z = _ground_below(g, ground, pos)
    here = g.separate(pos)
    # A profile is swept square to the curve, so on a slope the wall would lean along
    # it. Instead: remember each point's height, sweep along the curve laid flat, and
    # lift every vertex by the height where it came from — walls stay vertical.
    based = g.node("GeometryNodeCaptureAttribute",
                   {"Geometry": dense,
                    "Base": g.switch("FLOAT", g.logic("AND", grounded, hit), here["Z"], ground_z)},
                   items={"capture_items": [{"name": "Base", "data_type": "FLOAT"}]})
    base = based["Base"]
    path = _upright(g, g.node("GeometryNodeSetPosition",
                              {"Geometry": based["Geometry"],
                               "Position": g.xyz(here["X"], here["Y"], 0.0)})["Geometry"])
    whole = g.math("ADD", height, footing)
    rect = g.node("GeometryNodeCurvePrimitiveQuadrilateral", {"Width": thick,
                                                              "Height": whole})["Curve"]
    # swept along a curve whose normals point up, a profile's X goes to the right of
    # travel and its Y goes *down*; so this puts the top at Height and the base at
    # -Footing
    profile = g.node("GeometryNodeTransform",
                     {"Geometry": rect,
                      "Translation": g.xyz(0, g.math("MULTIPLY",
                                                     g.math("SUBTRACT", height, footing),
                                                     -0.5), 0)})["Geometry"]
    flat = g.node("GeometryNodeCurveToMesh", {"Curve": path, "Profile Curve": profile,
                                              "Fill Caps": True})["Mesh"]
    solid = g.node("GeometryNodeSetPosition",
                   {"Geometry": flat, "Offset": g.xyz(0, 0, base)})["Geometry"]

    spots = g.node("GeometryNodePoints", {"Count": doors})["Points"]
    index = g.node("GeometryNodeInputIndex")["Index"]
    along = g.math("DIVIDE", g.math("ADD", g.math("ADD", index, 0.5), shift), doors)
    sample = g.node("GeometryNodeSampleCurve", {"Curves": path, "Value": base, "Factor": along},
                    mode="FACTOR", use_all_curves=True, data_type="FLOAT")
    placed = g.node("GeometryNodeSetPosition",
                    {"Geometry": spots,
                     "Position": g.vmath("ADD", sample["Position"],
                                         g.xyz(0, 0, sample["Value"]))})["Geometry"]
    facing = g.node("FunctionNodeAlignRotationToVector", {"Vector": sample["Tangent"]},
                    axis="X", pivot_axis="Z")["Rotation"]
    cutter = g.node("GeometryNodeMeshCube",
                    {"Size": g.xyz(door_w, g.math("MULTIPLY", thick, 4.0),
                                   g.math("MULTIPLY", door_h, 2.0))})["Mesh"]
    cutters = g.node("GeometryNodeInstanceOnPoints", {"Points": placed, "Instance": cutter,
                                                      "Rotation": facing})["Instances"]
    cut = g.node("GeometryNodeMeshBoolean",
                 {"Mesh 1": solid,
                  "Mesh 2": g.node("GeometryNodeRealizeInstances",
                                   {"Geometry": cutters})["Geometry"]},
                 # Manifold: the swept wall is a closed solid, and the exact solver was
                 # seen to collapse a flat wall with three doorways to five vertices
                 operation="DIFFERENCE", solver="MANIFOLD")["Mesh"]

    stops = g.node("GeometryNodeCurveToPoints", {"Curve": path, "Length": spacing}, mode="LENGTH")
    tall = g.math("ADD", g.math("ADD", height, rise), footing)
    block = g.node("GeometryNodeMeshCube", {"Size": g.xyz(post_size, post_size, tall)})["Mesh"]
    post = _material(g, _lift(g, block, g.math("SUBTRACT", g.math("MULTIPLY", tall, 0.5),
                                               footing)), post_mat)
    turned = g.node("FunctionNodeAlignRotationToVector", {"Vector": stops["Tangent"]},
                    axis="X", pivot_axis="Z")["Rotation"]
    standing = g.node("GeometryNodeSetPosition", {"Geometry": stops["Points"],
                                                  "Offset": g.xyz(0, 0, base)})["Geometry"]
    row = g.node("GeometryNodeInstanceOnPoints", {"Points": standing, "Instance": post,
                                                  "Rotation": turned})["Instances"]
    # flat faces: smooth normals averaged round the corners make a wall look rounded,
    # especially once exported to the web
    flat = g.node("GeometryNodeSetShadeSmooth", {"Mesh": cut, "Shade Smooth": False})["Mesh"]
    g.output("Geometry", "geometry",
             g.join(_material(g, flat, wall_mat), g.switch("GEOMETRY", posts, None, row)))
    return g


@capability("path_bridge")
def path_bridge():
    g = Graph("CN Path and Bridge", "A walkway along a curve that hugs the ground and becomes "
                                    "a bridge — railings, posts and pillars — wherever the "
                                    "ground falls away more than Gap Depth below it. Point "
                                    "Ground at the terrain.")
    curve = g.input("Curve", "geometry")
    ground_obj = g.input("Ground", "object", about="the ground to follow, and to find gaps in")
    g.panel("Walkway")
    width = g.input("Width", "float", 2.4, 0.1, 100.0, subtype="DISTANCE")
    thick = g.input("Deck Thickness", "float", 0.3, 0.01, 20.0, subtype="DISTANCE")
    clear = g.input("Clearance", "float", 0.1, 0.0, 10.0,
                    "how high it sits above the ground where it is not a bridge",
                    subtype="DISTANCE")
    ease = g.input("Smoothing", "int", 10, 0, 500,
                   "how gently it eases between the ground and a bridge")
    path_mat = g.input("Path Material", "material")
    g.panel("Bridge")
    gap = g.input("Gap Depth", "float", 1.5, 0.01, 1000.0,
                  "where the ground is further below than this, it becomes a bridge",
                  subtype="DISTANCE")
    rail_h = g.input("Rail Height", "float", 1.0, 0.05, 20.0, subtype="DISTANCE")
    post_gap = g.input("Post Spacing", "float", 1.6, 0.1, 100.0, subtype="DISTANCE")
    pillar_gap = g.input("Pillar Spacing", "float", 6.0, 0.5, 1000.0, subtype="DISTANCE")
    pillar_w = g.input("Pillar Width", "float", 0.6, 0.02, 50.0, subtype="DISTANCE")
    bridge_mat = g.input("Bridge Material", "material")
    g.panel(None)

    ground = g.node("GeometryNodeObjectInfo", {"Object": ground_obj},
                    transform_space="RELATIVE")["Geometry"]
    fine = g.node("GeometryNodeResampleCurve", {"Curve": _upright(g, curve), "Mode": "Length",
                                                "Length": 0.4})["Curve"]
    pos = g.position()
    hit, ground_z = _ground_below(g, ground, pos)
    here = g.separate(pos)
    drop = g.math("SUBTRACT", here["Z"], ground_z)
    over_gap = g.logic("OR", g.logic("NOT", hit), g.compare("GREATER_THAN", drop, gap))
    target = g.switch("FLOAT", over_gap, g.math("ADD", ground_z, clear), here["Z"])
    marked = g.node("GeometryNodeCaptureAttribute",
                    {"Geometry": fine, "Bridge": over_gap, "Target": target},
                    items={"capture_items": [{"name": "Bridge", "data_type": "BOOLEAN"},
                                             {"name": "Target", "data_type": "FLOAT"}]})
    eased = g.node("GeometryNodeBlurAttribute", {"Value": marked["Target"], "Iterations": ease},
                   data_type="FLOAT")["Value"]
    now = g.separate(g.position())
    walk = g.node("GeometryNodeSetPosition",
                  {"Geometry": marked["Geometry"],
                   "Position": g.xyz(now["X"], now["Y"], eased)})["Geometry"]

    # the deck: a slab swept along the walkway, top face on the curve
    slab = g.node("GeometryNodeCurvePrimitiveQuadrilateral", {"Width": width,
                                                              "Height": thick})["Curve"]
    slab = g.node("GeometryNodeTransform",       # profile Y points down: see `wall`
                  {"Geometry": slab,
                   "Translation": g.xyz(0, g.math("MULTIPLY", thick, 0.5), 0)})["Geometry"]
    deck = g.node("GeometryNodeCurveToMesh", {"Curve": walk, "Profile Curve": slab,
                                              "Fill Caps": True})["Mesh"]
    deck = _material(g, _material(g, deck, path_mat), bridge_mat, marked["Bridge"])

    # the bridge parts follow only the stretches over a gap
    spine = g.node("GeometryNodeCurveToMesh", {"Curve": walk})["Mesh"]
    spans = g.node("GeometryNodeDeleteGeometry",
                   {"Geometry": spine, "Selection": g.logic("NOT", marked["Bridge"])},
                   domain="POINT")["Geometry"]
    spans = _upright(g, g.node("GeometryNodeMeshToCurve", {"Mesh": spans})["Curve"])
    inset = g.math("SUBTRACT", g.math("MULTIPLY", width, 0.5), 0.12)
    outset = g.math("MULTIPLY", inset, -1.0)

    def ring(radius, x, y):
        circle = g.node("GeometryNodeCurvePrimitiveCircle", {"Resolution": 8,
                                                             "Radius": radius})["Curve"]
        return g.node("GeometryNodeTransform", {"Geometry": circle,
                                                "Translation": g.xyz(x, y, 0)})["Geometry"]
    mid = g.math("MULTIPLY", rail_h, 0.5)
    up_top, up_mid = g.math("MULTIPLY", rail_h, -1.0), g.math("MULTIPLY", rail_h, -0.5)
    rail_profile = g.join(ring(0.06, inset, up_top), ring(0.06, outset, up_top),
                          ring(0.035, inset, up_mid), ring(0.035, outset, up_mid))
    rails = g.node("GeometryNodeCurveToMesh", {"Curve": spans,
                                               "Profile Curve": rail_profile})["Mesh"]

    stops = g.node("GeometryNodeCurveToPoints", {"Curve": spans, "Length": post_gap},
                   mode="LENGTH")
    pairs = g.node("GeometryNodeDuplicateElements", {"Geometry": stops["Points"], "Amount": 2},
                   domain="POINT")
    sign = g.math("SUBTRACT", g.math("MULTIPLY", pairs["Duplicate Index"], 2.0), 1.0)
    beside = g.node("GeometryNodeSetPosition",
                    {"Geometry": pairs["Geometry"],
                     "Offset": g.vmath("SCALE", _right_of(g, stops["Tangent"]),
                                       scale=g.math("MULTIPLY", sign, inset))})["Geometry"]
    stick = g.node("GeometryNodeMeshCube", {"Size": g.xyz(0.12, 0.12, rail_h)})["Mesh"]
    posts = g.node("GeometryNodeInstanceOnPoints",
                   {"Points": beside, "Instance": _lift(g, stick, mid)})["Instances"]

    # pillars from the underside of the deck down to the ground
    feet = g.node("GeometryNodeCurveToPoints", {"Curve": spans, "Length": pillar_gap},
                  mode="LENGTH")["Points"]
    fpos = g.position()
    f_hit, f_ground = _ground_below(g, ground, fpos)
    f_here = g.separate(fpos)
    underside = g.math("SUBTRACT", f_here["Z"], thick)
    reach = g.math("SUBTRACT", underside, f_ground)
    measured = g.node("GeometryNodeCaptureAttribute",
                      {"Geometry": feet, "Reach": reach,
                       "Stands": g.logic("AND", f_hit, g.compare("GREATER_THAN", reach, 0.3))},
                      items={"capture_items": [{"name": "Reach", "data_type": "FLOAT"},
                                               {"name": "Stands", "data_type": "BOOLEAN"}]})
    centred = g.node("GeometryNodeSetPosition",
                     {"Geometry": measured["Geometry"],
                      "Position": g.xyz(f_here["X"], f_here["Y"],
                                        g.math("MULTIPLY", g.math("ADD", underside, f_ground),
                                               0.5))})["Geometry"]
    column = g.node("GeometryNodeMeshCylinder", {"Vertices": 12, "Radius": 0.5,
                                                 "Depth": 1.0})["Mesh"]
    pillars = g.node("GeometryNodeInstanceOnPoints",
                     {"Points": centred, "Selection": measured["Stands"], "Instance": column,
                      "Scale": g.xyz(pillar_w, pillar_w, measured["Reach"])})["Instances"]

    parts = g.node("GeometryNodeRealizeInstances",
                   {"Geometry": g.join(rails, posts, pillars)})["Geometry"]
    # The walkway's own curve comes out too, with no thickness so it never renders, so
    # something else — lamps along it, say — can follow the path at its real height.
    # (Another object sees this one's result, not the curve it was drawn as.)
    guide = g.node("GeometryNodeSetCurveRadius", {"Curve": walk, "Radius": 0.0})["Curve"]
    g.output("Geometry", "geometry", g.join(deck, _material(g, parts, bridge_mat), guide))
    return g


@capability("along_curve")
def along_curve():
    g = Graph("CN Props Along Curve", "Places things beside a curve at a steady spacing — "
                                      "lamps along a path, benches, fence posts — on one "
                                      "side, both, or alternating, facing the curve. Uses a "
                                      "collection's objects if given, otherwise stand-in "
                                      "lamp posts.")
    curve = g.input("Curve", "geometry")
    follow = g.input("Follow Object", "object",
                     about="line this object's curve instead of the modifier's own — for a "
                           "path that already carries another modifier")
    g.panel("Placement")
    spacing = g.input("Spacing", "float", 6.0, 0.1, 1000.0, subtype="DISTANCE")
    offset = g.input("Side Offset", "float", 1.8, 0.0, 200.0, "how far from the curve",
                     subtype="DISTANCE")
    sides = g.input("Sides", "int", 3, 0, 3, "0 right, 1 left, 2 both, 3 alternating")
    face = g.input("Face the Curve", "bool", True)
    seed = g.input("Seed", "int", 0, 0, 1000000)
    g.panel("Variation")
    smin = g.input("Scale Min", "float", 1.0, 0.01, 100.0)
    smax = g.input("Scale Max", "float", 1.0, 0.01, 100.0)
    g.panel("Output")
    things = g.input("Instances", "collection",
                     about="objects to place; one is picked at random for each spot")
    g.panel(None)

    other = g.node("GeometryNodeObjectInfo", {"Object": follow},
                   transform_space="RELATIVE")["Geometry"]
    splines = g.node("GeometryNodeAttributeDomainSize", {"Geometry": other},
                     component="CURVE")["Spline Count"]
    source_curve = g.switch("GEOMETRY", g.compare("GREATER_THAN", splines, 0, "INT"), curve, other)
    stops = g.node("GeometryNodeCurveToPoints", {"Curve": _upright(g, source_curve),
                                                 "Length": spacing}, mode="LENGTH")
    numbered = g.node("GeometryNodeCaptureAttribute",
                      {"Geometry": stops["Points"],
                       "Number": g.node("GeometryNodeInputIndex")["Index"]},
                      items={"capture_items": [{"name": "Number", "data_type": "INT"}]})
    pairs = g.node("GeometryNodeDuplicateElements", {"Geometry": numbered["Geometry"],
                                                     "Amount": 2}, domain="POINT")
    copy = pairs["Duplicate Index"]
    alternate = g.compare("EQUAL", copy, g.math("FLOORED_MODULO", numbered["Number"], 2.0), "INT")
    keep = g.node("GeometryNodeIndexSwitch",
                  {"Index": sides, "0": g.compare("EQUAL", copy, 1, "INT"),
                   "1": g.compare("EQUAL", copy, 0, "INT"), "2": True, "3": alternate},
                  items={"index_switch_items": [{}, {}, {}, {}]}, data_type="BOOLEAN")["Output"]
    kept = g.node("GeometryNodeDeleteGeometry", {"Geometry": pairs["Geometry"],
                                                 "Selection": g.logic("NOT", keep)},
                  domain="POINT")["Geometry"]
    sign = g.math("SUBTRACT", g.math("MULTIPLY", copy, 2.0), 1.0)
    outward = g.vmath("SCALE", _right_of(g, stops["Tangent"]), scale=sign)
    placed = g.node("GeometryNodeSetPosition", {"Geometry": kept,
                                                "Offset": g.vmath("SCALE", outward,
                                                                  scale=offset)})["Geometry"]
    # Blender's convention is that -Y is an object's front, so +Y points away from the curve
    facing = g.node("FunctionNodeAlignRotationToVector", {"Vector": outward},
                    axis="Y", pivot_axis="Z")["Rotation"]
    pole = _lift(g, g.node("GeometryNodeMeshCylinder", {"Vertices": 8, "Radius": 0.07,
                                                        "Depth": 3.2})["Mesh"], 1.6)
    lamp = _lift(g, g.node("GeometryNodeMeshUVSphere", {"Segments": 12, "Rings": 6,
                                                        "Radius": 0.22})["Mesh"], 3.3)
    source, has, pick = _pick_source(g, things, g.join(pole, lamp), seed, 5)
    g.output("Geometry", "geometry",
             g.node("GeometryNodeInstanceOnPoints",
                    {"Points": placed, "Instance": source, "Pick Instance": has,
                     "Instance Index": pick, "Rotation": g.switch("ROTATION", face, None, facing),
                     "Scale": g.random("FLOAT", smin, smax, g.math("ADD", seed, 6))})["Instances"])
    return g


@capability("wall_network")
def wall_network():
    g = Graph("CN Wall Network", "Walls along every spline of a curve object, joined into one "
                                 "solid wherever they meet — corners, T-junctions, crossings — "
                                 "with doorways spaced along each wall. Draw each wall as its "
                                 "own spline; ends drawn to the same point make a clean corner.")
    curve = g.input("Curve", "geometry")
    ground_obj = g.input("Ground", "object",
                         about="if set, every wall rises and falls with this ground")
    g.panel("Walls")
    height = g.input("Height", "float", 3.0, 0.05, 200.0, subtype="DISTANCE")
    thick = g.input("Thickness", "float", 0.4, 0.01, 50.0, subtype="DISTANCE")
    footing = g.input("Footing", "float", 0.5, 0.0, 50.0,
                      "how far walls reach below their base, so they never float on a slope",
                      subtype="DISTANCE")
    wall_mat = g.input("Wall Material", "material")
    g.panel("Doorways")
    doors = g.input("Doorways per Wall", "int", 1, 0, 100,
                    "how many on each wall (each spline), spaced evenly along it")
    door_w = g.input("Doorway Width", "float", 1.4, 0.05, 100.0, subtype="DISTANCE")
    door_h = g.input("Doorway Height", "float", 2.3, 0.05, 200.0, subtype="DISTANCE")
    g.panel("Openings")
    openings_obj = g.input("Openings", "object",
                           about="a points object: every point cuts an opening where it stands, "
                                 "its bottom at the point, sized by the point's 'size' attribute "
                                 "(width, unused, height) and turned by its 'angle' (radians) — "
                                 "how a floor plan puts its doors, docks and windows exactly")
    g.panel(None)

    solid, path, base = _walls(g, curve, thick, height, footing, ground_obj)
    walls = g.node("GeometryNodeAttributeDomainSize", {"Geometry": path},
                   component="CURVE")["Spline Count"]
    spots = g.node("GeometryNodePoints", {"Count": g.math("MULTIPLY", doors, walls)})["Points"]
    index = g.node("GeometryNodeInputIndex")["Index"]
    which = g.math("FLOOR", g.math("DIVIDE", index, doors))
    along = g.math("DIVIDE", g.math("ADD", g.math("FLOORED_MODULO", index, doors), 0.5), doors)
    sample = g.node("GeometryNodeSampleCurve", {"Curves": path, "Value": base, "Factor": along,
                                                "Curve Index": which},
                    mode="FACTOR", use_all_curves=False, data_type="FLOAT")
    facing = g.node("FunctionNodeAlignRotationToVector", {"Vector": sample["Tangent"]},
                    axis="X", pivot_axis="Z")["Rotation"]
    oriented = g.node("GeometryNodeCaptureAttribute",
                      {"Geometry": g.node("GeometryNodeSetPosition",
                                          {"Geometry": spots,
                                           "Position": sample["Position"]})["Geometry"],
                       "Facing": facing, "Base": sample["Value"]},
                      items={"capture_items": [{"name": "Facing", "data_type": "QUATERNION"},
                                               {"name": "Base", "data_type": "FLOAT"}]})
    # A doorway never lands on a junction: none within half its width plus a wall's
    # thickness of any wall's end (where a T's stem meets, or a corner is drawn)
    ends = g.node("GeometryNodeDeleteGeometry",
                  {"Geometry": path,
                   "Selection": g.logic("NOT", g.node("GeometryNodeCurveEndpointSelection",
                                                      {"Start Size": 1, "End Size": 1})["Selection"])},
                  domain="POINT")["Geometry"]
    end_points = g.node("GeometryNodeCurveToPoints", {"Curve": ends}, mode="EVALUATED")["Points"]
    near_end = g.node("GeometryNodeProximity", {"Geometry": end_points,
                                                "Sample Position": g.position()},
                      target_element="POINTS")["Distance"]
    crowded = g.compare("LESS_THAN", near_end,
                        g.math("ADD", g.math("MULTIPLY", door_w, 0.5), thick))
    kept = g.node("GeometryNodeDeleteGeometry", {"Geometry": oriented["Geometry"],
                                                 "Selection": crowded},
                  domain="POINT")["Geometry"]
    placed = g.node("GeometryNodeSetPosition",
                    {"Geometry": kept, "Offset": g.xyz(0, 0, oriented["Base"])})["Geometry"]
    cutters = _cutters(g, placed, oriented["Facing"],
                       g.xyz(door_w, g.math("MULTIPLY", thick, 4.0), g.math("MULTIPLY", door_h, 2.0)),
                       0.0)
    # openings from a points object: a unit box standing on each point, scaled and turned
    spots_in = g.node("GeometryNodeObjectInfo", {"Object": openings_obj},
                      transform_space="RELATIVE")["Geometry"]
    size = g.separate(g.node("GeometryNodeInputNamedAttribute", {"Name": "size"},
                             data_type="FLOAT_VECTOR")["Attribute"])
    angle = g.node("GeometryNodeInputNamedAttribute", {"Name": "angle"}, data_type="FLOAT")["Attribute"]
    unit = _lift(g, g.node("GeometryNodeMeshCube", {"Size": (1.0, 1.0, 1.0)})["Mesh"], 0.5)
    boxes = g.node("GeometryNodeInstanceOnPoints",
                   {"Points": spots_in, "Instance": unit, "Rotation": g.xyz(0, 0, angle),
                    "Scale": g.xyz(size["X"], g.math("MULTIPLY", thick, 4.0), size["Z"])})["Instances"]
    planned = g.node("GeometryNodeRealizeInstances", {"Geometry": boxes})["Geometry"]
    g.output("Geometry", "geometry", _material(g, _flat_shaded(g, _cut(g, solid, g.join(cutters, planned))),
                                               wall_mat))
    return g


@capability("rooms")
def rooms():
    g = Graph("CN Rooms", "Rooms from a floor plan: each closed spline of a curve object is a "
                          "room outline. Makes a floor in every room and walls round them, "
                          "joined where rooms share a wall, with a doorway through every "
                          "shared wall long enough for one, windows along outside walls, and "
                          "an entrance. Draw rooms that touch along their edges.")
    plan = g.input("Floor Plan", "geometry")
    g.panel("Walls")
    height = g.input("Height", "float", 2.8, 0.5, 100.0, "floor to top of the walls",
                     subtype="DISTANCE")
    thick = g.input("Thickness", "float", 0.2, 0.02, 5.0, subtype="DISTANCE")
    footing = g.input("Footing", "float", 0.3, 0.0, 20.0,
                      "how far walls reach below the floor, so uneven ground never shows "
                      "under them", subtype="DISTANCE")
    wall_mat = g.input("Wall Material", "material")
    floor_mat = g.input("Floor Material", "material")
    g.panel("Doorways")
    inner_doors = g.input("Doorways", "bool", True,
                          about="a doorway through each wall two rooms share")
    door_w = g.input("Doorway Width", "float", 0.9, 0.3, 10.0, subtype="DISTANCE")
    door_h = g.input("Doorway Height", "float", 2.1, 0.5, 20.0, subtype="DISTANCE")
    entrance = g.input("Entrance", "int", 0, -1, 1000,
                       "which outside wall gets the front door, counting outside walls in "
                       "the order they were drawn; -1 for none")
    g.panel("Windows")
    windows = g.input("Windows", "bool", True)
    win_w = g.input("Window Width", "float", 1.2, 0.1, 20.0, subtype="DISTANCE")
    win_h = g.input("Window Height", "float", 1.2, 0.1, 20.0, subtype="DISTANCE")
    sill = g.input("Sill Height", "float", 0.9, 0.0, 20.0, subtype="DISTANCE")
    win_gap = g.input("Window Spacing", "float", 3.0, 0.3, 100.0,
                      "about one window per this much outside wall", subtype="DISTANCE")
    g.panel(None)

    outlines = g.node("GeometryNodeSetSplineCyclic", {"Curve": plan, "Cyclic": True})["Curve"]
    solid, _path, _base = _walls(g, outlines, thick, height, footing, extend=False)

    # floors: each outline filled, facing up whichever way the room was drawn
    # each outline filled on its own: filled together, edges two rooms share cancel out
    filled = g.node("GeometryNodeFillCurve",
                    {"Curve": outlines, "Mode": "N-gons",
                     "Group ID": g.node("GeometryNodeInputIndex")["Index"]})["Mesh"]
    down = g.compare("LESS_THAN", g.separate(g.node("GeometryNodeInputNormal")["Normal"])["Z"], 0.0)
    floors = g.node("GeometryNodeFlipFaces", {"Mesh": filled, "Selection": down})["Mesh"]
    floors_flat = g.node("GeometryNodeTransform",
                         {"Geometry": floors, "Scale": g.xyz(1.0, 1.0, 0.0)})["Geometry"]
    numbered_floors = g.node("GeometryNodeCaptureAttribute",
                             {"Geometry": floors_flat, "Room": g.node("GeometryNodeInputIndex")["Index"]},
                             domain="FACE",
                             items={"capture_items": [{"name": "Room", "data_type": "INT"}]})

    # every edge of every outline, as a point at its middle knowing its direction and length
    edges = g.node("GeometryNodeCurveToMesh", {"Curve": outlines})["Mesh"]
    ev = g.node("GeometryNodeInputMeshEdgeVertices")
    along = g.vmath("SUBTRACT", ev["Position 2"], ev["Position 1"])
    marked = g.node("GeometryNodeCaptureAttribute",
                    {"Geometry": edges, "Along": along, "Length": g.vmath("LENGTH", along)},
                    domain="EDGE",
                    items={"capture_items": [{"name": "Along", "data_type": "FLOAT_VECTOR"},
                                             {"name": "Length", "data_type": "FLOAT"}]})
    mids = g.node("GeometryNodeMeshToPoints", {"Mesh": marked["Geometry"]}, mode="EDGES")["Points"]
    # Two rooms list a shared wall in opposite directions, and merging their doorways
    # averages the two turns into none, so every wall faces the same way whichever
    # way it was drawn
    a = g.separate(marked["Along"])
    way = g.math("SIGN", g.math("ADD", a["X"], g.math("MULTIPLY", a["Y"], 0.0001)))
    faced = g.node("GeometryNodeCaptureAttribute",
                   {"Geometry": mids,
                    "Facing": g.node("FunctionNodeAlignRotationToVector",
                                     {"Vector": g.vmath("SCALE", marked["Along"], scale=way)},
                                     axis="X", pivot_axis="Z")["Rotation"]},
                   items={"capture_items": [{"name": "Facing", "data_type": "QUATERNION"}]})
    mids, facing = faced["Geometry"], faced["Facing"]
    here = g.separate(g.position())
    flat_here = g.xyz(here["X"], here["Y"], 0.0)
    side = g.vmath("SCALE", _right_of(g, marked["Along"]), scale=thick)

    def room(offset):
        """(is there floor at this offset from the middle of the wall, which room)"""
        cast = g.node("GeometryNodeRaycast",
                      {"Target Geometry": numbered_floors["Geometry"], "Attribute": numbered_floors["Room"],
                       "Interpolation": "Nearest",
                       "Source Position": g.vmath("ADD", g.vmath("ADD", flat_here, offset), (0.0, 0.0, 1000.0)),
                       "Ray Direction": DOWN, "Ray Length": 100000.0}, data_type="INT")
        return cast["Is Hit"], cast["Attribute"]
    back = g.vmath("SCALE", side, scale=-1.0)
    right_hit, right_room = room(side)
    left_hit, left_room = room(back)
    shared = g.logic("AND", right_hit, left_hit)
    # a doorway only where the same two rooms meet along the whole wall: a long wall that
    # borders two rooms on its other side is left to those rooms' shorter walls
    quarter = g.vmath("SCALE", marked["Along"], scale=0.25)

    def same_room_along(offset):
        hit_a, room_a = room(g.vmath("ADD", offset, quarter))
        hit_b, room_b = room(g.vmath("SUBTRACT", offset, quarter))
        return g.logic("AND", g.logic("AND", hit_a, hit_b), g.compare("EQUAL", room_a, room_b, "INT"))
    whole = g.logic("AND", same_room_along(side), same_room_along(back))
    corners = g.node("GeometryNodeCurveToPoints", {"Curve": outlines}, mode="EVALUATED")["Points"]
    to_corner = g.node("GeometryNodeProximity", {"Geometry": corners, "Sample Position": g.position()},
                       target_element="POINTS")["Distance"]

    def fits(width):
        return g.compare("GREATER_EQUAL", to_corner, g.math("ADD", g.math("MULTIPLY", width, 0.5), thick))

    # doorways: shared walls, once each (two rooms list the same wall), clear of corners
    inner = g.node("GeometryNodeDeleteGeometry",
                   {"Geometry": mids,
                    "Selection": g.logic("NOT", g.logic("AND", g.logic("AND", whole, fits(door_w)),
                                                        inner_doors))},
                   domain="POINT")["Geometry"]
    inner = g.node("GeometryNodeMergeByDistance", {"Geometry": inner, "Distance": 0.05})["Geometry"]

    # outside walls, numbered in drawing order, for the entrance and the windows
    outer = g.node("GeometryNodeDeleteGeometry", {"Geometry": mids, "Selection": shared},
                   domain="POINT")["Geometry"]
    numbered = g.node("GeometryNodeCaptureAttribute",
                      {"Geometry": outer, "Number": g.node("GeometryNodeInputIndex")["Index"]},
                      items={"capture_items": [{"name": "Number", "data_type": "INT"}]})
    is_entrance = g.compare("EQUAL", numbered["Number"], entrance, "INT")
    front = g.node("GeometryNodeDeleteGeometry",
                   {"Geometry": numbered["Geometry"],
                    "Selection": g.logic("NOT", g.logic("AND", is_entrance, fits(door_w)))},
                   domain="POINT")["Geometry"]
    doors = g.join(inner, front)
    door_cut = _cutters(g, doors, facing,        # from the floor up: the footing stays as a sill
                        g.xyz(door_w, g.math("MULTIPLY", thick, 4.0), door_h),
                        g.math("MULTIPLY", door_h, 0.5))

    # windows: along each outside wall except the entrance, as many as fit its length
    count = g.math("MAXIMUM", g.math("FLOOR", g.math("DIVIDE", marked["Length"], win_gap)), 1.0)
    glazed = g.node("GeometryNodeDeleteGeometry",
                    {"Geometry": numbered["Geometry"],
                     "Selection": g.logic("OR", g.logic("NOT", windows), is_entrance)},
                    domain="POINT")["Geometry"]
    copies = g.node("GeometryNodeDuplicateElements", {"Geometry": glazed, "Amount": count},
                    domain="POINT")
    step = g.math("SUBTRACT", g.math("DIVIDE", g.math("ADD", copies["Duplicate Index"], 0.5), count), 0.5)
    spread = g.node("GeometryNodeSetPosition",
                    {"Geometry": copies["Geometry"],
                     "Offset": g.vmath("SCALE", marked["Along"], scale=step)})["Geometry"]
    clear = g.node("GeometryNodeDeleteGeometry",
                   {"Geometry": spread, "Selection": g.logic("NOT", fits(win_w))},
                   domain="POINT")["Geometry"]
    win_cut = _cutters(g, clear, facing,
                       g.xyz(win_w, g.math("MULTIPLY", thick, 4.0), win_h),
                       g.math("ADD", sill, g.math("MULTIPLY", win_h, 0.5)))

    walls = _cut(g, solid, g.join(door_cut, win_cut))
    g.output("Geometry", "geometry", g.join(_material(g, _flat_shaded(g, walls), wall_mat),
                                            _material(g, floors, floor_mat)))
    return g


@capability("stairs")
def stairs():
    g = Graph("CN Stairs", "Stairs or a ramp along a curve, from the height of its start to the "
                           "height of its end: draw the curve from the bottom landing to the top. "
                           "Straight or curved. Solid down to the start's level, so they never "
                           "float, with optional side walls.")
    curve = g.input("Curve", "geometry")
    g.panel("Shape")
    ramp = g.input("Ramp", "bool", False, about="a smooth slope instead of steps")
    width = g.input("Width", "float", 1.4, 0.1, 50.0, subtype="DISTANCE")
    riser = g.input("Step Height", "float", 0.18, 0.02, 2.0,
                    "the most each step may rise; the steps divide the climb evenly",
                    subtype="DISTANCE")
    rise_in = g.input("Rise", "float", 3.0, 0.0, 200.0,
                      "the climb, used only when the curve's two ends are at the same height",
                      subtype="DISTANCE")
    stair_mat = g.input("Material", "material")
    g.panel("Sides")
    sides = g.input("Side Walls", "bool", False, about="a low wall along each side")
    side_h = g.input("Side Height", "float", 0.9, 0.05, 10.0, "above the steps", subtype="DISTANCE")
    side_t = g.input("Side Thickness", "float", 0.15, 0.01, 5.0, subtype="DISTANCE")
    g.panel(None)

    start = g.node("GeometryNodeSampleCurve", {"Curves": curve, "Factor": 0.0},
                   mode="FACTOR", use_all_curves=True)
    end = g.node("GeometryNodeSampleCurve", {"Curves": curve, "Factor": 1.0},
                 mode="FACTOR", use_all_curves=True)
    z0 = g.separate(start["Position"])["Z"]
    drawn = g.math("SUBTRACT", g.separate(end["Position"])["Z"], z0)
    rise = g.switch("FLOAT", g.compare("GREATER_THAN", g.math("ABSOLUTE", drawn), 0.01),
                    rise_in, drawn)
    cp = g.separate(g.position())
    plan = g.node("GeometryNodeSetPosition",          # measured and placed in plan
                  {"Geometry": curve, "Position": g.xyz(cp["X"], cp["Y"], 0.0)})["Geometry"]
    length = g.node("GeometryNodeCurveLength", {"Curve": plan})["Length"]
    steps = g.math("MAXIMUM", g.math("CEIL", g.math("DIVIDE", g.math("ABSOLUTE", rise), riser)), 1.0)
    run = g.math("DIVIDE", length, steps)

    # steps: one block per step, centred on its stretch of the curve, from the start's level
    # up to the step's top (a little longer than its stretch so curved flights close up)
    spots = g.node("GeometryNodePoints", {"Count": steps})["Points"]
    index = g.node("GeometryNodeInputIndex")["Index"]
    at = g.node("GeometryNodeSampleCurve",
                {"Curves": plan, "Factor": g.math("DIVIDE", g.math("ADD", index, 0.5), steps)},
                mode="FACTOR", use_all_curves=True)
    top = g.math("ADD", z0, g.math("MULTIPLY", rise, g.math("DIVIDE", g.math("ADD", index, 1.0), steps)))
    here = g.separate(at["Position"])
    lowest = g.math("MINIMUM", z0, g.math("ADD", z0, rise))
    placed = g.node("GeometryNodeSetPosition",
                    {"Geometry": spots,
                     "Position": g.xyz(here["X"], here["Y"],
                                       g.math("MULTIPLY", g.math("ADD", top, lowest), 0.5))})["Geometry"]
    facing = g.node("FunctionNodeAlignRotationToVector", {"Vector": at["Tangent"]},
                    axis="X", pivot_axis="Z")["Rotation"]
    block = g.node("GeometryNodeMeshCube", {"Size": (1.0, 1.0, 1.0)})["Mesh"]
    flight = g.node("GeometryNodeInstanceOnPoints",
                    {"Points": placed, "Instance": block, "Rotation": facing,
                     "Scale": g.xyz(g.math("MULTIPLY", run, 1.04), width,
                                    g.math("MAXIMUM", g.math("SUBTRACT", top, lowest), 0.01))})["Instances"]
    stepped = g.node("GeometryNodeRealizeInstances", {"Geometry": flight})["Geometry"]

    # the ramp: a band swept along the curve laid flat (so its ends stay vertical), then
    # each vertex set to the height of the slope where it came from — or, for the band's
    # marked lower edge, to the lowest level: a solid wedge
    fine = g.node("GeometryNodeResampleCurve", {"Curve": curve, "Mode": "Length",
                                                "Length": 0.25})["Curve"]
    climbed = g.node("GeometryNodeCaptureAttribute",
                     {"Geometry": fine,
                      "Top": g.math("ADD", z0, g.math("MULTIPLY", rise,
                                                      g.node("GeometryNodeSplineParameter")["Factor"]))},
                     items={"capture_items": [{"name": "Top", "data_type": "FLOAT"}]})
    fp = g.separate(g.position())
    path = _upright(g, g.node("GeometryNodeSetPosition",
                              {"Geometry": climbed["Geometry"],
                               "Position": g.xyz(fp["X"], fp["Y"], 0.0)})["Geometry"])

    def solid_along(profile_width, x, above):
        """A band from `above` over the slope down to the lowest level. Profile Y points
        down (see `wall`), so y = -above is the top and y = 1 is the marked bottom."""
        rect = g.node("GeometryNodeCurvePrimitiveQuadrilateral",
                      {"Width": profile_width, "Height": g.math("ADD", above, 1.0)})["Curve"]
        rect = g.node("GeometryNodeTransform",
                      {"Geometry": rect,
                       "Translation": g.xyz(x, g.math("MULTIPLY", g.math("SUBTRACT", 1.0, above), 0.5),
                                            0)})["Geometry"]
        marked = g.node("GeometryNodeCaptureAttribute",
                        {"Geometry": rect,
                         "Low": g.compare("GREATER_THAN", g.separate(g.position())["Y"], 0.5)},
                        items={"capture_items": [{"name": "Low", "data_type": "BOOLEAN"}]})
        swept = g.node("GeometryNodeCurveToMesh", {"Curve": path, "Profile Curve": marked["Geometry"],
                                                   "Fill Caps": True})["Mesh"]
        sp = g.separate(g.position())
        z = g.switch("FLOAT", marked["Low"], g.math("ADD", sp["Z"], climbed["Top"]), lowest)
        return g.node("GeometryNodeSetPosition",
                      {"Geometry": swept, "Position": g.xyz(sp["X"], sp["Y"], z)})["Geometry"]

    body = g.switch("GEOMETRY", ramp, stepped, solid_along(width, 0.0, 0.0))

    # side walls along both edges, from the lowest level to Side Height above the climb
    offset = g.math("MULTIPLY", g.math("ADD", width, side_t), 0.5)
    walls = g.join(solid_along(side_t, offset, side_h),
                   solid_along(side_t, g.math("MULTIPLY", offset, -1.0), side_h))
    result = g.join(body, g.switch("GEOMETRY", sides, None, walls))
    g.output("Geometry", "geometry", _material(g, _flat_shaded(g, result), stair_mat))
    return g


@capability("water")
def water():
    g = Graph("CN Water Along Curve", "A flat water surface along a curve, at the curve's own "
                                      "height plus Level — the river for a terrain carved "
                                      "along the same curve (terrain's Carve panel).")
    curve = g.input("Curve", "geometry")
    width = g.input("Width", "float", 5.0, 0.01, 1000.0,
                    "across the surface: about Carve Width plus Bank Width covers the banks",
                    subtype="DISTANCE")
    level = g.input("Level", "float", -0.5, -100.0, 100.0,
                    "height of the water relative to the curve", subtype="DISTANCE")
    water_mat = g.input("Material", "material")
    fine = _upright(g, g.node("GeometryNodeResampleCurve", {"Curve": curve, "Mode": "Length",
                                                          "Length": 0.5})["Curve"])
    half = g.math("MULTIPLY", width, 0.5)
    line = g.node("GeometryNodeCurvePrimitiveLine",
                  {"Start": g.xyz(g.math("MULTIPLY", half, -1.0), 0, 0), "End": g.xyz(half, 0, 0)})["Curve"]
    sheet = g.node("GeometryNodeCurveToMesh", {"Curve": fine, "Profile Curve": line})["Mesh"]
    raised = _lift(g, sheet, level)
    down = g.compare("LESS_THAN", g.separate(g.node("GeometryNodeInputNormal")["Normal"])["Z"], 0.0)
    up = g.node("GeometryNodeFlipFaces", {"Mesh": raised, "Selection": down})["Mesh"]
    surface = _material(g, g.node("GeometryNodeSetShadeSmooth", {"Mesh": up})["Mesh"], water_mat)
    # the curve comes out too, with no thickness, so a terrain carving along this object
    # still finds it (another object sees this one's result, not the curve it was drawn as)
    guide = g.node("GeometryNodeSetCurveRadius", {"Curve": curve, "Radius": 0.0})["Curve"]
    g.output("Geometry", "geometry", g.join(surface, guide))
    return g


@capability("roof")
def roof():
    g = Graph("CN Roof", "A roof over whatever the object already has — put it after rooms (or "
                         "walls) on the same object and it sits on top: flat, gable, or hip with "
                         "equal pitch all round, spanning the footprint's longer side, with an "
                         "overhang. For rectangular footprints; an L-shaped plan gets one roof "
                         "over its whole outline.")
    geo = g.input("Geometry", "geometry")
    style = g.input("Style", "int", 2, 0, 2, "0 flat, 1 gable, 2 hip")
    pitch = g.input("Pitch", "float", 0.61, 0.05, 1.4, "how steep the slopes are", subtype="ANGLE")
    over = g.input("Overhang", "float", 0.4, 0.0, 10.0, "how far the eaves reach past the walls",
                   subtype="DISTANCE")
    slab = g.input("Flat Thickness", "float", 0.25, 0.01, 5.0, "for the flat style", subtype="DISTANCE")
    roof_mat = g.input("Material", "material")

    box = g.node("GeometryNodeBoundBox", {"Geometry": geo})
    lo, hi = g.separate(box["Min"]), g.separate(box["Max"])
    sx, sy = g.math("SUBTRACT", hi["X"], lo["X"]), g.math("SUBTRACT", hi["Y"], lo["Y"])
    along_x = g.compare("GREATER_EQUAL", sx, sy)
    twice = g.math("MULTIPLY", over, 2.0)
    length = g.math("ADD", g.math("MAXIMUM", sx, sy), twice)
    width = g.math("ADD", g.math("MINIMUM", sx, sy), twice)
    half_l, half_w = g.math("MULTIPLY", length, 0.5), g.math("MULTIPLY", width, 0.5)
    rise = g.math("MULTIPLY", half_w, g.math("TANGENT", pitch))

    line = _upright(g, g.node("GeometryNodeCurvePrimitiveLine",
                              {"Start": g.xyz(g.math("MULTIPLY", half_l, -1.0), 0, 0),
                               "End": g.xyz(half_l, 0, 0)})["Curve"])
    # swept profiles point Y down (see `wall`), so the ridge is at y = -rise
    gable = g.node("GeometryNodeCurvePrimitiveQuadrilateral",
                   {"Point 1": g.xyz(g.math("MULTIPLY", half_w, -1.0), 0, 0),
                    "Point 2": g.xyz(0, g.math("MULTIPLY", rise, -1.0), 0),
                    "Point 3": g.xyz(half_w, 0, 0), "Point 4": (0.0, 0.0, 0.0)}, mode="POINTS")["Curve"]
    flat = g.node("GeometryNodeTransform",
                  {"Geometry": g.node("GeometryNodeCurvePrimitiveQuadrilateral",
                                      {"Width": width, "Height": slab})["Curve"],
                   "Translation": g.xyz(0, g.math("MULTIPLY", slab, -0.5), 0)})["Geometry"]
    is_flat = g.compare("EQUAL", style, 0, "INT")
    prism = g.node("GeometryNodeCurveToMesh",
                   {"Curve": line, "Profile Curve": g.switch("GEOMETRY", is_flat, gable, flat),
                    "Fill Caps": True})["Mesh"]
    # hip: the ridge's two ends move in by half the width, so every slope has the same pitch
    here = g.separate(g.position())
    ridge = g.logic("AND", g.compare("EQUAL", style, 2, "INT"),
                    g.compare("GREATER_THAN", here["Z"], g.math("MULTIPLY", rise, 0.999)))
    hipped = g.node("GeometryNodeSetPosition",
                    {"Geometry": prism, "Selection": ridge,
                     "Offset": g.xyz(g.math("MULTIPLY", g.math("SIGN", here["X"]),
                                            g.math("MULTIPLY", half_w, -1.0)), 0, 0)})["Geometry"]
    merged = g.node("GeometryNodeMergeByDistance", {"Geometry": hipped, "Distance": 0.0001})["Geometry"]
    placed = g.node("GeometryNodeTransform",
                    {"Geometry": merged,
                     "Translation": g.xyz(g.math("MULTIPLY", g.math("ADD", lo["X"], hi["X"]), 0.5),
                                          g.math("MULTIPLY", g.math("ADD", lo["Y"], hi["Y"]), 0.5), hi["Z"]),
                     "Rotation": g.xyz(0, 0, g.switch("FLOAT", along_x, 1.5707963, 0.0))})["Geometry"]
    g.output("Geometry", "geometry", g.join(geo, _material(g, _flat_shaded(g, placed), roof_mat)))
    return g
