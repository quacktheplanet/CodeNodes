"""Parametric equipment and building parts, written in the kit language (kit.py).

Every generator puts its origin at the middle of its footprint on the floor, with +X along
its length and its front (the side someone works from) facing -Y. Each has:

    params      sliders with defaults and ranges
    part(p)     the geometry, as formulas of the sliders
    clearance   free space it needs round its footprint (front, back, sides), in metres
    joints      for things that move (the robot arm): axis, limits, which links they join —
                recorded now for posing, animation and simulation export later

    generators()            every generator's summary (for the `assets` tool)
    build(kind, values)     -> (kit.Mesh, info) with footprint, height and clearance filled in
"""

from __future__ import annotations

import math

from . import kit
from .kit import Array, Box, Cone, Cyl, Group, Params, floor, maximum

# name -> (base colour, metallic, roughness); build.py makes a material for each
MATERIALS = {
    "steel": ((0.55, 0.57, 0.6), 0.9, 0.35),
    "dark_steel": ((0.18, 0.19, 0.2), 0.8, 0.45),
    "rack_blue": ((0.08, 0.22, 0.55), 0.3, 0.45),
    "rack_orange": ((0.9, 0.35, 0.05), 0.2, 0.45),
    "safety_yellow": ((0.95, 0.72, 0.05), 0.1, 0.45),
    "robot_orange": ((0.95, 0.42, 0.05), 0.1, 0.35),
    "machine_grey": ((0.72, 0.74, 0.76), 0.2, 0.4),
    "machine_blue": ((0.12, 0.3, 0.6), 0.2, 0.4),
    "glass": ((0.6, 0.75, 0.8), 0.0, 0.05),
    "rubber": ((0.03, 0.03, 0.03), 0.0, 0.8),
    "wood": ((0.55, 0.38, 0.2), 0.0, 0.7),
    "pallet_wood": ((0.68, 0.52, 0.32), 0.0, 0.8),
    "cardboard": ((0.62, 0.45, 0.26), 0.0, 0.85),
    "cabinet_grey": ((0.62, 0.63, 0.6), 0.2, 0.5),
    "fence_mesh": ((0.1, 0.1, 0.1), 0.6, 0.5),
    "green": ((0.1, 0.6, 0.15), 0.0, 0.4),
    "red": ((0.75, 0.05, 0.03), 0.0, 0.4),
    "white": ((0.85, 0.85, 0.83), 0.0, 0.5),
    "black": ((0.02, 0.02, 0.02), 0.0, 0.6),
    "door_grey": ((0.45, 0.48, 0.5), 0.4, 0.4),
    "fabric": ((0.12, 0.13, 0.16), 0.0, 0.9),
}

GENERATORS = {}


class Generator:
    def __init__(self, kind, category, about, params, part, clearance=None, joints=None,
                 info=None):
        self.kind, self.category, self.about = kind, category, about
        self.params, self.make = params, part
        self.clearance = clearance or (lambda v: {"front": 0.0, "back": 0.0, "sides": 0.0})
        self.joints = joints or []
        self.extra = info or (lambda v: {})

    def part(self):
        return self.make(self.params)

    def summary(self):
        v = self.params.defaults()
        return {"kind": self.kind, "category": self.category, "about": self.about,
                "params": {k: {kk: vv for kk, vv in s.items() if vv not in (None, "")}
                           for k, s in self.params.spec.items()},
                "clearance": self.clearance(v), "joints": self.joints,
                **self.extra(v)}


def generator(kind, category, about, params, clearance=None, joints=None, info=None):
    def register(fn):
        GENERATORS[kind] = Generator(kind, category, about, params, fn, clearance, joints, info)
        return fn
    return register


def get(kind):
    if kind not in GENERATORS:
        raise KeyError(f"no generator called {kind!r}; there is {', '.join(sorted(GENERATORS))}")
    return GENERATORS[kind]


def generators(category=None):
    return [g.summary() for k, g in sorted(GENERATORS.items())
            if category is None or g.category.lower().startswith(category.lower())]


def build(kind, values=None):
    """The generator's mesh at these values, and what a layout needs to know about it."""
    gen = get(kind)
    v = gen.params.resolve(values)
    m = kit.mesh(gen.part(), v)
    (x0, y0, z0), (x1, y1, z1) = m.bounds()
    info = {"kind": kind, "values": v, "footprint": [round(x1 - x0, 4), round(y1 - y0, 4)],
            "offset": [round((x0 + x1) / 2, 4), round((y0 + y1) / 2, 4)],
            "height": round(z1, 4), "bottom": round(z0, 4),
            "clearance": gen.clearance(v), "joints": gen.joints, **gen.extra(v)}
    return m, info


