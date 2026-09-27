"""Write a node tree as Python, get back the same plain data nodes_write takes.

For authoring capabilities, where a hand-written JSON tree would be long and brittle:

    g = Graph("Lift", "Moves geometry up.")
    geo = g.input("Geometry", "geometry")
    up = g.input("Height", "float", 1.0, 0, 10, "how far")
    moved = g.node("GeometryNodeTransform", {"Geometry": geo,
                                             "Translation": g.xyz(0, 0, up)})
    g.output("Geometry", "geometry", moved["Geometry"])
    serialize.write(g.data())

Sockets are named plainly; positions are left out, so the tree gets laid out on write.
Nothing here needs Blender.
"""

from __future__ import annotations

SOCKET_TYPES = {
    "geometry": "NodeSocketGeometry", "float": "NodeSocketFloat", "int": "NodeSocketInt",
    "bool": "NodeSocketBool", "vector": "NodeSocketVector", "color": "NodeSocketColor",
    "object": "NodeSocketObject", "collection": "NodeSocketCollection",
    "material": "NodeSocketMaterial", "rotation": "NodeSocketRotation",
    "string": "NodeSocketString", "menu": "NodeSocketMenu",
}


class Out:
    """A socket something can be connected from."""
    __slots__ = ("node", "socket")

    def __init__(self, node, socket):
        self.node, self.socket = node, socket

    def __repr__(self):
        return f"<{self.node}.{self.socket}>"


class Node:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __getitem__(self, socket):
        return Out(self.name, socket)


def _short(kind):
    for prefix in ("GeometryNode", "FunctionNode", "ShaderNode", "Node"):
        if kind.startswith(prefix):
            return kind[len(prefix):] or kind
    return kind


