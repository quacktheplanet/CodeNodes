"""Per-object Code -> Mesh settings, and keeping the code's @param sliders in sync."""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)

from . import sdf_code


def _changed(self, context):
    from . import live
    obj = self.id_data
    if obj.codenodes.enabled and obj.codenodes.live:
        live.request(obj)


def _sim_changed(self, context):
    """A particle setting changed: start the simulation over so the change is visible."""
    from . import live, particles
    obj = self.id_data
    particles.forget(obj.name)
    if obj.codenodes.enabled and obj.codenodes.live:
        live.request(obj)


class CN_Param(bpy.types.PropertyGroup):
    name: StringProperty()
    value: FloatProperty(update=_changed)
    min: FloatProperty()
    max: FloatProperty()


class CN_ObjectSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(default=False)
    kind: EnumProperty(name="Kind", default='MESH', items=[
        ('MESH', "Mesh", "A surface from a signed distance function"),
        ('PARTICLES', "Particles", "Points moved by your own solver on the GPU"),
        ('SHAPE', "Shape", "A model built from a parametric description: profiles, "
                           "revolve, extrude — exact edges and clean quads"),
    ])
    count: IntProperty(name="Particles", default=20000, min=1, max=2_000_000, soft_max=500_000,
                       update=_sim_changed)
    substeps: IntProperty(name="Substeps", default=1, min=1, max=20, update=_sim_changed,
                          description="Solver steps per frame; raise it if fast particles jitter")
    point_radius: FloatProperty(name="Point Size", default=0.02, min=0.0, soft_max=0.5, update=_changed)
    stagger: FloatProperty(name="Stagger", default=1.0, min=0.0, max=1.0, update=_sim_changed,
                           description="Spread starting ages so particles don't all die at once")
    text: PointerProperty(type=bpy.types.Text, name="Code", update=_changed,
                          description="Text block holding the sdf code")
    resolution: IntProperty(name="Resolution", default=96, min=8, max=512, soft_max=256, update=_changed,
                            description="Samples along the longest side of the bounds")
    bounds_min: FloatVectorProperty(name="Min", default=(-2.0, -2.0, -2.0), subtype='XYZ', update=_changed)
    bounds_max: FloatVectorProperty(name="Max", default=(2.0, 2.0, 2.0), subtype='XYZ', update=_changed)
    live: BoolProperty(name="Live", default=True,
                       description="Rebuild automatically when the code or a setting changes")
    animate: BoolProperty(name="Animate", default=False,
                          description="Rebuild on every frame change in the viewport (uTime changes)")
    smooth: BoolProperty(name="Smooth", default=True, update=_changed)
    params: CollectionProperty(type=CN_Param)
    last_error: StringProperty()
    stats: StringProperty()
    code_hash: StringProperty()


def sync_params(settings, source, kind='MESH'):
    """Match the sliders to what the code declares. Existing values are kept."""
    if kind == 'SHAPE':
        from .shapes import parse
        wanted = [sdf_code.Param(name, default, lo, hi)
                  for name, default, lo, hi in parse(source).params]
    else:
        wanted = sdf_code.parse_params(source)
    old = {p.name: p.value for p in settings.params}
    settings.params.clear()
    for prm in wanted:
        item = settings.params.add()
        item.name, item.min, item.max = prm.name, prm.min, prm.max
        item["value"] = old.get(prm.name, prm.default)     # no update callback while syncing
    return {p.name: p.value for p in settings.params}


classes = (CN_Param, CN_ObjectSettings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.codenodes = PointerProperty(type=CN_ObjectSettings)


def unregister():
    del bpy.types.Object.codenodes
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
