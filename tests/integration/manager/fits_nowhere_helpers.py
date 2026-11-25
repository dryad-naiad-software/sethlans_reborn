# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the per-machine memory test chunk 7 integration tests
(Unreal per-machine memory test D12, D15, D16 (b), FR-MT-N-1/2: the
fits-nowhere stop, ``services/fits_nowhere``).  Not a test module.

:func:`dnf_record` stores a current ``does_not_fit`` record of a pair
with the card figures the FR-MT-N-2 message names; :func:`stop_text` is
that message written out word for word from the spec (not from the
product constant); :func:`check_key` runs the check for a job's key with
the project's current CVar overrides.
"""

from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import captured_sql
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Job, Project, Worker
from workers.services import fits_nowhere

from .machine_test_helpers import HB, emulated_row_locks, pair_record
from .unreal_pilot_helpers import KIND

OFFLINE = timedelta(seconds=WORKER_STALENESS_SECONDS + 60)
PRESET_SIZE = "at the preset's size"
GPU = 'Example GPU 24'
GPU_TOTAL = 24564
LOWER = 23100


def stop_text(card, total, host, lower, size=PRESET_SIZE):
    """FR-MT-N-2's message, word for word."""
    return ("This render does not fit on any graphics card that is on. On the largest card tried, "
            f"{card} ({total} MB) on {host}, it used at least {lower} MB {size} without fitting. "
            "Options: render at a lower resolution, use draft settings, or add a machine with a larger card "
            "and render again.")


def go_offline(worker):
    """Make ``worker`` not seen within ``WORKER_STALENESS_SECONDS`` (not online, D5)."""
    Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - OFFLINE)


def dnf_record(asset, target_hash, worker, kind=KIND, *, total=GPU_TOTAL, lower=LOWER, name=GPU, **fields):
    """A current, not stale ``does_not_fit`` record of the pair with its card figures."""
    return pair_record(asset, target_hash, worker, kind, verdict='does_not_fit', gpu_total_mb=total,
                       lower_bound_mb=lower, gpu_name=name, **fields)


def overrides_of(asset):
    return Project.objects.values_list('unreal_overrides_sha256', flat=True).get(pk=asset.project_id)


def check_key(job, overrides=None, target=None):
    """``fits_nowhere.check`` for ``job``'s key; the project's current overrides unless given."""
    return fits_nowhere.check(job.asset.project_id, job.asset_id,
                              overrides_of(job.asset) if overrides is None else overrides,
                              job.unreal_target_sha256 if target is None else target)


def statuses(*jobs):
    by_pk = dict(Job.objects.filter(pk__in=[job.pk for job in jobs]).values_list('pk', 'status'))
    return [by_pk[job.pk] for job in jobs]


def error_of(job):
    return Job.objects.values_list('error_message', flat=True).get(pk=job.pk)


def sweep_sql(client, worker, capture):
    """POST a heartbeat, then run its on-commit callbacks under row-lock emulation; the captured SQL."""
    with capture() as callbacks:
        resp = client.post(HB, {'hostname': worker.hostname}, format='json')
    assert resp.status_code == 200, resp.data
    assert callbacks, "the heartbeat registered no on-commit sweep"
    with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
        for callback in callbacks:
            callback()
    return captured_sql(ctx)
