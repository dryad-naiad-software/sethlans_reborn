# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: what a poll offers under per-machine results (Unreal
per-machine memory test D2, D4, D14, FR-MT-T-1).

Covers: a render job goes only to a worker with a current, not stale,
``fits`` record of the key for one of its ticked kinds (not another
worker's, not an unticked kind's, not a stale, superseded, does-not-fit
or other-CVars record); a targeted pilot goes to its worker only; a pilot
whose target is null is never offered, to a worker or to an admin; an
admin poll lists render jobs with any worker's fits record and every
targeted current pilot.
"""

import pytest

from workers.models import Job

from .machine_test_helpers import KIND_AMD, kind_card, kinds_worker, pair_record, render_job, set_cards
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import JOBS, KIND, POLL, created_job, pilots, ready_worker


def _ids(client):
    resp = client.get(JOBS, POLL)
    assert resp.status_code == 200, resp.data
    return [row['id'] for row in resp.data]


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def two(worker_with_token, make_worker_client):
    """Two eligible workers: ``(worker a, client a, worker b, client b)``, one card of ``KIND`` each."""
    worker_a, client_a = worker_with_token
    worker_b, client_b, _token = make_worker_client('mt-poll-b')
    ready_worker(worker_a)
    ready_worker(worker_b)
    return worker_a, client_a, worker_b, client_b


@pytest.mark.django_db
class TestRenderJobOffer:

    def test_only_the_worker_with_a_fits_pair(self, admin_client, asset, two):
        worker_a, client_a, _worker_b, client_b = two
        job = render_job(asset)
        assert job.pk not in _ids(client_a) and job.pk not in _ids(admin_client)
        pair_record(asset, job.unreal_target_sha256, worker_a, verdict='fits')
        assert job.pk in _ids(client_a)
        assert job.pk not in _ids(client_b)
        assert job.pk in _ids(admin_client)

    @pytest.mark.parametrize('fields', [
        {'verdict': 'does_not_fit'},
        {'verdict': 'fits', 'stale': 'driver_changed'},
        {'verdict': 'fits', 'current': False},
        {'verdict': 'fits', 'overrides': 'f' * 64},
        {'verdict': '', 'pilot': 'QUEUED'},
        {'verdict': '', 'pilot': 'ERROR'},
    ], ids=['does_not_fit', 'stale', 'not_current', 'other_cvars', 'testing', 'failed'])
    def test_no_offer_without_a_usable_fits_record(self, admin_client, asset, two, fields):
        worker_a, client_a, _worker_b, _client_b = two
        job = render_job(asset)
        pair_record(asset, job.unreal_target_sha256, worker_a, **fields)
        assert job.pk not in _ids(client_a)
        assert job.pk not in _ids(admin_client)

    def test_a_fits_record_of_a_kind_the_worker_does_not_tick(self, asset, worker_with_token):
        worker, client = worker_with_token
        kinds_worker(worker, KIND, KIND_AMD)
        job = render_job(asset)
        pair_record(asset, job.unreal_target_sha256, worker, KIND_AMD, verdict='fits')
        assert job.pk in _ids(client)
        set_cards(worker, [kind_card(KIND, 0), kind_card(KIND_AMD, 1, ticked=False, state='unticked')])
        assert job.pk not in _ids(client)
        set_cards(worker, [kind_card(KIND, 0), kind_card(KIND_AMD, 1, state='lost')])
        assert job.pk not in _ids(client)
        set_cards(worker, [kind_card(KIND, 0), kind_card(KIND_AMD, 1)])
        assert job.pk in _ids(client)

    def test_a_fits_record_of_a_kind_the_worker_never_had(self, asset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        job = render_job(asset)
        pair_record(asset, job.unreal_target_sha256, worker, KIND_AMD, verdict='fits')
        assert job.pk not in _ids(client)

    def test_override_worker_gets_no_render_job(self, asset, worker_with_token):
        """D14: without a live card reading a worker has no kinds, whatever its records."""
        worker, client = worker_with_token
        ready_worker(worker)
        job = render_job(asset)
        pair_record(asset, job.unreal_target_sha256, worker, verdict='fits')
        assert job.pk in _ids(client)
        ready_worker(worker, source='override')
        assert job.pk not in _ids(client)

    def test_each_worker_sees_jobs_of_its_own_fitting_keys(self, asset, two):
        worker_a, client_a, worker_b, client_b = two
        first = render_job(asset, name='MtFirst')
        second = render_job(asset, name='MtSecond', render_settings={
            **first.render_settings, 'unreal.preset': '/Game/Presets/Other.Other'})
        assert first.unreal_target_sha256 != second.unreal_target_sha256
        pair_record(asset, first.unreal_target_sha256, worker_a, verdict='fits')
        pair_record(asset, second.unreal_target_sha256, worker_b, verdict='fits')
        assert (set(_ids(client_a)), set(_ids(client_b))) == ({first.pk}, {second.pk})


@pytest.mark.django_db
class TestPilotOffer:

    def test_each_pilot_goes_to_its_own_worker(self, admin_client, asset, two):
        worker_a, client_a, worker_b, client_b = two
        created_job(admin_client, asset)
        by_target = {pilot.target_worker_id: pilot.pk for pilot in pilots(asset)}
        assert set(by_target) == {worker_a.pk, worker_b.pk}
        assert _ids(client_a) == [by_target[worker_a.pk]]
        assert _ids(client_b) == [by_target[worker_b.pk]]
        assert set(_ids(admin_client)) == set(by_target.values())

    def test_a_pilot_without_a_target_is_never_offered(self, admin_client, asset, two):
        worker_a, client_a, _worker_b, client_b = two
        created_job(admin_client, asset)
        Job.objects.filter(unreal_kind='pilot').update(target_worker=None)
        assert _ids(client_a) == [] and _ids(client_b) == []
        assert _ids(admin_client) == []

    def test_a_pilot_for_another_kind_of_the_same_worker_is_its_own(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        kinds_worker(worker, KIND, KIND_AMD)
        created_job(admin_client, asset)
        found = pilots(asset)
        assert sorted(p.render_settings['unreal.card_kind'] for p in found) == sorted([KIND, KIND_AMD])
        assert {p.target_worker_id for p in found} == {worker.pk}
        assert sorted(_ids(client)) == sorted(p.pk for p in found)

    def test_a_worker_that_loses_its_card_reading_gets_no_pilot(self, admin_client, asset, two):
        _worker_a, _client_a, worker_b, client_b = two
        created_job(admin_client, asset)
        assert len(_ids(client_b)) == 1
        ready_worker(worker_b, source='override')
        assert _ids(client_b) == []