# ---- handling -------------------------------------------------------------------------------

P_CONVEYOR = Params(length=(4.0, 0.6, 40.0, "along the belt"),
                    width=(0.6, 0.2, 2.0, "between the side rails"),
                    height=(0.85, 0.3, 6.0, "top of the rollers; 4.5 m and up clears forklifts"),
                    pitch=(0.15, 0.05, 0.5, "roller spacing"),
                    leg_spacing=(1.5, 0.5, 100.0, "between leg pairs; large leaves legs only at the ends"))


@generator("conveyor", "Factory/Handling", "A roller conveyor on legs, with side rails.",
           P_CONVEYOR, clearance=lambda v: {"front": 0.8, "back": 0.5, "sides": 0.3})
def conveyor(p):
    rail_y = p.width / 2 + 0.03
    legs = maximum(floor(p.length / p.leg_spacing), 1)
    leg_h = p.height - 0.12
    leg = Box((0.05, 0.05, leg_h), mat="dark_steel")
    return Group([
        Box((p.length, 0.05, 0.14), at=(0, rail_y, p.height - 0.05), mat="steel"),
        Box((p.length, 0.05, 0.14), at=(0, -rail_y, p.height - 0.05), mat="steel"),
        Array(Cyl(0.03, p.width + 0.03, rot=(90, 0, 0), mat="steel", segments=10),
              count=floor(p.length / p.pitch), step=(p.pitch, 0, 0),
              at=(-p.length / 2 + p.pitch / 2, 0, p.height - 0.03)),
        Array(Group([Box((0.05, 0.05, leg_h), at=(0, rail_y, 0), mat="dark_steel"),
                     Box((0.05, 0.05, leg_h), at=(0, -rail_y, 0), mat="dark_steel"),
                     Box((0.04, p.width + 0.06, 0.04), at=(0, 0, -leg_h / 2 + 0.2), mat="dark_steel")]),
              count=legs + 1, step=((p.length - 0.1) / legs, 0, 0),
              at=(-p.length / 2 + 0.05, 0, leg_h / 2)),
    ])


P_RACK = Params(bays=(3, 1, 20, "sections side by side"),
                bay_width=(2.7, 1.0, 4.0, "one section, beam to beam"),
                depth=(1.1, 0.6, 1.6, "front to back"),
                levels=(4, 1, 8, "beam levels"),
                level_height=(1.6, 0.8, 3.0, "between beam levels"),
                loaded=(1, 0, 1, "1 puts pallets of boxes on the beams"))


@generator("rack", "Factory/Storage", "Selective pallet racking: blue uprights, orange beams, "
           "optionally loaded with pallets.", P_RACK,
           clearance=lambda v: {"front": 3.6, "back": 0.1, "sides": 0.3},
           info=lambda v: {"pallet_positions": int(v["bays"] * v["levels"] * 2)})
def rack(p):
    length = p.bays * p.bay_width
    top = p.levels * p.level_height + 0.3
    upright = Group([Box((0.08, 0.08, top), at=(0, -p.depth / 2 + 0.04, top / 2), mat="rack_blue"),
                     Box((0.08, 0.08, top), at=(0, p.depth / 2 - 0.04, top / 2), mat="rack_blue"),
                     Array(Box((0.04, p.depth - 0.08, 0.04), mat="rack_blue"),
                           count=floor(top / 1.2), step=(0, 0, 1.2), at=(0, 0, 0.6))])
    beams = Group([Box((length, 0.05, 0.12), at=(0, -p.depth / 2 + 0.04, 0), mat="rack_orange"),
                   Box((length, 0.05, 0.12), at=(0, p.depth / 2 - 0.04, 0), mat="rack_orange")])
    # two pallets per bay on the floor and on every beam level but the top one; `loaded` 0
    # takes them all away
    # pallets span the rack's depth and overhang the beams a little, as they are loaded
    load = Group([Box((1.0, p.depth + 0.1, 0.14), at=(0, 0, 0.07), mat="pallet_wood"),
                  Box((0.95, p.depth, 0.9), at=(0, 0, 0.14 + 0.45), mat="cardboard")])
    per_bay = Array(Group([load], at=(-p.bay_width / 4, 0, 0)), count=2, step=(p.bay_width / 2, 0, 0))
    first_bay = -length / 2 + p.bay_width / 2
    return Group([
        Array(upright, count=p.bays + 1, step=(p.bay_width, 0, 0), at=(-length / 2, 0, 0)),
        Array(beams, count=p.levels, step=(0, 0, p.level_height), at=(0, 0, p.level_height)),
        Array(per_bay, count=p.bays * p.loaded, step=(p.bay_width, 0, 0), at=(first_bay, 0, 0)),
        Array(Array(per_bay, count=p.bays, step=(p.bay_width, 0, 0)),
              count=(p.levels - 1) * p.loaded, step=(0, 0, p.level_height),
              at=(first_bay, 0, p.level_height + 0.06)),
    ])


