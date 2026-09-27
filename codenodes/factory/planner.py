"""Spec -> plan: where every space goes, the aisles between them, walls, doors, docks,
windows, columns and stairs. Pure Python, seeded: the same spec and seed give the same plan.

The method (after SLP / CORELAP-style planning, see docs/MODELING_RESEARCH.md):

1. Spaces are laid in strips along the building's long side, with an aisle between every
   two strips, so every space fronts an aisle and every aisle runs from one end wall to
   the other (exits at both ends). Strip depth follows the areas in it; each space's length
   follows its area, so areas come out right by construction.
2. Simulated annealing reorders spaces and moves them between strips to score well on shape
   (aspect ratio, minimum width), exterior needs (docks, windows, a named side), closeness
   ratings (A E I O U X), material flow distance, and — on upper floors — areas.
3. The winner is snapped to the grid and turned into geometry: rectangles, aisle
   rectangles, interior walls round enclosed spaces, doors onto aisles, dock doors and
   windows on outside walls, exits at the aisle ends, a column grid kept out of aisles,
   and stairs (in the first aisle) when there is more than one floor.

    plan(spec, seed=1) -> plan dict (JSON-friendly), see `_geometry` for its shape
"""

from __future__ import annotations

import math
import random

from . import spec as specs

STAIR_WIDTH = 1.1
STEP_RISE, STEP_RUN = 0.18, 0.28


# ---- the frame: u along the long side, v across -----------------------------------------------

class Frame:
    def __init__(self, W, D):
        self.W, self.D = W, D
        self.along_x = W >= D
        self.L, self.S = (W, D) if self.along_x else (D, W)

    def xy(self, u, v):
        return (u, v) if self.along_x else (v, u)

    def rect(self, u0, v0, u1, v1):
        (x0, y0), (x1, y1) = self.xy(u0, v0), self.xy(u1, v1)
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]

    def side(self, local):
        """World side for a local side: 'v0', 'v1', 'u0', 'u1'."""
        if self.along_x:
            return {"v0": "south", "v1": "north", "u0": "west", "u1": "east"}[local]
        return {"v0": "west", "v1": "east", "u0": "south", "u1": "north"}[local]

    def local(self, world):
        for loc in ("v0", "v1", "u0", "u1"):
            if self.side(loc) == world:
                return loc
        raise KeyError(world)


# ---- evaluating one arrangement ---------------------------------------------------------------

class Level:
    """One floor's spaces and the fixed things the planner needs about them."""

    def __init__(self, s, level, frame):
        self.s, self.level, self.f = s, level, frame
        self.spaces = [sp for sp in s["spaces"] if sp["level"] == level]
        self.by_name = {sp["name"]: sp for sp in self.spaces}
        names = set(self.by_name)
        self.relations = [(a, b, specs.RATINGS[r]) for a, b, r in s["relations"] if a in names and b in names]
        self.flow = [(a, b, float(w)) for a, b, w in s["flow"] if a in names and b in names and w > 0]
        self.flow_total = sum(w for _, _, w in self.flow) or 1.0
        self.rules = s["rules"]
        self.multi = s["site"]["levels"] > 1


def aisle_widths(lv, strips, stairs):
    out = []
    for j in range(len(strips) - 1):
        kinds = {lv.by_name[n]["type"] for n in strips[j] + strips[j + 1] if n in lv.by_name}
        w = lv.rules["aisle_forklift"] if kinds & specs.FORKLIFT_TYPES else lv.rules["corridor"]
        if stairs and j == 0:
            w += STAIR_WIDTH
        out.append(float(w))
    return out


