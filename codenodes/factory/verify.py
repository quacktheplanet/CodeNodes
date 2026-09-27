"""Measured checks on a plan and its layout, and an automatic repair loop.

    check(plan, layout) -> {"violations": [...], "stats": {...}}

Every violation is {"severity": "error"|"warning", "code", "message", "where", "fix"} with
the numbers that failed. Checked, all by measurement:

    space      areas within 25 % of the spec, shapes within their aspect limit, outside walls
               where a space needs one (docks, windows, a named side), docks as asked
    collision  no two things' footprints overlap (a robot cell counts as its fence ring)
    bounds     everything inside its space
    aisle      building aisles and rack aisles keep their full clear width (the narrowest
               clear width is measured, not assumed)
    clearance  service space in front of machines, panels, benches kept free
    egress     from every reachable floor spot to an exit, walking round walls, racks and
               machines (8-connected distance on a 0.25 m grid) — longest walk against
               the max_travel rule, and any floor walled off by equipment
    reach      each robot's fixture within its reach, its fence at least reach + fence
               clearance from the robot

    repair(plan, layout, rounds=3) -> (layout, report): re-places what collides or blocks
               an aisle, drops what still cannot fit, keeps the best result (a repair that
               makes things worse is rolled back).

Pure Python.
"""

from __future__ import annotations

import heapq
import math

from .layout import area, inside, overlap

CELL = 0.25


def _v(sev, code, message, where=None, fix=None, **data):
    out = {"severity": sev, "code": code, "message": message, "where": where}
    if fix:
        out["fix"] = fix
    out.update(data)
    return out


