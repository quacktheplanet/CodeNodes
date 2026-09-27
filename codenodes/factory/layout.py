"""Plan + equipment list -> where every machine, rack, robot cell and desk stands.

Each space gets a local frame: `a` runs along the edge that faces its aisle, `b` goes from
that edge into the space. Things face the aisle (their front, -Y, towards it) unless they
stand against a side wall. Placement is a constraint search, in this order:

    1. conveyors (they link spaces, so they go first; across an aisle they become an
       overhead bridge conveyor, clear of forklifts)
    2. robot cells: a fenced square of 2 x (reach + fence clearance), arm in the middle,
       a fixture table in reach, the gate towards the aisle
    3. racks ("fill"): rows at right angles to the aisle, back to back, forklift aisles
       between them, every face on an aisle
    4. rows, grids, clusters and perimeter items: the first free spot in preference order

A spot is free when the footprint stays inside the space, clear of every other footprint,
column, door approach and dock approach, and its service clearance (front, back, sides)
stays inside the space and clear of every other footprint. Clearances may overlap each
other — two machines can share a service aisle. What does not fit is reported, never
forced. Pure Python; the same inputs give the same layout.

    solve(plan, seed=1) -> layout dict (items with world positions, unplaced, rack aisles)
"""

from __future__ import annotations

import math

from . import equipment as eq

EPS = 1e-6
STEP = 0.25
OVERHEAD_Z = 4.6


# ---- rectangles -------------------------------------------------------------------------------

def overlap(r1, r2, pad=0.0):
    return (r1[0] < r2[2] - pad - EPS and r2[0] < r1[2] - pad - EPS and
            r1[1] < r2[3] - pad - EPS and r2[1] < r1[3] - pad - EPS)


def inside(r, box, pad=0.0):
    return (r[0] >= box[0] + pad - EPS and r[1] >= box[1] + pad - EPS and
            r[2] <= box[2] - pad + EPS and r[3] <= box[3] - pad + EPS)