P_PALLET = Params(width=(1.2, 0.6, 2.0), depth=(1.0, 0.6, 2.0),
                  layers=(2, 0, 6, "layers of boxes on it"),
                  box=(0.4, 0.2, 0.8, "box size"))


@generator("pallet", "Factory/Storage", "A wooden pallet with stacked cartons.", P_PALLET,
           clearance=lambda v: {"front": 0.3, "back": 0.1, "sides": 0.1})
def pallet(p):
    nx, ny = floor(p.width / p.box), floor(p.depth / p.box)
    carton = Box((p.box - 0.02, p.box - 0.02, p.box), mat="cardboard")
    # a block pallet: bottom deck, 3 x 3 blocks, three stringers along X on the blocks, and
    # top boards across them
    return Group([
        Box((p.width, p.depth, 0.022), at=(0, 0, 0.011), mat="pallet_wood"),
        Array(Array(Box((0.1, 0.1, 0.1), mat="pallet_wood"), count=3,
                    step=((p.width - 0.1) / 2, 0, 0)), count=3, step=(0, (p.depth - 0.1) / 2, 0),
              at=(-p.width / 2 + 0.05, -p.depth / 2 + 0.05, 0.072)),
        Array(Box((p.width, 0.1, 0.022), mat="pallet_wood"), count=3,
              step=(0, (p.depth - 0.1) / 2, 0), at=(0, -p.depth / 2 + 0.05, 0.133)),
        Array(Box((0.1, p.depth, 0.022), mat="pallet_wood"), count=5,
              step=((p.width - 0.1) / 4, 0, 0), at=(-p.width / 2 + 0.05, 0, 0.155)),
        Array(Array(Array(carton, count=nx, step=(p.box, 0, 0)), count=ny, step=(0, p.box, 0)),
              count=p.layers, step=(0, 0, p.box),
              at=(-(nx - 1) * p.box / 2, -(ny - 1) * p.box / 2, 0.166 + p.box / 2)),
    ])


P_FORKLIFT = Params(length=(2.4, 1.6, 4.0, "body, without the forks"),
                    width=(1.1, 0.8, 2.0), mast=(2.3, 1.5, 6.0, "mast height"),
                    lift=(0.1, 0.0, 5.0, "how high the forks are raised"))


@generator("forklift", "Factory/Vehicles", "A counterbalance forklift.", P_FORKLIFT,
           clearance=lambda v: {"front": 1.5, "back": 0.6, "sides": 0.4})
def forklift(p):
    wheel = Cyl(0.3, 0.2, rot=(90, 0, 0), mat="rubber", segments=18)
    x_front = p.length / 2 - 0.35
    lift = kit.minimum(p.lift, p.mast - 0.6)      # the carriage never leaves the mast
    return Group([
        Box((p.length - 0.3, p.width - 0.2, 0.7), at=(-0.15, 0, 0.55), mat="safety_yellow"),
        Box((0.5, p.width - 0.1, 0.8), at=(-p.length / 2 + 0.25, 0, 0.8), mat="dark_steel"),
        Box((0.45, 0.5, 0.1), at=(-0.25, 0, 0.95), mat="black"),
        Box((0.08, 0.5, 0.55), at=(-0.5, 0, 1.2), mat="black"),
        Array(Array(Box((0.06, 0.06, 1.3), mat="dark_steel"), count=2, step=(0, p.width - 0.3, 0)),
              count=2, step=(1.05, 0, 0), at=(-0.75, -(p.width - 0.3) / 2, 1.55)),
        Box((1.2, p.width - 0.2, 0.05), at=(-0.2, 0, 2.2), mat="dark_steel"),
        Array(Box((0.1, 0.1, p.mast), mat="dark_steel"), count=2, step=(0, 0.7, 0),
              at=(p.length / 2 - 0.1, -0.35, p.mast / 2)),
        Array(Box((1.1, 0.12, 0.05), mat="steel"), count=2, step=(0, 0.5, 0),
              at=(p.length / 2 + 0.5, -0.25, 0.05 + lift)),
        Box((0.06, 0.9, 0.5), at=(p.length / 2 - 0.02, 0, 0.3 + lift), mat="steel"),
        Array(Array(wheel, count=2, step=(0, p.width - 0.2, 0)), count=2, step=(x_front + p.length / 2 - 0.45, 0, 0),
              at=(-p.length / 2 + 0.45, -(p.width - 0.2) / 2, 0.3)),
    ])