def check(p, lay=None, egress=True):
    out = []
    rules = p["spec"]["rules"]
    spaces = {sp["name"]: sp for lvl in p["levels"] for sp in lvl["spaces"]}
    specd = {sp["name"]: sp for sp in p["spec"]["spaces"]}
    for lvl in p["levels"]:
        got = sum(sp["area"] for sp in lvl["spaces"])
        asked = sum(specd[sp["name"]]["area"] for sp in lvl["spaces"]) or 1.0
        scale = got / asked
        if not 0.8 <= scale <= 1.25:
            out.append(_v("warning", "area", f"level {lvl['level']}: the spaces come to {got:.0f} m² against "
                          f"{asked:.0f} m² asked (x{scale:.2f}) — the site and the spaces do not match",
                          f"level {lvl['level']}", "change site.size or the areas"))
        for sp in lvl["spaces"]:
            target = specd[sp["name"]]
            fair = target["area"] * scale
            dev = (sp["area"] - fair) / fair
            if abs(dev) > 0.25:
                out.append(_v("warning", "area", f"{sp['name']} is {sp['area']:.0f} m², {dev:+.0%} against its "
                              f"share ({fair:.0f} m²)", sp["name"], "resize spaces with edit_plan"))
    for name, sp in spaces.items():
        target = specd[name]
        x0, y0, x1, y1 = sp["rect"]
        w, d = x1 - x0, y1 - y0
        aspect = max(w, d) / max(min(w, d), 1e-6)
        if aspect > target["max_aspect"] * 1.2:
            out.append(_v("warning", "shape", f"{name} is {w:.1f} x {d:.1f} m (aspect {aspect:.1f}, "
                          f"limit {target['max_aspect']})", name))
        need = target["exterior"]
        if need is True and not sp["exterior"]:
            out.append(_v("error", "exterior", f"{name} needs an outside wall and has none", name,
                          "move it to an end of a strip or the first/last strip"))
        elif isinstance(need, str) and need not in sp["exterior"]:
            out.append(_v("error", "exterior", f"{name} should be on the {need} side", name))
    for lvl in p["levels"]:
        for sp in lvl["spaces"]:
            asked = specd[sp["name"]].get("docks")
            if asked:
                got = sum(1 for o in lvl["openings"] if o["kind"] == "dock" and o["space"] == sp["name"])
                if got < asked:
                    out.append(_v("warning", "docks", f"{sp['name']} has {got} dock doors of {asked} asked "
                                  "(its outside wall is too short)", sp["name"]))
    stats = {"spaces": len(spaces), "items": 0}
    if lay is None:
        return {"violations": out, "stats": stats}

    items = lay["items"]
    stats["items"] = len(items)
    stats["unplaced"] = sum(u.get("count", 1) for u in lay["unplaced"])
    for u in lay["unplaced"]:
        out.append(_v("warning", "unplaced", f"{u.get('count', 1)} x {u['kind']} did not fit in {u['space']}: "
                      f"{u['reason']}", u["space"], "make the space bigger, or ask for fewer"))

    # things inside their spaces
    for it in items:
        sp = spaces[it["space"]]
        if it.get("spans"):
            continue
        for s in it["solids"]:
            if not inside(s, sp["rect"], -0.01):
                out.append(_v("error", "bounds", f"{it['id']} sticks out of {it['space']}", it["id"],
                              "re-place it", item=it["id"]))
                break

    # collisions between floor footprints (cell parts inside their own fence are fine)
    floor = [(it, s) for it in items for s in it["solids"]]
    seen = set()
    for i in range(len(floor)):
        a, sa = floor[i]
        for j in range(i + 1, len(floor)):
            b, sb = floor[j]
            if a["id"] == b["id"] or a["level"] != b["level"]:
                continue
            if a.get("group") and a.get("group") == b.get("group") and "cell" in str(a.get("group")):
                continue
            if overlap(sa, sb, 0.01):
                key = tuple(sorted((a["id"], b["id"])))
                if key not in seen:
                    seen.add(key)
                    out.append(_v("error", "collision", f"{a['id']} and {b['id']} overlap", a["space"],
                                  "re-place one of them", items=list(key)))

    # clearances kept free (overhead parts and a cell's own parts excepted)
    for it in items:
        if it.get("overhead") or it["role"] in ("rack", "fixture", "robot_arm"):
            continue
        cr = it["clearance"]
        for b, sb in floor:
            if b["id"] == it["id"] or b["level"] != it["level"] or (it.get("group") and it["group"] == b.get("group")):
                continue
            if overlap(cr, sb, 0.02):
                out.append(_v("warning", "clearance", f"the working space round {it['id']} is blocked by {b['id']}",
                              it["space"], "re-place one of them", items=[it["id"], b["id"]]))
                break

    # aisles
    for lvl in p["levels"]:
        for a in lvl["aisles"]:
            r = a["rect"]
            horizontal = r[2] - r[0] >= r[3] - r[1]
            blockers = [(b, s) for b, s in floor if b["level"] == lvl["level"] and overlap(r, s, 0.01)]
            for b, s in blockers:
                out.append(_v("error", "aisle", f"{b['id']} stands in the aisle {a['index']}", b["space"],
                              "re-place it", item=b["id"]))
            need = rules["aisle_forklift"] if a["kind"] == "forklift" else rules["corridor"]
            if a["clear_width"] + 1e-6 < need:
                out.append(_v("error", "aisle_width", f"aisle {a['index']} is {a['clear_width']:.2f} m clear, "
                              f"{need} m needed", f"aisle {a['index']}"))
    for ra in lay.get("rack_aisles", []):
        r = ra["rect"]
        for b, s in floor:
            if b["space"] == ra["space"] and overlap(r, s, 0.01):
                out.append(_v("error", "aisle", f"{b['id']} blocks a rack aisle in {ra['space']}", ra["space"],
                              "re-place it", item=b["id"]))
        w = min(r[2] - r[0], r[3] - r[1])
        stats.setdefault("narrowest_rack_aisle", w)
        stats["narrowest_rack_aisle"] = min(stats["narrowest_rack_aisle"], w)

    # robots
    for it in items:
        if it["kind"] != "robot_arm":
            continue
        reach = it.get("reach") or 0.0
        base = it["footprint"]
        bx, by = (base[0] + base[2]) / 2, (base[1] + base[3]) / 2
        mates = [b for b in items if b.get("group") == it.get("group") and b["id"] != it["id"]]
        fixture = next((b for b in mates if b["role"] == "fixture"), None)
        fence = next((b for b in mates if b["kind"] == "fence"), None)
        if fixture:
            f = fixture["footprint"]
            d = math.hypot((f[0] + f[2]) / 2 - bx, (f[1] + f[3]) / 2 - by)
            if d > reach:
                out.append(_v("error", "reach", f"{it['id']} cannot reach its fixture ({d:.2f} m, reach {reach:.2f} m)",
                              it["space"], item=it["id"]))
        if fence:
            fr = fence["footprint"]
            gap = min(bx - fr[0], fr[2] - bx, by - fr[1], fr[3] - by)
            if gap + 1e-6 < reach + rules["fence_clearance"]:
                out.append(_v("error", "fence", f"{it['id']}'s fence is {gap:.2f} m away; reach {reach:.2f} m + "
                              f"{rules['fence_clearance']} m clearance needed", it["space"], item=it["id"]))
        else:
            out.append(_v("warning", "fence", f"{it['id']} has no safety fence", it["space"], item=it["id"]))
    if egress:
        e_out, e_stats = egress_check(p, lay)
        out.extend(e_out)
        stats.update(e_stats)
    return {"violations": out, "stats": stats}