class Graph:
    def __init__(self, name, about=""):
        self.name, self.about = name, about
        self.interface, self.nodes, self.links = [], [], []
        self._names = set()
        self._panel = None
        self._has_in = self._has_out = False

    # ---- the group's contract ---------------------------------------------------------

    def panel(self, name, about="", closed=False):
        """Following inputs go in this panel; panel(None) ends it."""
        if name is None:
            self._panel = None
            return
        self.interface.append({"panel": name, "about": about or None, "closed": closed})
        self._panel = name

    def input(self, name, kind, default=None, min=None, max=None, about="", subtype=None,
              **extra):
        if any(i.get("socket") == name and i.get("in_out", "INPUT") == "INPUT"
               for i in self.interface):
            raise ValueError(f"two inputs called {name!r}")
        entry = {"socket": name, "type": SOCKET_TYPES.get(kind, kind), "in_out": "INPUT"}
        if default is not None:
            entry["default_value"] = list(default) if isinstance(default, tuple) else default
        if min is not None:
            entry["min_value"] = min
        if max is not None:
            entry["max_value"] = max
        if about:
            entry["description"] = about
        if subtype:
            entry["subtype"] = subtype
        if self._panel:
            entry["in_panel"] = self._panel
        entry.update(extra)
        self.interface.append(entry)
        if not self._has_in:
            self.nodes.append({"name": "Group Input", "type": "NodeGroupInput"})
            self._has_in = True
        return Out("Group Input", name)

    def output(self, name, kind, source):
        self.interface.append({"socket": name, "type": SOCKET_TYPES.get(kind, kind),
                               "in_out": "OUTPUT"})
        if not self._has_out:
            self.nodes.append({"name": "Group Output", "type": "NodeGroupOutput"})
            self._has_out = True
        self.links.append({"from": [source.node, source.socket], "to": ["Group Output", name]})

    # ---- nodes ------------------------------------------------------------------------

    def node(self, kind, inputs=None, *, name=None, label=None, items=None, pairs_with=None,
             **settings):
        base = name or _short(kind)
        name, n = base, 1
        while name in self._names:
            n += 1
            name = f"{base}.{n}"
        self._names.add(name)
        entry = {"name": name, "type": kind}
        if label:
            entry["label"] = label
        if items:
            entry["items"] = items
        if pairs_with:
            entry["pairs_with"] = pairs_with
        if settings:
            entry["settings"] = settings
        values = {}
        for socket, value in (inputs or {}).items():
            sources = value if isinstance(value, list) and value and isinstance(value[0], Out) \
                else [value] if isinstance(value, Out) else None
            if sources:
                for source in sources:
                    self.links.append({"from": [source.node, source.socket],
                                       "to": [name, socket]})
            elif value is not None:
                values[socket] = list(value) if isinstance(value, tuple) else value
        if values:
            entry["values"] = values
        self.nodes.append(entry)
        return Node(name)

    def link(self, source, node, socket):
        self.links.append({"from": [source.node, source.socket],
                           "to": [node.name if isinstance(node, Node) else node, socket]})

    # ---- shorthand for the nodes every tree needs ----------------------------------------

    def math(self, op, a, b=None, c=None, clamp=False):
        inputs = {"Value": a}
        if b is not None:
            inputs["Value_001"] = b
        if c is not None:
            inputs["Value_002"] = c
        settings = {"operation": op}
        if clamp:
            settings["use_clamp"] = True
        return self.node("ShaderNodeMath", inputs, name=f"{op.title()}", **settings)["Value"]

    def vmath(self, op, a, b=None, scale=None):
        inputs = {"Vector": a}
        if b is not None:
            inputs["Vector_001"] = b
        if scale is not None:
            inputs["Scale"] = scale
        n = self.node("ShaderNodeVectorMath", inputs, name=f"V{op.title()}", operation=op)
        return n["Value"] if op in ("DOT_PRODUCT", "DISTANCE", "LENGTH") else n["Vector"]

    def xyz(self, x=0.0, y=0.0, z=0.0):
        return self.node("ShaderNodeCombineXYZ", {"X": x, "Y": y, "Z": z})["Vector"]

    def separate(self, vector):
        return self.node("ShaderNodeSeparateXYZ", {"Vector": vector})

    def compare(self, op, a, b, data_type="FLOAT"):
        ids = {"FLOAT": ("A", "B"), "INT": ("A_INT", "B_INT"), "VECTOR": ("A_VEC3", "B_VEC3")}
        ka, kb = ids[data_type]
        return self.node("FunctionNodeCompare", {ka: a, kb: b}, operation=op,
                         data_type=data_type)["Result"]

    def logic(self, op, a, b=None):
        inputs = {"Boolean": a}
        if b is not None:
            inputs["Boolean_001"] = b
        return self.node("FunctionNodeBooleanMath", inputs, name=f"{op.title()}",
                         operation=op)["Boolean"]

    def switch(self, kind, condition, false=None, true=None):
        return self.node("GeometryNodeSwitch", {"Switch": condition, "False": false,
                                                "True": true}, input_type=kind)["Output"]

    def random(self, kind, low, high, seed, id=None):
        inputs = {"Min": low, "Max": high, "Seed": seed}
        if id is not None:
            inputs["ID"] = id
        return self.node("FunctionNodeRandomValue", inputs, data_type=kind)["Value"]

    def join(self, *geometry):
        return self.node("GeometryNodeJoinGeometry", {"Geometry": list(geometry)})["Geometry"]

    def position(self):
        return self.node("GeometryNodeInputPosition")["Position"]

    # ---- out --------------------------------------------------------------------------

    def data(self):
        return {"name": self.name, "about": self.about, "interface": self.interface,
                "nodes": self.nodes, "links": self.links}

    def manifest(self):
        """What someone needs to use it: inputs with their ranges, and outputs."""
        def item(i):
            out = {"name": i["socket"], "type": i["type"].replace("NodeSocket", "").lower()}
            for key, label in (("default_value", "default"), ("min_value", "min"),
                               ("max_value", "max"), ("description", "about")):
                if key in i:
                    out[label] = i[key]
            return out
        return {"name": self.name, "about": self.about,
                "inputs": [item(i) for i in self.interface
                           if "socket" in i and i["in_out"] == "INPUT"],
                "outputs": [item(i) for i in self.interface
                            if "socket" in i and i["in_out"] == "OUTPUT"]}
