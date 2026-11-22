# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the admin measurement retry per (worker, card kind)
pair (``services/unreal_retry.py``; Unreal per-machine memory test D2, D5,
FR-MT-R-5; foundation FR-PM-6).

Covers: one worker's ``fits`` result does not block retrying another
worker's failed pair (no re-measurement of the fitting one); a pair that
is testing never gets a second pilot and does not block the others, with
or without ``remeasure``; pairs that are due a re-test or were never tried
are retried; ``remeasure`` re-measures pairs with a result (``remeasure``
true on their records only); the 409 texts of ``_refusal``: every pair
measured (or measured and testing) without ``remeasure``, every pair
testing, and no machine that can run the test (offline, or at the D5 cap
of open pilots); a refused retry creates nothing.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Job, UnrealMeasurement, Worker
from workers.services import machine_tests

from .machine_test_helpers import current_of, pair_record, render_job
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import pilots, ready_worker

ALREADY_MEASURED = 'This target is already measured; send "remeasure": true to measure it again.'
ALREADY_PENDING = "A pilot render is already queued or running."
NO_MACHINE = "No machine that is on can run this memory test now."
STATES = {
    'fits': {'verdict': 'fits'},
    'does_not_fit': {'verdict': 'does_not_fit'},
    'testing': {'pilot': 'QUEUED'},
    'rendering': {'pilot': 'RENDERING'},
    'failed': {'pilot': 'ERROR', 'error': 'boom'},
    'retest_due': {'verdict': 'fits', 'stale': 'driver_changed'},
}


@pytest.fixture
def setup(db, worker_with_token, make_worker_client):
    """A render job (no pilot yet) and two eligible workers with one card kind each."""
    worker_a = ready_worker(worker_with_token[0])
    worker_b = ready_worker(make_worker_client('mt-retry-b')[0])
    job = render_job(unreal_asset())
    return job, worker_a, worker_b


def _retry(admin_client, job, **body):
    return admin_client.post(f'/api/projects/{job.asset.project_id}/unreal-measurement/retry/',
                             {'job_id': job.pk, **body}, format='json')


def _new_pilots(job, before):
    """``{target worker pk: remeasure}`` of the pilots made since ``before`` (a set of pilot pks)."""
    made = [p for p in pilots(job.asset) if p.pk not in before]
    return {p.target_worker_id: UnrealMeasurement.objects.get(pilot_job=p).remeasure for p in made}


def _pilot_pks(job):
    return {p.pk for p in pilots(job.asset)}


@pytest.mark.django_db
class TestPerPair:

    def test_a_fits_pair_does_not_block_a_failed_one(self, admin_client, setup):
        job, worker_a, worker_b = setup
        fits = pair_record(job.asset, job.unreal_target_sha256, worker_a, **STATES['fits'])
        failed = pair_record(job.asset, job.unreal_target_sha256, worker_b, **STATES['failed'])
        before = _pilot_pks(job)
        resp = _retry(admin_client, job)
        assert resp.status_code == 201, resp.data
        assert _new_pilots(job, before) == {worker_b.pk: False}
        assert Job.objects.get(pk=resp.data['pilot_job_id']).target_worker_id == worker_b.pk
        assert current_of(job.asset, job.unreal_target_sha256, worker_a) == [fits.pk]
        assert current_of(job.asset, job.unreal_target_sha256, worker_b) == [resp.data['measurement_id']]
        assert UnrealMeasurement.objects.get(pk=failed.pk).current is False

    @pytest.mark.parametrize('testing', ['testing', 'rendering'])
    @pytest.mark.parametrize('remeasure', [False, True], ids=['plain', 'remeasure'])
    def test_a_testing_pair_is_never_retested(self, admin_client, setup, testing, remeasure):
        job, worker_a, worker_b = setup
        busy = pair_record(job.asset, job.unreal_target_sha256, worker_a, **STATES[testing])
        pair_record(job.asset, job.unreal_target_sha256, worker_b, **STATES['failed'])
        before = _pilot_pks(job)
        resp = _retry(admin_client, job, remeasure=remeasure)
        assert resp.status_code == 201, resp.data
        assert _new_pilots(job, before) == {worker_b.pk: False}
        assert current_of(job.asset, job.unreal_target_sha256, worker_a) == [busy.pk]
        assert Job.objects.get(pk=busy.pilot_job_id).status == STATES[testing]['pilot']

    def test_retest_due_and_untried_pairs_are_retried(self, admin_client, setup):
        job, worker_a, worker_b = setup
        pair_record(job.asset, job.unreal_target_sha256, worker_a, **STATES['retest_due'])
        before = _pilot_pks(job)
        resp = _retry(admin_client, job)
        assert resp.status_code == 201, resp.data
        assert _new_pilots(job, before) == {worker_a.pk: False, worker_b.pk: False}

    def test_remeasure_marks_only_pairs_with_a_result(self, admin_client, setup):
        job, worker_a, worker_b = setup
        pair_record(job.asset, job.unreal_target_sha256, worker_a, **STATES['does_not_fit'])
        pair_record(job.asset, job.unreal_target_sha256, worker_b, **STATES['failed'])
        before = _pilot_pks(job)
        resp = _retry(admin_client, job, remeasure=True)
        assert resp.status_code == 201, resp.data
        assert _new_pilots(job, before) == {worker_a.pk: True, worker_b.pk: False}


@pytest.mark.django_db
class TestRefusals:

    @pytest.mark.parametrize('state_a, state_b, remeasure, message', [
        ('fits', 'fits', False, ALREADY_MEASURED),
        ('does_not_fit', 'fits', False, ALREADY_MEASURED),
        ('fits', 'testing', False, ALREADY_MEASURED),
        ('testing', 'rendering', False, ALREADY_PENDING),
        ('testing', 'testing', True, ALREADY_PENDING),
    ])
    def test_no_pair_needs_a_pilot(self, admin_client, setup, state_a, state_b, remeasure, message):
        job, worker_a, worker_b = setup
        pair_record(job.asset, job.unreal_target_sha256, worker_a, **STATES[state_a])
        pair_record(job.asset, job.unreal_target_sha256, worker_b, **STATES[state_b])
        before = _pilot_pks(job)
        resp = _retry(admin_client, job, remeasure=remeasure)
        assert (resp.status_code, resp.data) == (409, {'error': message})
        assert _pilot_pks(job) == before

    def test_no_machine_is_on(self, admin_client, setup):
        job, _worker_a, _worker_b = setup
        old = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS + 60)
        Worker.objects.update(last_seen=old)
        resp = _retry(admin_client, job)
        assert (resp.status_code, resp.data) == (409, {'error': NO_MACHINE})
        assert pilots(job.asset) == []

    def test_every_machine_at_the_open_pilot_cap(self, admin_client, setup):
        job, worker_a, worker_b = setup
        other = unreal_asset(job.asset.project, name='MtRetryCapFill')
        for worker in (worker_a, worker_b):
            for n in range(machine_tests.MAX_OPEN_PILOTS):
                pair_record(other, f'{worker.pk:02x}{n:062x}', worker, pilot='QUEUED')
        before = _pilot_pks(job)
        resp = _retry(admin_client, job)
        assert (resp.status_code, resp.data) == (409, {'error': NO_MACHINE})
        assert _pilot_pks(job) == before