def layout(lv, strips, fixed=None):
    """Rectangles (in u, v) for an arrangement. `fixed` = (thicknesses, aisle widths) to reuse
    the ground floor's strips upstairs."""
    f = lv.f
    if fixed:
        thick, widths = fixed
    else:
        widths = aisle_widths(lv, strips, lv.multi)
        areas = [sum(lv.by_name[n]["area"] for n in st) for st in strips]
        total = sum(areas) or 1.0
        free = max(f.S - sum(widths), 1.0)
        thick = [free * a / total for a in areas]
    rects, v = {}, 0.0
    for j, st in enumerate(strips):
        t = thick[j]
        a_strip = sum(lv.by_name[n]["area"] for n in st) or 1.0
        u = 0.0
        for n in st:
            ln = f.L * lv.by_name[n]["area"] / a_strip
            rects[n] = (u, v, u + ln, v + t, j)
            u += ln
        v += t + (widths[j] if j < len(widths) else 0.0)
    return rects, thick, widths


def score(lv, strips, rects, target_scale=None):
    f, S, L = lv.f, lv.f.S, lv.f.L
    k = len(strips)
    pen, parts = 0.0, {"shape": 0.0, "exterior": 0.0, "closeness": 0.0, "flow": 0.0, "area": 0.0}
    for n, (u0, v0, u1, v1, j) in rects.items():
        sp = lv.by_name[n]
        ln, t = u1 - u0, v1 - v0
        lo, hi = min(ln, t), max(ln, t)
        r = hi / max(lo, 1e-6)
        parts["shape"] += max(0.0, r - sp["max_aspect"]) ** 2 * 30.0
        if lo < sp["min_width"]:
            parts["shape"] += (sp["min_width"] - lo) ** 2 * 20.0
        touches = set()
        if j == 0:
            touches.add("v0")
        if j == k - 1:
            touches.add("v1")
        st = strips[j]
        if st[0] == n:
            touches.add("u0")
        if st[-1] == n:
            touches.add("u1")
        need = sp["exterior"]
        if need is True and not touches:
            parts["exterior"] += 200.0
        elif isinstance(need, str):
            loc = f.local(need)
            if loc not in touches:
                parts["exterior"] += 200.0
            elif sp.get("docks"):
                edge = ln if loc in ("v0", "v1") else t
                deficit = sp["docks"] * lv.rules["dock_spacing"] - edge
                if deficit > 0:
                    parts["exterior"] += deficit * 30.0
        if target_scale is not None:
            parts["area"] += abs(ln * t - sp["area"] * target_scale) / (sp["area"] * target_scale) * 40.0

    def centre(n):
        u0, v0, u1, v1, _ = rects[n]
        return (u0 + u1) / 2, (v0 + v1) / 2

    for a, b, w in lv.relations:
        ra, rb = rects[a], rects[b]
        close = 0.0
        if ra[4] == rb[4]:
            st = strips[ra[4]]
            if abs(st.index(a) - st.index(b)) == 1:
                close = 1.0
        elif abs(ra[4] - rb[4]) == 1:
            overlap = min(ra[2], rb[2]) - max(ra[0], rb[0])
            if overlap > 0:
                close = 0.7 * overlap / max(min(ra[2] - ra[0], rb[2] - rb[0]), 1e-6)
        (ua, va), (ub, vb) = centre(a), centre(b)
        near = 1.0 - (abs(ua - ub) + abs(va - vb)) / (L + S)
        if w > 0:
            parts["closeness"] -= w * (close + 0.4 * near)
        elif w < 0:
            parts["closeness"] += -w * (close * 8.0 + near * 2.0)
    for a, b, w in lv.flow:
        (ua, va), (ub, vb) = centre(a), centre(b)
        parts["flow"] += w / lv.flow_total * (abs(ua - ub) + abs(va - vb)) / (L + S) * 60.0
    pen = sum(parts.values())
    return pen, parts


# ---- search -----------------------------------------------------------------------------------

