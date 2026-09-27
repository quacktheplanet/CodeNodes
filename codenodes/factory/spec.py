"""The building spec: what an assistant writes (or edits) to describe a building.

    {"site": {"size": [60, 40], "levels": 1, "clear_height": 8, "column_grid": [10, 10]},
     "spaces": [{"name": "Receiving", "area": 300, "type": "dock", "exterior": "south"}, ...],
     "relations": [["Receiving", "Storage", "A"], ...],      # SLP closeness A E I O U X
     "flow": [["Receiving", "Storage", 40], ...],             # trips per day, or any weight
     "equipment": [{"kind": "cnc", "count": 6, "in": "Machining"}, ...],
     "rules": {"aisle_forklift": 3.6}}                        # overrides of RULES

`normalize(spec)` fills every default and returns (spec, problems); `problems` are dicts
{"severity": "error"|"warning", "where": "spaces[2].area", "message": ...}, written for a
reader. Nothing here needs Blender. Numbers are metres and square metres.
"""

from __future__ import annotations

import copy
import json
import os

# editable defaults with where they come from; never presented as compliance
RULES = {
    "aisle_forklift": 3.6,      # two-way forklift aisle (vehicle + load + 0.9 m each side)
    "aisle_forklift_one_way": 2.9,
    "aisle_narrow": 1.8,        # very narrow aisle trucks
    "corridor": 1.2,            # IBC 1020: 1118 mm, rounded up
    "service_clearance": 0.9,   # in front of machines and panels (NEC 110.26 working space ~0.9 m)
    "fence_clearance": 0.5,     # robot reach to the safety fence
    "max_travel": 76.0,         # IBC 1017 exit access travel, sprinklered F-1 (250 ft)
    "door_width": 0.9,
    "door_height": 2.1,
    "dock_door": [2.7, 3.0],    # 9 x 10 ft
    "dock_spacing": 4.5,        # door centre to centre
    "drive_in_door": [4.0, 4.5],
    "window": [1.5, 1.2, 0.9],  # width, height, sill
    "window_spacing": 3.0,
    "wall_exterior": 0.3,
    "wall_interior": 0.15,
    "room_height": 3.2,         # enclosed rooms inside a tall shell
}

SPACE_TYPES = {
    # type: (enclosed by default, max aspect ratio, min width, needs an exterior wall)
    "production": (False, 3.0, 8.0, False),
    "storage": (False, 5.0, 8.0, False),
    "dock": (False, 8.0, 6.0, True),        # staging along an outside wall is long and shallow
    "shipping": (False, 8.0, 6.0, True),
    "qa": (True, 2.5, 4.0, False),
    "office": (True, 2.5, 3.0, False),
    "amenity": (True, 3.0, 2.4, False),
    "utility": (True, 3.0, 2.4, False),
    "lab": (True, 2.5, 4.0, False),
    "retail": (False, 2.5, 4.0, True),
    # houses
    "living": (True, 2.0, 3.5, False),
    "kitchen": (True, 2.5, 2.4, False),
    "bedroom": (True, 2.0, 2.8, False),
    "bath": (True, 2.5, 1.6, False),
    "room": (True, 2.5, 2.4, False),
    "stair": (True, 3.0, 1.2, False),
}
FORKLIFT_TYPES = {"production", "storage", "dock", "shipping"}
RATINGS = {"A": 4.0, "E": 3.0, "I": 2.0, "O": 1.0, "U": 0.0, "X": -4.0}
SIDES = ("south", "north", "west", "east")

ARRANGEMENTS = ("auto", "row", "grid", "perimeter", "cluster", "fill")


def _problem(sev, where, msg):
    return {"severity": sev, "where": where, "message": msg}


