# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the chunk 3 review round (Unreal sequence spec D8,
OQ-2 (b), FR-SS-8, AC-36, FR-SL-5, D18, D22).

Covers:
- the key's shot set follows what the pilot measures: the same chosen shots
  with and without sound, or with another lead-in, get different keys;
- the Django admin keeps a piece's target, status, submission and lead-in
  read-only;
- ``frames/`` refuses a job that is not the caller's piece before reading
  the body;
- an admin gets 403 on ``piece-result/``; the project settings' bounds;
- project cancel-all refreshes the submissions it touched (AC-36);
- a progress PATCH does not refresh the submission; a status PATCH does.
"""

from unittest import mock

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory

from workers.models import Job, Project, SequenceFrame
from workers.services.sequence_frames import NOT_RENDERING_HERE

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, claim, pilots, ready_worker
from .submission_helpers import (
    claimed_piece, done_discovery, piece_body, pieces_of, post_piece, submit, upload_all, upload_frame,
)

PROJECTS = '/api/projects/'


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


def _set_lead(asset, ms):
    Project.objects.filter(pk=asset.project_id).update(unreal_lead_in_ms=ms)


@pytest.mark.django_db
class TestKeyFollowsTheMeasuredShots:
    """Shot sh020 [4, 10) chosen; with lead-in 0 only the audio lead-in (8 frames) reaches sh010 [0, 4)."""

    def _key(self, admin_client, asset, **overrides):
        submission, _ = submit(admin_client, asset, shots=['sh020'], **overrides)
        return submission, pieces_of(submission)[0].unreal_target_sha256

    def test_sound_changes_the_key_and_the_pilot(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        _set_lead(asset, 0)
        silent, silent_key = self._key(admin_client, asset, sound=False)
        loud, loud_key = self._key(admin_client, asset, sound=True)
        assert silent_key != loud_key
        assert (silent.shot_set_sha256 != '', loud.shot_set_sha256 != silent.shot_set_sha256) == (True, True)
        silent.refresh_from_db()
        loud.refresh_from_db()
        assert (silent.pilot_frames, loud.pilot_frames) == ([4], [0, 4])
        frames = sorted(pilot.render_settings['unreal.pilot_frames'] for pilot in pilots(asset))
        assert frames == [[0, 4], [4]]

    def test_lead_in_changes_the_key(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        _set_lead(asset, 0)
        _, without = self._key(admin_client, asset, sound=False)
        _set_lead(asset, 1000)
        with_lead, key = self._key(admin_client, asset, sound=False)
        assert pieces_of(with_lead)[0].unreal_lead_in_frames == 4
        assert key != without

    def test_same_measured_shots_share_the_key(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        _, first = self._key(admin_client, asset, sound=True)
        _, second = self._key(admin_client, asset, sound=True)
        assert first == second
        assert len(pilots(asset)) == 1


@pytest.mark.django_db
class TestAdminReadOnly:

    def test_piece_fields_read_only(self, admin_client, admin_user, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        piece = pieces_of(submission)[0]
        request = RequestFactory().get('/admin/')
        request.user = admin_user
        fields = set(site._registry[Job].get_readonly_fields(request, piece))
        assert {'render_settings', 'start_frame', 'end_frame', 'status', 'asset',
                'sequence_submission', 'unreal_lead_in_frames'} <= fields

    def test_standalone_job_status_stays_editable(self, admin_user, asset):
        job = Job.objects.create(name='Still01', asset=asset, output_file_pattern='//r/s.png',
                                 render_settings={'unreal.map': '/Game/M.M', 'unreal.sequence': '/Game/S.S',
                                                  'unreal.preset': '/Game/P.P'})
        request = RequestFactory().get('/admin/')
        request.user = admin_user
        fields = set(site._registry[Job].get_readonly_fields(request, job))
        assert 'status' not in fields and 'render_settings' not in fields
        assert {'sequence_submission', 'unreal_lead_in_frames'} <= fields


@pytest.mark.django_db
class TestFramesOwnershipFirst:

    def test_other_worker_refused_before_the_body_is_read(
            self, admin_client, asset, worker_with_token, make_worker_client):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client)
        _other, other_client, _ = make_worker_client('other-node')
        with mock.patch('rest_framework.parsers.MultiPartParser.parse',
                        side_effect=AssertionError('body read')) as parse:
            resp = upload_frame(other_client, piece, piece.start_frame)
        assert (resp.status_code, resp.data) == (409, {'error': NOT_RENDERING_HERE})
        parse.assert_not_called()

    def test_owner_still_checked_under_the_lock(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client)

        def _requeue_meanwhile(*_args):
            # Between the early check and the job lock, the piece is requeued.
            Job.objects.filter(pk=piece.pk).update(status='QUEUED', assigned_worker=None)

        with mock.patch('workers.services.sequence_frames.check_frame_file', side_effect=_requeue_meanwhile):
            resp = upload_frame(client, piece, piece.start_frame)
        assert (resp.status_code, resp.data) == (409, {'error': NOT_RENDERING_HERE})
        assert not SequenceFrame.objects.exists()


@pytest.mark.django_db
class TestAccessAndBounds:

    def test_admin_gets_403_on_piece_result(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client)
        assert post_piece(admin_client, piece, piece_body(piece)).status_code == 403

    @pytest.mark.parametrize('field, value, ok', [
        ('unreal_piece_seconds', 4, False), ('unreal_piece_seconds', 5, True),
        ('unreal_piece_seconds', 600, True), ('unreal_piece_seconds', 601, False),
        ('unreal_lead_in_ms', -1, False), ('unreal_lead_in_ms', 0, True),
        ('unreal_lead_in_ms', 10000, True), ('unreal_lead_in_ms', 10001, False),
        ('unreal_sound_offsets', {'/Game/P.P@30/1': 10000}, True),
        ('unreal_sound_offsets', {'/Game/P.P@30/1': -10001}, False),
        ('unreal_sound_offsets', {'/Game/P.P': 5}, False),
        ('unreal_sound_offsets', {'/Game/P.P@30/1': True}, False),
        ('unreal_sound_offsets', {f'/Game/P{i}.P{i}@30/1': 1 for i in range(65)}, False),
        ('unreal_sound_offsets', None, True),
    ])
    def test_project_setting_bounds(self, admin_client, asset, field, value, ok):
        resp = admin_client.patch(f'{PROJECTS}{asset.project_id}/', {field: value}, format='json')
        assert resp.status_code == (200 if ok else 400), resp.data
        if ok:
            assert resp.data[field] == value
        else:
            assert field in resp.data


@pytest.mark.django_db
class TestProjectCancelAll:
    """AC-36: cancel-all refreshes the submissions whose pieces it canceled."""

    def test_partly_done_and_canceled(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, piece = claimed_piece(admin_client, asset, worker, client)
        upload_all(client, piece)
        assert post_piece(client, piece, piece_body(piece)).status_code == 200
        untouched, _ = submit(admin_client, asset)
        resp = admin_client.post(f'{PROJECTS}{asset.project_id}/cancel_all_jobs/')
        assert resp.status_code == 200, resp.data
        submission.refresh_from_db()
        untouched.refresh_from_db()
        assert submission.status == 'PARTLY_DONE' and submission.completed_at is not None
        assert untouched.status == 'CANCELED'
        assert {shot.status for shot in untouched.shots.all()} == {'CANCELED'}


@pytest.mark.django_db
class TestRefreshOnlyOnStatus:

    def test_progress_patch_does_not_refresh(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client)
        with mock.patch('workers.services.sequence_status.refresh_for_job') as refresh:
            resp = client.patch(f'{JOBS}{piece.pk}/', {'last_output': 'frame 3 of 10'}, format='json')
            assert resp.status_code == 200, resp.data
            refresh.assert_not_called()
            resp = client.patch(f'{JOBS}{piece.pk}/', {'status': 'ERROR', 'error_message': 'GPU lost'}, format='json')
            assert resp.status_code == 200, resp.data
            refresh.assert_called_once()

    def test_claim_refreshes(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, _piece = claimed_piece(admin_client, asset, worker, client)
        other = pieces_of(submission)[1]
        ready_worker(worker)
        with mock.patch('workers.services.sequence_status.refresh_for_job') as refresh:
            assert claim(client, other, worker).status_code == 200
        refresh.assert_called_once()