P_AMR = Params(length=(1.0, 0.5, 2.0), width=(0.7, 0.4, 1.5), height=(0.35, 0.2, 0.8))


@generator("amr", "Factory/Vehicles", "An autonomous mobile robot: a low deck on wheels "
           "with a lidar and a light strip.", P_AMR,
           clearance=lambda v: {"front": 0.5, "back": 0.5, "sides": 0.3})
def amr(p):
    return Group([
        Box((p.length, p.width, p.height - 0.08), at=(0, 0, 0.04 + (p.height - 0.08) / 2), mat="white"),
        Box((p.length - 0.04, p.width - 0.04, 0.04), at=(0, 0, p.height - 0.02), mat="dark_steel"),
        Box((p.length + 0.01, 0.03, 0.03), at=(0, -p.width / 2, p.height * 0.6), mat="green"),
        Cyl(0.06, 0.07, at=(p.length / 2 - 0.12, 0, p.height + 0.035), mat="black"),
        Array(Array(Cyl(0.075, 0.05, rot=(90, 0, 0), mat="rubber", segments=12), count=2,
                    step=(0, p.width - 0.1, 0)), count=2, step=(p.length - 0.3, 0, 0),
              at=(-p.length / 2 + 0.15, -(p.width - 0.1) / 2, 0.075)),
    ])


# ---- production -----------------------------------------------------------------------------

P_ARM = Params(pedestal=(0.5, 0.1, 1.5, "base riser height"),
               upper=(0.85, 0.3, 2.0, "shoulder to elbow"),
               fore=(0.8, 0.3, 2.0, "elbow to wrist"),
               j1=(0.0, -170, 170, "base turn, degrees"),
               j2=(-15.0, -90, 90, "shoulder, degrees (0 is upright)"),
               j3=(20.0, -90, 180, "elbow, degrees (0 is the forearm level)"),
               j4=(0.0, -180, 180, "forearm roll, degrees"),
               j5=(40.0, -120, 120, "wrist bend, degrees"),
               j6=(0.0, -360, 360, "flange turn, degrees"))

ARM_JOINTS = [
    {"name": "j1", "type": "revolute", "axis": "z", "limits": [-170, 170], "parent": "pedestal", "child": "turret"},
    {"name": "j2", "type": "revolute", "axis": "y", "limits": [-90, 90], "parent": "turret", "child": "upper_arm"},
    {"name": "j3", "type": "revolute", "axis": "y", "limits": [-90, 180], "parent": "upper_arm", "child": "forearm"},
    {"name": "j4", "type": "revolute", "axis": "x", "limits": [-180, 180], "parent": "forearm", "child": "wrist"},
    {"name": "j5", "type": "revolute", "axis": "y", "limits": [-120, 120], "parent": "wrist", "child": "hand"},
    {"name": "j6", "type": "revolute", "axis": "x", "limits": [-360, 360], "parent": "hand", "child": "flange"},
]


def arm_reach(v):
    """Horizontal reach from the base axis with the arm stretched out."""
    return round(v["upper"] + v["fore"] + 0.25, 3)


@generator("robot_arm", "Factory/Robots", "A six-axis industrial robot arm on a pedestal, "
           "posed by its joint angles (j1..j6, degrees).", P_ARM,
           clearance=lambda v: {"front": 0.0, "back": 0.0, "sides": 0.0, "reach": arm_reach(v)},
           joints=ARM_JOINTS, info=lambda v: {"reach": arm_reach(v)})
