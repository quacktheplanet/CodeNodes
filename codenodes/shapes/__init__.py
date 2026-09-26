"""Shapes: maths turned into models, built directly rather than marched.

The signed-distance side of CodeNodes samples a field on a grid, which suits organic
forms but gives grid-shaped topology and rounded corners. This side constructs the mesh
from a description, so edges land exactly where the maths puts them, corners stay sharp,
the quads follow the form, and the UVs mean something. Lamps, bottles, columns, walls,
turned and extruded parts.

    from codenodes.shapes import parse
    shape = parse(source)             # the text language, see language.py
    solid = shape.build({"height": 0.4})

Nothing here needs Blender or a GPU, and nothing here can run code.
"""

from . import expr, solids
from .expr import ExprError, evaluate
from .language import KEYWORDS, TEMPLATE, Part, Shape, ShapeError, parse, split_args
from .solids import (MAX_FACES, Path, Profile, Solid, array, extrude, join, loft, revolve,
                     shell, sweep, transform)

__all__ = ["parse", "Shape", "Part", "ShapeError", "ExprError", "evaluate", "TEMPLATE",
           "KEYWORDS", "split_args", "Profile", "Path", "Solid", "revolve", "extrude",
           "sweep", "loft", "shell", "array", "join", "transform", "MAX_FACES", "solids", "expr"]
