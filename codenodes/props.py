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


def _real_changed(self, context):
    from . import gn_link
    gn_link._dirty[0] = True


def _look_changed(self, context):
    from . import gpu_live
    gpu_live.redraw()


def _emitter_changed(self, context):
    from . import gpu_live, particles
    particles.forget(self.id_data.name)
    gpu_live.redraw()


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
        ('DEFORM', "GPU Mesh", "Code run on every vertex of a mesh coming in from Geometry Nodes"),
    ])
    template_key: StringProperty(description="The template this code started from (the node's Template menu)")
    count: IntProperty(name="Particles", default=20000, min=1, max=16_777_216, soft_max=4_194_304,
                       update=_sim_changed,
                       description="How many particles. Live on GPU handles millions; Real Geometry is "
                                   "comfortable up to about 100,000")
    # Set by the Geometry Nodes sync from the Make Real nodes this code node feeds (never by hand):
    # NONE (drawn live only), EVERY_FRAME, ON_CHANGE or RENDER_ONLY.
    real_mode: StringProperty(default="STANDALONE")
    real_limit: IntProperty(default=0, min=0)
    # live particles: how they look
    color_by: EnumProperty(name="Colour", default='SPEED', update=_look_changed, items=[
        ('SPEED', "Speed", "Blend the two colours by speed"),
        ('AGE', "Age", "Blend the two colours by age / life"),
        ('CODE', "Code", "Use the code's own look() function"),
    ])
    color_a: FloatVectorProperty(name="Slow / Young", subtype='COLOR', size=3, min=0.0, soft_max=1.0,
                                 default=(0.15, 0.3, 1.0), update=_look_changed)
    color_b: FloatVectorProperty(name="Fast / Old", subtype='COLOR', size=3, min=0.0, soft_max=1.0,
                                 default=(1.0, 0.55, 0.2), update=_look_changed)
    speed_range: FloatProperty(name="Speed Range", default=2.0, min=1e-4, soft_max=20.0, update=_look_changed,
                               description="The speed that counts as fully 'fast' for colouring")
    blend: EnumProperty(name="Blend", default='ADD', update=_look_changed, items=[
        ('ADD', "Glow", "Additive: overlapping particles add up to light, like Myriad"),
        ('SOLID', "Solid", "Opaque round points that hide what's behind them"),
    ])
    point_px: FloatProperty(name="Point Size (px)", default=1.5, min=0.5, max=32.0, update=_look_changed)
    gain: FloatProperty(name="Brightness", default=0.35, min=0.0, soft_max=4.0, update=_look_changed)
    prewarm: FloatProperty(name="Pre-warm (s)", default=0.0, min=0.0, max=120.0, update=_sim_changed,
                           description="Seconds simulated before the first frame, so it opens in shape")
    emitter: PointerProperty(type=bpy.types.Object, name="Emit From", update=_emitter_changed,
                             description="Particles can spawn on this object's evaluated geometry "
                                         "(emitPoint / emitNormal in the code)")
    # live surfaces: how they're lit
    quality: FloatProperty(name="Resolution", default=0.6, min=0.15, max=1.0, subtype='FACTOR',
                           update=_look_changed,
                           description="Fraction of the viewport's pixels the live surface is traced at")
    surface_color: FloatVectorProperty(name="Colour", subtype='COLOR', size=3, min=0.0, max=1.0,
                                       default=(0.72, 0.66, 0.58), update=_look_changed,
                                       description="Used when the code has no color(p) function")
    shadows: BoolProperty(name="Shadows", default=True, update=_look_changed)
    ao: BoolProperty(name="Ambient Occlusion", default=True, update=_look_changed)
    fog: FloatProperty(name="Fog", default=0.0, min=0.0, soft_max=0.2, update=_look_changed,
                       description="Distance haze density")
    sky: BoolProperty(name="Sky Background", default=False, update=_look_changed,
                      description="Paint a sky behind everything (Blender objects still draw in front)")
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


class CN_RealSettings(bpy.types.PropertyGroup):
    """Settings of a Make Real node (stored on its node group)."""
    when: EnumProperty(name="When", default='AUTO', update=_real_changed, items=[
        ('AUTO', "Automatic", "Every frame for particles and animated code; when something changes otherwise"),
        ('EVERY_FRAME', "Every Frame", "Make it real on every frame change (animation plays in real geometry)"),
        ('ON_CHANGE', "When Changed", "Only when the code or an input changes; the result stays frozen in time"),
        ('RENDER_ONLY', "Only for Render", "Keep it live in the viewport; make it real just for 'Render with "
                                           "CodeNodes' (Blender's own F12 won't see it)"),
    ])
    keep_velocity: BoolProperty(name="velocity / speed", default=True, update=_real_changed)
    keep_age: BoolProperty(name="age / life", default=True, update=_real_changed)
    stats: StringProperty()


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


classes = (CN_Param, CN_ObjectSettings, CN_RealSettings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.codenodes = PointerProperty(type=CN_ObjectSettings)
    bpy.types.NodeTree.codenodes_real = PointerProperty(type=CN_RealSettings)


def unregister():
    del bpy.types.NodeTree.codenodes_real
    del bpy.types.Object.codenodes
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
