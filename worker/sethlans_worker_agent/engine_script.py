# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Render engine selection in the generated Blender script (#242).

The product names engines ``CYCLES``, ``BLENDER_EEVEE_NEXT`` and
``WORKBENCH`` (the manager's ``RenderEngine`` values, stored with every
job). Blender's own identifiers differ by version: Eevee is
``BLENDER_EEVEE_NEXT`` on 4.2 to 4.5 and ``BLENDER_EEVEE`` before and
after, and Workbench is ``BLENDER_WORKBENCH`` everywhere. Rather than
keeping a version table, the script sets the engine by family: a small
fixed helper tries the family's identifiers in a fixed order and keeps the
first one this Blender accepts, catching only the assignment's "enum not
found" ``TypeError``. When none is accepted it raises an error naming the
requested engine and the identifiers tried; the failing settings script
then stops Blender before the render and the job fails with that error
(``--python-exit-code``, #240, blender_failure.py).

Everything the worker decides by engine (GPU setup, yield handling,
thumbnails, progress parsing) keeps using the product's name; only the
value inside Blender is translated.
"""

# Product (or Blender) engine name -> Blender identifiers to try, in order.
# Eevee tries BLENDER_EEVEE first so a build from 4.1 or earlier with the
# experimental Eevee Next enabled still gets the stable engine.
ENGINE_IDENTIFIERS = {
    'CYCLES': ('CYCLES',),
    'BLENDER_EEVEE_NEXT': ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'),
    'BLENDER_EEVEE': ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'),
    'WORKBENCH': ('BLENDER_WORKBENCH',),
    'BLENDER_WORKBENCH': ('BLENDER_WORKBENCH',),
}

# Emitted once at the top of every render script.
ENGINE_HELPER = r"""
def _sethlans_set_engine(render, requested, identifiers):
    for identifier in identifiers:
        try:
            render.engine = identifier
        except TypeError as exc:
            if 'enum' in str(exc) and 'not found' in str(exc):
                continue
            raise
        print(f'Render engine {requested}: set {identifier}')
        return
    raise ValueError(
        f'Render engine {requested!a}: this Blender accepts none of {list(identifiers)!a}')
"""


def is_known_engine(value) -> bool:
    """True if ``value`` is an engine name the helper can translate."""
    return isinstance(value, str) and value in ENGINE_IDENTIFIERS


def engine_call(render_expr: str, engine: str) -> str:
    """Return the helper call that sets ``engine`` on ``render_expr``.

    ``render_expr`` is fixed script text chosen by the generator (never
    external input); ``engine`` must be a known engine name.
    """
    if not is_known_engine(engine):
        raise ValueError(f"Unknown render engine: {engine!r}")
    return (
        f"_sethlans_set_engine({render_expr}, {ascii(engine)}, "
        f"{ascii(ENGINE_IDENTIFIERS[engine])})"
    )