def robot_arm(p):
    flange = Group([Cyl(0.05, 0.06, rot=(0, 90, 0), at=(0.03, 0, 0), mat="dark_steel"),
                    Box((0.06, 0.02, 0.1), at=(0.09, 0.035, 0), mat="steel"),
                    Box((0.06, 0.02, 0.1), at=(0.09, -0.035, 0), mat="steel")],
                   at=(0.12, 0, 0), rot=(p.j6, 0, 0))
    hand = Group([Cyl(0.07, 0.16, rot=(90, 0, 0), mat="robot_orange"),
                  Box((0.14, 0.1, 0.1), at=(0.07, 0, 0), mat="robot_orange"), flange],
                 rot=(0, p.j5, 0))
    wrist = Group([hand], at=(p.fore, 0, 0), rot=(p.j4, 0, 0))
    forearm = Group([Cyl(0.13, 0.26, rot=(90, 0, 0), mat="robot_orange"),
                     Box((p.fore, 0.16, 0.16), at=(p.fore / 2, 0, 0), mat="robot_orange"),
                     Cyl(0.09, 0.1, rot=(0, 90, 0), at=(p.fore - 0.05, 0, 0), mat="dark_steel"), wrist],
                    at=(0, 0, p.upper), rot=(0, p.j3, 0))
    upper = Group([Cyl(0.16, 0.3, rot=(90, 0, 0), mat="robot_orange"),
                   Box((0.22, 0.2, p.upper), at=(0, 0, p.upper / 2), mat="robot_orange"),
                   Cyl(0.08, 0.24, rot=(90, 0, 0), at=(0, 0.14, 0), mat="dark_steel"), forearm],
                  at=(0.1, 0, 0.3), rot=(0, p.j2, 0))
    turret = Group([Cyl(0.28, 0.12, at=(0, 0, 0.06), mat="dark_steel", segments=24),
                    Box((0.42, 0.36, 0.3), at=(0.05, 0, 0.24), mat="robot_orange"), upper],
                   at=(0, 0, p.pedestal), rot=(0, 0, p.j1))
    return Group([Box((0.7, 0.7, 0.03), at=(0, 0, 0.015), mat="dark_steel"),
                  Box((0.5, 0.5, p.pedestal - 0.03), at=(0, 0, (p.pedestal - 0.03) / 2 + 0.03), mat="machine_grey"),
                  turret])


P_CNC = Params(width=(2.6, 1.0, 6.0), depth=(2.2, 1.0, 5.0), height=(2.5, 1.2, 4.0))


@generator("cnc", "Factory/Machines", "A CNC machining centre: enclosure with a glazed door, "
           "control pendant, chip conveyor and signal tower.", P_CNC,
           clearance=lambda v: {"front": 0.9, "back": 0.6, "sides": 0.6})
def cnc(p):
    return Group([
        Box((p.width, p.depth - 0.35, p.height), at=(0, 0.175 - 0.0, p.height / 2), mat="machine_grey"),
        Box((p.width * 0.5, 0.04, p.height * 0.45), at=(-p.width * 0.1, -p.depth / 2 + 0.33, p.height * 0.55), mat="glass"),
        Box((p.width + 0.02, 0.2, p.height * 0.12), at=(0, 0.175 - (p.depth - 0.35) / 2 + 0.0, p.height * 0.12 / 2),
            mat="machine_blue"),
        Box((0.45, 0.15, 0.6), at=(p.width / 2 - 0.3, -p.depth / 2 + 0.275, p.height * 0.58), mat="dark_steel"),
        Box((0.8, 0.35, 0.9), at=(p.width / 2 - 0.5, p.depth / 2 - 0.175, 0.45), mat="machine_blue"),
        Cyl(0.03, 0.4, at=(p.width / 2 - 0.15, p.depth / 2 - 0.5, p.height + 0.2), mat="dark_steel", segments=8),
        Cyl(0.05, 0.08, at=(p.width / 2 - 0.15, p.depth / 2 - 0.5, p.height + 0.44), mat="green", segments=12),
        Cyl(0.05, 0.08, at=(p.width / 2 - 0.15, p.depth / 2 - 0.5, p.height + 0.52), mat="safety_yellow", segments=12),
        Cyl(0.05, 0.08, at=(p.width / 2 - 0.15, p.depth / 2 - 0.5, p.height + 0.6), mat="red", segments=12),
    ])


P_BENCH = Params(length=(1.8, 0.8, 4.0), depth=(0.75, 0.5, 1.5), height=(0.9, 0.6, 1.2),
                 board=(1, 0, 1, "1 adds a tool board at the back"))


