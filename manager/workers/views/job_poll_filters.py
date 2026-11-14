# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker-poll filtering for ``GET /api/jobs/`` (Unreal foundation FR-RT-1..3, 15).

* :func:`apply_worker_poll_filters` runs **only** inside the worker-poll
  branch of ``JobViewSet.get_queryset`` (``status`` and
  ``assigned_worker__isnull`` both present), after the pause checks.  It
  selects each requested renderer's jobs through its strategy's
  ``poll_q`` and orders the result **oldest first** (D7).
* :func:`apply_device_prefs_filter` applies to every list, as before.

``renderers`` absent means Blender only, so workers that predate the
renderer work never receive another renderer's jobs.
"""

import functools
import logging
import operator

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes
from rest_framework.exceptions import ValidationError

from ..constants import RenderDevice, Renderer
from ..renderers import get_strategy

logger = logging.getLogger(__name__)

# Oldest first; ``id`` breaks ties between bulk-created children, whose
# per-instance ``submitted_at`` defaults can coincide (FR-RT-15).
POLL_ORDERING = ('submitted_at', 'id')

LIST_PARAMETERS = [
    OpenApiParameter(
        name='device_prefs',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description=(
            "Comma-separated list of RenderDevice values (CPU, GPU, ANY) "
            "to include. Used by workers to filter for jobs matching "
            "their currently free slots. Empty or invalid values return "
            "HTTP 400. Example: ``device_prefs=GPU,ANY``."
        ),
    ),
    OpenApiParameter(
        name='available_versions',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description=(
            "Comma-separated list of installed Blender versions "
            "(e.g. ``4.5.8,4.4.3``) for worker-poll version filtering. "
            "Matches at the series level (major.minor)."
        ),
    ),
    OpenApiParameter(
        name='renderers',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description=(
            "Worker polls only: comma-separated Renderer values "
            "(BLENDER, UNREAL) the worker can render. Absent means "
            "BLENDER only. Invalid values return HTTP 400. A worker poll "
            "returns jobs oldest first (submitted_at, then id)."
        ),
    ),
    OpenApiParameter(
        name='unreal_builds',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description=(
            "Worker polls only: comma-separated Unreal Engine builds "
            "(e.g. ``5.8.4-58902052``), at most 10. Ignored unless "
            "``renderers`` includes UNREAL."
        ),
    ),
]


def _parse_renderers(params):
    """Return the requested renderer set; absent means ``{BLENDER}``."""
    param = params.get('renderers')
    if param is None:
        return {Renderer.BLENDER.value}
    requested = {v.strip().upper() for v in param.split(',') if v.strip()}
    if not requested:
        raise ValidationError(
            f"renderers must be a non-empty CSV of {sorted(Renderer.values)}."
        )
    invalid = sorted(requested - set(Renderer.values))
    if invalid:
        raise ValidationError(
            f"Invalid renderers value(s): {invalid}. "
            f"Allowed: {sorted(Renderer.values)}."
        )
    return requested


def apply_worker_poll_filters(queryset, params, worker=None):
    """Restrict a worker poll to the requested renderers, oldest first.

    Each renderer's strategy contributes one ``Q`` (Blender: today's
    ``available_versions`` series filter), OR-ed together.  ``worker`` is
    the polling caller's ``Worker`` row, or ``None`` for a non-worker
    caller; it lets a strategy leave out jobs its claim check would
    refuse for that worker (FR-MS-5).

    Raises:
        rest_framework.exceptions.ValidationError: an invalid
            ``renderers`` value, or a strategy's own parameter error
            (HTTP 400).
    """
    renderers = _parse_renderers(params)
    q = functools.reduce(
        operator.or_,
        (get_strategy(r).poll_q(params, worker) for r in sorted(renderers)),
    )
    return queryset.filter(q).order_by(*POLL_ORDERING)


def apply_device_prefs_filter(queryset, params):
    """Filter by device_prefs CSV query param (FR-9a, FR-9b).

    Parses a comma-separated list of RenderDevice values and
    restricts the queryset to jobs whose ``render_device`` is in the
    requested set. Invalid values or an empty parsed list raise a
    ValidationError (HTTP 400); the parameter being absent is a no-op.
    """
    param = params.get('device_prefs')
    if param is None:
        return queryset

    raw = [v.strip().upper() for v in param.split(',') if v.strip()]
    if not raw:
        raise ValidationError(
            "device_prefs must be a non-empty CSV of "
            f"{sorted(RenderDevice.values)}."
        )
    allowed = set(RenderDevice.values)
    invalid = [v for v in raw if v not in allowed]
    if invalid:
        raise ValidationError(
            f"Invalid device_prefs value(s): {invalid}. "
            f"Allowed: {sorted(allowed)}."
        )
    logger.debug(
        "Filtering jobs by device_prefs=%s.", raw,
    )
    return queryset.filter(render_device__in=raw)
