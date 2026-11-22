# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the claim under per-machine results (Unreal per-machine
memory test D2, D4, D14, FR-MT-T-2).

Covers: the target check comes first, for every renderer (another
worker's pilot, render job or Blender row with a target is refused with
"for another machine" before any other rule; a pilot without a target is
refused with "has no machine"); then the caller's own pair states give
the message, with the interim precedence fits > retest_due > testing /
not tried > failed > does_not_fit across its kinds; a worker without a
live card reading or without a ticked kind gets its own refusal.
"""

import pytest

from workers.models import Job

from .machine_test_helpers import (
    DOES_NOT_FIT, KIND_AMD, NO_CARD_READING, NO_KIND, NO_MACHINE, NOT_TESTED, OTHER_MACHINE, RETEST,
    kind_card, kinds_worker, pair_record, pilot_job, render_job, set_cards,
)
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, claim, created_job, pilots, ready_worker

# A pair's state as ``pair_record`` keywords; ``None``: no record (not tried).
STATES = {
    'fits': {'verdict': 'fits'},
    'does_not_fit': {'verdict': 'does_not_fit'},
    'testing': {'pilot': 'QUEUED'},
    'rendering': {'pilot': 'RENDERING'},
    'failed': {'pilot': 'ERROR', 'error': 'boom'},
    'retest_due': {'verdict': 'fits', 'stale': 'driver_changed'},
    'retest_due_dnf': {'verdict': 'does_not_fit', 'stale': 'driver_changed'},
    'superseded_fits': {'verdict': 'fits', 'current': False},
    'not_tried': None,
}


def _failed(project_id):
    return (f"This project's memory measurement failed: boom. An admin can retry it "
            f"(POST /api/projects/{project_id}/unreal-measurement/retry/).")


def _refusal(resp):
    assert resp.status_code == 409, resp.data
    return resp.data['error']


@pytest.fixture
def uasset(db):
    return unreal_asset()


@pytest.fixture
def two(worker_with_token, make_worker_client):
    worker_a, client_a = worker_with_token
    worker_b, client_b, _token = make_worker_client('mt-claim-b')
    ready_worker(worker_a)
    ready_worker(worker_b)
    return worker_a, client_a, worker_b, client_b


@pytest.mark.django_db
class TestTargetFirst:

    def test_another_workers_pilot(self, admin_client, uasset, two):
        worker_a, client_a, worker_b, client_b = two
        created_job(admin_client, uasset)
        pilot_a = next(p for p in pilots(uasset) if p.target_worker_id == worker_a.pk)
        assert _refusal(claim(client_b, pilot_a, worker_b)) == OTHER_MACHINE
        assert claim(client_a, pilot_a, worker_a).status_code == 200
        # Still first while it renders: not "Job is not available for claiming."
        assert _refusal(claim(client_b, pilot_a, worker_b)) == OTHER_MACHINE

    def test_before_the_build_paused_and_card_checks(self, uasset, two):
        worker_a, _client_a, worker_b, client_b = two
        pilot = pilot_job(uasset, worker_a)
        Job.objects.filter(pk=pilot.pk).update(is_paused=True)
        worker_b.unreal_capabilities = {}
        worker_b.gpu_memory = {}
        worker_b.save(update_fields=['unreal_capabilities', 'gpu_memory'])
        assert _refusal(claim(client_b, pilot, worker_b)) == OTHER_MACHINE

    def test_an_unreal_render_job_with_a_target(self, uasset, two):
        worker_a, client_a, worker_b, client_b = two
        job = render_job(uasset)
        pair_record(uasset, job.unreal_target_sha256, worker_a, verdict='fits')
        Job.objects.filter(pk=job.pk).update(target_worker=worker_b)
        assert _refusal(claim(client_a, job, worker_a)) == OTHER_MACHINE

    def test_a_blender_row_with_a_target(self, asset, worker_with_token, make_worker_client):
        worker_a, client_a = worker_with_token
        worker_b, client_b, _token = make_worker_client('mt-claim-blender')
        job = Job.objects.create(name='MtBlender', asset=asset, output_file_pattern='//r/f_####')
        Job.objects.filter(pk=job.pk).update(target_worker=worker_b)
        assert _refusal(claim(client_a, job, worker_a)) == OTHER_MACHINE
        # The target itself passes the check; Blender's own rule (no Blender version) then refuses.
        assert _refusal(claim(client_b, job, worker_b)) != OTHER_MACHINE
        Job.objects.filter(pk=job.pk).update(target_worker=worker_a)
        resp = claim(client_a, job, worker_a)
        assert resp.status_code == 200, resp.data

    def test_a_blender_row_without_a_target_is_not_refused(self, asset, worker_with_token):
        worker, client = worker_with_token
        job = Job.objects.create(name='MtBlenderFree', asset=asset, output_file_pattern='//r/f_####')
        assert claim(client, job, worker).status_code == 200

    def test_a_pilot_without_a_target(self, admin_client, uasset, two):
        worker_a, client_a, worker_b, client_b = two
        created_job(admin_client, uasset)
        Job.objects.filter(unreal_kind='pilot').update(target_worker=None)
        pilot = pilots(uasset)[0]
        assert _refusal(claim(client_a, pilot, worker_a)) == NO_MACHINE
        ready_worker(worker_b, source='override')
        assert _refusal(claim(client_b, pilot, worker_b)) == NO_MACHINE


@pytest.mark.django_db
class TestPairMessages:

    @pytest.mark.parametrize('state, expected', [
        ('fits', None), ('testing', NOT_TESTED), ('rendering', NOT_TESTED), ('not_tried', NOT_TESTED),
        ('superseded_fits', NOT_TESTED), ('retest_due', RETEST), ('retest_due_dnf', RETEST),
        ('failed', 'failed'), ('does_not_fit', DOES_NOT_FIT),
    ])
    def test_one_kind(self, uasset, worker_with_token, state, expected):
        worker, client = worker_with_token
        ready_worker(worker)
        job = render_job(uasset)
        if STATES[state] is not None:
            pair_record(uasset, job.unreal_target_sha256, worker, **STATES[state])
        resp = claim(client, job, worker)
        if expected is None:
            assert resp.status_code == 200, resp.data
            return
        assert _refusal(resp) == (_failed(uasset.project_id) if expected == 'failed' else expected)

    @pytest.mark.parametrize('first, second, expected', [
        ('fits', 'does_not_fit', None), ('does_not_fit', 'fits', None), ('failed', 'fits', None),
        ('retest_due', 'testing', RETEST), ('does_not_fit', 'retest_due', RETEST),
        ('testing', 'failed', NOT_TESTED), ('not_tried', 'failed', NOT_TESTED),
        ('does_not_fit', 'not_tried', NOT_TESTED), ('failed', 'does_not_fit', 'failed'),
        ('does_not_fit', 'does_not_fit', DOES_NOT_FIT),
    ])
    def test_two_kinds_precedence(self, uasset, worker_with_token, first, second, expected):
        worker, client = worker_with_token
        kinds_worker(worker, KIND, KIND_AMD)
        job = render_job(uasset)
        for kind, state in ((KIND, first), (KIND_AMD, second)):
            if STATES[state] is not None:
                pair_record(uasset, job.unreal_target_sha256, worker, kind, **STATES[state])
        resp = claim(client, job, worker)
        if expected is None:
            assert resp.status_code == 200, resp.data
            return
        assert _refusal(resp) == (_failed(uasset.project_id) if expected == 'failed' else expected)

    def test_another_workers_fits_record_does_not_count(self, uasset, two):
        worker_a, _client_a, worker_b, client_b = two
        job = render_job(uasset)
        pair_record(uasset, job.unreal_target_sha256, worker_a, verdict='fits')
        assert _refusal(claim(client_b, job, worker_b)) == NOT_TESTED

    def test_no_live_card_reading(self, uasset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker, source='override')
        job = render_job(uasset)
        pair_record(uasset, job.unreal_target_sha256, worker, verdict='fits')
        assert _refusal(claim(client, job, worker)) == NO_CARD_READING

    def test_no_ticked_kind(self, uasset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        set_cards(worker, [kind_card(KIND, 0, ticked=False, state='unticked'),
                           kind_card(KIND_AMD, 1, vendor_id=None)])
        job = render_job(uasset)
        pair_record(uasset, job.unreal_target_sha256, worker, verdict='fits')
        assert _refusal(claim(client, job, worker)) == NO_KIND

    def test_an_untested_pilot_of_a_live_worker_claims(self, admin_client, uasset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        created_job(admin_client, uasset)
        resp = claim(client, pilots(uasset)[0], worker)
        assert resp.status_code == 200, resp.data