@generator("workbench", "Factory/Workstations", "A workbench with a lower shelf and a tool board.",
           P_BENCH, clearance=lambda v: {"front": 1.0, "back": 0.1, "sides": 0.2})
def workbench(p):
    leg = Box((0.05, 0.05, p.height - 0.04), mat="dark_steel")
    return Group([
        Box((p.length, p.depth, 0.04), at=(0, 0, p.height - 0.02), mat="wood"),
        Box((p.length - 0.1, p.depth - 0.1, 0.02), at=(0, 0, 0.2), mat="steel"),
        Array(Array(leg, count=2, step=(p.length - 0.1, 0, 0)), count=2, step=(0, p.depth - 0.1, 0),
              at=(-p.length / 2 + 0.05, -p.depth / 2 + 0.05, (p.height - 0.04) / 2)),
        Box((p.length, 0.03, 0.8 * p.board + 0.001), at=(0, p.depth / 2 - 0.015, p.height + 0.4 * p.board),
            mat="cabinet_grey"),
        Box((0.25, 0.12, 0.12), at=(-p.length / 2 + 0.2, -p.depth / 2 + 0.08, p.height + 0.06), mat="machine_blue"),
    ])


P_TANK = Params(radius=(1.0, 0.3, 4.0), height=(3.0, 0.8, 12.0, "of the shell"),
                legs=(0.6, 0.2, 2.0, "clearance under it"))


@generator("tank", "Factory/Process", "A vertical process tank on legs with a coned top and a "
           "ladder.", P_TANK, clearance=lambda v: {"front": 0.9, "back": 0.5, "sides": 0.5})
def tank(p):
    shell_z = p.legs + p.height / 2
    return Group([
        Cyl(p.radius, p.height, at=(0, 0, shell_z), mat="steel", segments=32),
        Cone(p.radius, 0.12, p.radius * 0.35, at=(0, 0, p.legs + p.height + p.radius * 0.175), mat="steel", segments=32),
        Cone(0.12, p.radius, p.radius * 0.3, at=(0, 0, p.legs - p.radius * 0.15 + 0.001), mat="steel", segments=32),
        Array(Array(Box((0.1, 0.1, p.legs), mat="dark_steel"), count=2, step=(p.radius * 1.2, 0, 0)),
              count=2, step=(0, p.radius * 1.2, 0), at=(-p.radius * 0.6, -p.radius * 0.6, p.legs / 2)),
        Array(Box((0.04, 0.04, p.legs + p.height), mat="safety_yellow"), count=2, step=(0.45, 0, 0),
              at=(-0.225, -p.radius - 0.2, (p.legs + p.height) / 2)),
        Array(Box((0.45, 0.03, 0.03), mat="safety_yellow"), count=floor((p.legs + p.height) / 0.3),
              step=(0, 0, 0.3), at=(0, -p.radius - 0.2, 0.3)),
        Cyl(0.08, 0.6, rot=(90, 0, 0), at=(0, p.radius + 0.25, p.legs + 0.3), mat="steel", segments=12),
    ])


P_CABINET = Params(width=(0.8, 0.4, 3.0), depth=(0.4, 0.2, 1.0), height=(2.0, 0.5, 2.4),
                   doors=(2, 1, 4))


@generator("cabinet", "Factory/Electrical", "An electrical control cabinet on a plinth, with "
           "doors and a warning label.", P_CABINET,
           clearance=lambda v: {"front": 1.0, "back": 0.0, "sides": 0.0})
def cabinet(p):
    return Group([
        Box((p.width, p.depth, 0.1), at=(0, 0, 0.05), mat="black"),
        Box((p.width, p.depth, p.height - 0.1), at=(0, 0, 0.1 + (p.height - 0.1) / 2), mat="cabinet_grey"),
        Array(Box((0.01, 0.01, p.height - 0.2), mat="dark_steel"), count=p.doors - 1,
              step=(p.width / p.doors, 0, 0), at=(-p.width / 2 + p.width / p.doors, -p.depth / 2 - 0.004, p.height / 2 + 0.05)),
        Array(Box((0.02, 0.03, 0.15), mat="black"), count=p.doors, step=(p.width / p.doors, 0, 0),
              at=(-p.width / 2 + p.width / p.doors - 0.06, -p.depth / 2 - 0.015, p.height * 0.55)),
        Box((0.12, 0.005, 0.1), at=(-p.width / 2 + 0.15, -p.depth / 2 - 0.003, p.height * 0.8), mat="safety_yellow"),
    ])


