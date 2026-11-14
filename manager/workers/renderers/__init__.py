# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Renderer strategy registry (Unreal foundation spec FR-MR-7).

``get_strategy(renderer)`` and ``strategy_for_source_type(source_type)``
are the only places a renderer value selects behavior; serializers and
views call the returned strategy's methods instead of branching.  Both
raise ``UnsupportedRendererError`` (a ``KeyError``) for an unknown value,
which callers on the request path translate into HTTP 400.
"""

from .base import RendererStrategy, UnsupportedRendererError
from .blender import BlenderStrategy
from .unreal import UnrealStrategy

_STRATEGIES = (BlenderStrategy(), UnrealStrategy())
_BY_RENDERER = {strategy.renderer: strategy for strategy in _STRATEGIES}
_BY_SOURCE_TYPE = {strategy.source_type: strategy for strategy in _STRATEGIES}


def get_strategy(renderer):
    """Return the strategy for a ``Renderer`` value.

    Raises:
        UnsupportedRendererError: no strategy handles *renderer*.
    """
    try:
        return _BY_RENDERER[renderer]
    except (KeyError, TypeError):
        raise UnsupportedRendererError(renderer) from None


def strategy_for_source_type(source_type):
    """Return the strategy for an ``AssetSourceType`` value.

    ``blend_file`` maps to Blender and ``shared_project`` to Unreal.

    Raises:
        UnsupportedRendererError: no strategy handles *source_type*.
    """
    try:
        return _BY_SOURCE_TYPE[source_type]
    except (KeyError, TypeError):
        raise UnsupportedRendererError(source_type) from None


__all__ = [
    'RendererStrategy',
    'UnsupportedRendererError',
    'get_strategy',
    'strategy_for_source_type',
]