def _initial(lv, k, rng):
    """Flow order (a chain through the flow graph), dealt snake-wise into k strips."""
    names = [sp["name"] for sp in lv.spaces]
    succ = {}
    for a, b, w in sorted(lv.flow, key=lambda x: -x[2]):
        succ.setdefault(a, b)
    has_in = {b for _, b, _ in lv.flow}
    order, seen = [], set()
    for start in [n for n in names if n not in has_in] + names:
        n = start
        while n and n not in seen:
            order.append(n)
            seen.add(n)
            n = succ.get(n)
    total = sum(lv.by_name[n]["area"] for n in order)
    # fill strips in flow order, moving on when a strip has its share of the area or when
    # the spaces left are only just enough to give every remaining strip one
    strips, j, filled = [[] for _ in range(k)], 0, 0.0
    for idx, n in enumerate(order):
        left_spaces, left_strips = len(order) - idx, k - j
        if strips[j] and j < k - 1 and (filled >= total / k * 0.85 or left_spaces <= left_strips - 1):
            j, filled = j + 1, 0.0
        strips[j].append(n)
        filled += lv.by_name[n]["area"]
    strips = [st for st in strips if st]
    for j in range(1, len(strips), 2):
        strips[j].reverse()
    return strips


def _neighbour(strips, rng):
    s = [list(st) for st in strips]
    move = rng.random()
    flat = [(j, i) for j, st in enumerate(s) for i in range(len(st))]
    if move < 0.4 and len(flat) > 1:
        (j1, i1), (j2, i2) = rng.sample(flat, 2)
        s[j1][i1], s[j2][i2] = s[j2][i2], s[j1][i1]
    elif move < 0.7 and len(s) > 1:
        j1 = rng.randrange(len(s))
        if len(s[j1]) > 1:
            n = s[j1].pop(rng.randrange(len(s[j1])))
            j2 = rng.choice([j for j in range(len(s)) if j != j1])
            s[j2].insert(rng.randrange(len(s[j2]) + 1), n)
    elif move < 0.85:
        j = rng.randrange(len(s))
        s[j].reverse()
    elif len(s) > 1:
        j1, j2 = rng.sample(range(len(s)), 2)
        s[j1], s[j2] = s[j2], s[j1]
    return s


def anneal(lv, strips, rng, iterations, fixed=None, target_scale=None, t0=4.0):
    def cost(st):
        rects, _, _ = layout(lv, st, fixed)
        return score(lv, st, rects, target_scale)[0]
    cur, cur_cost = strips, cost(strips)
    best, best_cost = cur, cur_cost
    for it in range(iterations):
        t = t0 * (0.002 / t0) ** (it / max(iterations - 1, 1))
        cand = _neighbour(cur, rng)
        c = cost(cand)
        if c < cur_cost or rng.random() < math.exp(-(c - cur_cost) / max(t, 1e-9)):
            cur, cur_cost = cand, c
            if c < best_cost:
                best, best_cost = cand, c
    return best, best_cost


def strip_counts(lv):
    n = len(lv.spaces)
    S = lv.f.S
    options = [k for k in range(2, 6) if k <= n and 4.0 <= (S - (k - 1) * 3.0) / k <= 32.0]
    return options or [min(2, n) if n >= 2 else 1]


# ---- the plan ---------------------------------------------------------------------------------

