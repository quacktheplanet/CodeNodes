"""Buildings from a spec: spec -> floor plan -> building -> equipment -> verification.

    spec.py       what an assistant writes: site, spaces, closeness, flow, equipment, rules
    planner.py    the floor plan (pure Python, seeded)
    kit.py        parts as formulas of sliders -> plain mesh or Geometry Nodes group
    equipment.py  the parametric generators (conveyor, rack, robot arm, CNC, ...)
    layout.py     where each piece of equipment stands
    verify.py     measured checks and the repair loop
    edits.py      small changes, re-solved locally
    drawing.py    a plan as a PNG, in pure Python
    build.py      the building and equipment in Blender (bpy)
    tools.py      the six tools an assistant uses (bpy)

See docs/MODELING_RESEARCH.md for the reasoning and ROADMAP P2.8 for what is done.
"""
