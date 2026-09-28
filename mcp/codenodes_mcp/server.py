"""The MCP server process. Every tool is a thin wrapper over one call into Blender.

    python -m codenodes_mcp            (or the Claude Code plugin, which runs mcp/run_server.py)

Standard library only: nothing to install. It finds the open Blender by itself (see
connection.py). Images come back as real MCP images so the assistant can look at what
it made.
"""

from __future__ import annotations

import base64

from .connection import BlenderNotRunning, Connection

from .stdio import Image, MCPServer
from .connection import instances

INSTRUCTIONS = """CodeNodes turns code into real Blender geometry, live in the user's open Blender.
Call `guide` before writing code the first time. `code_node` makes geometry the way a person
does in the UI (a code node inside Geometry Nodes, with the code's sliders as node inputs);
`make` makes a plain code object. Look at results with `viewport` or `render` (set `light`
first). Geometry Nodes: `nodes_help`, `nodes_library`/`nodes_use` for ready-made capabilities,
`nodes_find`/`nodes_describe`/`nodes_write`/`nodes_edit` to build or change trees. If Blender
isn't open, ask the user to open it with the CodeNodes add-on enabled; there is nothing to start."""

mcp = MCPServer("codenodes", version="0.1.2", instructions=INSTRUCTIONS)
_link = Connection()


def _call(tool, **args):
    """One call into Blender, with connection problems phrased for a reader."""
    try:
        return _link.call(tool, **args)
    except BlenderNotRunning as exc:
        raise RuntimeError(str(exc)) from None


@mcp.tool()
def guide(kind: str = "") -> dict:
    """How to write CodeNodes code: the kinds, the GLSL helpers, and a template for each.

    Read this before writing code for the first time.
    """
    return _call("help", **({"kind": kind} if kind else {}))


@mcp.tool()
def make(kind: str, code: str, name: str = "", options: dict | None = None) -> dict:
    """Build or update something in Blender from GPU code.

    kind is "mesh" (a surface from `float sdf(vec3 p)`), "shape" (a constructed model in
    the shape language: profiles revolved, extruded, swept), "particles" (a solver with
    `spawn` and `update`) or "volume" (smoke from `float density(vec3 p)`).
    options passes extras such as resolution, count, bounds_min/bounds_max, params.

    A mistake in the code comes back as {"ok": false, "error": "line 3: ..."} — fix the
    code and call again. Call `guide` first if unsure of the shape.
    """
    args = {"kind": kind, "code": code}
    if name:
        args["name"] = name
    args.update(options or {})
    return _call("make", **args)


@mcp.tool()
def set_params(name: str, values: dict) -> dict:
    """Change an object's sliders (its `// @param` values) and rebuild it."""
    return _call("set_params", name=name, **values)


@mcp.tool()
def scene() -> dict:
    """What is in the scene: objects, their kind, sliders, stats and any errors."""
    return _call("scene")


@mcp.tool()
def get_code(name: str) -> dict:
    """The code currently driving an object, so it can be edited rather than replaced."""
    return _call("code", name=name)


@mcp.tool()
def set_frame(number: int) -> dict:
    """Move the timeline. Animated objects re-simulate to that frame."""
    return _call("frame", number=number)


@mcp.tool()
def look_at(target: str = "", azimuth: float = 35.0, elevation: float = 22.0,
            distance: float | None = None, lens: float = 50.0) -> dict:
    """Aim the camera at an object (empty target means the origin) and frame it."""
    return _call("look_at", target=target or None, azimuth=azimuth, elevation=elevation,
                 distance=distance, lens=lens)


@mcp.tool()
def light(style: str = "studio", strength: float = 1.0) -> dict:
    """Set up lighting: "studio" (objects), "outdoor" (sun and sky, for landscapes and
    levels), "dark" or "flat". Without it a render comes out black."""
    return _call("light", style=style, strength=strength)


@mcp.tool()
def material(name: str, color: list, roughness: float = 0.5, metallic: float = 0.0,
             emission: list | None = None, emission_strength: float = 1.0,
             variation: float = 0.0, variation_scale: float = 1.0,
             objects: list | None = None) -> dict:
    """Make or update a material by name — then pass that name to any material input, or
    list objects to put it on them directly.

    color is [r, g, b], 0-1. variation (0-1) breaks a flat colour up with soft noise:
    ground, stone and wood look far better with 0.3-0.6. emission [r, g, b] makes it glow.
    """
    return _call("material", name=name, color=color, roughness=roughness, metallic=metallic,
                 emission=emission, emission_strength=emission_strength,
                 variation=variation, variation_scale=variation_scale, objects=objects or [])


