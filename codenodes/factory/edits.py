"""Small edits to a planned building, re-solved locally so what was not touched stays put.

    new_plan, report = apply(plan, ops, seed)

ops, applied in order:

    {"op": "swap", "a": "QA", "b": "Offices"}                exchange two spaces' places
    {"op": "move", "space": "QA", "strip": 2, "index": 0}    to a strip (0 = south/west-most),
                                                             at a position along it
    {"op": "resize", "space": "Storage", "area": 600}
    {"op": "add_space", "space": {"name": "Paint", "area": 150, "type": "production"}}
    {"op": "remove_space", "space": "Break Room"}            (its equipment goes too)
    {"op": "relation", "a": "Paint", "b": "Assembly", "rating": "I"}
    {"op": "flow", "a": "Assembly", "b": "Paint", "amount": 10}
    {"op": "rule", "name": "aisle_forklift", "value": 4.0}
    {"op": "site", "size": [70, 40]}                         (also levels, clear_height, ...)
    {"op": "add_equipment", "item": {"kind": "cnc", "count": 2, "in": "Machining"}}
    {"op": "remove_equipment", "kind": "amr", "in": "Assembly"}
    {"op": "set_equipment", "kind": "cnc", "in": "Machining", "changes": {"count": 6}}
    {"op": "resolve"}                                        forget the old arrangement

Only swaps and moves: the arrangement is kept exactly as edited. Anything else: a short,
cool re-solve that starts from the old arrangement. Pure Python.
"""

from __future__ import annotations

import copy

from . import planner


class EditError(ValueError):
    pass


def _strips_of(assign, level):
    return assign.setdefault(str(level), [])


def _find(assign, name):
    for lvl, strips in assign.items():
        for j, st in enumerate(strips):
            if name in st:
                return lvl, j, st.index(name)
    raise EditError(f"no space called {name!r} in the plan")


def apply(p, ops, seed=None):
    spec = copy.deepcopy(p["spec"])
    assign = copy.deepcopy(p["assignment"])
    seed = p.get("seed", 1) if seed is None else seed
    arrangement_only, fresh, done = True, False, []
    names = {sp["name"] for sp in spec["spaces"]}

    def space(name):
        for sp in spec["spaces"]:
            if sp["name"] == name:
                return sp
        raise EditError(f"no space called {name!r}")

    for i, op in enumerate(ops or []):
        kind = op.get("op") if isinstance(op, dict) else None
        try:
            if kind == "swap":
                la, ja, ia = _find(assign, op["a"])
                lb, jb, ib = _find(assign, op["b"])
                if la != lb:
                    raise EditError("only spaces on the same floor can swap")
                assign[la][ja][ia], assign[lb][jb][ib] = op["b"], op["a"]
            elif kind == "move":
                lv, j, idx = _find(assign, op["space"])
                strips = assign[lv]
                target = int(op["strip"])
                if not 0 <= target < len(strips):
                    raise EditError(f"strip must be 0..{len(strips) - 1}")
                if len(strips[j]) == 1 and lv == "0":
                    raise EditError("that would empty a strip; swap instead")
                strips[j].pop(idx)
                pos = op.get("index", len(strips[target]))
                strips[target].insert(max(0, min(int(pos), len(strips[target]))), op["space"])
            elif kind == "resize":
                space(op["space"])["area"] = float(op["area"])
                arrangement_only = False
            elif kind == "add_space":
                new = dict(op["space"])
                if new.get("name") in names:
                    raise EditError(f"there is already a space called {new.get('name')!r}")
                spec["spaces"].append(new)
                names.add(new["name"])
                strips = _strips_of(assign, new.get("level", 0))
                if strips:
                    smallest = min(range(len(strips)), key=lambda j: len(strips[j]))
                    strips[smallest].append(new["name"])
                arrangement_only = False
            elif kind == "remove_space":
                name = op["space"]
                space(name)
                spec["spaces"] = [sp for sp in spec["spaces"] if sp["name"] != name]
                spec["relations"] = [r for r in spec["relations"] if name not in r[:2]]
                spec["flow"] = [r for r in spec["flow"] if name not in r[:2]]
                spec["equipment"] = [e for e in spec["equipment"]
                                     if e.get("in") != name and e.get("to") != name and e.get("from") != name]
                lv, j, idx = _find(assign, name)
                assign[lv][j].pop(idx)
                assign[lv] = [st for st in assign[lv] if st] if lv == "0" else assign[lv]
                names.discard(name)
                arrangement_only = False
            elif kind in ("relation", "flow"):
                key, val = ("relations", op["rating"]) if kind == "relation" else ("flow", op["amount"])
                rows = [r for r in spec[key] if {r[0], r[1]} != {op["a"], op["b"]}]
                rows.append([op["a"], op["b"], val])
                spec[key] = rows
                arrangement_only = False
            elif kind == "rule":
                spec["rules"][op["name"]] = op["value"]
                arrangement_only = False
            elif kind == "site":
                for k, v in op.items():
                    if k != "op":
                        spec["site"][k] = v
                if "levels" in op or "size" in op:
                    fresh = True
                arrangement_only = False
            elif kind == "add_equipment":
                spec["equipment"].append(dict(op["item"]))
            elif kind in ("remove_equipment", "set_equipment"):
                hits = [e for e in spec["equipment"] if e["kind"] == op["kind"]
                        and (op.get("in") is None or e.get("in") == op["in"])]
                if not hits:
                    raise EditError(f"no {op['kind']} equipment{' in ' + op['in'] if op.get('in') else ''}")
                if kind == "remove_equipment":
                    spec["equipment"] = [e for e in spec["equipment"] if e not in hits]
                else:
                    for e in hits:
                        e.update(op.get("changes") or {})
            elif kind == "resolve":
                fresh = True
                arrangement_only = False
            else:
                raise EditError(f"unknown op {kind!r}")
            done.append(kind)
        except (KeyError, TypeError) as exc:
            raise EditError(f"ops[{i}] ({kind}): missing or bad field {exc}") from None
        except EditError as exc:
            raise EditError(f"ops[{i}] ({kind}): {exc}") from None
    # the rules the spec came back normalized with would mask a changed default; keep only
    # what differs from the built-in rules
    from .spec import RULES
    spec["rules"] = {k: v for k, v in spec["rules"].items() if RULES.get(k) != v}
    new = planner.plan(spec, seed=seed, assignment=None if fresh else assign, locked=arrangement_only)
    before = {sp["name"]: sp["rect"] for lvl in p["levels"] for sp in lvl["spaces"]}
    after = {sp["name"]: sp["rect"] for lvl in new.get("levels", []) for sp in lvl["spaces"]}
    moved = sorted(n for n in after if n in before and after[n] != before[n])
    report = {"applied": done, "kept_arrangement": arrangement_only and not fresh, "moved": moved,
              "added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after))}
    return new, report
