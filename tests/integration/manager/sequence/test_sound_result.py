# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for audio jobs and ``POST /api/jobs/{id}/sound-result/``
(Unreal sequence spec FR-SA-1, FR-SA-5, FR-SA-6, FR-SA-11, FR-SR-5, D3,
D20, D21, D24, AC-13, AC-30, AC-41, AC-45d, AC-47, AC-48).

Covers: planning makes one audio job per run before the pieces, with only
map, sequence and preset as settings, and none without sound; an audio job
is offered and claimed with no measurement and no card reading; a result
stores the ``.wav`` under a manager-made name, records the run with its
correction and finishes the job; a run that is not measured uses the
preset's override, else none, with the D24 warning (reason cleaned); a
measured run names disagreeing shots; a silent file is kept with a
warning; refused offsets, files, fields, owners and repeats; uploads go to
temporary files only; a PATCH to DONE is refused.
"""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import TemporaryFileUploadHandler

from workers.models import Job
from workers.services.sequence_sound_result import upload_slot
from workers.views import job_sound_actions

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, claim, ready_worker
from .sequence_helpers import MAP, PRESET, SEQUENCE, poll_ids
from .sound_helpers import (
    RATE, audio_jobs_of, claimed_audio, offset, post_sound, run_samples, shot_row, unmeasured, wav_file,
)
from .submission_helpers import done_discovery, measure_submission, pieces_of, submit


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def audio(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_audio(admin_client, asset, worker, client)


@pytest.mark.django_db
class TestPlanning:

    def test_one_audio_job_per_run_before_the_pieces(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=True)
        jobs = audio_jobs_of(submission)
        assert [(j.name, j.start_frame, j.end_frame, j.unreal_lead_in_frames) for j in jobs] == [
            (f'sethlans-seq-{submission.pk}-a1', 0, 9, 0), (f'sethlans-seq-{submission.pk}-a2', 20, 23, 0)]
        assert {j.unreal_kind for j in jobs} == {'audio'}
        base = {'unreal.map': MAP, 'unreal.sequence': SEQUENCE, 'unreal.preset': PRESET}
        assert [j.render_settings for j in jobs] == [
            dict(base, **{'unreal.piece_shots': [[1, 0, 4], [2, 4, 10]]}),
            dict(base, **{'unreal.piece_shots': [[3, 20, 24]]}),
        ]
        assert max(j.pk for j in jobs) < min(p.pk for p in pieces_of(submission))
        assert {(j.renderer, j.engine_build, j.status) for j in jobs} == {('UNREAL', submission.engine_build, 'QUEUED')}
        assert jobs[0].unreal_target_sha256 == Job.objects.get(pk=jobs[0].pk).compute_target_sha256()

    def test_audio_lead_in_reaches_back_eight_frames(self, admin_client, asset, worker_with_token):
        """Shot sh020 [4, 10) alone, lead-in 1 s: the audio job renders from frame 0 (FR-SP-5)."""
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=True, shots=['sh020'])
        [job] = audio_jobs_of(submission)
        assert (job.start_frame, job.end_frame, job.unreal_lead_in_frames) == (4, 9, 4)

    def test_no_audio_job_without_sound(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=False)
        assert audio_jobs_of(submission) == []

    def test_offered_only_with_a_fits_result_of_the_piece_key(self, admin_client, asset, worker_with_token):
        """Per-machine D10: an audio job is gated like a piece, by the submission's piece key."""
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=True)
        jobs = audio_jobs_of(submission)
        assert not {j.pk for j in jobs} & set(poll_ids(client))
        assert claim(client, jobs[0], worker).data == {'error': "This machine has not tested this render yet."}
        measure_submission(asset, worker=worker, submission=submission)
        offered = poll_ids(client)
        assert {j.pk for j in jobs} <= set(offered)
        resp = claim(client, jobs[0], worker)
        assert resp.status_code == 200, resp.data
        [result] = resp.data['unreal_machine_results']
        assert result['peak_mb'] == 8102
        ready_worker(worker, source='override')  # D14: no live card reading, no audio job
        assert not {j.pk for j in jobs} & set(poll_ids(client))


