"""CodeNodes MCP server: write GPU code, get real Blender geometry, look at the result.

Run it from an MCP client (see the README). It talks to the CodeNodes add-on inside a
running Blender over a localhost socket. It cannot run arbitrary code in Blender —
only the fixed set of tools below.
"""

__version__ = "0.1.2"
