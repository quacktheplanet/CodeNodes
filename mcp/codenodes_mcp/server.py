"""The MCP server process. Every tool is a thin wrapper over one call into Blender.

    uvx --from . codenodes-mcp          (or: python -m codenodes_mcp)

Images come back as real MCP images so the assistant can look at what it made.
"""

from __future__ import annotations

import base64

from .connection import BlenderNotRunning, Connection

try:  # MCP SDK 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import Image, MCPServer
except ImportError:
    try:  # 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer, Image
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit(
            "The MCP SDK is missing. Install it with:  pip install \"mcp[cli]\"\n"
            "or run this server with:  uvx --from <path to CodeNodes/mcp> codenodes-mcp"
        ) from exc

mcp = MCPServer("CodeNodes")
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
    """Ready-made, tested world-building capabilities — terrain, scatter, walls with
    doorways, paths that become bridges over gaps, props along a curve — with every
    input, its range and what it does. Compose these before writing nodes from scratch."""
    return _call("nodes_library")


@mcp.tool()
def nodes_use(capability: str, object: str = "", values: dict | None = None,
              name: str = "") -> dict:
    """Build a capability from nodes_library and put it on an object, inputs set by name,
    e.g. nodes_use("wall", "Courtyard", {"Height": 4, "Doorways": 2}).

    Leave object empty for a new object. Curve-following ones (wall, path_bridge,
    along_curve) go on a curve object — make one with `curve`. Object, collection and
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
def curve(name: str, points: list, cyclic: bool = False, smooth: bool = True) -> dict:
    """Make or reshape a curve object through points [[x, y, z], ...] — the line of a
    path, a wall, a river. smooth=False gives straight segments with sharp corners."""
    return _call("curve", name=name, points=points, cyclic=cyclic, smooth=smooth)


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
    """Check the link to Blender: whether it is listening, and what it has served."""
    return _call("status")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
