# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/jobs/{id}/frames/`` (Unreal sequence spec
FR-SS-2, FR-SS-2b, FR-SV-1, FR-BL-3, D21, AC-30 handler part, AC-31).

Covers: frames outside the kept range (a lead-in frame included) or in no
shot of ``unreal.piece_shots`` are refused naming the frame; ownership
(another worker, a queued piece, a job that is not a piece, an unknown
job, an admin); the manager-made storage name under ``MEDIA_ROOT``; a
repeat upload replaces the file and removes the old one after commit; the
file checks (extension, PNG verify, size, EXR magic, 413); the upload
reaches the service as a temporary file, never in memory.
"""

import os
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile

from workers.models import Job, SequenceFrame
from workers.services import sequence_frames

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, claim, created_job, ready_worker
from .submission_helpers import (
    EXR_MAGIC, claimed_piece, done_discovery, measure_submission, pieces_of, png_file, small_shots_body, submit,
    upload_frame,
)


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return settings.MEDIA_ROOT


@pytest.fixture
def asset(db, media):
    return unreal_asset()


@pytest.fixture
def first_piece(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_piece(admin_client, asset, worker, client)


def _error(resp, status):
    assert resp.status_code == status, resp.data
    return resp.data.get('error', '')


@pytest.mark.django_db
class TestFrameNumber:

    def test_outside_the_kept_range(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client, index=1)
        assert (piece.start_frame, piece.end_frame) == (20, 23)
        for frame in (19, 24):
            text = _error(upload_frame(client, piece, frame), 400)
            assert text == f"Frame {frame} is outside this piece's kept frames (20-23)."
        assert not SequenceFrame.objects.exists()

    def test_lead_in_frame_refused(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client, frame_start=6, frame_end=9)
        assert (piece.start_frame, piece.unreal_lead_in_frames) == (6, 6)
        assert "Frame 5 is outside" in _error(upload_frame(client, piece, 5), 400)
        assert upload_frame(client, piece, 6).status_code == 200

    def test_in_range_but_in_no_shot(self, first_piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, piece = first_piece
        Job.objects.filter(pk=piece.pk).update(
            render_settings=dict(piece.render_settings, **{'unreal.piece_shots': [[1, 0, 4]]}))
        assert _error(upload_frame(client, piece, 5), 400) == "Frame 5 is in no shot of this piece (0-9)."
        assert upload_frame(client, piece, 3).status_code == 200

    @pytest.mark.parametrize('frame', ['', 'x', '-1', '1.5', '123456789'])
    def test_frame_must_be_plain_digits(self, first_piece, worker_with_token, frame):
        _worker, client = worker_with_token
        _submission, piece = first_piece
        resp = client.post(f'{JOBS}{piece.pk}/frames/', {'frame': frame, 'file': png_file()}, format='multipart')
        assert _error(resp, 400) == "Send 'frame' (an integer) and 'file'."

    def test_missing_file(self, first_piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, piece = first_piece
        resp = client.post(f'{JOBS}{piece.pk}/frames/', {'frame': '0'}, format='multipart')
        assert resp.status_code == 400


@pytest.mark.django_db
class TestOwnership:

    def test_other_worker(self, first_piece, make_worker_client):
        _submission, piece = first_piece
        _other, other_client, _token = make_worker_client('otherhost')
        assert _error(upload_frame(other_client, piece, 0), 409) == "Job is not rendering on this worker."

    def test_queued_piece(self, first_piece, worker_with_token):
        _worker, client = worker_with_token
        submission, _piece = first_piece
        queued = pieces_of(submission)[1]
        assert _error(upload_frame(client, queued, 20), 409) == "Job is not rendering on this worker."

    def test_not_a_piece(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        job = created_job(admin_client, asset)
        Job.objects.filter(pk=job.pk).update(status='RENDERING', assigned_worker=worker)
        assert _error(upload_frame(client, job, 1000), 409) == "Job is not a sequence piece."

    def test_unknown_job(self, db, worker_with_token):
        _worker, client = worker_with_token
        resp = client.post(f'{JOBS}987654/frames/', {'frame': '0', 'file': png_file()}, format='multipart')
        assert resp.status_code == 404

    def test_admin_refused(self, first_piece, admin_client):
        _submission, piece = first_piece
        assert upload_frame(admin_client, piece, 0).status_code == 403


@pytest.mark.django_db
class TestStorage:

    def test_manager_made_name(self, first_piece, worker_with_token, asset, media):
        _worker, client = worker_with_token
        submission, piece = first_piece
        resp = upload_frame(client, piece, 0, png_file(name='../../evil name.png'))
        assert resp.data == {'frame': 0, 'shot': 1, 'replaced': False, 'kept_newer': False}
        row = SequenceFrame.objects.get()
        assert row.file.name == f'sequences/{asset.project_id}/{submission.pk}/shots/0001/0000000.png'
        assert (row.job_id, row.shot.index, row.frame_number) == (piece.pk, 1, 0)
        assert os.path.isfile(os.path.join(media, row.file.name))

    def test_repeat_replaces_and_removes_the_old_file(self, first_piece, worker_with_token, media,
                                                      django_capture_on_commit_callbacks):
        _worker, client = worker_with_token
        _submission, piece = first_piece
        assert upload_frame(client, piece, 7).status_code == 200
        old = SequenceFrame.objects.get().file.name
        with django_capture_on_commit_callbacks(execute=True):
            resp = upload_frame(client, piece, 7)
        assert resp.data['replaced'] is True
        new = SequenceFrame.objects.get().file.name
        assert new != old and SequenceFrame.objects.count() == 1
        assert os.path.isfile(os.path.join(media, new))
        assert not os.path.exists(os.path.join(media, old))

    def test_upload_is_a_temporary_file(self, first_piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, piece = first_piece
        seen = []
        real = sequence_frames.store_frame

        def _record(job_pk, worker, frame, file_obj):
            seen.append(type(file_obj))
            return real(job_pk, worker, frame, file_obj)

        with mock.patch('workers.views.job_sequence_actions.store_frame', side_effect=_record):
            assert upload_frame(client, piece, 0).status_code == 200
        assert seen == [TemporaryUploadedFile]


@pytest.mark.django_db
class TestFileChecks:

    @pytest.mark.parametrize('file_obj, text', [
        (lambda: png_file(name='frame.jpg'), "the file must end with .png"),
        (lambda: SimpleUploadedFile('frame.png', b'not an image at all'), "not a valid PNG"),
        (lambda: png_file(size=[32, 32]), "The frame is 32x32; this piece renders 64x36."),
    ], ids=['extension', 'not-png', 'size'])
    def test_refused(self, first_piece, worker_with_token, file_obj, text):
        _worker, client = worker_with_token
        _submission, piece = first_piece
        assert text in _error(upload_frame(client, piece, 0, file_obj()), 400)
        assert not SequenceFrame.objects.exists()

    def test_over_the_cap_is_413(self, first_piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, piece = first_piece
        with mock.patch('workers.services.sequence_frames.FRAME_FILE_CAP', 10):
            assert _error(upload_frame(client, piece, 0), 413) == "A frame may be at most 10 bytes."

    def test_exr_frames(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        body = small_shots_body()
        body['preset_facts']['can_exr'] = True
        done_discovery(admin_client, asset, worker, client, body)
        submission, _ = submit(admin_client, asset, frame_format='EXR')
        measure_submission(asset)
        ready_worker(worker)
        piece = pieces_of(submission)[0]
        assert piece.render_settings['unreal.output_format'] == 'EXR'
        assert claim(client, piece, worker).status_code == 200
        good = SimpleUploadedFile('frame.exr', EXR_MAGIC + b'\x00' * 64)
        assert upload_frame(client, piece, 0, good).status_code == 200
        assert SequenceFrame.objects.get().file.name.endswith('/0000000.exr')
        bad = SimpleUploadedFile('frame.exr', b'\x00' * 68)
        assert _error(upload_frame(client, piece, 1, bad), 400) == "The file is not an EXR image."
        assert "must end with .exr" in _error(upload_frame(client, piece, 2, png_file()), 400)
