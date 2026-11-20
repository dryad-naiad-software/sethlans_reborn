# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``shots`` jobs and ``POST /api/jobs/{id}/shots-result/``
(Unreal sequence spec D3, D12, FR-SD-3, FR-SD-8, AC-1, AC-2 manager side,
AC-13, AC-33, AC-41).

Covers: the poll and claim offer a ``shots`` job to a worker with no card
reading and no measurement; the discovery mirrors RUNNING on claim; the
strict body (unknown keys, bounds, refused names, capped warnings); the
check order (400 before 404 before 409); the result stored in one
transaction with the job DONE; a failure injected after the discovery
write rolls everything back; DONE by PATCH and requeue refused; the
routing note; worker-only access.
"""

import json
from unittest import mock

import pytest

from workers.models import Job, SequenceDiscovery

from ..unreal_helpers import SHARE, set_share, unreal_asset
from ..unreal_pilot_helpers import JOBS, claim, ready_worker
from tests._sequence_bodies import shots_body, with_facts
from .sequence_helpers import claimed_shots_job, poll_ids, post_shots, shots_job_of, start_discovery


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.mark.django_db
class TestOffer:

    def test_offered_without_card_reading_or_measurement(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, _ = start_discovery(admin_client, asset)
        ready_worker(worker, source='override')
        job = shots_job_of(discovery)
        assert poll_ids(client) == [job.pk]
        resp = claim(client, job, worker)
        assert resp.status_code == 200, resp.data
        assert resp.data['unreal_kind'] == 'shots'
        discovery.refresh_from_db()
        assert discovery.status == 'RUNNING'

    def test_not_offered_without_the_build_or_share(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, _ = start_discovery(admin_client, asset)
        ready_worker(worker)
        worker.unreal_capabilities = dict(worker.unreal_capabilities, share_reachable=False)
        worker.save(update_fields=['unreal_capabilities'])
        assert poll_ids(client) == []
        assert claim(client, shots_job_of(discovery), worker).status_code == 409

    def test_routing_note_never_names_memory(self, admin_client, asset, worker_with_token):
        worker, _client = worker_with_token
        discovery, _ = start_discovery(admin_client, asset)
        job = shots_job_of(discovery)
        set_share(SHARE)
        assert 'No online worker reports' in admin_client.get(f'{JOBS}{job.pk}/').data['routing_note']
        # A 1 GB card would fail any memory need; a shots job has none.
        ready_worker(worker, total_mb=1024)
        assert admin_client.get(f'{JOBS}{job.pk}/').data['routing_note'] is None

    def test_release_mirrors_queued(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        resp = client.post(f'{JOBS}{job.pk}/release/', {'reason': 'shutdown'}, format='json')
        assert resp.status_code == 200, resp.data
        discovery.refresh_from_db()
        assert discovery.status == 'QUEUED'


@pytest.mark.django_db
class TestResult:

    def test_stores_the_result_and_finishes_the_job(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        body = with_facts(custom_frame_rate=[30, 1])
        body['warnings'] = ['The preset renders every 2nd frame.']
        resp = post_shots(client, job.pk, body)
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'discovery': discovery.pk, 'shots': 3})
        data = admin_client.get(f'/api/sequence-discoveries/{discovery.pk}/').data
        assert data['status'] == 'DONE'
        assert (data['playback_start'], data['playback_end'], data['frame_rate_num'], data['frame_rate_den']) == (
            0, 360, 30, 1)
        assert data['shot_source'] == 'shot_tracks'
        assert data['shots'][1] == {'index': 2, 'name': 'sh020_walk', 'start': 120, 'end': 300}
        assert data['preset_facts']['preset_kind'] == 'legacy'
        assert data['preset_facts']['custom_frame_rate'] == [30, 1]
        assert data['warnings'] == ['The preset renders every 2nd frame.']
        job.refresh_from_db()
        assert (job.status, job.completed_at is not None) == ('DONE', True)

    def test_names_cleaned_and_deduplicated_again(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        body = shots_body(shots=[['Intro shot', 0, 10], ['intro_shot', 10, 20], ['x/../y', 20, 30]])
        assert post_shots(client, job.pk, body).status_code == 200
        discovery.refresh_from_db()
        assert [s['name'] for s in discovery.shots] == ['Intro_shot', 'intro_shot-2', 'x_.._y']

    def test_repeat_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        assert post_shots(client, job.pk, shots_body()).status_code == 200
        resp = post_shots(client, job.pk, shots_body())
        assert (resp.status_code, resp.data) == (409, {'error': 'Job is not rendering on this worker.'})

    def test_other_worker_is_409(self, admin_client, asset, worker_with_token, make_worker_client):
        worker, client = worker_with_token
        _discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        _other, other_client, _token = make_worker_client('otherhost')
        resp = post_shots(other_client, job.pk, shots_body())
        assert resp.status_code == 409
        assert SequenceDiscovery.objects.get(job=job).status == 'RUNNING'

    def test_queued_job_is_409(self, admin_client, asset, worker_with_token):
        _worker, client = worker_with_token
        discovery, _ = start_discovery(admin_client, asset)
        assert post_shots(client, discovery.job_id, shots_body()).status_code == 409

    def test_render_job_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        Job.objects.filter(pk=job.pk).update(unreal_kind='render')
        resp = post_shots(client, job.pk, shots_body())
        assert (resp.status_code, resp.data) == (409, {'error': 'Job is not a shots job.'})

    def test_settled_discovery_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        SequenceDiscovery.objects.filter(pk=discovery.pk).update(status='ERROR')
        resp = post_shots(client, job.pk, shots_body())
        assert resp.status_code == 409 and 'no longer waiting' in resp.data['error']
        job.refresh_from_db()
        assert job.status == 'RENDERING'

    def test_unknown_job_is_404_after_the_body_check(self, worker_with_token):
        _worker, client = worker_with_token
        assert post_shots(client, 999999, shots_body()).status_code == 404
        assert post_shots(client, 999999, shots_body(frame_rate=[0, 1])).status_code == 400

    def test_admin_token_refused(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        assert post_shots(admin_client, job.pk, shots_body()).status_code == 403


@pytest.mark.django_db
class TestStrictBody:

    @pytest.fixture
    def rendering(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        return discovery, job, client

    def _refused(self, rendering, body):
        discovery, job, client = rendering
        resp = post_shots(client, job.pk, body)
        assert resp.status_code == 400, resp.data
        discovery.refresh_from_db()
        job.refresh_from_db()
        assert (discovery.status, discovery.shots, job.status) == ('RUNNING', [], 'RENDERING')
        return resp.data

    def test_unknown_keys_at_both_levels(self, rendering):
        data = self._refused(rendering, with_facts(extra=1) | {'note': 'x'})
        assert data == {'detail': 'Unknown keys: note, preset_facts.extra.'}

    @pytest.mark.parametrize('name', ['..', '.', '-x', '.env'])
    def test_refused_shot_name(self, rendering, name):
        data = self._refused(rendering, shots_body(shots=[['ok', 0, 10], [name, 10, 20]]))
        assert 'shots[1].name' in data

    def test_more_than_64_warnings(self, rendering):
        assert 'warnings' in self._refused(rendering, shots_body(warnings=['w'] * 65))

    def test_warning_over_512_characters(self, rendering):
        assert 'warnings[0]' in self._refused(rendering, shots_body(warnings=['w' * 513]))

    @pytest.mark.parametrize('override, field', [
        ({'frame_rate': ['30', 1]}, 'frame_rate'),
        ({'playback_range': [-1, 360]}, 'playback_range'),
        ({'shots': [['a', 0, 400]]}, 'shots[0]'),
        ({'shots': []}, 'shots'),
    ])
    def test_bounds(self, rendering, override, field):
        assert field in self._refused(rendering, shots_body(**override))

    def test_not_an_object(self, rendering):
        _discovery, job, client = rendering
        resp = client.post(f'{JOBS}{job.pk}/shots-result/', [1, 2], format='json')
        assert resp.status_code == 400


@pytest.mark.django_db
class TestOneTransaction:

    def test_failure_after_the_discovery_write_rolls_back(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        client.raise_request_exception = False
        with mock.patch('workers.services.sequence_discovery.plan_waiting_submissions',
                        side_effect=RuntimeError('planning failed')):
            resp = post_shots(client, job.pk, shots_body())
        assert resp.status_code == 500
        discovery.refresh_from_db()
        job.refresh_from_db()
        assert (discovery.status, discovery.shots, discovery.playback_end) == ('RUNNING', [], None)
        assert job.status == 'RENDERING'

    def test_planning_hook_sees_the_stored_result(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        seen = []

        def _hook(record):
            stored = SequenceDiscovery.objects.values_list('status', flat=True).get(pk=record.pk)
            seen.append((stored, record.status, [s['name'] for s in record.shots], record.playback_end))

        with mock.patch('workers.services.sequence_discovery.plan_waiting_submissions', side_effect=_hook):
            assert post_shots(client, job.pk, shots_body()).status_code == 200
        # The hook gets the record with the stored result on it, not a stale copy.
        assert seen == [('DONE', 'DONE', ['sh010_open', 'sh020_walk', 'sh030_door'], 360)]

    def test_control_characters_stripped_from_warnings(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        body = shots_body(warnings=['a\u0000b\tc\u001b[31m\u007fd\u0085e', 'lone \ud800 surrogate'])
        # ASCII JSON with \u escapes, as the worker sends it (DRF's test renderer cannot encode a lone surrogate).
        resp = client.post(f'{JOBS}{job.pk}/shots-result/', json.dumps(body), content_type='application/json')
        assert resp.status_code == 200, resp.data
        discovery.refresh_from_db()
        assert discovery.warnings == ['abc[31mde', 'lone  surrogate']


@pytest.mark.django_db
class TestManagerOwnedStatus:

    def test_worker_patch_done_is_400(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        resp = client.patch(f'{JOBS}{job.pk}/', {'status': 'DONE'}, format='json')
        assert (resp.status_code, resp.data) == (
            400, {'detail': f"Shots jobs complete through POST /api/jobs/{job.pk}/shots-result/."})

    def test_admin_patch_done_is_400(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        assert admin_client.patch(f'{JOBS}{job.pk}/', {'status': 'DONE'}, format='json').status_code == 400
        job.refresh_from_db()
        assert job.status == 'RENDERING'

    def test_requeue_refused(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        Job.objects.filter(pk=discovery.job_id).update(status='ERROR')
        resp = admin_client.post(f'{JOBS}{discovery.job_id}/requeue/')
        assert (resp.status_code, resp.data) == (
            409, {'error': "Read the shots again with POST /api/sequence-discoveries/."})

    def test_target_fields_fixed(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        job = shots_job_of(discovery)
        settings = dict(job.render_settings, **{'unreal.sequence': '/Game/Other/Seq.Seq'})
        resp = admin_client.patch(f'{JOBS}{job.pk}/', {'render_settings': settings}, format='json')
        assert resp.status_code == 400
        assert resp.data['render_settings'] == ["A shots job's render_settings cannot be changed."]