P_FENCE = Params(width=(4.0, 0.5, 30.0, "along the front"), depth=(4.0, 0.0, 30.0, "0 for a straight run"),
                 height=(2.0, 0.9, 3.0), gate=(1.0, 0.0, 3.0, "opening in the middle of the front"),
                 spacing=(1.5, 0.5, 3.0, "between posts"))


def _fence_run(p, length, at, rot):
    posts = maximum(floor(length / p.spacing), 1)
    return Group([
        Array(Box((0.06, 0.06, p.height), mat="safety_yellow"), count=posts + 1,
              step=(length / posts, 0, 0), at=(-length / 2, 0, p.height / 2)),
        Box((length, 0.02, p.height - 0.2), at=(0, 0, 0.15 + (p.height - 0.2) / 2), mat="fence_mesh"),
        Box((length, 0.05, 0.05), at=(0, 0, p.height - 0.025), mat="safety_yellow"),
    ], at=at, rot=rot)


@generator("fence", "Factory/Safety", "A safety fence round a robot cell (width x depth, with a "
           "gate in the front), or a straight run when depth is 0.", P_FENCE,
           clearance=lambda v: {"front": 0.6, "back": 0.0, "sides": 0.0})
def fence(p):
    # depth 0 folds the back onto the front and the sides to nothing: a straight run
    side = (p.width - p.gate) / 2
    return Group([
        _fence_run(p, side, at=(-p.width / 2 + side / 2, -p.depth / 2, 0), rot=(0, 0, 0)),
        _fence_run(p, side, at=(p.width / 2 - side / 2, -p.depth / 2, 0), rot=(0, 0, 0)),
        _fence_run(p, p.width, at=(0, p.depth / 2, 0), rot=(0, 0, 0)),
        _fence_run(p, p.depth, at=(-p.width / 2, 0, 0), rot=(0, 0, 90)),
        _fence_run(p, p.depth, at=(p.width / 2, 0, 0), rot=(0, 0, 90)),
    ])


P_DESK = Params(width=(1.6, 0.8, 2.4), depth=(0.8, 0.5, 1.2), chair=(1, 0, 1))


@generator("desk", "Office", "An office workstation: desk, monitor and chair.", P_DESK,
           clearance=lambda v: {"front": 1.0, "back": 0.1, "sides": 0.1})
def desk(p):
    chair = Group([Box((0.5, 0.5, 0.08), at=(0, 0, 0.47), mat="fabric"),
                   Box((0.5, 0.06, 0.55), at=(0, -0.25, 0.78), mat="fabric"),
                   Cyl(0.03, 0.43, at=(0, 0, 0.245), mat="dark_steel", segments=8),
                   Box((0.55, 0.06, 0.03), at=(0, 0, 0.015), mat="dark_steel"),
                   Box((0.06, 0.55, 0.03), at=(0, 0, 0.015), mat="dark_steel")],
                  at=(0, -p.depth / 2 - 0.45, 0))
    return Group([
        Box((p.width, p.depth, 0.03), at=(0, 0, 0.735), mat="white"),
        Array(Box((0.05, p.depth - 0.1, 0.72), mat="dark_steel"), count=2, step=(p.width - 0.1, 0, 0),
              at=(-p.width / 2 + 0.05, 0, 0.36)),
        Box((0.6, 0.03, 0.36), at=(0, p.depth / 2 - 0.2, 0.75 + 0.3), mat="black"),
        Box((0.08, 0.15, 0.12), at=(0, p.depth / 2 - 0.15, 0.75 + 0.06), mat="black"),
        Array(chair, count=p.chair, step=(0, 0, 0)),
    ])


# ---- building parts -------------------------------------------------------------------------

P_COLUMN = Params(height=(8.0, 2.5, 30.0), size=(0.3, 0.15, 1.0, "flange width and depth"))


@generator("column", "Architecture/Structure", "A steel H column on a base plate.", P_COLUMN,
           clearance=lambda v: {"front": 0.1, "back": 0.1, "sides": 0.1})
def column(p):
    return Group([
        Box((p.size, 0.025, p.height), at=(0, -p.size / 2 + 0.0125, p.height / 2), mat="safety_yellow"),
        Box((p.size, 0.025, p.height), at=(0, p.size / 2 - 0.0125, p.height / 2), mat="safety_yellow"),
        Box((0.015, p.size - 0.05, p.height), at=(0, 0, p.height / 2), mat="safety_yellow"),
        Box((p.size + 0.15, p.size + 0.15, 0.03), at=(0, 0, 0.015), mat="dark_steel"),
    ])