@mcp.tool()
def web_page(path: str, sliders: list | None = None, objects: list | None = None,
             static: list | None = None, overrides: dict | None = None,
             title: str = "Level", subtitle: str = "") -> dict:
    """Write the scene as one self-contained web page (three.js, orbit controls) with
    sliders for modifier inputs, e.g. [{"object": "Courtyard", "input": "Doorways",
    "values": [0, 1, 2, 3]}, {"object": "Trail", "input": "Width", "values": [2, 3, 4],
    "unit": "m"}].

    Each slider position is built in Blender first. Objects a slider changes indirectly
    (trees that keep clear of a path) are detected and baked with it; sliders that change
    the same objects are baked as combinations. static: objects to export once anyway.
    overrides: inputs set only for the export, e.g. {"Ground": {"Resolution": 150}}.
    """
    return _call("web_page", path=path, sliders=sliders or [], objects=objects,
                 static=static or [], overrides=overrides, title=title, subtitle=subtitle)


@mcp.tool()
def web_shape(path: str, object: str = "", source: str = "", title: str = "", subtitle: str = "",
              colors: dict | None = None, color: list | None = None, roughness: float = 0.45,
              metalness: float = 0.0) -> dict:
    """Write a Code Shape as one web page that rebuilds it in the browser as its sliders
    move — any value, nothing baked. Give the shape object (its code and tuned sliders)
    or the shape-language source. colors: {part name: [r, g, b]} (0-1).

    A shape whose parts cut other parts (subtract, intersect) needs Blender, so it is
    refused: use web_page for that. Bevels are left off on the page.
    """
    args = {"path": path, "subtitle": subtitle, "roughness": roughness, "metalness": metalness}
    for key, value in (("object", object), ("source", source), ("title", title),
                       ("colors", colors), ("color", color)):
        if value:
            args[key] = value
    return _call("web_shape", **args)


@mcp.tool()
def collect(name: str, objects: list, parent: str = "", keep_in_scene: bool = False) -> dict:
    """Put objects in a collection — assets to scatter from (keep_in_scene=False takes the
    originals out of the scene so they do not render), or a named group such as everything
    trees should keep clear of (keep_in_scene=True). A collection inside another
    (parent=...) is picked as one piece, so a tree's trunk and crown stay together."""
    return _call("collect", name=name, objects=objects, keep_in_scene=keep_in_scene,
                 **({"parent": parent} if parent else {}))


@mcp.tool()
def render(samples: int = 48, width: int = 800, engine: str = "CYCLES") -> Image:
    """Render the current frame and return the picture, to look at and judge.

    Aim the camera (`look_at`) and set up lighting (`light`) first.
    """
    result = _call("render", samples=samples, width=width, engine=engine)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "the render failed"))
    got = _call("read_image", path=result["path"])
    if not got.get("ok"):
        raise RuntimeError(got.get("error", "could not read the render back"))
    return Image(data=base64.b64decode(got["image_base64"]), format="png")


@mcp.tool()
def viewport() -> Image:
    """A screenshot of Blender's 3D viewport: quicker than a render, and shows overlays."""
    result = _call("viewport")
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "could not grab the viewport"))
    got = _call("read_image", path=result["path"])
    if not got.get("ok"):
        raise RuntimeError(got.get("error", "could not read the screenshot back"))
    return Image(data=base64.b64decode(got["image_base64"]), format="png")


@mcp.tool()
def bake(name: str, frame_start: int | None = None, frame_end: int | None = None) -> dict:
    """Bake an animation to disk so it renders in F12 and on machines with no GPU."""
    return _call("bake", name=name, frame_start=frame_start, frame_end=frame_end)


@mcp.tool()
def bake_to_nodes(name: str) -> dict:
    """Turn a Code Mesh's GLSL into a real Geometry Nodes network: no files and no GPU,
    evaluated every frame, sliders kept on the modifier. Needs the ExpressNode add-on.

    Loops, `if` statements and noise3/fbm3 can't convert yet: the answer says which line
    and suggests `bake` (to disk) instead, and the object is left as it was.
    """
    return _call("bake_to_nodes", name=name)


