"""The MCP server process. Every tool is a thin wrapper over one call into Blender.

    uvx --from . codenodes-mcp          (or: python -m codenodes_mcp)

Images come back as real MCP images so the assistant can look at what it made.
"""

from __future__ import annotations

import base64

from .connection import BlenderNotRunning, Connection

try:
    from mcp.server.fastmcp import FastMCP, Image
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise SystemExit(
        "The MCP SDK is missing. Install it with:  pip install \"mcp[cli]\"\n"
        "or run this server with:  uvx --from <path to CodeNodes/mcp> codenodes-mcp"
    ) from exc

mcp = FastMCP("CodeNodes")
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

    kind is "mesh" (a surface from `float sdf(vec3 p)`), "particles" (a solver with
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
    """Set up lighting: "studio", "dark" or "flat". Without it a render comes out black."""
    return _call("light", style=style, strength=strength)


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
    """Start here for Geometry Nodes: how many node types exist and how to work with them."""
    return _call("nodes_help")


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
