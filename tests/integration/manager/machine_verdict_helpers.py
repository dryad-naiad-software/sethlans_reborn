# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the per-machine memory test chunk 5 integration tests
(Unreal per-machine memory test FR-MT-V-3, D3, D6: the pilot's card kind,
driver and verdict on ``POST /api/jobs/{id}/pilot-result/``).  Not a test
module.

``pilot_body()`` (``unreal_pilot_helpers``) runs on a 32,607 MB card with
``baseline_used_mb`` 945, so D6's 93% line is ``floor(32607 * 0.93)`` =
:data:`LINE` and the largest ``fits`` peak is ``LINE - 945``.
"""

import math

from workers.models import UnrealMeasurement
from workers.renderers.unreal_machine import pair_state_case

from .unreal_pilot_helpers import JOBS, POLL, pilot_body

TOTAL = 32607
BASELINE = 945
LINE = math.floor(TOTAL * 0.93)
LARGEST_FITS_PEAK = LINE - BASELINE
OLD_DRIVER = '31.0.15.5222'
NEW_DRIVER = '32.0.16.1692'


def peak_body(peak_mb, verdict='fits', **overrides):
    """A plausible body whose gate figure is ``peak_mb + BASELINE`` (card peak, no CSV peak)."""
    body = pilot_body(peak_mb=peak_mb, card_delta_peak_mb=peak_mb, csv_local_used_peak_mb=None,
                      peak_source='card', baseline_used_mb=BASELINE, verdict=verdict,
                      lower_bound_mb=peak_mb if verdict == 'does_not_fit' else None)
    body.update(overrides)
    return body


def dnf_body(**overrides):
    """``pilot_body()`` with D6's ``does_not_fit`` verdict (``lower_bound_mb`` = ``peak_mb``)."""
    body = pilot_body(verdict='does_not_fit', lower_bound_mb=8102)
    body.update(overrides)
    return body


def state_of(record):
    """The D2 state of ``record`` as the poll and claim read it."""
    return UnrealMeasurement.objects.filter(pk=record.pk).annotate(
        _state=pair_state_case()).values_list('_state', flat=True).get()


def fresh(record):
    return UnrealMeasurement.objects.get(pk=record.pk)


def poll_ids(client):
    """The job ids a poll offers the calling worker."""
    resp = client.get(JOBS, POLL)
    assert resp.status_code == 200, resp.data
    return [row['id'] for row in resp.data]


def assert_untouched(job):
    """A refused pilot-result changed neither the pilot nor its record."""
    job.refresh_from_db()
    assert job.status == 'RENDERING'
    record = UnrealMeasurement.objects.get(pilot_job=job)
    assert (record.peak_mb, record.verdict, record.driver_version) == (None, '', '')
