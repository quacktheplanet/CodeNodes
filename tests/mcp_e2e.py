"""The whole Claude loop through the real MCP server process, as an MCP client drives it.

    <python with mcp + codenodes-mcp> tests/mcp_e2e.py <output folder>

Needs Blender open with CodeNodes' server started (tests/install_check.ps1 does all of it).
It spawns `python -m codenodes_mcp` over stdio, exactly as Claude Code would, and calls the
tools: make a Code→Mesh and a Code Shape, build and edit a Geometry Nodes capability,
explain it, bake particles, render and screenshot, and write a web page. Prints each check
and ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "mcp_out")
TORUS = """// @param radius 1.0 0.3 2.0
// @param thickness 0.3 0.05 0.8
float sdf(vec3 p) { return smin(sdTorus(p, radius, thickness), sdSphere(p, 0.5), 0.3); }
"""
LAMP = """param height 0.34 0.10 0.80
param radius 0.14 0.03 0.40
part shade
  profile
    move radius * 0.38, height
    curve x = radius * (0.38 + 0.62 * t)  y = height - height * 0.22 * t  steps 20
  revolve segments 48
"""
SWIRL = """void spawn(inout Particle p) { p.position = randBall(p.seed) * 1.5; p.life = 5.0; }
void update(inout Particle p, float dt) { p.velocity = vec3(-p.position.y, p.position.x, 0.2); p.position += p.velocity * dt; }
"""

checks = 0


def field(obj, snake, camel):
    """MCP SDK 2.x uses snake_case field names, 1.x camelCase."""
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel, None)


class Fail(Exception):
    pass


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


class Tools:
    def __init__(self, session):
        self.session = session

    async def raw(self, name, args=None):
        return await self.session.call_tool(name, args or {})

    async def __call__(self, tool, /, **args):
        """A dict-returning tool, as the model sees it."""
        res = await self.raw(tool, args)
        if field(res, 'is_error', 'isError'):
            return {"ok": False, "error": " ".join(getattr(c, "text", "") for c in res.content),
                    "mcp_error": True}
        data = field(res, 'structured_content', 'structuredContent')
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        if data is None:
            data = json.loads(res.content[0].text)
        return data

    async def image(self, tool, path, /, **args):
        res = await self.raw(tool, args)
        if field(res, 'is_error', 'isError'):
            raise Fail(f"{tool} failed: " + " ".join(getattr(c, "text", "") for c in res.content))
        img = next((c for c in res.content if getattr(c, "type", "") == "image"), None)
        if img is None:
            raise Fail(f"{tool} returned no image")
        import base64
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(img.data))
        return os.path.getsize(path), field(img, 'mime_type', 'mimeType')


async def loop(t):
    listed = await t.session.list_tools()
    names = {tool.name for tool in listed.tools}
    check({"make", "nodes_use", "nodes_edit", "render", "web_page"} <= names and "exec" not in names,
          f"the MCP server lists {len(names)} tools, none of them exec")

    st = await t("status")
    check(st.get("running"), f"status: Blender is listening on port {st.get('port')}")
    g = await t("guide")
    check(bool(g) and "error" not in g, "guide returns the writing guide")

    # --- Code -> Mesh ---------------------------------------------------------------------
    bad = await t("make", kind="mesh", code="float sdf(vec3 p) { return lenght(p) - 1.0; }", name="Ring")
    check(bad.get("ok") is False and "line" in bad.get("error", ""),
          f"a typo comes back as a fixable error: {bad.get('error', '')[:70]}")
    r = await t("make", kind="mesh", code=TORUS, name="Ring", options={"resolution": 96})
    check(r.get("ok") and r.get("faces", 0) > 1000, f"make mesh: {r.get('faces')} faces")
    sc = await t("scene")
    check("Ring" in json.dumps(sc), "scene lists the new object")
    sp = await t("set_params", name="Ring", values={"radius": 1.4})
    check(sp.get("ok"), "set_params rebuilds it")
    code = await t("get_code", name="Ring")
    check("sdTorus" in code.get("code", ""), "get_code gives the code back to edit")
    sh = await t("make", kind="shape", code=LAMP, name="Lamp")
    check(sh.get("ok") and sh.get("faces", 0) > 100, f"make shape: {sh.get('faces')} faces")

    # --- Geometry Nodes capability ----------------------------------------------------------
    lib = await t("nodes_library")
    check("wall" in json.dumps(lib), "nodes_library lists the wall capability")
    cv = await t("curve", name="WallLine", points=[[-6, 0, 0], [0, 0, 0], [0, 6, 0]], smooth=False)
    check(cv.get("ok"), "curve makes the wall's line")
    w = await t("nodes_use", capability="wall", object="WallLine", values={"Height": 3, "Doorways": 1})
    made = w.get("made", {})
    check(w.get("ok") and made.get("faces", made.get("verts", 0)) > 0, f"nodes_use wall: {made}")
    group = w["group"]
    w2 = await t("nodes_set_inputs", object="WallLine", values={"Doorways": 2})
    check(w2.get("ok"), "nodes_set_inputs changes a slider")
    ex = await t("nodes_explain", group=group)
    text = json.dumps(ex)
    check(ex.get("ok", True) and len(text) > 200, f"nodes_explain describes '{group}' ({len(text)} chars)")
    ed = await t("nodes_edit", group=group, ops=[{"op": "set_socket", "socket": "Height", "max_value": 25.0}])
    check(ed.get("ok"), f"nodes_edit applies an edit: {ed.get('error', 'ok')}")
    ck = await t("nodes_check", group=group, on="WallLine")
    check(ck.get("ok") and ck.get("verts", 0) > 0 and not ck.get("problems"),
          f"nodes_check after the edit: {ck.get('verts')} verts, problems {ck.get('problems')}")
    bad_edit = await t("nodes_edit", group=group, ops=[{"op": "link", "from": ["Nope", "Geometry"],
                                                        "to": ["Group Output", "Geometry"]}])
    check(bad_edit.get("ok") is False, "a bad edit is refused and explained")

    # --- particles and bake ---------------------------------------------------------------
    pr = await t("make", kind="particles", code=SWIRL, name="Swirl", options={"count": 2000})
    check(pr.get("ok"), f"make particles: {pr.get('error', pr.get('stats', ''))}")
    fr = await t("set_frame", number=4)
    check(fr.get("ok", True), "set_frame moves the timeline")
    bk = await t("bake", name="Swirl", frame_start=1, frame_end=4)
    check(bk.get("ok"), f"bake writes the animation to disk: {bk.get('stats', bk.get('error'))}")

    # --- look at it -----------------------------------------------------------------------
    lt = await t("light", style="outdoor")
    check(lt.get("ok", True), "light")
    la = await t("look_at", target="WallLine", azimuth=40, elevation=25)
    check(la.get("ok", True), "look_at frames the wall")
    os.makedirs(OUT, exist_ok=True)
    size, mime = await t.image("render", os.path.join(OUT, "render.png"), samples=8, width=480, engine="CYCLES")
    check(size > 5000 and mime == "image/png", f"render comes back as an image ({size} bytes)")
    size, mime = await t.image("viewport", os.path.join(OUT, "viewport.png"))
    check(size > 5000, f"viewport comes back as an image ({size} bytes)")

    # --- web page -------------------------------------------------------------------------
    page = os.path.join(OUT, "wall.html")
    wp = await t("web_page", path=page, sliders=[{"object": "WallLine", "input": "Doorways", "values": [0, 1, 2]}],
                 objects=["WallLine"], title="Install check")
    check(wp.get("ok") and os.path.exists(page) and os.path.getsize(page) > 10000,
          f"web_page writes {os.path.getsize(page) if os.path.exists(page) else 0} bytes")

    # --- limits ---------------------------------------------------------------------------
    nope = await t.raw("exec", {"code": "import os"})
    check(field(nope, "is_error", "isError"), "a tool that does not exist is refused")
    rm = await t("remove", name="Ring")
    check(rm.get("ok"), "remove")


async def main():
    env = {k: os.environ[k] for k in ("CODENODES_TOKEN_FILE", "CODENODES_TOKEN", "CODENODES_PORT")
           if k in os.environ}
    params = StdioServerParameters(command=sys.executable, args=["-m", "codenodes_mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await loop(Tools(session))


if __name__ == "__main__":
    try:
        asyncio.run(main())
        print(f"\nALL {checks} CHECKS PASSED", flush=True)
    except BaseException as exc:          # the MCP client wraps errors in exception groups
        while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
        if not isinstance(exc, Fail):
            import traceback
            traceback.print_exception(exc)
        print(f"FAIL: {exc}", flush=True)
        sys.exit(1)
