"""Geometry Nodes, read and written as plain data.

So an assistant can build a setup, understand one that already exists, and change part
of it without disturbing the rest:

    catalog     what node types exist and exactly what each takes, read from Blender
    serialize   a tree as plain data and back, losslessly; validate() and unused()
    edit        small all-or-nothing changes to a tree that already exists
    explain     a tree described in words, in flow order
    builder     write a tree as Python; get the same plain data
    library     ready-made, tested world-building capabilities

Needs Blender, but no GPU (builder needs neither).
"""

from . import builder, catalog, edit, explain, library, serialize
from .serialize import (BuildError, read, read_interface, unused, validate, write,
                        write_interface)

__all__ = ["builder", "catalog", "edit", "explain", "library", "serialize", "read", "write",
           "read_interface", "write_interface", "validate", "unused", "BuildError"]
