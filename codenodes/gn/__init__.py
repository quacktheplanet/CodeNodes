"""Geometry Nodes, read and written as plain data.

So an assistant can build a setup, understand one that already exists, and change part
of it without disturbing the rest:

    from codenodes.gn import catalog, serialize
    catalog.search("distribute points")        # what nodes exist, and what they take
    data = serialize.read(tree)                # an existing tree as plain data
    data["nodes"][2]["values"]["Density"] = 40
    serialize.write(data, tree)                # put it back

Needs Blender, but no GPU.
"""

from . import catalog, serialize
from .serialize import BuildError, read, read_interface, write, write_interface

__all__ = ["catalog", "serialize", "read", "write", "read_interface", "write_interface",
           "BuildError"]