def _with_images(result, paths):
    """A tool's answer as text, followed by its pictures as real images."""
    import json
    content = [json.dumps(result, indent=1, default=str)]
    for path in paths:
        got = _call("read_image", path=path)
        if got.get("ok"):
            content.append(Image(data=base64.b64decode(got["image_base64"]), format="png"))
    return content


@mcp.tool()
def plan_site(spec: dict | None = None, name: str = "Factory", example: str = "", seed: int = 1):
    """Plan a building (a factory floor, a warehouse, a lab, a house) from a spec, before
    anything is built. Returns every space's rectangle, the aisles, a text grid view, the
    measured problems and a picture of the plan.

    spec: {"site": {"size": [60, 40], "levels": 1, "clear_height": 8, "column_grid": [10, 10]},
           "spaces": [{"name": "Receiving", "area": 240, "type": "dock", "exterior": "south",
                       "docks": 2}, ...],
           "relations": [["Receiving", "Storage", "A"], ...],   # closeness A E I O U X
           "flow": [["Receiving", "Storage", 40], ...],
           "equipment": [{"kind": "cnc", "count": 4, "in": "Machining", "arrange": "row"}, ...],
           "rules": {"aisle_forklift": 3.6}}
    Space types: production storage dock shipping qa office amenity utility lab retail living
    kitchen bedroom bath room. example="factory" (or machine_shop, warehouse, bakery, lab,
    house) starts from an example; top-level keys given in spec replace the example's.
    Then: build_plan, place_equipment, verify. Change it with edit_plan.
    """
    args = {"name": name, "seed": seed}
    if spec:
        args["spec"] = spec
    if example:
        args["example"] = example
    result = _call("plan_site", **args)
    return _with_images(result, result.get("images", [])[:1]) if result.get("ok") else result


@mcp.tool()
def edit_plan(name: str, ops: list):
    """Change a planned building, then re-solve locally. ops, in order:
    {"op": "swap", "a": "QA", "b": "Offices"}; {"op": "move", "space": "QA", "strip": 2, "index": 0};
    {"op": "resize", "space": "Storage", "area": 600};
    {"op": "add_space", "space": {"name": "Paint", "area": 150, "type": "production"}};
    {"op": "remove_space", "space": "Break Room"};
    {"op": "relation", "a": "Paint", "b": "Assembly", "rating": "I"};
    {"op": "flow", "a": "Assembly", "b": "Paint", "amount": 10};
    {"op": "rule", "name": "aisle_forklift", "value": 4.0}; {"op": "site", "size": [70, 40]};
    {"op": "add_equipment", "item": {...}}; {"op": "remove_equipment", "kind": "amr", "in": "Assembly"};
    {"op": "set_equipment", "kind": "cnc", "in": "Machining", "changes": {"count": 6}}; {"op": "resolve"}.
    Rebuild afterwards with build_plan / place_equipment."""
    result = _call("edit_plan", name=name, ops=ops)
    return _with_images(result, result.get("images", [])[:1]) if result.get("ok") else result


@mcp.tool()
def build_plan(name: str) -> dict:
    """Build a planned building in Blender from the Geometry Nodes library: floor slabs with
    painted zones and aisle lines, the shell and interior walls with every door, dock and
    window cut where the plan put it, a roof, stairs, columns, lights. Editable afterwards:
    the walls are wall_network modifiers, the roof a roof modifier."""
    return _call("build_plan", name=name)


@mcp.tool()
def place_equipment(name: str, items: list | None = None, repair: bool = True):
    """Lay out and build the plan's equipment: conveyors (overhead across aisles), fenced
    robot cells, pallet-rack rows with forklift aisles, machines and benches in rows, desks
    in grids. `items` adds more, like {"kind": "tank", "count": 2, "in": "Mixing"}.
    Returns what went where, what did not fit and a picture of the layout."""
    args = {"name": name, "repair": repair}
    if items:
        args["items"] = items
    result = _call("place_equipment", **args)
    return _with_images(result, [result["image"]] if result.get("image") else []) if result.get("ok") else result


@mcp.tool()
def verify(name: str, renders: bool = True, repair: bool = True):
    """Check a building by measurement: areas, outside walls, docks, collisions, aisles and
    their clear width, service clearance, the walk from anywhere to an exit, robot reach and
    fences, floating parts, meshes that intersect, and rays through every opening. Repairs
    the layout where it can (and rolls back a repair that makes things worse). Returns the
    violations, the numbers, and renders: a plan from above, a cut-away, a walk down the aisle."""
    result = _call("verify", name=name, renders=renders, repair=repair)
    paths = list((result.get("images") or {}).values())
    return _with_images(result, paths) if result.get("ok") else result