def area(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


# ---- a space's frame ----------------------------------------------------------------------------

class SpaceFrame:
    """Local (a, b) coordinates for one space: a along the aisle edge, b into the space."""

    def __init__(self, sp, level):
        self.sp, self.level = sp, level
        x0, y0, x1, y1 = sp["rect"]
        self.rect = sp["rect"]
        side = None
        for ai in level["aisles"]:
            r = ai["rect"]
            if abs(r[3] - y0) < 1e-3 and r[0] < x1 and r[2] > x0:
                side = "south"
            elif abs(r[1] - y1) < 1e-3 and r[0] < x1 and r[2] > x0:
                side = "north"
            elif abs(r[2] - x0) < 1e-3 and r[1] < y1 and r[3] > y0:
                side = "west"
            elif abs(r[0] - x1) < 1e-3 and r[1] < y1 and r[3] > y0:
                side = "east"
            if side:
                break
        self.aisle_side = side or "south"
        s = self.aisle_side
        if s == "south":
            self.origin, self.t, self.n = (x0, y0), (1, 0), (0, 1)
        elif s == "north":
            self.origin, self.t, self.n = (x1, y1), (-1, 0), (0, -1)
        elif s == "west":
            self.origin, self.t, self.n = (x0, y1), (0, -1), (1, 0)
        else:
            self.origin, self.t, self.n = (x1, y0), (0, 1), (-1, 0)
        self.angle = {"south": 0, "north": 180, "west": -90, "east": 90}[s]
        self.A = abs((x1 - x0) * self.t[0] + (y1 - y0) * self.t[1])
        self.B = abs((x1 - x0) * self.n[0] + (y1 - y0) * self.n[1])

    def world(self, a, b):
        return (self.origin[0] + a * self.t[0] + b * self.n[0],
                self.origin[1] + a * self.t[1] + b * self.n[1])

    def local(self, x, y):
        dx, dy = x - self.origin[0], y - self.origin[1]
        return dx * self.t[0] + dy * self.t[1], dx * self.n[0] + dy * self.n[1]

    def world_rect(self, r):
        (xa, ya), (xb, yb) = self.world(r[0], r[1]), self.world(r[2], r[3])
        return [round(min(xa, xb), 4), round(min(ya, yb), 4), round(max(xa, xb), 4), round(max(ya, yb), 4)]

    def local_rect(self, r):
        (a0, b0), (a1, b1) = self.local(r[0], r[1]), self.local(r[2], r[3])
        return [min(a0, a1), min(b0, b1), max(a0, a1), max(b0, b1)]


# ---- things to place ----------------------------------------------------------------------------

class Thing:
    """One item in a space's local frame. rot is its turn in the local frame (0 = front to
    the aisle, 90 = front to +a, -90 = front to -a, 180 = front away from the aisle)."""

    def __init__(self, kind, values, info, role="item", group=None):
        self.kind, self.values, self.info = kind, values, info
        self.role, self.group = role, group
        self.a = self.b = 0.0
        self.rot = 0
        self.children = []          # (Thing, da, db) moved with this one (robot cells)
        self.solids_local = None    # override: list of rects relative to the centre

    def size(self):
        sx, sy = self.info["footprint"]
        return (sy, sx) if self.rot in (90, -90, 270) else (sx, sy)

    def rect(self, a=None, b=None):
        a = self.a if a is None else a
        b = self.b if b is None else b
        w, d = self.size()
        return [a - w / 2, b - d / 2, a + w / 2, b + d / 2]

    def clear_rect(self, a=None, b=None):
        r = self.rect(a, b)
        c = self.info["clearance"]
        front, back, sides = c.get("front", 0), c.get("back", 0), c.get("sides", 0)
        # which local side the item's front faces
        if self.rot == 0:
            return [r[0] - sides, r[1] - front, r[2] + sides, r[3] + back]
        if self.rot == 180:
            return [r[0] - sides, r[1] - back, r[2] + sides, r[3] + front]
        if self.rot == 90:
            return [r[0] - back, r[1] - sides, r[2] + front, r[3] + sides]
        return [r[0] - front, r[1] - sides, r[2] + back, r[3] + sides]

    def solids(self, a=None, b=None):
        if self.solids_local is None:
            return [self.rect(a, b)]
        a = self.a if a is None else a
        b = self.b if b is None else b
        return [[a + r[0], b + r[1], a + r[2], b + r[3]] for r in self.solids_local]


def make_thing(kind, params=None, role="item"):
    m, info = eq.build(kind, params)
    return Thing(kind, info["values"], info, role)


# ---- the per-space solver -----------------------------------------------------------------------

class SpaceSolver:
    def __init__(self, fr, rules, columns, notes):
        self.fr, self.rules, self.notes = fr, rules, notes
        enclosed = fr.sp["enclosed"]
        self.margin = 0.25 if enclosed else 0.4          # clear of the walls, which stand on the edges
        self.front = 0.3                         # paint line gap to the aisle
        self.bounds = [self.margin, self.front, fr.A - self.margin, fr.B - self.margin]
        self.clear_bounds = [0.0, 0.0, fr.A, fr.B]
        self.obstacles = []
        for c in columns:
            a, b = fr.local(*c)
            if -0.5 <= a <= fr.A + 0.5 and -0.5 <= b <= fr.B + 0.5:
                self.obstacles.append([a - 0.4, b - 0.4, a + 0.4, b + 0.4])
        self.placed = []
        self.rack_aisles = []
        self.item_solids = []       # fixed things when re-placing: nothing may overlap them
        self.soft = []              # their working space: new footprints keep out of it

    def add_approach(self, opening, depth, extra):
        """Keep the floor in front of a door or dock free."""
        x, y = opening["pos"]
        a, b = self.fr.local(x, y)
        w = opening["width"] + extra
        if opening["axis"] == ("x" if self.fr.t[0] else "y"):       # the wall runs along a
            b_in = 0.0 if abs(b) < abs(b - self.fr.B) else self.fr.B
            r = [a - w / 2, b_in, a + w / 2, b_in + depth] if b_in == 0.0 else [a - w / 2, b_in - depth, a + w / 2, b_in]
        else:
            a_in = 0.0 if abs(a) < abs(a - self.fr.A) else self.fr.A
            r = [a_in, b - w / 2, a_in + depth, b + w / 2] if a_in == 0.0 else [a_in - depth, b - w / 2, a_in, b + w / 2]
        self.obstacles.append(r)

    def free(self, t, a, b):
        solids = t.solids(a, b)
        cr = t.clear_rect(a, b)
        if not all(inside(s, self.bounds) for s in solids):
            return False
        if not inside(cr, self.clear_bounds):
            return False
        for s in solids:
            if any(overlap(s, o) for o in self.obstacles):
                return False
            if any(overlap(s, ra) for ra in self.rack_aisles):
                return False
            if any(overlap(s, o) for o in self.item_solids) or any(overlap(s, o) for o in self.soft):
                return False
        if any(overlap(cr, o) for o in self.item_solids):
            return False
        for p in self.placed:
            # a robot cell's whole square is taken; an overhead conveyor only where its legs stand
            blocks = [p.rect()] if p.role == "cell" else p.solids()
            for bl in blocks:
                if any(overlap(s, bl) for s in solids) or overlap(cr, bl):
                    return False
            if not getattr(p, "overhead", False) and any(overlap(s, p.clear_rect()) for s in solids):
                return False
        return True

    def put(self, t, a, b):
        t.a, t.b = a, b
        self.placed.append(t)

    # -- scanning -------------------------------------------------------------------------

    def scan(self, t, order="rows", b_start=None, a_start=None, rots=(0,)):
        fr = self.fr
        for rot in rots:
            t.rot = rot
            w, d = t.size()
            bs = _frange(self.bounds[1] + d / 2, self.bounds[3] - d / 2, STEP)
            as_ = _frange(self.bounds[0] + w / 2, self.bounds[2] - w / 2, STEP)
            if order == "back":
                bs = list(reversed(bs))
            if b_start is not None:
                bs = [b_start] + [x for x in bs if x != b_start]
            if a_start is not None:
                as_ = [a for a in as_ if a >= a_start - EPS] + [a for a in as_ if a < a_start - EPS]
            for b in bs:
                for a in as_:
                    if self.free(t, a, b):
                        return a, b
        return None

    def place_row(self, things, back=False):
        """Side by side along the aisle edge (or the back wall), front to the aisle."""
        left = []
        b_row = a_next = None
        for t in things:
            spot = None
            t.rot = 0
            if b_row is not None:
                spot = self.scan(t, b_start=b_row, a_start=a_next)
                if spot and abs(spot[1] - b_row) > EPS:
                    spot = self.scan(t, "back" if back else "rows")
            else:
                spot = self.scan(t, "back" if back else "rows")
            if spot is None:
                left.append(t)
                continue
            self.put(t, *spot)
            b_row = spot[1]
            a_next = spot[0] + t.size()[0] / 2 + t.info["clearance"].get("sides", 0) + t.size()[0] / 2
        return left

    def place_grid(self, things, spacing=0.8):
        left = []
        if not things:
            return left
        t0 = things[0]
        c = t0.info["clearance"]
        w, d = t0.size()
        step_a = w + max(2 * c.get("sides", 0), spacing)
        step_b = d + c.get("front", 0) + c.get("back", 0)
        per_row = max(1, int((self.bounds[2] - self.bounds[0] + step_a - w) // step_a))
        rows = math.ceil(len(things) / per_row)
        used_a = min(per_row, len(things)) * step_a - (step_a - w)
        a0 = (self.fr.A - used_a) / 2 + w / 2
        b0 = self.bounds[1] + c.get("front", 0) + d / 2
        k = 0
        for r in range(rows):
            for i in range(per_row):
                if k >= len(things):
                    break
                t = things[k]
                t.rot = 0
                a, b = a0 + i * step_a, b0 + r * step_b
                if self.free(t, a, b):
                    self.put(t, a, b)
                else:
                    spot = self.scan(t)
                    if spot:
                        self.put(t, *spot)
                    else:
                        left.append(t)
                k += 1
        return left

    def place_perimeter(self, things):
        """Backs to the walls: along the back wall first, then down the side walls."""
        left = []
        for t in things:
            spot = None
            for rot, order in ((0, "back"), (90, "side_left"), (-90, "side_right")):
                t.rot = rot
                w, d = t.size()
                if order == "back":
                    b = self.bounds[3] - d / 2
                    for a in _frange(self.bounds[0] + w / 2, self.bounds[2] - w / 2, STEP):
                        if self.free(t, a, b):
                            spot = (a, b)
                            break
                else:
                    a = self.bounds[0] + w / 2 if order == "side_left" else self.bounds[2] - w / 2
                    for b in reversed(_frange(self.bounds[1] + d / 2, self.bounds[3] - d / 2, STEP)):
                        if self.free(t, a, b):
                            spot = (a, b)
                            break
                if spot:
                    break
            if spot is None:
                t.rot = 0
                spot = self.scan(t, "back", rots=(0, 90, -90, 180))
            if spot is None:
                left.append(t)
            else:
                self.put(t, *spot)
        return left

    def place_racks(self, item, rules):
        """Rows of racking at right angles to the aisle edge, back to back, a forklift aisle
        between every two faces. Returns (placed, notes)."""
        params = dict(item.get("params") or {})
        aisle = item["aisle_width"]
        probe = make_thing("rack", params, role="rack")
        depth = probe.info["footprint"][1]
        bay = probe.values["bay_width"]
        run = self.bounds[3] - self.bounds[1] - 0.6
        bays = int(run // bay)
        if bays < 1:
            return 0
        params["bays"] = min(bays, 20)
        flue = 0.15
        rows = []
        a = self.bounds[0]
        rows.append((a + depth / 2, 90))
        a += depth
        limit = item["count"] if isinstance(item["count"], int) else 10 ** 6
        while len(rows) < limit:
            a += aisle
            if a + 2 * depth + flue + aisle + depth <= self.bounds[2] + EPS and len(rows) + 2 <= limit:
                rows.append((a + depth / 2, -90))
                rows.append((a + depth + flue + depth / 2, 90))
                self.rack_aisles.append([a - aisle, 0.0, a, self.fr.B])
                a += 2 * depth + flue
            elif a + depth <= self.bounds[2] + EPS:
                rows.append((a + depth / 2, -90))
                self.rack_aisles.append([a - aisle, 0.0, a, self.fr.B])
                break
            else:
                rows.pop() if len(rows) == 1 else None
                break
        placed = 0
        for ra, rot in rows:
            t = make_thing("rack", params, role="rack")
            t.rot = rot
            w, d = t.size()
            b = self.bounds[1] + 0.3 + d / 2
            # racks keep their own aisles clear, so their clearance is the rack aisle itself
            t.info = dict(t.info, clearance={"front": 0.0, "back": 0.0, "sides": 0.0})
            spot = None
            for bb in [b] + _frange(b, self.bounds[3] - d / 2, STEP):
                if self._rack_free(t, ra, bb):
                    spot = (ra, bb)
                    break
            if spot is None:
                # shorten the row by a bay until it misses the columns
                vals = dict(params)
                while vals["bays"] > 1 and spot is None:
                    vals["bays"] -= 1
                    t = make_thing("rack", vals, role="rack")
                    t.rot = rot
                    t.info = dict(t.info, clearance={"front": 0.0, "back": 0.0, "sides": 0.0})
                    w, d = t.size()
                    for bb in _frange(self.bounds[1] + 0.3 + d / 2, self.bounds[3] - d / 2, STEP):
                        if self._rack_free(t, ra, bb):
                            spot = (ra, bb)
                            break
            if spot:
                self.put(t, *spot)
                placed += 1
        return placed

    def _rack_free(self, t, a, b):
        r = t.rect(a, b)
        if not inside(r, self.bounds):
            return False
        if any(overlap(r, o) for o in self.obstacles):
            return False
        return not any(overlap(r, s) for p in self.placed for s in p.solids())

    def place_cells(self, count, item, rules):
        """Fenced robot cells in a row along the aisle, gates to the aisle."""
        arm = make_thing("robot_arm", dict(item.get("params") or {}, j1=90.0), role="robot_arm")
        reach = arm.info["reach"]
        side = math.ceil((reach + rules["fence_clearance"]) * 2 / 0.5) * 0.5
        left = 0
        for i in range(count):
            cell = make_thing("fence", {"width": side, "depth": side, "gate": 1.2}, role="cell")
            cell.info = dict(cell.info, footprint=[side, side],
                             clearance={"front": 0.8, "back": 0.3, "sides": 0.4})
            t = 0.08
            h = side / 2
            cell.solids_local = [[-h, -h - t / 2, -0.6, -h + t / 2], [0.6, -h - t / 2, h, -h + t / 2],
                                 [-h, h - t / 2, h, h + t / 2], [-h - t / 2, -h, -h + t / 2, h],
                                 [h - t / 2, -h, h + t / 2, h]]
            spot = self.scan(cell)
            if spot is None:
                left += 1
                continue
            self.put(cell, *spot)
            a_arm = make_thing("robot_arm", dict(item.get("params") or {}, j1=90.0, j2=25.0, j3=-5.0, j5=70.0),
                               role="robot_arm")
            fixture = make_thing("workbench", {"length": 1.2, "depth": 0.7, "board": 0}, role="fixture")
            cell.children = [(a_arm, 0.0, 0.0), (fixture, 0.0, min(1.25, reach - 0.4))]
            cell.group = f"cell {len([p for p in self.placed if p.role == 'cell'])}"
        return left

    def place_conveyor(self, a0, b0, a1, b1, overhead):
        length = math.hypot(a1 - a0, b1 - b0)
        params = {"length": round(length, 2)}
        if overhead:
            params.update(height=OVERHEAD_Z, leg_spacing=100.0)
        t = make_thing("conveyor", params, role="conveyor")
        t.rot = 90 if abs(b1 - b0) > abs(a1 - a0) else 0
        t.info = dict(t.info, clearance={"front": 0.0, "back": 0.0, "sides": 0.4})
        t.overhead = overhead
        if overhead:
            w, d = t.size()
            # only the legs stand on the floor
            if t.rot == 90:
                t.solids_local = [[-0.5, -d / 2, 0.5, -d / 2 + 0.2], [-0.5, d / 2 - 0.2, 0.5, d / 2]]
            else:
                t.solids_local = [[-w / 2, -0.5, -w / 2 + 0.2, 0.5], [w / 2 - 0.2, -0.5, w / 2, 0.5]]
        t.a, t.b = (a0 + a1) / 2, (b0 + b1) / 2
        self.placed.append(t)
        return t


def _new_solver(p, fr, name, rules, notes):
    sv = SpaceSolver(fr, rules, p["columns"] if fr.level["level"] == 0 else [], notes)
    for o in fr.level["openings"]:
        if o["space"] == name and o["kind"] in ("door", "double_door", "drive_in"):
            sv.add_approach(o, 1.5, 0.6)
        elif o["space"] == name and o["kind"] == "dock":
            sv.add_approach(o, 4.5, 1.0)
        elif o["kind"] == "opening" and name in (o["space"] or "").split("/"):
            sv.add_approach(o, 1.2, 0.4)
        elif o["kind"] in ("exit", "drive_in") and o["wall"] == "exterior":
            r = fr.rect
            x, y = o["pos"]
            if r[0] - 0.01 <= x <= r[2] + 0.01 and r[1] - 0.01 <= y <= r[3] + 0.01:
                sv.add_approach(o, 1.5, 0.6)
    return sv


def _frange(lo, hi, step):
    if hi < lo - EPS:
        return []
    n = int((hi - lo) / step + EPS)
    return [round(lo + i * step, 6) for i in range(n + 1)]


ROW_KINDS = {"cnc", "workbench", "tank", "conveyor", "fence"}
GRID_KINDS = {"desk", "pallet"}
PERIMETER_KINDS = {"cabinet", "forklift", "amr"}


def _arrangement(item, enclosed):
    arr = item["arrange"]
    if arr != "auto":
        return arr
    k = item["kind"]
    if k == "rack":
        return "fill"
    if k == "robot_arm":
        return "cells"
    if k in GRID_KINDS:
        return "grid"
    if k in PERIMETER_KINDS or (enclosed and k == "workbench"):
        return "perimeter"
    return "row"


# ---- whole plan ---------------------------------------------------------------------------------

def solve(p, seed=1):
    s = p["spec"]
    rules = s["rules"]
    items_out, unplaced, notes, rack_aisles = [], [], [], []
    by_space = {}
    for it in s["equipment"]:
        by_space.setdefault(it["in"], []).append(it)
    frames = {}
    for lvl in p["levels"]:
        for sp in lvl["spaces"]:
            frames[sp["name"]] = SpaceFrame(sp, lvl)
    solvers = {}

    def solver(name):
        if name not in solvers:
            solvers[name] = _new_solver(p, frames[name], name, rules, notes)
        return solvers[name]

    # 1. conveyors between spaces
    for it in s["equipment"]:
        if it["kind"] != "conveyor" or "to" not in it:
            continue
        A, B = frames[it["from"]], frames[it["to"]]
        ra, rb = A.rect, B.rect
        ox0, ox1 = max(ra[0], rb[0]), min(ra[2], rb[2])
        oy0, oy1 = max(ra[1], rb[1]), min(ra[3], rb[3])
        if ox1 - ox0 > 2.0 and (abs(ra[3] - rb[1]) < 1e-3 or abs(rb[3] - ra[1]) < 1e-3 or oy1 < oy0):
            # one above the other: a run in y, across the aisle if there is one
            x = (ox0 + ox1) / 2
            gap = oy0 - oy1 if oy1 < oy0 else 0.0
            y0, y1 = (ra[3], rb[1]) if ra[1] < rb[1] else (ra[1], rb[3])
            ya, yb = (y0 - 3.0, y1 + 3.0) if ra[1] < rb[1] else (y0 + 3.0, y1 - 3.0)
            overhead = gap > 0.1
            seg = (x, ya, x, yb)
        elif oy1 - oy0 > 2.0 and (abs(ra[2] - rb[0]) < 1e-3 or abs(rb[2] - ra[0]) < 1e-3 or ox1 < ox0):
            y = (oy0 + oy1) / 2
            gap = ox0 - ox1 if ox1 < ox0 else 0.0
            x0, x1 = (ra[2], rb[0]) if ra[0] < rb[0] else (ra[0], rb[2])
            xa, xb = (x0 - 3.0, x1 + 3.0) if ra[0] < rb[0] else (x0 + 3.0, x1 - 3.0)
            overhead = gap > 0.1
            seg = (xa, y, xb, y)
        else:
            notes.append(f"conveyor {it['from']} -> {it['to']}: the spaces do not face each other; left out")
            unplaced.append({"kind": "conveyor", "space": it["from"], "reason": "spaces not facing each other"})
            continue
        # the conveyor belongs to its first space's frame, but is checked in both
        sv = solver(it["from"])
        a0, b0 = sv.fr.local(seg[0], seg[1])
        a1, b1 = sv.fr.local(seg[2], seg[3])
        t = sv.place_conveyor(a0, b0, a1, b1, overhead)
        t.group = f"{it['from']} -> {it['to']}"
        t.spans = [it["from"], it["to"]]
        # the other space sees its end as an obstacle
        other = solver(it["to"])
        for piece in ([t.rect()] if not overhead else t.solids()):
            w = sv.fr.world_rect(piece)
            if overlap(w, other.fr.rect):
                other.obstacles.append(other.fr.local_rect([w[0] - 0.4, w[1] - 0.4, w[2] + 0.4, w[3] + 0.4]))

    # 2-4. everything else, space by space, cells and racks first
    order = {"cells": 0, "fill": 1, "row": 2, "cluster": 3, "grid": 4, "perimeter": 5}
    for name, items in by_space.items():
        sv = solver(name)
        enclosed = frames[name].sp["enclosed"]
        todo = []
        for it in items:
            if it["kind"] == "conveyor" and "to" in it:
                continue
            todo.append((order.get(_arrangement(it, enclosed), 9), it))
        for _, it in sorted(todo, key=lambda x: x[0]):
            arr = _arrangement(it, enclosed)
            if arr == "fill":
                n = sv.place_racks(it, rules)
                if n == 0:
                    unplaced.append({"kind": "rack", "space": name, "reason": "no room for a rack row"})
                continue
            if arr == "cells":
                left = sv.place_cells(it["count"], it, rules)
                if left:
                    unplaced.append({"kind": "robot_arm", "space": name, "count": left,
                                     "reason": "no room for another fenced cell"})
                continue
            things = [make_thing(it["kind"], it.get("params")) for _ in range(it["count"])]
            if arr in ("grid", "cluster"):
                left = sv.place_grid(things, 0.8 if arr == "grid" else 0.3)
            elif arr == "perimeter":
                left = sv.place_perimeter(things)
            else:
                left = sv.place_row(things)
            if left:
                unplaced.append({"kind": it["kind"], "space": name, "count": len(left),
                                 "reason": "no free spot with its clearances"})
    for name, sv in solvers.items():
        fr = sv.fr
        for ra in sv.rack_aisles:
            rack_aisles.append({"space": name, "rect": fr.world_rect(ra)})
        counters = {}
        for t in sv.placed:
            items_out.extend(_emit(t, fr, name, counters))
    return {"items": items_out, "unplaced": unplaced, "notes": notes, "rack_aisles": rack_aisles,
            "seed": seed}


def _emit(t, fr, space, counters, parent=None, da=0.0, db=0.0):
    kind = t.kind if t.role != "cell" else "fence"
    counters[kind] = counters.get(kind, 0) + 1
    ident = f"{space} {kind} {counters[kind]}"
    a, b = (t.a, t.b) if parent is None else (parent.a + da, parent.b + db)
    rot_world = (fr.angle + t.rot) % 360
    cx, cy = fr.world(a, b)
    off = t.info.get("offset", [0.0, 0.0])
    r = math.radians(rot_world)
    ox, oy = off[0] * math.cos(r) - off[1] * math.sin(r), off[0] * math.sin(r) + off[1] * math.cos(r)
    z = fr.level["z"]
    local_solids = t.solids(a, b)
    out = {"id": ident, "kind": kind, "space": space, "level": fr.level["level"], "role": t.role,
           "values": t.values, "location": [round(cx - ox, 4), round(cy - oy, 4), z],
           "rotation": rot_world, "footprint": fr.world_rect(t.rect(a, b)),
           "solids": [fr.world_rect(s) for s in local_solids],
           "clearance": fr.world_rect(t.clear_rect(a, b)), "height": t.info["height"],
           "overhead": bool(getattr(t, "overhead", False)), "group": t.group,
           "reach": t.info.get("reach"), "spans": getattr(t, "spans", None)}
    items = [out]
    for child, cda, cdb in t.children:
        child.a, child.b = a + cda, b + cdb
        sub = _emit(child, fr, space, counters)
        for s2 in sub:
            s2["group"] = t.group
        items.extend(sub)
    return items


def replace(p, lay, bad_ids, seed=2):
    """Re-place the items named in bad_ids (a robot cell moves as a whole) around everything
    else, which stays put. What finds no spot is dropped and listed as unplaced."""
    rules = p["spec"]["rules"]
    items = lay["items"]
    by_id = {it["id"]: it for it in items}
    bad = set()
    for i in bad_ids:
        it = by_id.get(i)
        if it is None:
            continue
        if it.get("group") and "cell" in str(it["group"]):
            bad.update(x["id"] for x in items if x.get("group") == it["group"] and x["space"] == it["space"])
        else:
            bad.add(i)
    keep = [it for it in items if it["id"] not in bad]
    unplaced = list(lay["unplaced"])
    frames = {sp["name"]: SpaceFrame(sp, lvl) for lvl in p["levels"] for sp in lvl["spaces"]}
    notes = list(lay.get("notes", []))
    out = list(keep)
    leaders = [by_id[i] for i in sorted(bad) if not (by_id[i].get("group") and "cell" in str(by_id[i]["group"])
                                                     and by_id[i]["kind"] != "fence")]
    for it in leaders:
        fr = frames[it["space"]]
        sv = _new_solver(p, fr, it["space"], rules, notes)
        for other in out:
            if other["level"] != it["level"]:
                continue
            for sd in other["solids"]:
                sv.item_solids.append(fr.local_rect(sd))
            if not other.get("overhead") and other["role"] not in ("rack",):
                sv.soft.append(fr.local_rect(other["clearance"]))
        for ra in lay.get("rack_aisles", []):
            if ra["space"] == it["space"]:
                sv.rack_aisles.append(fr.local_rect(ra["rect"]))
        counters = {}
        for other in out:
            if other["space"] == it["space"]:
                n = int(other["id"].rsplit(" ", 1)[-1]) if other["id"].rsplit(" ", 1)[-1].isdigit() else 0
                counters[other["kind"]] = max(counters.get(other["kind"], 0), n)
        if it["kind"] == "fence" and it.get("group") and "cell" in str(it["group"]):
            arm = next((x for x in items if x.get("group") == it["group"] and x["kind"] == "robot_arm"), None)
            params = {k: v for k, v in (arm or {}).get("values", {}).items() if not k.startswith("j")}
            left = sv.place_cells(1, {"params": params}, rules)
            if left:
                unplaced.append({"kind": "robot_arm", "space": it["space"], "count": 1,
                                 "reason": "removed while repairing: no free spot for its cell"})
                continue
        else:
            t = make_thing(it["kind"], it["values"], role=it["role"] if it["role"] != "item" else "item")
            spot = sv.scan(t, rots=(0, 90, -90, 180))
            if spot is None:
                unplaced.append({"kind": it["kind"], "space": it["space"], "count": 1,
                                 "reason": "removed while repairing: no free spot"})
                continue
            sv.put(t, *spot)
        for t in sv.placed:
            out.extend(_emit(t, fr, it["space"], counters))
    return {"items": out, "unplaced": unplaced, "notes": notes, "rack_aisles": lay.get("rack_aisles", []),
            "seed": seed}
