# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the driver re-test and a superseded record on
``memory-report/`` (Unreal per-machine memory test FR-MT-S-3 step (2),
D3, AC-6, AC-8).

Covers: a report with a non-null driver other than a current record's
non-null driver stales every current record of the pair (any key, fits
and does_not_fit, unknown driver included), never another worker's,
another kind's or a superseded record; when that stales the report's own
record the answer is ``retest_due`` and the driver change wins: no peak
raise and no verdict change, even with a ``self`` spill over the line;
an older-driver record of the pair is staled while the report's record
(already on the new driver) still takes the spill; a null or ``Unknown``
driver stales nothing and the other effects still apply; a record whose
driver is unknown is not a reason to stale.  The next heartbeat sweep
creates a re-test pilot for each staled key with QUEUED jobs (AC-6).  A
report naming a record that is no longer current changes no verdict,
peak or driver state anywhere and answers the pair's current state
(AC-8).  Neither path registers the fits-nowhere check.
"""

import pytest

from workers.models import Job, UnrealMeasurement

from .machine_test_helpers import KIND_AMD, heartbeat, kinds_worker, pair_record, render_job
from .machine_verdict_helpers import LARGEST_FITS_PEAK, NEW_DRIVER, OLD_DRIVER
from .memory_report_helpers import (
    accepted, keyed_record, rendering_render, report_body, self_spill, state,
    stored_report,
)
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, OTHER_SEQUENCE, pilots, ready_worker, settings

CHECK = 'workers.services.fits_nowhere.check'
OVER = LARGEST_FITS_PEAK + 1


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def caller(worker_with_token):
    worker, client = worker_with_token
    return kinds_worker(worker, KIND, KIND_AMD), client


@pytest.fixture
def rendering(asset, caller):
    worker, client = caller
    job, record = rendering_render(asset, worker, client)
    return job, record, worker, client


def _stale(*records):
    return [state(record)[2] for record in records]


@pytest.mark.django_db
class TestDriverChange:

    def test_a_new_driver_stales_the_whole_pair(self, rendering, asset, make_worker_client):
        job, record, worker, client = rendering
        other, _other_client, _token = make_worker_client('mt-driver-other')
        ready_worker(other)
        staled = [keyed_record(asset, 'd1' * 32, worker),
                  keyed_record(asset, 'd2' * 32, worker, verdict='does_not_fit'),
                  keyed_record(asset, 'd3' * 32, worker, driver_version='')]
        kept = [keyed_record(asset, 'd4' * 32, other),
                keyed_record(asset, 'd5' * 32, worker, KIND_AMD),
                keyed_record(asset, 'd6' * 32, worker, current=False)]
        accepted(client, job, report_body(record, driver_version=NEW_DRIVER), result='retest_due')
        assert _stale(record, *staled) == ['driver_changed'] * 4
        assert _stale(*kept) == [''] * 3

    def test_the_driver_change_wins_over_spill_and_peak(self, rendering, mocker,
                                                        django_capture_on_commit_callbacks):
        job, record, _worker, client = rendering
        check = mocker.patch(CHECK)
        body = self_spill(record, driver_version=NEW_DRIVER, job_peak_mb=OVER)
        with django_capture_on_commit_callbacks(execute=True):
            accepted(client, job, body, result='retest_due')
        assert state(record) == ('fits', 'pilot', 'driver_changed', None, None, OLD_DRIVER, True)
        assert stored_report(job) == {'report': body, 'result': 'retest_due'}
        check.assert_not_called()

    def test_an_older_record_is_staled_while_the_report_still_counts(self, asset, caller):
        worker, client = caller
        older = keyed_record(asset, 'd7' * 32, worker)
        job, record = rendering_render(asset, worker, client, driver_version=NEW_DRIVER)
        accepted(client, job, self_spill(record, driver_version=NEW_DRIVER), result='does_not_fit')
        assert _stale(older, record) == ['driver_changed', '']
        assert state(record)[:2] == ('does_not_fit', 'spill')

    @pytest.mark.parametrize('driver', [None, 'Unknown'], ids=['null', 'unknown'])
    def test_an_unknown_driver_stales_nothing(self, rendering, asset, driver):
        job, record, worker, client = rendering
        other_key = keyed_record(asset, 'd8' * 32, worker)
        accepted(client, job, self_spill(record, driver_version=driver), result='does_not_fit')
        assert _stale(record, other_key) == ['', '']
        assert state(record)[:2] == ('does_not_fit', 'spill')

    def test_an_unknown_stored_driver_is_no_reason_to_stale(self, asset, caller):
        worker, client = caller
        job, record = rendering_render(asset, worker, client, driver_version='')
        accepted(client, job, report_body(record, driver_version=NEW_DRIVER))
        assert _stale(record) == ['']


@pytest.mark.django_db
class TestRetestPilot:

    def test_the_next_sweep_retests_each_staled_key(self, rendering, asset, django_capture_on_commit_callbacks):
        job, record, worker, client = rendering
        queued = render_job(asset, name='MtDriverQueued')
        other_job = render_job(asset, name='MtDriverOther', render_settings=settings(sequence=OTHER_SEQUENCE))
        other = keyed_record(asset, other_job.unreal_target_sha256, worker)
        assert pilots(asset) == []
        accepted(client, job, report_body(record, driver_version=NEW_DRIVER), result='retest_due')
        assert pilots(asset) == []  # the report creates nothing itself
        heartbeat(client, worker, django_capture_on_commit_callbacks)
        found = sorted((p.unreal_target_sha256, p.target_worker_id, p.render_settings['unreal.card_kind'])
                       for p in pilots(asset) if p.render_settings['unreal.card_kind'] == KIND)
        assert found == sorted([(queued.unreal_target_sha256, worker.pk, KIND),
                                (other.target_sha256, worker.pk, KIND)])
        assert not UnrealMeasurement.objects.filter(pk__in=[record.pk, other.pk], current=True).exists()


@pytest.mark.django_db
class TestSupersededRecord:

    @pytest.fixture
    def superseded(self, rendering, asset):
        job, record, worker, client = rendering
        UnrealMeasurement.objects.filter(pk=record.pk).update(current=False)
        newer = pair_record(asset, job.unreal_target_sha256, worker, pilot='QUEUED', driver_version=OLD_DRIVER)
        older_key = keyed_record(asset, 'd9' * 32, worker)
        return job, record, newer, older_key, client

    def test_nothing_changes_but_the_stored_report(self, superseded, mocker, django_capture_on_commit_callbacks):
        job, record, newer, older_key, client = superseded
        before = [state(r) for r in (record, newer, older_key)]
        check = mocker.patch(CHECK)
        body = self_spill(record, driver_version=NEW_DRIVER, job_peak_mb=OVER)
        with django_capture_on_commit_callbacks(execute=True):
            accepted(client, job, body, result='testing')
        assert [state(r) for r in (record, newer, older_key)] == before
        assert stored_report(job) == {'report': body, 'result': 'testing'}
        check.assert_not_called()

    def test_the_answer_is_the_pairs_current_state(self, superseded):
        job, record, newer, _older_key, client = superseded
        UnrealMeasurement.objects.filter(pk=newer.pk).update(verdict='fits', peak_mb=8102)
        Job.objects.filter(pk=newer.pilot_job_id).update(status='DONE')
        accepted(client, job, report_body(record), result='fits')

    def test_without_a_current_record_the_answer_is_not_tried(self, superseded):
        job, record, newer, _older_key, client = superseded
        UnrealMeasurement.objects.filter(pk=newer.pk).update(current=False)
        accepted(client, job, report_body(record), result='not_tried')