@mcp.tool()
def assets(kind: str = "") -> dict:
    """What a building can be made of: the parametric generators (conveyor, rack, pallet,
    forklift, amr, robot_arm, cnc, workbench, tank, cabinet, fence, desk, column, dock_door,
    door, window) with their sliders, clearances and joints; the space types and rules;
    example specs. assets("rack") for one generator, assets("example:warehouse") for a
    whole spec to start from."""
    return _call("assets", **({"kind": kind} if kind else {}))


@mcp.tool()
def remove(name: str, delete_cache: bool = False) -> dict:
    """Delete an object that was made here."""
    return _call("remove", name=name, delete_cache=delete_cache)


@mcp.tool()
def nodes_help() -> dict:
    """Start here for Geometry Nodes: the working guide, the ready-made capabilities, and
    how big the node surface is. Read it before building a node setup."""
    return _call("nodes_help")


@mcp.tool()
def nodes_library() -> dict:
    """Ready-made, tested world-building capabilities — terrain (with river and road
    carving), scatter, walls with doorways, wall networks joined at corners and T's, rooms
    from a floor plan, roofs, stairs and ramps, paths that become bridges over gaps, props along a
    curve, water — with every input, its range and what it does. Compose these before
    writing nodes from scratch."""
    return _call("nodes_library")


@mcp.tool()
def nodes_use(capability: str, object: str = "", values: dict | None = None,
              name: str = "") -> dict:
    """Build a capability from nodes_library and put it on an object, inputs set by name,
    e.g. nodes_use("wall", "Courtyard", {"Height": 4, "Doorways": 2}).

    Leave object empty for a new object. Curve-following ones (wall, wall_network, rooms,
    stairs, water, path_bridge, along_curve) go on a curve object — make one with `curve`. Object, collection and
    material inputs take a name. Reports what geometry came out.
    """
    args = {"capability": capability, "values": values or {}}
    if object:
        args["object"] = object
    if name:
        args["name"] = name
    return _call("nodes_use", **args)


@mcp.tool()
def nodes_set_inputs(object: str, values: dict, modifier: str = "") -> dict:
    """Change the sliders on an object's Geometry Nodes modifier by input name, e.g.
    {"Height": 5, "Seed": 3}. Reports what geometry came out."""
    args = {"object": object, "values": values}
    if modifier:
        args["modifier"] = modifier
    return _call("nodes_set_inputs", **args)


@mcp.tool()
def curve(name: str, points: list | None = None, cyclic: bool = False, smooth: bool = True,
          splines: list | None = None) -> dict:
    """Make or reshape a curve object through points [[x, y, z], ...] — the line of a
    path, a wall, a river. smooth=False gives straight segments with sharp corners.

    splines=[[[x, y, z], ...], [[x, y, z], ...]] puts several in one object instead: a
    network of walls for wall_network, or room outlines for rooms (cyclic=True,
    smooth=False; rooms that share a wall share its two corner points)."""
    args = {"name": name, "cyclic": cyclic, "smooth": smooth}
    if splines is not None:
        args["splines"] = splines
    else:
        args["points"] = points
    return _call("curve", **args)


@mcp.tool()
def nodes_explain(group: str) -> dict:
    """An existing node tree in plain words: inputs, what each node does in flow order,
    where each connection comes from, what is unused, and anything that looks wrong."""
    return _call("nodes_explain", group=group)


@mcp.tool()
def nodes_edit(group: str, ops: list) -> dict:
    """Small changes to an existing tree without rewriting it — all or nothing.

    ops is a list like:
      {"op": "set", "node": "Grid", "values": {"Vertices X": 40}, "settings": {...}}
      {"op": "add", "node": {"name": "Jitter", "type": "GeometryNodeSetPosition"}}
      {"op": "link", "from": ["Jitter", "Geometry"], "to": ["Group Output", "Geometry"]}
      {"op": "unlink", "to": ["Group Output", "Geometry"]}
      {"op": "insert", "node": {...}, "between": {"from": [n, s], "to": [n, s]}}
      {"op": "remove", "node": "Jitter", "bridge": true}
      {"op": "rename", "node": "Grid", "to": "Ground"}
      {"op": "input", "socket": "Seed", "type": "NodeSocketInt", "default_value": 0}
      {"op": "set_socket", "socket": "Seed", "max_value": 100}
      {"op": "remove_socket", "socket": "Seed"}
    Values tuned on modifiers stay put.
    """
    return _call("nodes_edit", group=group, ops=ops)


