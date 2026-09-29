"""CodeNodes: GPU code inside Blender. First piece: Code -> Mesh.

Write a signed distance function in GLSL (by hand or with Claude), and CodeNodes
samples it on the GPU and turns it into a real, editable, renderable mesh.
"""

bl_info = {
    "name": "CodeNodes",
    "author": "Lucas DeMeritt",
    "version": (0, 2, 0),
    "blender": (5, 0, 0),
    "location": "Geometry Nodes > Add > CodeNodes; View3D > Add > Mesh; Render > Render with CodeNodes",
    "description": "GPU code inside Geometry Nodes: live particles, raymarched surfaces, mesh code, To Geometry",
    "category": "Mesh",
}

_modules = ("prefs", "props", "live", "gpu_live", "ops", "bake_ops", "nodes", "gn_ui", "ui", "render_ops",
            "server")


def register():
    import importlib
    for name in _modules:
        importlib.import_module(f".{name}", __package__).register()


def unregister():
    import importlib
    for name in reversed(_modules):
        importlib.import_module(f".{name}", __package__).unregister()