# ---- egress -----------------------------------------------------------------------------------

def egress_check(p, lay):
    rules = p["spec"]["rules"]
    W, D = p["site"]["size"]
    nx, ny = int(math.ceil(W / CELL)), int(math.ceil(D / CELL))
    out, stats = [], {}
    worst_all = 0.0
    for lvl in p["levels"]:
        blocked = bytearray(nx * ny)

        def mark(r, val=1):
            i0, i1 = max(0, int(r[0] / CELL)), min(nx, int(math.ceil(r[2] / CELL)))
            j0, j1 = max(0, int(r[1] / CELL)), min(ny, int(math.ceil(r[3] / CELL)))
            for j in range(j0, j1):
                row = j * nx
                for i in range(i0, i1):
                    blocked[row + i] = val

        half = max(rules["wall_interior"] / 2, 0.13)
        for w in lvl["walls"]:
            x0, y0, x1, y1 = w
            mark([min(x0, x1) - half, min(y0, y1) - half, max(x0, x1) + half, max(y0, y1) + half])
        for it in (lay["items"] if lay else []):
            if it["level"] != lvl["level"]:
                continue
            for s in it["solids"]:
                mark([s[0] + 0.05, s[1] + 0.05, s[2] - 0.05, s[3] - 0.05])
        for st in lvl["stairs"]:
            mark(st["rect"])
        for hole in lvl["holes"]:
            mark(hole)
        exits = []
        for o in lvl["openings"]:
            x, y = o["pos"]
            hw = o["width"] / 2
            if o["wall"] == "interior" or o["kind"] in ("door", "double_door", "opening", "drive_in"):
                r = [x - hw, y - 0.4, x + hw, y + 0.4] if o["axis"] == "x" else [x - 0.4, y - hw, x + 0.4, y + hw]
                if o["wall"] == "interior":
                    mark(r, 0)
            if o["kind"] == "exit" or (o["kind"] in ("door",) and o["wall"] == "exterior"):
                exits.append(o)
        if lvl["level"] > 0 or not exits:
            # upper floors walk to the stair; a floor with no exit at all is its own problem
            targets = [((st["rect"][0] + st["rect"][2]) / 2, (st["rect"][1] + st["rect"][3]) / 2)
                       for l2 in p["levels"] if l2["level"] == lvl["level"] - 1 for st in l2["stairs"]]
            if not targets and not exits:
                out.append(_v("error", "egress", f"level {lvl['level']} has no exit", f"level {lvl['level']}"))
                continue
        dist = [math.inf] * (nx * ny)
        heap = []

        def seed_cell(i, j):
            if 0 <= i < nx and 0 <= j < ny and not blocked[j * nx + i]:
                dist[j * nx + i] = 0.0
                heapq.heappush(heap, (0.0, i, j))

        for o in exits:
            x, y = o["pos"]
            hw = o["width"] / 2
            if o["axis"] == "x":
                j = 0 if y < D / 2 else ny - 1
                for i in range(int((x - hw) / CELL), int(math.ceil((x + hw) / CELL))):
                    seed_cell(i, j)
            else:
                i = 0 if x < W / 2 else nx - 1
                for j in range(int((y - hw) / CELL), int(math.ceil((y + hw) / CELL))):
                    seed_cell(i, j)
        if lvl["level"] > 0:
            for l2 in p["levels"]:
                if l2["level"] == lvl["level"] - 1:
                    for st in l2["stairs"]:
                        r = st["rect"]
                        for j in range(int(r[1] / CELL), int(math.ceil(r[3] / CELL))):
                            for i in range(int(r[0] / CELL), int(math.ceil(r[2] / CELL))):
                                if 0 <= i < nx and 0 <= j < ny:
                                    blocked[j * nx + i] = 0
                                    seed_cell(i, j)
        steps = [(1, 0, CELL), (-1, 0, CELL), (0, 1, CELL), (0, -1, CELL),
                 (1, 1, CELL * 1.4142), (1, -1, CELL * 1.4142), (-1, 1, CELL * 1.4142), (-1, -1, CELL * 1.4142)]
        while heap:
            d0, i, j = heapq.heappop(heap)
            if d0 > dist[j * nx + i]:
                continue
            for di, dj, c in steps:
                a, b = i + di, j + dj
                if not (0 <= a < nx and 0 <= b < ny) or blocked[b * nx + a]:
                    continue
                if di and dj and (blocked[j * nx + a] or blocked[b * nx + i]):
                    continue        # no cutting corners through a gap
                nd = d0 + c
                if nd < dist[b * nx + a]:
                    dist[b * nx + a] = nd
                    heapq.heappush(heap, (nd, a, b))
        for sp in lvl["spaces"]:
            x0, y0, x1, y1 = sp["rect"]
            worst, walled, free_cells = 0.0, 0, 0
            for j in range(int(y0 / CELL) + 1, int(y1 / CELL) - 1):
                for i in range(int(x0 / CELL) + 1, int(x1 / CELL) - 1):
                    k = j * nx + i
                    if blocked[k]:
                        continue
                    free_cells += 1
                    if dist[k] == math.inf:
                        walled += 1
                    else:
                        worst = max(worst, dist[k])
            worst_all = max(worst_all, worst)
            stats.setdefault("travel", {})[sp["name"]] = round(worst, 1)
            if free_cells and walled / free_cells > 0.02:
                out.append(_v("error", "egress", f"{walled * CELL * CELL:.0f} m² of {sp['name']} has no way out "
                              "(walled off by walls or equipment)", sp["name"],
                              "add a door or move equipment", walled_m2=round(walled * CELL * CELL, 1)))
            if worst > rules["max_travel"]:
                out.append(_v("error", "egress", f"the longest walk out of {sp['name']} is {worst:.0f} m "
                              f"(limit {rules['max_travel']:.0f} m)", sp["name"], "add an exit", travel=round(worst, 1)))
    stats["longest_walk"] = round(worst_all, 1)
    return out, stats


# ---- repair -----------------------------------------------------------------------------------

def _errors(res):
    return [v for v in res["violations"] if v["severity"] == "error"]


def repair(p, lay, rounds=3):
    """Re-place what collides, sticks out or blocks an aisle; drop what cannot be placed.
    Returns (best layout, report)."""
    from . import layout as L
    best, best_res = lay, check(p, lay)
    report = {"rounds": [], "before": len(_errors(best_res))}
    for r in range(rounds):
        bad = set()
        for v in _errors(best_res):
            if v["code"] in ("collision", "bounds", "aisle", "reach", "fence"):
                bad.update(v.get("items") or ([v["item"]] if v.get("item") else []))
        if not bad:
            break
        cand = L.replace(p, best, bad, seed=r + 2)
        res = check(p, cand)
        n_old, n_new = len(_errors(best_res)), len(_errors(res))
        report["rounds"].append({"moved": sorted(bad), "errors_before": n_old, "errors_after": n_new,
                                 "kept": n_new < n_old})
        if n_new < n_old:
            best, best_res = cand, res
        else:
            break           # rolled back: this repair made nothing better
    report["after"] = len(_errors(best_res))
    return best, best_res, report