@mcp.tool()
def nodes_find(words: str = "", detail: bool = False, limit: int = 40) -> dict:
    """Look up Blender node types by plain words — "distribute points", "curve to mesh".

    With detail=True you get each one's sockets, which take a field, and what its
    dropdowns accept. Use this instead of guessing socket names.
    """
    return _call("nodes_find", words=words, detail=detail, limit=limit)


@mcp.tool()
def nodes_describe(name: str) -> dict:
    """One node type in full, e.g. "GeometryNodeDistributePointsOnFaces"."""
    return _call("nodes_describe", name=name)


@mcp.tool()
def nodes_list() -> dict:
    """The node groups in the open file."""
    return _call("nodes_list")


@mcp.tool()
def nodes_read(group: str) -> dict:
    """An existing node tree as plain data: every node, setting, value and link.

    This is how to understand a setup before changing it.
    """
    return _call("nodes_read", group=group)


@mcp.tool()
def nodes_write(description: dict, name: str = "", apply_to: str = "") -> dict:
    """Build a node tree from plain data, replacing any group of the same name.

    To edit rather than replace: nodes_read it, change that data, pass it back here.
    Mistakes come back as {"ok": false, "error": "... has no output 'X'. It has: ..."}.
    """
    args = {"description": description}
    if name:
        args["name"] = name
    if apply_to:
        args["apply_to"] = apply_to
    return _call("nodes_write", **args)


@mcp.tool()
def nodes_apply(object_name: str, group: str) -> dict:
    """Put a node group on an object as a Geometry Nodes modifier."""
    return _call("nodes_apply", object_name=object_name, group=group)


@mcp.tool()
def nodes_check(group: str, on: str = "") -> dict:
    """Build a group and report what geometry actually comes out — the quickest way to
    tell whether it does anything."""
    return _call("nodes_check", group=group, **({"on": on} if on else {}))


@mcp.tool()
def status() -> dict:
    """Is Blender connected? Lists every open Blender running CodeNodes (port, version, file)
    and which one this session talks to. Call this first if a tool says Blender isn't open."""
    live = [{k: i.get(k) for k in ("port", "blender", "file", "pid")} for i in instances()]
    try:
        current = _link.call("status")
        return {"connected": True, "current": current, "blenders": live}
    except (BlenderNotRunning, OSError) as exc:
        _link.close()
        return {"connected": False, "blenders": live, "hint": str(exc)}


@mcp.tool()
def use_blender(port: int) -> dict:
    """Switch to another open Blender, by the port `status` lists for it."""
    info = _link.use(port)
    if info is None:
        return {"ok": False, "error": f"no Blender with CodeNodes on port {port}",
                "blenders": [{k: i.get(k) for k in ("port", "blender", "file")} for i in instances()]}
    return {"ok": True, "port": info.get("port"), "blender": info.get("blender"), "file": info.get("file")}


@mcp.tool()
def code_node(kind: str = "mesh", code: str | None = None, template: str | None = None,
              name: str | None = None, object: str | None = None, values: dict | None = None) -> dict:
    """Geometry made by code as a node inside Geometry Nodes, the way a person makes it
    (Add › Mesh › Code Mesh / Shape / Particles). The code's sliders become inputs on the node.

    New: leave `object` out (or give a new name) and pass kind ("mesh" = `float sdf(vec3 p)`,
    "shape" = the shape language, "particles" = spawn/update) with `code` or a `template`
    ("Donut", "Rounded Box", "Gyroid Ball", "Blob"; "Desk Lamp", "Vase"; "Swirl", "Fountain").
    Update: pass the `object` it returned, with new `code` and/or `values` ({"major": 1.2}).
    The result names the object, its Geometry Nodes tree and the node, so nodes_edit and
    nodes_read can wire it into more nodes. Errors in the code come back with the line.
    """
    args = {"kind": kind}
    for key, value in (("code", code), ("template", template), ("name", name),
                       ("object", object), ("values", values)):
        if value is not None:
            args[key] = value
    return _call("code_node", **args)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
