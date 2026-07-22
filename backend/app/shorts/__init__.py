"""Shorts: vertical 9:16 cut-downs of a **finished** long render.

Nothing in this package re-renders a scene. A Short is always cut out of an
already-exported MP4 that carries a versioned render manifest, so the narration,
music, burned-in subtitles and in-scene transitions come through exactly as the
long pipeline mixed them.

The long render pipeline is untouched by this package apart from one additive
call: :func:`app.shorts.manifest.write_render_manifest`, which records the
section timeline alongside the export so a Short never has to guess where a
scene started.
"""
