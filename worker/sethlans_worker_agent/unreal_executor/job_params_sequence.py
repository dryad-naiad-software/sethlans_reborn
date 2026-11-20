# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal job kinds of sequence rendering in a poll payload (sequence
rendering FR-SW-1, FR-SD-3, D3), kept apart so ``job_params`` stays small.

* ``shots`` (shot discovery): ``start_frame == end_frame == 0`` and no
  measured peak; the job renders nothing (FR-SD-4).

``check(kind, job_data, require)`` runs the kind's checks with
``job_params``' own ``require(condition, field, reason)`` and returns the
job's measured peak (``None`` for these kinds).
"""
from typing import Any, Callable, Optional

SHOTS = 'shots'
KINDS = (SHOTS,)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check(kind: str, job_data: dict, require: Callable[[bool, str, str], None]) -> Optional[int]:
    """FR-SD-3: a ``shots`` job carries frame 0 only and is held by no measurement key."""
    require(job_data.get('unreal_measured_peak_mb') is None, 'unreal_measured_peak_mb',
            f"must be null for a {kind} job")
    start, end = job_data.get('start_frame'), job_data.get('end_frame')
    require(_is_int(start) and _is_int(end) and start == end == 0, 'start_frame',
            f"a {kind} job has start_frame == end_frame == 0")
    return None