def load_example(name):
    path = os.path.join(os.path.dirname(__file__), "examples", f"{name}.json")
    if not os.path.exists(path):
        raise KeyError(f"no example called {name!r}; there is {', '.join(examples())}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def examples():
    folder = os.path.join(os.path.dirname(__file__), "examples")
    return sorted(n[:-5] for n in os.listdir(folder) if n.endswith(".json"))


def normalize(spec):
    """Fill defaults and check the spec. Returns (normalized spec, problems)."""
    from . import equipment as eq
    problems = []
    if not isinstance(spec, dict):
        return None, [_problem("error", "", "the spec must be an object")]
    s = copy.deepcopy(spec)
    site = s.setdefault("site", {})
    size = site.get("size")
    if size is None and "footprint" in site:
        pts = site["footprint"]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        rect = {(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))}
        if len(pts) != 4 or {tuple(p) for p in pts} != rect:
            problems.append(_problem("error", "site.footprint",
                                     "only rectangular footprints are planned so far; give site.size [width, depth]"))
        size = [max(xs) - min(xs), max(ys) - min(ys)]
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or not all(isinstance(v, (int, float)) and v > 0 for v in size)):
        problems.append(_problem("error", "site.size", "site.size must be [width, depth] in metres"))
        size = [20.0, 20.0]
    site["size"] = [float(size[0]), float(size[1])]
    site.pop("footprint", None)
    house = not any(sp.get("type") in FORKLIFT_TYPES for sp in s.get("spaces", []) if isinstance(sp, dict))
    site.setdefault("kind", "house" if house else "industrial")
    site.setdefault("levels", 1)
    site.setdefault("clear_height", 2.8 if house else 8.0)
    site.setdefault("floor_height", site["clear_height"] + (0.3 if house else 0.0))
    site.setdefault("column_grid", None if house else [10.0, 10.0])
    site.setdefault("grid", 0.5 if house else 1.0)
    site.setdefault("drive_in", not house)
    if not isinstance(site["levels"], int) or not 1 <= site["levels"] <= 4:
        problems.append(_problem("error", "site.levels", "levels must be 1 to 4"))
        site["levels"] = 1
    cg = site["column_grid"]
    if cg is not None and (not isinstance(cg, (list, tuple)) or len(cg) != 2 or min(cg) < 3):
        problems.append(_problem("warning", "site.column_grid", "column_grid should be [dx, dy] of at least 3 m; columns left out"))
        site["column_grid"] = None

    rules = dict(RULES)
    for k, v in (s.get("rules") or {}).items():
        if k not in RULES:
            problems.append(_problem("warning", f"rules.{k}", f"unknown rule {k!r} ignored; rules are {', '.join(RULES)}"))
            continue
        rules[k] = v
    s["rules"] = rules

    spaces = s.get("spaces")
    if not isinstance(spaces, list) or not spaces:
        problems.append(_problem("error", "spaces", "give at least one space"))
        spaces = []
    names = set()
    for i, sp in enumerate(spaces):
        where = f"spaces[{i}]"
        if not isinstance(sp, dict):
            problems.append(_problem("error", where, "each space must be an object"))
            continue
        name = sp.get("name")
        if not name or not isinstance(name, str):
            problems.append(_problem("error", f"{where}.name", "every space needs a name"))
            sp["name"] = name = f"Space {i + 1}"
        if name in names:
            problems.append(_problem("error", f"{where}.name", f"two spaces are called {name!r}"))
        names.add(name)
        kind = sp.setdefault("type", "room")
        if kind not in SPACE_TYPES:
            problems.append(_problem("error", f"{where}.type",
                                     f"unknown type {kind!r}; use one of {', '.join(SPACE_TYPES)}"))
            sp["type"] = kind = "room"
        enclosed, max_aspect, min_width, exterior = SPACE_TYPES[kind]
        area = sp.get("area")
        if not isinstance(area, (int, float)) or area <= 0:
            problems.append(_problem("error", f"{where}.area", f"{name}: area must be a positive number of m²"))
            sp["area"] = 20.0
        sp["area"] = float(sp["area"])
        sp.setdefault("enclosed", enclosed)
        sp.setdefault("max_aspect", max_aspect)
        sp.setdefault("min_width", min_width)
        ext = sp.get("exterior", exterior)
        if ext not in (True, False, None) and ext not in SIDES:
            problems.append(_problem("error", f"{where}.exterior", f"exterior is true, false or one of {', '.join(SIDES)}"))
            ext = True
        sp["exterior"] = ext or False
        sp.setdefault("windows", kind in ("office", "living", "bedroom", "kitchen", "lab"))
        if sp["windows"] and not sp["exterior"]:
            sp["exterior"] = True
        sp.setdefault("level", 0)
        if not isinstance(sp["level"], int) or not 0 <= sp["level"] < site["levels"]:
            problems.append(_problem("error", f"{where}.level", f"{name}: level must be 0..{site['levels'] - 1}"))
            sp["level"] = 0
        if kind in ("dock", "shipping"):
            sp.setdefault("docks", None)
        if min_width * min_width > sp["area"]:
            problems.append(_problem("warning", f"{where}.area",
                                     f"{name}: {sp['area']:.0f} m² is small for a {kind} (min width {min_width} m)"))
    s["spaces"] = [sp for sp in spaces if isinstance(sp, dict)]

    W, D = site["size"]
    for lvl in range(site["levels"]):
        total = sum(sp["area"] for sp in s["spaces"] if sp["level"] == lvl)
        # a rough allowance for aisles between strips of spaces
        usable = W * D * (0.8 if not house else 0.85)
        if total > usable * 1.15:
            problems.append(_problem("warning", "spaces",
                                     f"level {lvl}: spaces add up to {total:.0f} m² but about {usable:.0f} m² "
                                     "is usable after aisles; they will be shrunk to fit"))
        elif total and total < usable * 0.6:
            problems.append(_problem("warning", "spaces",
                                     f"level {lvl}: spaces add up to only {total:.0f} m² of about {usable:.0f} m²; "
                                     "they will be enlarged to fill the building"))
        if not total and site["levels"] > 1:
            problems.append(_problem("error", "spaces", f"level {lvl} has no spaces"))

    def pair_list(key, check_third):
        out = []
        for i, item in enumerate(s.get(key) or []):
            where = f"{key}[{i}]"
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                problems.append(_problem("error", where, f"each {key[:-1]} is [space, space, value]"))
                continue
            a, b, v = item
            bad = [n for n in (a, b) if n not in names]
            if bad:
                problems.append(_problem("error", where, f"no space called {bad[0]!r}"))
                continue
            msg = check_third(v)
            if msg:
                problems.append(_problem("error", where, msg))
                continue
            out.append([a, b, v])
        return out

    s["relations"] = pair_list("relations", lambda v: None if v in RATINGS else
                               f"closeness must be one of {' '.join(RATINGS)}")
    s["flow"] = pair_list("flow", lambda v: None if isinstance(v, (int, float)) and v >= 0 else
                          "flow must be a number of trips (or a weight) of 0 or more")

    kinds = eq.GENERATORS
    items = []
    for i, it in enumerate(s.get("equipment") or []):
        where = f"equipment[{i}]"
        if not isinstance(it, dict) or it.get("kind") not in kinds:
            problems.append(_problem("error", f"{where}.kind",
                                     f"unknown equipment {it.get('kind') if isinstance(it, dict) else it!r}; "
                                     f"there is {', '.join(sorted(kinds))}"))
            continue
        it = dict(it)
        if it["kind"] == "conveyor" and ("from" in it or "to" in it):
            bad = [n for n in (it.get("from"), it.get("to")) if n not in names]
            if bad:
                problems.append(_problem("error", where, f"conveyor end {bad[0]!r} is not a space"))
                continue
            it.setdefault("in", it["from"])
        if it.get("in") not in names:
            problems.append(_problem("error", f"{where}.in", f"equipment needs \"in\": the name of a space"))
            continue
        it.setdefault("count", "fill" if it["kind"] == "rack" else 1)
        if it["count"] != "fill" and (not isinstance(it["count"], int) or it["count"] < 1):
            problems.append(_problem("error", f"{where}.count", "count is a whole number of 1 or more, or \"fill\""))
            continue
        it.setdefault("arrange", "auto")
        if it["arrange"] not in ARRANGEMENTS:
            problems.append(_problem("error", f"{where}.arrange", f"arrange is one of {', '.join(ARRANGEMENTS)}"))
            it["arrange"] = "auto"
        params = it.setdefault("params", {})
        try:
            eq.get(it["kind"]).params.resolve(params)
        except KeyError as exc:
            problems.append(_problem("error", f"{where}.params", str(exc).strip('"')))
            it["params"] = {}
        if it["kind"] == "robot_arm":
            it.setdefault("fenced", True)
            it.setdefault("fixture", True)
        if it["kind"] == "rack":
            aisle = it.setdefault("aisle", "forklift_two_way")
            widths = {"forklift_two_way": rules["aisle_forklift"], "forklift_one_way": rules["aisle_forklift_one_way"],
                      "narrow": rules["aisle_narrow"]}
            if aisle not in widths and not isinstance(aisle, (int, float)):
                problems.append(_problem("error", f"{where}.aisle", f"aisle is one of {', '.join(widths)} or metres"))
                aisle = "forklift_two_way"
            it["aisle_width"] = float(widths.get(aisle, aisle))
        items.append(it)
    s["equipment"] = items
    errors = [p for p in problems if p["severity"] == "error"]
    return s, problems if errors or problems else []


def check(spec):
    """Just the problems."""
    return normalize(spec)[1]
