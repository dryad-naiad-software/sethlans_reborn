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
testing, no machine that can run the test (offline), and every machine
that needs one at the D5 cap of open pilots; a refused retry creates
nothing.  With ``worker`` (FR-MT-P-4): only that worker's eligible kinds are
tested, ``remeasure`` re-measures only that worker, a worker that cannot
test now (offline, override, unknown) is 400, a malformed ``worker`` is
400, and that worker at the cap is 409 at the limit.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Job, UnrealMeasurement, Worker
from workers.services import machine_tests

from .machine_test_helpers import KIND_AMD, current_of, kinds_worker, pair_record, render_job
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, pilots, ready_worker

ALREADY_MEASURED = 'This target is already measured; send "remeasure": true to measure it again.'
ALREADY_PENDING = "A pilot render is already queued or running."
NO_MACHINE = "No machine that is on can run this memory test now."
AT_LIMIT = ("Every machine that needs this memory test already has 20 queued memory tests; "
            "retry when some of them have run.")
NOT_ELIGIBLE = "This machine cannot run this memory test now."
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
                pair_record(other, f'{worker.pk:016x}{n:048x}', worker, pilot='QUEUED')
        before = _pilot_pks(job)
        resp = _retry(admin_client, job)
        assert (resp.status_code, resp.data) == (409, {'error': AT_LIMIT})
        assert _pilot_pks(job) == before


@pytest.mark.django_db
class TestOneWorker:
    """FR-MT-P-4: ``worker`` narrows the retry to that worker's eligible card kinds."""

    def test_only_that_workers_kinds(self, admin_client, setup):
        job, worker_a, worker_b = setup
        kinds_worker(worker_a, KIND, KIND_AMD)
        resp = _retry(admin_client, job, worker=worker_a.pk)
        assert resp.status_code == 201, resp.data
        assert sorted((p.target_worker_id, p.render_settings['unreal.card_kind']) for p in pilots(job.asset)) == sorted(
            [(worker_a.pk, KIND), (worker_a.pk, KIND_AMD)])
        assert Job.objects.get(pk=resp.data['pilot_job_id']).target_worker_id == worker_a.pk

    def test_remeasure_only_that_worker(self, admin_client, setup):
        job, worker_a, worker_b = setup
        pair_record(job.asset, job.unreal_target_sha256, worker_a, **STATES['fits'])
        fits_b = pair_record(job.asset, job.unreal_target_sha256, worker_b, **STATES['fits'])
        resp = _retry(admin_client, job, worker=worker_a.pk)
        assert (resp.status_code, resp.data) == (409, {'error': ALREADY_MEASURED})
        resp = _retry(admin_client, job, worker=worker_a.pk, remeasure=True)
        assert resp.status_code == 201, resp.data
        assert _new_pilots(job, set()) == {worker_a.pk: True}
        assert current_of(job.asset, job.unreal_target_sha256, worker_b) == [fits_b.pk]

    @pytest.mark.parametrize('who', ['offline', 'override', 'unknown'])
    def test_a_worker_that_cannot_test_now_is_400(self, admin_client, setup, who):
        job, _worker_a, worker_b = setup
        if who == 'offline':
            Worker.objects.filter(pk=worker_b.pk).update(
                last_seen=timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS + 60))
        elif who == 'override':
            ready_worker(worker_b, source='override')
        resp = _retry(admin_client, job, worker=worker_b.pk if who != 'unknown' else 99999)
        assert (resp.status_code, resp.data) == (400, {'worker': [NOT_ELIGIBLE]})
        assert pilots(job.asset) == []

    @pytest.mark.parametrize('value', [0, -1, 'one', True, 1.5])
    def test_a_malformed_worker_is_400(self, admin_client, setup, value):
        job, _worker_a, _worker_b = setup
        resp = _retry(admin_client, job, worker=value)
        assert resp.status_code == 400 and 'worker' in resp.data, resp.data
        assert pilots(job.asset) == []

    def test_that_worker_at_the_cap(self, admin_client, setup):
        job, worker_a, worker_b = setup
        other = unreal_asset(job.asset.project, name='MtRetryOneCap')
        for n in range(machine_tests.MAX_OPEN_PILOTS):
            pair_record(other, f'{n:064x}', worker_a, pilot='QUEUED')
        resp = _retry(admin_client, job, worker=worker_a.pk)
        assert (resp.status_code, resp.data) == (409, {'error': AT_LIMIT})
        assert pilots(job.asset) == []
        assert _retry(admin_client, job, worker=worker_b.pk).status_code == 201
