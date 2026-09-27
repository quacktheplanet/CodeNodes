"""A factory from a plain request, through the real MCP server process, as an assistant would
drive it:

    "Make me a 60 x 40 m factory floor: receiving and shipping docks, pallet storage,
     machining, two assembly cells with robot arms, QA and offices, with forklift aisles."

    <python with mcp + codenodes-mcp> tests/mcp_factory.py <output folder>

Needs Blender open with CodeNodes' server started (tests/install_check.ps1 -Client
mcp_factory.py does all of it). It looks the parts up (assets), writes the spec, plans
(plan_site), builds (build_plan), places the equipment (place_equipment), checks
(verify), then edits the plan (a paint booth next to assembly), rebuilds and checks again.
Saves every picture it gets back. Ends with "ALL n CHECKS PASSED" or "FAIL: ...".
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "mcp_factory_out")
checks = 0

REQUEST = ("Make me a 60 x 40 m factory floor: receiving and shipping docks, pallet storage, machining, "
           "two assembly cells with robot arms, QA and offices, with forklift aisles.")

# what an assistant writes from the request (after reading assets())
SPEC = {
    "site": {"size": [60, 40], "clear_height": 8, "column_grid": [10, 10]},
    "spaces": [
        {"name": "Receiving", "area": 240, "type": "dock", "exterior": "south", "docks": 2},
        {"name": "Pallet Storage", "area": 480, "type": "storage"},
        {"name": "Machining", "area": 400, "type": "production"},
        {"name": "Assembly", "area": 440, "type": "production"},
        {"name": "QA", "area": 110, "type": "qa"},
        {"name": "Shipping", "area": 220, "type": "shipping", "exterior": "north", "docks": 2},
        {"name": "Offices", "area": 170, "type": "office"},
    ],
    "relations": [["Receiving", "Pallet Storage", "A"], ["Pallet Storage", "Machining", "E"],
                  ["Machining", "Assembly", "A"], ["Assembly", "QA", "A"], ["QA", "Shipping", "E"],
                  ["Offices", "QA", "I"], ["Offices", "Machining", "X"]],
    "flow": [["Receiving", "Pallet Storage", 40], ["Pallet Storage", "Machining", 30],
             ["Machining", "Assembly", 30], ["Assembly", "QA", 25], ["QA", "Shipping", 25]],
    "equipment": [
        {"kind": "rack", "in": "Pallet Storage", "count": "fill", "aisle": "forklift_two_way"},
        {"kind": "cnc", "count": 4, "in": "Machining", "arrange": "row"},
        {"kind": "robot_arm", "count": 2, "in": "Assembly", "fenced": True},
        {"kind": "conveyor", "from": "Machining", "to": "Assembly"},
        {"kind": "workbench", "count": 2, "in": "Assembly", "arrange": "row"},
        {"kind": "workbench", "count": 2, "in": "QA"},
        {"kind": "pallet", "count": 6, "in": "Receiving", "arrange": "grid"},
        {"kind": "pallet", "count": 6, "in": "Shipping", "arrange": "grid"},
        {"kind": "forklift", "count": 2, "in": "Receiving"},
        {"kind": "desk", "count": 8, "in": "Offices", "arrange": "grid"},
    ],
    "rules": {"aisle_forklift": 3.6},
}


def field(obj, snake, camel):
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel, None)


class Fail(Exception):
    pass


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}", flush=True)


async def call(session, tool, **args):
    """(the answer as a dict, [saved image paths])"""
    res = await session.call_tool(tool, args)
    if field(res, "is_error", "isError"):
        raise Fail(f"{tool} errored: " + " ".join(getattr(c, "text", "") for c in res.content))
    data, images = None, []
    structured = field(res, "structured_content", "structuredContent")
    if isinstance(structured, dict):
        data = structured.get("result", structured) if set(structured) == {"result"} else structured
    for c in res.content:
        if getattr(c, "type", "") == "image":
            path = os.path.join(OUT, f"{tool}_{len(os.listdir(OUT))}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(c.data))
            images.append(path)
        elif getattr(c, "type", "") == "text" and data is None:
            try:
                data = json.loads(c.text)
            except ValueError:
                pass
    if isinstance(data, dict) and "result" in data and len(data) == 1:
        data = data["result"]
    return data, images


async def main():
    os.makedirs(OUT, exist_ok=True)
    params = StdioServerParameters(command=sys.executable, args=["-m", "codenodes_mcp"], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            check({"plan_site", "edit_plan", "build_plan", "place_equipment", "verify", "assets"} <= tools,
                  "the six building tools are listed")
            print(f"  request: {REQUEST}", flush=True)
            a, _ = await call(session, "assets")
            check(a["ok"] and any(g["kind"] == "robot_arm" for g in a["generators"]), "assets lists the generators")
            arm, _ = await call(session, "assets", kind="robot_arm")
            check(arm["reach"] > 1.5 and len(arm["joints"]) == 6, "a robot arm's reach and joints can be looked up")
            plan, imgs = await call(session, "plan_site", spec=SPEC, name="Request")
            check(plan["ok"] and not plan["errors"], f"plan_site plans the request: {len(plan['levels'][0]['spaces'])} "
                                                     f"spaces, {len(plan['levels'][0]['aisles'])} aisles")
            check(imgs and os.path.getsize(imgs[0]) > 2000, "and sends a picture of the plan")
            print("\n".join("    " + line for line in plan["grid"].splitlines()), flush=True)
            b, _ = await call(session, "build_plan", name="Request")
            check(b["ok"] and abs(b["shell_size"][0] - 60.3) < 0.1, f"build_plan builds it ({b['parts']})")
            e, imgs = await call(session, "place_equipment", name="Request")
            check(e["ok"] and not e["errors"] and not e["unplaced"],
                  f"place_equipment places {e['objects']} things, nothing left over")
            check(imgs, "and sends a picture of the layout")
            v, imgs = await call(session, "verify", name="Request")
            check(v["ok"] and v["passed"], f"verify passes: {v.get('errors', [])[:2]}")
            st = v["stats"]
            check(st["opening_rays_blocked"] == 0 and st["longest_walk"] < 76,
                  f"every opening is cut ({st['opening_rays']}) and the longest walk out is {st['longest_walk']} m")
            check(len(imgs) == 3, "three renders come back: plan, cut-away, aisle walk")
            # a change of mind: a paint booth by assembly
            ed, _ = await call(session, "edit_plan", name="Request", ops=[
                {"op": "add_space", "space": {"name": "Paint Booth", "area": 120, "type": "production",
                                              "enclosed": True}},
                {"op": "relation", "a": "Paint Booth", "b": "Assembly", "rating": "A"},
                {"op": "add_equipment", "item": {"kind": "tank", "count": 2, "in": "Paint Booth",
                                                 "params": {"radius": 0.6, "height": 1.8}}}])
            check(ed["ok"] and "Paint Booth" in ed["edit"]["added"], f"edit_plan adds a paint booth "
                                                                     f"(moved: {ed['edit']['moved']})")
            await call(session, "build_plan", name="Request")
            e2, _ = await call(session, "place_equipment", name="Request")
            v2, imgs2 = await call(session, "verify", name="Request")
            check(v2["passed"], f"rebuilt with the booth, it still verifies ({v2.get('errors', [])[:2]})")
            print(f"  pictures in {OUT}", flush=True)


try:
    asyncio.run(main())
    print(f"ALL {checks} CHECKS PASSED", flush=True)
except Fail as exc:
    print(f"FAIL: {exc}", flush=True)
    sys.exit(1)
