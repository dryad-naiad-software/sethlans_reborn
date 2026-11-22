# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the fits-nowhere rule of ``fits_nowhere.check``
(Unreal per-machine memory test D12, FR-MT-N-1, AC-11).

Covers: with at least one eligible online pair, every such pair
``does_not_fit`` and nothing of the key RENDERING, every QUEUED job of the
key (a paused one too) becomes ERROR and the count is returned; DONE jobs
and jobs of another key are untouched.  Nothing stops with no online pair,
no eligible pair, an online pair not tried (another machine or another
kind of the same machine), a failed, testing or ``retest_due`` pair, a
``fits`` record on an offline machine (stale or not), or a render or pilot
job of the key RENDERING (it stops once that job ended).  An offline
machine not tried does not block; records of other CVar overrides and
superseded records do not count.  A check for overrides the project no
longer has, an empty target or a deleted project stops nothing.  A check
called in a transaction that already locked the Worker rows raises
(D15: its Project and Job locks come first) and changes nothing; inside
an outer transaction without that lock it still runs.
"""

import uuid

import pytest
from django.db import transaction

from workers.models import Job, Project, UnrealMeasurement, Worker
from workers.services import fits_nowhere
from workers.services.worker_locks import lock_workers

from .fits_nowhere_helpers import (
    GPU, GPU_TOTAL, LOWER, check_key, dnf_record, error_of, go_offline, overrides_of, statuses, stop_text,
)
from .machine_test_helpers import KIND_AMD, kinds_worker, pair_record, pilot_job, render_job
from .unreal_helpers import caps, unreal_asset
from .unreal_pilot_helpers import KIND, ready_worker, settings

OTHER_OVERRIDES = 'f0' * 32


@pytest.fixture
def job(db):
    return render_job(unreal_asset())


@pytest.fixture
def worker(worker_with_token):
    return ready_worker(worker_with_token[0])


@pytest.fixture
def other(make_worker_client):
    return ready_worker(make_worker_client('mt-fn-other')[0])


def _dnf(job, worker, kind=KIND, **fields):
    return dnf_record(job.asset, job.unreal_target_sha256, worker, kind, **fields)


@pytest.mark.django_db
class TestStops:

    def test_one_online_pair_that_does_not_fit_stops_the_key(self, job, worker):
        _dnf(job, worker)
        assert check_key(job) == 1
        job.refresh_from_db()
        assert job.status == 'ERROR' and job.completed_at is not None
        assert job.error_message == stop_text(GPU, GPU_TOTAL, worker.hostname, LOWER)

    def test_every_pair_of_two_machines_does_not_fit(self, job, worker, other):
        kinds_worker(other, KIND, KIND_AMD)
        _dnf(job, worker)
        _dnf(job, other)
        _dnf(job, other, KIND_AMD)
        assert check_key(job) == 1
        assert statuses(job) == ['ERROR']

    def test_every_queued_job_of_the_key_and_only_those(self, job, worker):
        again = render_job(job.asset, name='MtFnAgain')
        paused = render_job(job.asset, name='MtFnPaused', is_paused=True)
        done = render_job(job.asset, name='MtFnDone', status='DONE')
        other_key = render_job(job.asset, name='MtFnOtherKey', render_settings=settings(preset='/Game/P/O.O'))
        assert again.unreal_target_sha256 == paused.unreal_target_sha256 == job.unreal_target_sha256
        assert other_key.unreal_target_sha256 != job.unreal_target_sha256
        _dnf(job, worker)
        assert check_key(job) == 3
        assert statuses(job, again, paused, done, other_key) == ['ERROR', 'ERROR', 'ERROR', 'DONE', 'QUEUED']
        assert error_of(paused) == error_of(job) and error_of(done) == error_of(other_key) == ''

    def test_an_offline_machine_not_tried_does_not_block(self, job, worker, other):
        go_offline(other)
        _dnf(job, worker)
        assert check_key(job) == 1

    def test_records_of_other_overrides_and_superseded_records_do_not_count(self, job, worker, other):
        go_offline(other)
        pair_record(job.asset, job.unreal_target_sha256, other, verdict='fits', overrides=OTHER_OVERRIDES)
        pair_record(job.asset, job.unreal_target_sha256, other, verdict='fits', current=False)
        _dnf(job, worker)
        assert check_key(job) == 1

    def test_an_ineligible_online_machine_neither_blocks_nor_counts(self, job, worker, other):
        Worker.objects.filter(pk=other.pk).update(unreal_capabilities=caps(builds=('5.7.0-1',)))
        _dnf(job, worker)
        _dnf(job, other, total=49140)  # not an eligible pair: not the largest card tried either
        assert check_key(job) == 1
        assert error_of(job) == stop_text(GPU, GPU_TOTAL, worker.hostname, LOWER)


@pytest.mark.django_db
class TestNoStop:

    def test_no_online_pair(self, job, worker):
        _dnf(job, worker)
        go_offline(worker)
        assert check_key(job) == 0
        assert statuses(job) == ['QUEUED'] and error_of(job) == ''

    def test_no_eligible_pair(self, job, worker):
        _dnf(job, worker)
        Worker.objects.filter(pk=worker.pk).update(unreal_capabilities=caps(reachable=False))
        assert check_key(job) == 0

    def test_only_records_of_other_overrides(self, job, worker):
        _dnf(job, worker, overrides=OTHER_OVERRIDES)
        assert check_key(job) == 0

    def test_an_online_machine_not_tried_blocks(self, job, worker, other):
        _dnf(job, worker)
        assert check_key(job) == 0
        assert statuses(job) == ['QUEUED']

    def test_an_untried_kind_of_the_same_machine_blocks(self, job, worker):
        kinds_worker(worker, KIND, KIND_AMD)
        _dnf(job, worker)
        assert check_key(job) == 0

    @pytest.mark.parametrize('state', [
        dict(pilot='ERROR', error='boom'),
        dict(pilot='QUEUED'),
        dict(verdict='does_not_fit', stale='driver_changed'),
        dict(verdict='fits', stale='driver_changed'),
    ], ids=['failed', 'testing', 'retest-due-does-not-fit', 'retest-due-fits'])
    def test_a_pair_not_settled_blocks(self, job, worker, other, state):
        _dnf(job, worker)
        pair_record(job.asset, job.unreal_target_sha256, other, **state)
        assert check_key(job) == 0
        assert statuses(job) == ['QUEUED']

    @pytest.mark.parametrize('stale', ['', 'driver_changed'], ids=['fits', 'fits-retest-due'])
    def test_a_fits_record_on_an_offline_machine_blocks(self, job, worker, other, stale):
        pair_record(job.asset, job.unreal_target_sha256, other, verdict='fits', stale=stale)
        go_offline(other)
        _dnf(job, worker)
        assert check_key(job) == 0

    def test_overrides_the_project_no_longer_has(self, job, worker):
        _dnf(job, worker)
        old = overrides_of(job.asset)
        Project.objects.filter(pk=job.asset.project_id).update(
            unreal_overrides_sha256=OTHER_OVERRIDES)
        assert check_key(job, overrides=old) == 0
        assert check_key(job) == 0  # the current key has no records yet
        assert statuses(job) == ['QUEUED']

    def test_an_empty_target_or_a_deleted_project(self, job, worker):
        _dnf(job, worker)
        assert check_key(job, target='') == 0
        assert fits_nowhere.check(uuid.uuid4(), job.asset_id, overrides_of(job.asset), job.unreal_target_sha256) == 0
        assert statuses(job) == ['QUEUED']


@pytest.mark.django_db
class TestRendering:

    def _rendering_render(self, job, worker):
        return render_job(job.asset, name='MtFnRendering', status='RENDERING', assigned_worker=worker)

    def _rendering_pilot(self, job, worker):
        """A pilot of the key RENDERING whose record a newer one superseded (so the pair is not ``testing``)."""
        pilot = pilot_job(job.asset, worker, status='RENDERING')
        UnrealMeasurement.objects.filter(pk=pair_record(job.asset, job.unreal_target_sha256, worker,
                                                        current=False).pk).update(pilot_job=pilot)
        return pilot

    @pytest.mark.parametrize('make', ['_rendering_render', '_rendering_pilot'], ids=['render', 'pilot'])
    def test_nothing_stops_while_a_job_of_the_key_renders_then_it_does(self, job, worker, make):
        rendering = getattr(self, make)(job, worker)
        _dnf(job, worker)
        assert check_key(job) == 0
        assert statuses(job, rendering) == ['QUEUED', 'RENDERING']
        Job.objects.filter(pk=rendering.pk).update(status='DONE')
        assert check_key(job) == 1
        assert statuses(job, rendering) == ['ERROR', 'DONE']

    def test_a_job_of_another_key_rendering_does_not_block(self, job, worker):
        render_job(job.asset, name='MtFnOther', status='RENDERING', assigned_worker=worker,
                   render_settings=settings(preset='/Game/P/O.O'))
        _dnf(job, worker)
        assert check_key(job) == 1


@pytest.mark.django_db
class TestOwnTransaction:

    def test_after_lock_workers_it_raises_and_changes_nothing(self, job, worker):
        _dnf(job, worker)
        with transaction.atomic():
            lock_workers()
            with pytest.raises(RuntimeError, match='own transaction'):
                check_key(job)
        assert statuses(job) == ['QUEUED'] and error_of(job) == ''

    def test_inside_an_outer_transaction_without_it_runs(self, job, worker):
        _dnf(job, worker)
        with transaction.atomic():
            assert check_key(job) == 1
        assert statuses(job) == ['ERROR']
