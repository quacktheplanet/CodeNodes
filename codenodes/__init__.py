"""CodeNodes: GPU code inside Blender. First piece: Code -> Mesh.

Write a signed distance function in GLSL (by hand or with Claude), and CodeNodes
samples it on the GPU and turns it into a real, editable, renderable mesh.
"""

bl_info = {
    "name": "CodeNodes",
    "author": "Lucas DeMeritt",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Add > Mesh > Code Mesh; View3D > Sidebar > CodeNodes",
    "description": "Turn GPU code (signed distance functions) into real meshes",
    "category": "Mesh",
}

_modules = ("props", "live", "ops", "bake_ops", "nodes", "ui")


def register():
    import importlib
    for name in _modules:
        importlib.import_module(f".{name}", __package__).register()


def unregister():
    import importlib
    for name in reversed(_modules):
        importlib.import_module(f".{name}", __package__).unregister()