@pytest.mark.django_db
class TestResult:

    def test_measured_run_is_recorded(self, audio, worker_with_token):
        _worker, client = worker_with_token
        submission, job = audio
        resp = post_sound(client, job, file_obj=wav_file(run_samples(job) + 7 * 1600), skipped='true',
                          render_s='61.5', summary='sound ok')
        assert resp.data == {'status': 'DONE', 'applied_samples': 9418, 'applied_source': 'measured'}
        job.refresh_from_db()
        assert (job.status, job.render_time_seconds) == ('DONE', 62)
        assert job.output_file.name == f'sequences/{submission.project_id}/{submission.pk}/runs/{job.pk}.wav'
        assert job.last_output.startswith('audio_samples=27200 audio_rate=48000 audio_channels=2')
        submission.refresh_from_db()
        [run] = submission.sound_runs
        assert (run['job'], run['start'], run['end'], run['r0'], run['rate']) == (job.pk, 0, 10, 0, RATE)
        assert (run['applied_source'], run['latency_compensation_skipped'], run['warnings']) == ('measured', True, [])
        assert run['offset']['offset_samples'] == 9418
        assert submission.status == 'RENDERING'

    def test_unmeasured_with_override(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        project = asset.project
        project.unreal_sound_offsets = {f'{PRESET}@30/1': 196}
        project.save(update_fields=['unreal_sound_offsets'])
        submission, job = claimed_audio(admin_client, asset, worker, client)
        resp = post_sound(client, job, offset_value=unmeasured('low_confidence', 'shots \x00disagree'))
        assert (resp.data['applied_samples'], resp.data['applied_source']) == (9408, 'override')
        submission.refresh_from_db()
        assert submission.sound_runs[0]['offset']['reason'] == 'shots disagree'
        assert ("Sound for shots sh010–sh020 could not be lined up automatically (low_confidence: shots "
                "disagree); the preset's sound offset of 196 ms was applied.") in submission.warnings

    def test_unmeasured_without_override(self, audio, worker_with_token):
        _worker, client = worker_with_token
        submission, job = audio
        resp = post_sound(client, job, offset_value=unmeasured())
        assert (resp.data['applied_samples'], resp.data['applied_source']) == (0, 'none')
        submission.refresh_from_db()
        assert submission.sound_runs[0]['applied_source'] == 'none'
        assert any('no correction was applied' in text and PRESET in text for text in submission.warnings)

    def test_disagreeing_shots_named(self, audio, worker_with_token):
        _worker, client = worker_with_token
        submission, job = audio
        rows = [shot_row(1, 9418), shot_row(2, 11_418)]
        assert post_sound(client, job, offset_value=offset(shots=rows)).status_code == 200
        submission.refresh_from_db()
        assert ("Sound offset: shots sh020 differ from the run's offset by up to 42 ms; check the sound there."
                in submission.warnings)

    def test_silent_file_kept_with_a_warning(self, audio, worker_with_token):
        _worker, client = worker_with_token
        submission, job = audio
        assert post_sound(client, job, file_obj=wav_file(run_samples(job), silent=True)).status_code == 200
        submission.refresh_from_db()
        assert "The sound render is silent." in submission.warnings and submission.sound_runs[0]['silent']

    def test_uploads_go_to_temporary_files_only(self, audio, worker_with_token, monkeypatch):
        _worker, client = worker_with_token
        _submission, job = audio
        seen = []
        original = job_sound_actions.check_wav

        def recording(file_obj, *args, **kwargs):
            seen.append(type(file_obj).__name__)
            return original(file_obj, *args, **kwargs)

        monkeypatch.setattr(job_sound_actions, 'check_wav', recording)
        handlers = []
        real_init = TemporaryFileUploadHandler.__init__

        def init(self, *args, **kwargs):
            handlers.append(type(self))
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(TemporaryFileUploadHandler, '__init__', init)
        assert post_sound(client, job).status_code == 200
        assert seen == ['TemporaryUploadedFile'] and handlers == [TemporaryFileUploadHandler]


@pytest.mark.django_db
class TestRefused:

    @pytest.mark.parametrize('value, field', [
        (offset(extra=1), 'Invalid sound result (offset)'),
        (offset(offset_samples=10 * RATE + 1), 'offset.offset_samples'),
        (offset(shots=[dict(shot_row(1, 1), peak=1.5)]), 'offset.shots[0].peak'),
        ('{"status": NaN}', 'not valid JSON'),
    ], ids=['unknown-key', 'over-10-s', 'peak', 'nan'])
    def test_offset(self, audio, worker_with_token, value, field):
        _worker, client = worker_with_token
        submission, job = audio
        resp = post_sound(client, job, offset_value=value if isinstance(value, str) else json.dumps(value))
        assert resp.status_code == 400 and field in resp.data['error']
        job.refresh_from_db()
        assert job.status == 'RENDERING' and not job.output_file

    @pytest.mark.parametrize('kwargs, text', [
        ({'samples_delta': -300}, 'the run needs 16000'),
        ({'width': 1}, '16-bit'),
        ({'garbage': True}, 'not a PCM .wav'),
    ], ids=['300-short', '8-bit', 'garbage'])
    def test_file(self, audio, worker_with_token, kwargs, text):
        _worker, client = worker_with_token
        _submission, job = audio
        if kwargs.get('garbage'):
            file_obj = SimpleUploadedFile('run.wav', b'not a wav at all')
        else:
            file_obj = wav_file(run_samples(job) + kwargs.get('samples_delta', 0), width=kwargs.get('width', 2))
        resp = post_sound(client, job, file_obj=file_obj)
        assert resp.status_code == 400 and text in resp.data['error']

    def test_skipped_compensation_wav_is_seven_frames_longer(self, audio, worker_with_token):
        """A .wav of the run + 7 frames is the run when MRG skipped compensation, else 400."""
        _worker, client = worker_with_token
        _submission, job = audio
        longer = run_samples(job) + 7 * 1600
        resp = post_sound(client, job, file_obj=wav_file(longer), skipped='false')
        assert resp.status_code == 400 and 'the run needs 16000' in resp.data['error']
        assert post_sound(client, job, file_obj=wav_file(run_samples(job)), skipped='true').status_code == 400
        resp = post_sound(client, job, file_obj=wav_file(longer), skipped='true')
        assert resp.status_code == 200, resp.data

    def test_huge_lag_is_400_not_500(self, audio, worker_with_token):
        _worker, client = worker_with_token
        _submission, job = audio
        resp = post_sound(client, job, offset_value=offset(shots=[shot_row(1, 10 ** 400)]))
        assert resp.status_code == 400 and 'offset.shots[0].lag_samples' in resp.data['error']
        job.refresh_from_db()
        assert job.status == 'RENDERING'

    def test_upload_already_in_progress_is_409_without_storing(self, audio, worker_with_token):
        _worker, client = worker_with_token
        _submission, job = audio
        with upload_slot(job.pk):
            resp = post_sound(client, job)
        assert (resp.status_code, resp.data) == (409, {'error': "A sound upload for this job is already in progress."})
        job.refresh_from_db()
        assert job.status == 'RENDERING' and not job.output_file
        assert post_sound(client, job).status_code == 200

    def test_file_over_the_cap_is_413(self, audio, worker_with_token, monkeypatch):
        _worker, client = worker_with_token
        _submission, job = audio
        monkeypatch.setattr('workers.services.sequence_sound_result.SOUND_FILE_CAP', 1024)
        assert post_sound(client, job).status_code == 413

    @pytest.mark.parametrize('extra, text', [
        ({'frame': '1'}, 'Unknown fields: frame.'),
        ({'latency_compensation_skipped': 'yes'}, "true or false"),
        ({'render_s': 'fast'}, 'plain decimal'),
    ], ids=['unknown', 'flag', 'seconds'])
    def test_fields(self, audio, worker_with_token, extra, text):
        _worker, client = worker_with_token
        _submission, job = audio
        resp = post_sound(client, job, **extra)
        assert resp.status_code == 400 and text in resp.data['error']

    def test_other_worker_repeat_and_unknown_job(self, audio, worker_with_token, make_worker_client):
        _worker, client = worker_with_token
        _submission, job = audio
        _other, other_client, _token = make_worker_client('otherhost')
        assert post_sound(other_client, job).status_code == 409
        assert post_sound(client, job).status_code == 200
        assert post_sound(client, job).status_code == 409
        assert client.post(f'{JOBS}987654/sound-result/', {}, format='multipart').status_code == 404

    def test_piece_is_not_an_audio_job(self, audio, worker_with_token):
        _worker, client = worker_with_token
        submission, _job = audio
        piece = pieces_of(submission)[0]
        resp = post_sound(client, piece)
        assert (resp.status_code, resp.data) == (409, {'error': "Job is not a sequence audio job."})

    def test_admin_token_is_403(self, audio, admin_client):
        _submission, job = audio
        assert post_sound(admin_client, job).status_code == 403

    @pytest.mark.parametrize('use_admin', [False, True], ids=['worker', 'admin'])
    def test_patch_done_refused(self, audio, worker_with_token, admin_client, use_admin):
        _worker, client = worker_with_token
        _submission, job = audio
        resp = (admin_client if use_admin else client).patch(f'{JOBS}{job.pk}/', {'status': 'DONE'}, format='json')
        assert resp.status_code == 400
        assert f"Audio jobs complete through POST /api/jobs/{job.pk}/sound-result/." in str(resp.data)
