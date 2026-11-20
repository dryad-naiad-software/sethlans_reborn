# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/sequence-submissions/`` (Unreal sequence
spec FR-SQ-2..9, FR-SD-8, FR-SD-9, FR-SP-8, FR-SL-5, D5b, D19, AC-6, AC-7,
AC-8, AC-33, AC-38).

Covers: versions per project and sequence; the project's sound remembered
and used as the default; a submission waits (DISCOVERING) for its shots
and is planned inside the ``shots-result/`` transaction; facts that fail
at planning set it ERROR; a failed discovery fails it; an error planning
one submission sets only that one ERROR (its own savepoint) and keeps the
discovery and the others; the request and facts rules (400 naming the
field); a canceled waiting submission is skipped; worker tokens get 403.
"""

from unittest import mock

import pytest

from workers.models import Job, Project, SequenceDiscovery, SequenceSubmission
from workers.services.sequence_submit import PLANNING_FAILED

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, claim, ready_worker
from .sequence_helpers import shots_job_of
from .submission_helpers import (
    SUBMISSIONS, VIDEO, done_discovery, ffmpeg_ready, finish_discovery, pieces_of, shots_of, small_shots_body,
    submission_body, submit,
)

D5B_TEXT = "The preset renders at 25/1 fps but the sequence runs at 30/1 fps"


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


def _facts(**facts):
    body = small_shots_body()
    body['preset_facts'].update(facts)
    return body


@pytest.mark.django_db
class TestVersions:

    def test_versions_count_up_per_sequence(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        first, _ = submit(admin_client, asset)
        second, _ = submit(admin_client, asset, sound=False)
        assert (first.version, second.version) == (1, 2)
        assert second.name == 'Ep01 v2'
        assert SequenceSubmission.objects.get(pk=first.pk).status == 'QUEUED'

    def test_sound_remembered_and_used_as_default(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submit(admin_client, asset, sound=False)
        assert Project.objects.get(pk=asset.project_id).unreal_sound is False
        later, resp = submit(admin_client, asset)
        assert later.sound is False and resp.data['sound'] is False
        submit(admin_client, asset, sound=True)
        assert Project.objects.get(pk=asset.project_id).unreal_sound is True

    def test_settings_copied_at_create(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        Project.objects.filter(pk=asset.project_id).update(unreal_piece_seconds=12, unreal_lead_in_ms=500)
        submission, _ = submit(admin_client, asset)
        assert (submission.piece_seconds, submission.lead_in_ms) == (12, 500)


@pytest.mark.django_db
class TestWaitingForShots:

    def test_waits_then_planned_in_the_result_transaction(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, resp = submit(admin_client, asset)
        assert resp.data['status'] == 'DISCOVERING'
        assert pieces_of(submission) == [] and shots_of(submission) == []
        assert finish_discovery(worker, client, submission.discovery).status_code == 200
        submission.refresh_from_db()
        assert submission.status == 'QUEUED'
        assert len(shots_of(submission)) == 3 and len(pieces_of(submission)) == 2
        assert Job.objects.filter(asset=asset, unreal_kind='pilot').count() == 1

    @pytest.mark.parametrize('overrides, facts, message', [
        ({'frame_format': 'EXR'}, {}, "The preset has no EXR output node; add one to render EXR."),
        ({'sound': True}, {'can_sound': False}, "The preset has no audio output; add one to render sound."),
        ({'video_settings': VIDEO}, {'resolution': [65, 36]},
         "Video needs an even width and height; the output is 65x36."),
    ], ids=['exr', 'sound', 'odd-size'])
    def test_facts_failing_at_planning_set_error(self, admin_client, asset, worker_with_token, overrides, facts,
                                                 message):
        worker, client = worker_with_token
        with ffmpeg_ready():
            submission, _ = submit(admin_client, asset, **overrides)
        assert finish_discovery(worker, client, submission.discovery, _facts(**facts)).status_code == 200
        submission.refresh_from_db()
        assert (submission.status, submission.error_message) == ('ERROR', message)
        assert pieces_of(submission) == []

    def test_failed_discovery_fails_waiting_submissions(
            self, admin_client, asset, worker_with_token, django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        submission, _ = submit(admin_client, asset)
        ready_worker(worker, source='override')
        job = shots_job_of(submission.discovery)
        assert claim(client, job, worker).status_code == 200
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.patch(f'{JOBS}{job.pk}/', {'status': 'ERROR', 'error_message': 'Prep timed out.'},
                                format='json')
        assert resp.status_code == 200, resp.data
        submission.refresh_from_db()
        assert (submission.status, submission.error_message) == ('ERROR', "Reading the shots failed: Prep timed out.")
        assert submission.completed_at is not None

    def test_one_failing_submission_does_not_undo_the_discovery(self, admin_client, asset, worker_with_token):
        """Review round: each waiting submission is planned in its own savepoint."""
        from workers.services import sequence_create
        worker, client = worker_with_token
        failing, _ = submit(admin_client, asset)
        other, _ = submit(admin_client, asset)
        real = sequence_create._piece_jobs
        calls = []

        def _piece_jobs(submission, *args):
            calls.append(submission.pk)
            if submission.pk == failing.pk:
                raise RuntimeError('boom')  # after its shot rows were inserted
            return real(submission, *args)

        with mock.patch.object(sequence_create, '_piece_jobs', side_effect=_piece_jobs):
            resp = finish_discovery(worker, client, failing.discovery)
        assert resp.status_code == 200, resp.data
        assert calls == [failing.pk, other.pk]
        assert SequenceDiscovery.objects.get(pk=failing.discovery_id).status == 'DONE'
        failing.refresh_from_db()
        other.refresh_from_db()
        assert (failing.status, failing.error_message) == ('ERROR', PLANNING_FAILED)
        assert shots_of(failing) == [] and pieces_of(failing) == []
        assert other.status == 'QUEUED' and len(pieces_of(other)) == 2

    def test_canceled_waiting_submission_is_skipped(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, _ = submit(admin_client, asset)
        other, _ = submit(admin_client, asset)
        SequenceSubmission.objects.filter(pk=submission.pk).update(status='CANCELED')
        assert finish_discovery(worker, client, submission.discovery).status_code == 200
        submission.refresh_from_db()
        other.refresh_from_db()
        assert submission.status == 'CANCELED' and pieces_of(submission) == [] and shots_of(submission) == []
        assert other.status == 'QUEUED' and len(pieces_of(other)) == 2


def _refused(admin_client, asset, field, text, **overrides):
    _, resp = submit(admin_client, asset, expect=400, **overrides)
    assert field in resp.data, resp.data
    assert text in str(resp.data[field])
    return resp


@pytest.mark.django_db
class TestRules:

    def test_read_the_shots_first(self, admin_client, asset):
        _refused(admin_client, asset, 'shots', "Read the shots first", shots=['sh010'])
        assert SequenceSubmission.objects.count() == 0
        # The rollback also removed the discovery the request created.
        assert SequenceDiscovery.objects.count() == 0

    @pytest.fixture
    def done(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        return done_discovery(admin_client, asset, worker, client)

    def test_unknown_shot_names(self, admin_client, asset, done):
        _refused(admin_client, asset, 'shots', "Unknown shots: sh999.", shots=['sh010', 'sh999'])

    def test_chosen_shots_planned(self, admin_client, asset, done):
        submission, _ = submit(admin_client, asset, shots=['sh020'])
        assert [(s.index, s.name) for s in shots_of(submission)] == [(2, 'sh020')]

    def test_frame_range_outside(self, admin_client, asset, done):
        _refused(admin_client, asset, 'frame_start', "outside the sequence (0-23)", frame_start=5, frame_end=24)

    def test_shots_and_range_together(self, admin_client, asset, done):
        _refused(admin_client, asset, 'shots', "not both", shots=['sh010'], frame_start=0, frame_end=3)

    @pytest.mark.parametrize('draft, text', [
        ({'temporal_samples': 9}, "draft.temporal_samples"),
        ({'screen_percentage': 150}, "draft.screen_percentage"),
        ({'motion_blur': True}, "draft.motion_blur"),
        ({'lens_flare': False}, "lens_flare"),
    ], ids=['samples-above-preset', 'screen-150', 'motion-blur-true', 'unknown-key'])
    def test_draft_refused_naming_the_key(self, admin_client, asset, done, draft, text):
        _refused(admin_client, asset, 'draft', text, draft=draft)

    def test_draft_within_bounds_accepted(self, admin_client, asset, done):
        submission, _ = submit(admin_client, asset, draft={'temporal_samples': 2, 'motion_blur': False})
        assert pieces_of(submission)[0].render_settings['unreal.draft'] == {'temporal_samples': 2, 'motion_blur': False}

    def test_exr_with_video(self, admin_client, asset, done):
        with ffmpeg_ready():
            _refused(admin_client, asset, 'video_settings', "Video output is not available for EXR format",
                     frame_format='EXR', video_settings=VIDEO)

    def test_exr_without_exr_output(self, admin_client, asset, done):
        _refused(admin_client, asset, 'frame_format', "no EXR output node", frame_format='EXR')

    def test_sound_without_audio_output(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client, _facts(can_sound=False))
        _refused(admin_client, asset, 'sound', "no audio output", sound=True)

    def test_custom_rate_unlike_the_sequence(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client, _facts(custom_frame_rate=[25, 1]))
        _refused(admin_client, asset, 'preset', D5B_TEXT)

    def test_equal_custom_rate_accepted(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client, _facts(custom_frame_rate=[60, 2]))
        submission, _ = submit(admin_client, asset)
        assert submission.status == 'QUEUED'

    def test_framerate_in_video_settings_refused(self, admin_client, asset, done):
        with ffmpeg_ready():
            _refused(admin_client, asset, 'video_settings', "Do not send framerate",
                     video_settings={'preset': 'web_h264', 'framerate': 30})

    def test_merge_without_video(self, admin_client, asset, done):
        _refused(admin_client, asset, 'merge_sound', "needs sound and video_settings", sound=True, merge_sound=True)

    def test_unknown_body_key(self, admin_client, asset, done):
        resp = admin_client.post(SUBMISSIONS, submission_body(asset, colour='red'), format='json')
        assert (resp.status_code, resp.data) == (400, {'detail': 'Unknown keys: colour.'})


@pytest.mark.django_db
class TestAdminOnly:

    def test_worker_token_403(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        assert client.get(SUBMISSIONS).status_code == 403
        assert client.get(f'{SUBMISSIONS}{submission.pk}/').status_code == 403
        assert client.post(SUBMISSIONS, submission_body(asset), format='json').status_code == 403
        assert SequenceSubmission.objects.count() == 1

    def test_no_patch(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        assert admin_client.patch(f'{SUBMISSIONS}{submission.pk}/', {'name': 'x'}, format='json').status_code == 405