P_DOCK = Params(width=(2.7, 2.0, 5.0), height=(3.0, 2.0, 6.0), open=(0.0, 0.0, 1.0, "0 shut .. 1 rolled up"))


@generator("dock_door", "Architecture/Openings", "A loading dock: roll-up door in a frame, "
           "rubber bumpers and a leveller plate inside.", P_DOCK,
           clearance=lambda v: {"front": 4.0, "back": 0.0, "sides": 0.5})
def dock_door(p):
    shut = 1 - p.open
    slats = maximum(floor(p.height * shut / 0.25), 0)
    return Group([
        Box((0.15, 0.25, p.height + 0.15), at=(-p.width / 2 - 0.075, 0, (p.height + 0.15) / 2), mat="safety_yellow"),
        Box((0.15, 0.25, p.height + 0.15), at=(p.width / 2 + 0.075, 0, (p.height + 0.15) / 2), mat="safety_yellow"),
        Box((p.width + 0.3, 0.5, 0.45), at=(0, -0.1, p.height + 0.3), mat="door_grey"),
        Array(Box((p.width, 0.05, 0.24), mat="door_grey"), count=slats, step=(0, 0, 0.25),
              at=(0, 0.02, p.height - 0.125 - (slats - 1) * 0.25)),
        Box((p.width - 0.2, 2.0, 0.04), at=(0, -1.15, 0.02), mat="dark_steel"),
        Array(Box((0.25, 0.12, 0.45), mat="rubber"), count=2, step=(p.width - 0.2, 0, 0),
              at=(-p.width / 2 + 0.1, 0.18, 0.5)),
    ])


P_DOOR = Params(width=(0.9, 0.6, 2.4), height=(2.1, 1.8, 3.0), wall=(0.2, 0.05, 0.6, "wall thickness"))


@generator("door", "Architecture/Openings", "A door frame with its leaf swung open.", P_DOOR,
           clearance=lambda v: {"front": 1.2, "back": 1.2, "sides": 0.0})
def door(p):
    return Group([
        Box((0.05, p.wall + 0.02, p.height), at=(-p.width / 2 - 0.025, 0, p.height / 2), mat="dark_steel"),
        Box((0.05, p.wall + 0.02, p.height), at=(p.width / 2 + 0.025, 0, p.height / 2), mat="dark_steel"),
        Box((p.width + 0.1, p.wall + 0.02, 0.05), at=(0, 0, p.height + 0.025), mat="dark_steel"),
        Box((0.04, p.width - 0.02, p.height - 0.02), at=(p.width / 2 - 0.02, p.wall / 2 + (p.width - 0.02) / 2 + 0.01,
                                                          (p.height - 0.02) / 2 + 0.01), mat="door_grey"),
    ])


P_WINDOW = Params(width=(1.5, 0.4, 5.0), height=(1.2, 0.4, 3.0), wall=(0.2, 0.05, 0.6))


@generator("window", "Architecture/Openings", "A window: frame, mullion and glass.", P_WINDOW)
def window(p):
    return Group([
        Box((p.width, 0.02, p.height), at=(0, 0, p.height / 2), mat="glass"),
        Box((p.width + 0.1, p.wall + 0.02, 0.05), at=(0, 0, 0.025), mat="white"),
        Box((p.width + 0.1, p.wall + 0.02, 0.05), at=(0, 0, p.height - 0.025), mat="white"),
        Box((0.05, p.wall + 0.02, p.height), at=(-p.width / 2, 0, p.height / 2), mat="white"),
        Box((0.05, p.wall + 0.02, p.height), at=(p.width / 2, 0, p.height / 2), mat="white"),
        Box((0.04, 0.06, p.height), at=(0, 0, p.height / 2), mat="white"),
    ])


P_LIGHT = Params(length=(1.2, 0.3, 3.0))


@generator("light_fixture", "Architecture/Services", "A linear high-bay light housing (the lamp "
           "itself is a Blender light placed with it).", P_LIGHT)
def light_fixture(p):
    return Group([Box((p.length, 0.3, 0.08), at=(0, 0, 0.04), mat="white"),
                  Box((p.length - 0.05, 0.25, 0.01), at=(0, 0, -0.005), mat="white")])