def plan(spec_in, seed=1, iterations=2500, assignment=None, locked=False):
    """Solve a spec. `assignment` ({level: strips}) warm-starts the search (an edit re-solves
    from the previous plan); locked=True keeps it exactly."""
    s, problems = specs.normalize(spec_in)
    errors = [p for p in problems if p["severity"] == "error"]
    if errors:
        return {"ok": False, "problems": problems}
    site = s["site"]
    frame = Frame(*site["size"])
    rng = random.Random(seed)
    chosen, results = {}, {}
    lv0 = Level(s, 0, frame)
    if assignment and "0" in assignment:
        start = [list(st) for st in assignment["0"]]
        best = start if locked else anneal(lv0, start, rng, max(iterations // 3, 200), t0=0.6)[0]
    else:
        cands = []
        for k in strip_counts(lv0):
            init = _initial(lv0, k, rng)
            st, c = anneal(lv0, init, rng, iterations)
            cands.append((c, k, st))
        best = min(cands, key=lambda x: (x[0], x[1]))[2]
    chosen["0"] = best
    rects0, thick, widths = layout(lv0, best)
    fixed = (thick, widths)
    results[0] = (lv0, best, rects0)
    for level in range(1, site["levels"]):
        lv = Level(s, level, frame)
        k = len(best)
        free_area = sum(thick) * frame.L
        scale = free_area / (sum(sp["area"] for sp in lv.spaces) or 1.0)
        if assignment and str(level) in assignment and len(assignment[str(level)]) == k:
            st = [list(x) for x in assignment[str(level)]]
            if not locked:
                st = anneal(lv, st, rng, max(iterations // 3, 200), fixed, scale, t0=0.6)[0]
        else:
            # the ground floor's strips; a strip upstairs may stay empty (open to below)
            init = _initial(lv, k, rng)
            init += [[] for _ in range(k - len(init))]
            st = anneal(lv, init, rng, iterations, fixed, scale)[0]
        chosen[str(level)] = st
        results[level] = (lv, st, layout(lv, st, fixed)[0])
    out = _geometry(s, frame, results, fixed)
    out.update(ok=True, seed=seed, assignment=chosen, problems=problems, spec=s)
    out["grid_view"] = grid_view(out)
    return out


def _snap(x, g):
    return round(round(x / g) * g, 6)


def _geometry(s, f, results, fixed):
    site, rules = s["site"], s["rules"]
    g = site["grid"]
    thick, widths = fixed
    k = len(thick)
    # strip and aisle boundaries across (v), snapped once so every floor shares them
    # the strip's far edge snaps to the grid; the aisle after it keeps its exact width
    bounds, v = [], 0.0
    for j in range(k):
        v0 = v
        v1 = max(_snap(v0 + thick[j], g), v0 + g)
        bounds.append((round(v0, 6), v1))
        v = v1 + (widths[j] if j < len(widths) else 0.0)
    bounds[-1] = (bounds[-1][0], f.S)
    aisles_uv = [(bounds[j][1], bounds[j + 1][0]) for j in range(k - 1)]
    multi = site["levels"] > 1
    fh = site["floor_height"] if multi else site["clear_height"]
    levels_out, scores = [], {}
    stair_len = None
    if multi:
        steps = math.ceil(fh / STEP_RISE)
        stair_len = round(steps * STEP_RUN, 3)
    for level, (lv, strips, rects) in sorted(results.items()):
        z = level * fh
        spaces, walls, openings = [], [], []
        pen, parts = score(lv, strips, rects, None)
        scores[str(level)] = {"total": round(pen, 2), **{k2: round(v2, 2) for k2, v2 in parts.items()}}
        snapped = {}
        for j, st in enumerate(strips):
            v0, v1 = bounds[j]
            u = 0.0
            for i, n in enumerate(st):
                u0 = u
                u1 = f.L if i == len(st) - 1 else _snap(rects[n][2], g)
                snapped[n] = (u0, v0, u1, v1, j, i, len(st))
                u = u1
        for n, (u0, v0, u1, v1, j, i, cnt) in snapped.items():
            sp = lv.by_name[n]
            sides = []
            if j == 0:
                sides.append("v0")
            if j == k - 1:
                sides.append("v1")
            if i == 0:
                sides.append("u0")
            if i == cnt - 1:
                sides.append("u1")
            spaces.append({"name": n, "type": sp["type"], "enclosed": bool(sp["enclosed"]),
                           "rect": [round(c, 3) for c in f.rect(u0, v0, u1, v1)],
                           "area": round((u1 - u0) * (v1 - v0), 1), "target_area": sp["area"],
                           "strip": j, "exterior": [f.side(x) for x in sides]})
        # interior walls: every edge of every enclosed space that is not on the outside
        seen = set()

        def wall(u0, v0, u1, v1):
            if (abs(v0 - v1) < 1e-6 and (abs(v0) < 1e-6 or abs(v0 - f.S) < 1e-6)) or \
               (abs(u0 - u1) < 1e-6 and (abs(u0) < 1e-6 or abs(u0 - f.L) < 1e-6)):
                return
            key = tuple(round(c, 3) for c in (u0, v0, u1, v1))
            if key in seen:
                return
            seen.add(key)
            (x0, y0), (x1, y1) = f.xy(u0, v0), f.xy(u1, v1)
            walls.append([round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)])

        def opening(kind, u, v, along_u, width, height, sill=0.0, space=None, wall_kind="interior", faces=None):
            x, y = f.xy(u, v)
            axis = ("x" if f.along_x else "y") if along_u else ("y" if f.along_x else "x")
            openings.append({"kind": kind, "pos": [round(x, 3), round(y, 3)], "axis": axis,
                             "width": round(width, 3), "height": round(height, 3), "sill": round(sill, 3),
                             "space": space, "wall": wall_kind, "faces": faces})

        for n, (u0, v0, u1, v1, j, i, cnt) in snapped.items():
            sp = lv.by_name[n]
            if sp["enclosed"]:
                wall(u0, v0, u1, v0)
                wall(u0, v1, u1, v1)
                wall(u0, v0, u0, v1)
                wall(u1, v0, u1, v1)
                # a door onto the aisle (below the strip if there is one, else above)
                door_v = v0 if j > 0 else v1
                if k == 1:
                    door_v = None
                if door_v is not None:
                    kind, w, h = "door", rules["door_width"], rules["door_height"]
                    if sp["type"] in ("qa", "lab", "utility"):
                        kind, w = "double_door", rules["door_width"] * 2
                    if sp["type"] in specs.FORKLIFT_TYPES:
                        kind, (w, h) = "drive_in", rules["drive_in_door"]
                    if sp["type"] in ("bath",):
                        w = min(w, 0.8)
                    length = u1 - u0
                    n_doors = 2 if length > 24 and kind == "door" else 1
                    for d in range(n_doors):
                        u = u0 + length * (d + 1) / (n_doors + 1)
                        if n_doors == 1:
                            u = u0 + min(max(length * 0.5, w / 2 + 0.6), length - w / 2 - 0.6)
                        opening(kind, u, door_v, True, w, h, space=n, faces=f.side("v0" if door_v == v0 else "v1"))
            # windows on outside walls
            if sp["windows"]:
                ww, wh, sill = rules["window"]
                for side in [x for x in ("v0", "v1", "u0", "u1") if f.side(x) in
                             [f.side(y) for y in (["v0"] if j == 0 else []) + (["v1"] if j == k - 1 else [])
                              + (["u0"] if i == 0 else []) + (["u1"] if i == cnt - 1 else [])]]:
                    along = side in ("v0", "v1")
                    a0, a1 = (u0, u1) if along else (v0, v1)
                    fixed_c = {"v0": 0.0, "v1": f.S, "u0": 0.0, "u1": f.L}[side]
                    count = int((a1 - a0) // rules["window_spacing"])
                    for c in range(count):
                        a = a0 + (a1 - a0) * (c + 0.5) / count
                        if a - a0 < ww / 2 + 0.5 or a1 - a < ww / 2 + 0.5:
                            continue
                        uu, vv = (a, fixed_c) if along else (fixed_c, a)
                        opening("window", uu, vv, along, ww, wh, sill, space=n, wall_kind="exterior", faces=f.side(side))
            # docks
            if sp["type"] in ("dock", "shipping"):
                want = sp["exterior"] if isinstance(sp["exterior"], str) else None
                ext = [x for x in (["v0"] if j == 0 else []) + (["v1"] if j == k - 1 else [])
                       + (["u0"] if i == 0 else []) + (["u1"] if i == cnt - 1 else [])]
                if want and f.local(want) in ext:
                    ext = [f.local(want)]
                if ext:
                    side = ext[0]
                    along = side in ("v0", "v1")
                    a0, a1 = (u0, u1) if along else (v0, v1)
                    dw, dh = rules["dock_door"]
                    n_d = sp.get("docks") or max(1, min(4, int((a1 - a0) // rules["dock_spacing"])))
                    n_d = max(1, min(n_d, int((a1 - a0 - 2.0) // (dw + 0.6)) or 1))
                    fixed_c = {"v0": 0.0, "v1": f.S, "u0": 0.0, "u1": f.L}[side]
                    for c in range(n_d):
                        a = a0 + (a1 - a0) * (c + 0.5) / n_d
                        uu, vv = (a, fixed_c) if along else (fixed_c, a)
                        opening("dock", uu, vv, along, dw, dh, space=n, wall_kind="exterior", faces=f.side(side))
        # openings between neighbouring spaces rated A or E, when a wall is between them
        for a, b, r in lv.relations:
            if r < 3.0:
                continue
            sa, sb = snapped[a], snapped[b]
            if sa[4] != sb[4] or abs(sa[5] - sb[5]) != 1:
                continue
            if not (lv.by_name[a]["enclosed"] or lv.by_name[b]["enclosed"]):
                continue
            u = sa[2] if sa[5] < sb[5] else sa[0]
            v0, v1 = sa[1], sa[3]
            w = min(1.5, (v1 - v0) / 2)
            opening("opening", u, (v0 + v1) / 2, False, w, rules["door_height"], space=f"{a}/{b}")
        # exits at both ends of every aisle, a drive-in door at one end of the first
        aisles = []
        stairs, holes = [], []
        for ai, (a0, a1) in enumerate(aisles_uv):
            w = a1 - a0
            kinds = {lv.by_name[n]["type"] for n in strips[ai] + strips[ai + 1] if n in lv.by_name}
            clear0 = a0 + (STAIR_WIDTH if multi and ai == 0 else 0.0)
            aisles.append({"index": ai, "rect": [round(c, 3) for c in f.rect(0.0, a0, f.L, a1)],
                           "width": round(w, 3), "clear_width": round(a1 - clear0, 3),
                           "kind": "forklift" if kinds & specs.FORKLIFT_TYPES else "corridor"})
            if level == 0:
                mid = (clear0 + a1) / 2
                drive = site["drive_in"] and ai == 0
                opening("exit", 0.0, mid, False, rules["door_width"] * (2 if kinds & specs.FORKLIFT_TYPES else 1),
                        rules["door_height"], space=f"aisle {ai}", wall_kind="exterior", faces=f.side("u0"))
                if drive:
                    dw = min(rules["drive_in_door"][0], a1 - clear0 - 0.3)
                    opening("drive_in", f.L, mid, False, dw, rules["drive_in_door"][1], space=f"aisle {ai}",
                            wall_kind="exterior", faces=f.side("u1"))
                    # an overhead door is not an exit: a personnel door beside it, in the end
                    # wall of whichever neighbouring space is open floor
                    below, above = strips[ai][-1] if strips[ai] else None, strips[ai + 1][-1] if strips[ai + 1] else None
                    beside = None
                    if below and not lv.by_name[below]["enclosed"]:
                        beside = a0 - 1.2
                    elif above and not lv.by_name[above]["enclosed"]:
                        beside = a1 + 1.2
                    if beside is not None:
                        opening("exit", f.L, beside, False, rules["door_width"], rules["door_height"],
                                space=f"aisle {ai}", wall_kind="exterior", faces=f.side("u1"))
                else:
                    opening("exit", f.L, mid, False, rules["door_width"] * (2 if kinds & specs.FORKLIFT_TYPES else 1),
                            rules["door_height"], space=f"aisle {ai}", wall_kind="exterior", faces=f.side("u1"))
            if multi and ai == 0:
                su0, su1 = 1.0, 1.0 + stair_len
                sv = a0 + STAIR_WIDTH / 2
                if level < site["levels"] - 1:
                    (x0, y0), (x1, y1) = f.xy(su0, sv), f.xy(su1, sv)
                    stairs.append({"rect": [round(c, 3) for c in f.rect(su0, a0, su1, a0 + STAIR_WIDTH)],
                                   "from": [round(x0, 3), round(y0, 3), round(z, 3)],
                                   "to": [round(x1, 3), round(y1, 3), round(z + fh, 3)],
                                   "width": STAIR_WIDTH})
                if level > 0:
                    holes.append([round(c, 3) for c in f.rect(su0 - 0.1, a0, su1 + 0.1, a0 + STAIR_WIDTH)])
        levels_out.append({"level": level, "z": round(z, 3), "height": fh, "spaces": spaces, "aisles": aisles,
                           "walls": walls, "openings": openings, "stairs": stairs, "holes": holes})
    columns = []
    cg = site["column_grid"]
    if cg:
        W, D = site["size"]
        nx, ny = int(W // cg[0]), int(D // cg[1])
        ox, oy = (W - nx * cg[0]) / 2, (D - ny * cg[1]) / 2
        base = levels_out[0]
        for ix in range(1, nx + (1 if ox > 1.0 else 0)):
            for iy in range(1, ny + (1 if oy > 1.0 else 0)):
                x, y = ox + ix * cg[0], oy + iy * cg[1]
                if not (0.5 < x < W - 0.5 and 0.5 < y < D - 0.5):
                    continue
                if any(a["rect"][0] - 0.3 <= x <= a["rect"][2] + 0.3 and a["rect"][1] - 0.3 <= y <= a["rect"][3] + 0.3
                       for a in base["aisles"]):
                    continue
                if any(_near_segment(x, y, w) < 0.5 for w in base["walls"]):
                    continue
                columns.append([round(x, 3), round(y, 3)])
    return {"version": 1, "site": site, "long_axis": "x" if f.along_x else "y", "levels": levels_out,
            "columns": columns, "scores": scores}


def _near_segment(x, y, w):
    x0, y0, x1, y1 = w
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / L2))
    return math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))


# ---- reading a plan ---------------------------------------------------------------------------

SYMBOLS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def grid_view(p, max_cols=72):
    """The plan as text, north at the top: a letter per space, '.' for aisles, '+' columns,
    'D' docks, '>' exits, '=' drive-in doors, '/' stairs. For an assistant to read at a glance."""
    W, D = p["site"]["size"]
    cell = max(p["site"]["grid"], W / max_cols)
    cols, rows = max(1, int(round(W / cell))), max(1, int(round(D / (cell * 2))))
    ch = D / rows
    out = []
    for lvl in p["levels"]:
        legend = {}
        grid = [[" "] * cols for _ in range(rows)]
        for i, sp in enumerate(lvl["spaces"]):
            sym = SYMBOLS[i % len(SYMBOLS)]
            legend[sym] = f"{sp['name']} ({sp['type']}, {sp['area']:.0f} m2{', walled' if sp['enclosed'] else ''})"
            x0, y0, x1, y1 = sp["rect"]
            _fill(grid, x0, y0, x1, y1, cell, ch, rows, cols, sym)
        for a in lvl["aisles"]:
            _fill(grid, *a["rect"], cell, ch, rows, cols, ".")
        for st in lvl["stairs"]:
            _fill(grid, *st["rect"], cell, ch, rows, cols, "/")
        for o in lvl["openings"]:
            mark = {"dock": "D", "exit": ">", "drive_in": "="}.get(o["kind"])
            if mark:
                _put(grid, o["pos"][0], o["pos"][1], cell, ch, rows, cols, mark)
        if lvl["level"] == 0:
            for c in p["columns"]:
                _put(grid, c[0], c[1], cell, ch, rows, cols, "+")
        out.append(f"level {lvl['level']} ({W:g} x {D:g} m, one character = {cell:.2g} m across, {ch:.2g} m down; north up)")
        out.extend("".join(r) for r in reversed(grid))
        out.extend(f"  {k} = {v}" for k, v in legend.items())
    return "\n".join(out)


def _fill(grid, x0, y0, x1, y1, cw, ch, rows, cols, sym):
    for r in range(rows):
        yc = (r + 0.5) * ch
        if not y0 <= yc < y1:
            continue
        for c in range(cols):
            xc = (c + 0.5) * cw
            if x0 <= xc < x1:
                grid[r][c] = sym


def _put(grid, x, y, cw, ch, rows, cols, sym):
    c = min(max(int(x / cw), 0), cols - 1)
    r = min(max(int(y / ch), 0), rows - 1)
    grid[r][c] = sym


def space(p, name, level=None):
    for lvl in p["levels"]:
        if level is not None and lvl["level"] != level:
            continue
        for sp in lvl["spaces"]:
            if sp["name"] == name:
                return lvl, sp
    raise KeyError(f"no space called {name!r} in the plan")
