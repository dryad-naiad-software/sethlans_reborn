# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the Unreal sequence submission tests (chunk 3:
submissions, planning, frames, piece results).  Not a test module.

The made-up sequence has small shots so a piece has few frames:
``sh010`` [0, 4), ``sh020`` [4, 10), a gap, ``sh030`` [20, 24) at 30 fps,
so the default piece length (30 s) gives two pieces, one per run.
"""

import contextlib
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from workers.models import Job, SequenceShot, SequenceSubmission, Worker
from workers.services.parts_check import registry as parts_registry

from ..unreal_pilot_helpers import JOBS, claim, fits_record, pilots, ready_worker
from tests._sequence_bodies import shots_body
from .sequence_helpers import MAP, PRESET, SEQUENCE, claimed_shots_job, post_shots, shots_job_of

SUBMISSIONS = '/api/sequence-submissions/'
VIDEO = {'preset': 'web_h264'}
EXR_MAGIC = b'\x76\x2f\x31\x01'
SMALL_SHOTS = [['sh010', 0, 4], ['sh020', 4, 10], ['sh030', 20, 24]]
SIZE = [64, 36]


@contextlib.contextmanager
def ffmpeg_ready():
    """Report FFmpeg ready for the video create guard, restoring the prior state after."""
    prior = parts_registry.get_status('ffmpeg')
    parts_registry._publish('ffmpeg', parts_registry.Status(status='ready'))
    try:
        yield
    finally:
        parts_registry._publish('ffmpeg', prior)


def small_shots_body(**overrides):
    """A ``shots-result/`` body with :data:`SMALL_SHOTS` and a 64x36 preset."""
    body = shots_body(playback_range=[0, 24], shots=SMALL_SHOTS)
    body['preset_facts']['resolution'] = list(SIZE)
    body.update(overrides)
    return body


def done_discovery(admin_client, asset, worker, worker_client, body=None):
    """Read the shots: claim the shots job and post ``body`` (default :func:`small_shots_body`).

    The worker then has a live card reading and a ticked card (``ready_worker``), so a
    submission planned after this gets its pilot for that machine (per-machine D5).
    """
    discovery, job = claimed_shots_job(admin_client, asset, worker, worker_client)
    resp = post_shots(worker_client, job.pk, body or small_shots_body())
    assert resp.status_code == 200, resp.data
    discovery.refresh_from_db()
    ready_worker(worker)
    return discovery


def finish_discovery(worker, worker_client, discovery, body=None):
    """Claim a waiting discovery's shots job and post ``body``; return the response.

    The worker has a live card reading (``ready_worker``), so the submissions planned
    by the result get their pilot for that machine (per-machine D5).
    """
    ready_worker(worker)
    job = shots_job_of(discovery)
    resp = claim(worker_client, job, worker)
    assert resp.status_code == 200, resp.data
    return post_shots(worker_client, job.pk, body or small_shots_body())


def submission_body(asset, **overrides):
    body = {'asset': asset.pk, 'map': MAP, 'sequence': SEQUENCE, 'preset': PRESET}
    body.update(overrides)
    return body


def submit(admin_client, asset, expect=201, **overrides):
    """POST a submission; return ``(submission or None, response)``."""
    resp = admin_client.post(SUBMISSIONS, submission_body(asset, **overrides), format='json')
    assert resp.status_code == expect, resp.data
    submission = SequenceSubmission.objects.get(pk=resp.data['id']) if expect == 201 else None
    return submission, resp


def pieces_of(submission):
    return list(Job.objects.filter(sequence_submission=submission, unreal_kind='render').order_by('start_frame'))


def shots_of(submission):
    return list(SequenceShot.objects.filter(submission=submission).order_by('index'))


def pilot_of(asset):
    return pilots(asset)[-1]


def measure_submission(asset, peak_mb=8102, worker=None, submission=None):
    """A ``fits`` result of the submission's piece key for ``worker``, else for every worker.

    As a successful pilot on that machine would leave it (per-machine D2),
    for the card kind ``ready_worker`` gives.  ``submission`` defaults to
    the asset's latest.
    """
    if submission is None:
        submission = SequenceSubmission.objects.filter(asset=asset).order_by('-pk').first()
    workers = [worker] if worker is not None else list(Worker.objects.order_by('pk'))
    for each in workers:
        fits_record(asset, submission.piece_target_sha256, each, peak_mb=peak_mb)


def png_file(size=SIZE, name='frame.png'):
    buffer = io.BytesIO()
    Image.new('RGB', tuple(size), (20, 40, 60)).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def upload_frame(client, job, frame, file_obj=None):
    data = {'frame': str(frame), 'file': file_obj or png_file()}
    return client.post(f'{JOBS}{job.pk}/frames/', data, format='multipart')


def upload_all(client, job):
    for frame in range(job.start_frame, job.end_frame + 1):
        resp = upload_frame(client, job, frame)
        assert resp.status_code == 200, resp.data


def piece_body(job, **overrides):
    """A ``piece-result/`` body that matches ``job``'s ``unreal.piece_shots``."""
    shots = [
        {'index': index, 'frames': end - start, 'seconds': 2.0 * (end - start), 's_per_frame': 2.0}
        for index, start, end in job.render_settings['unreal.piece_shots']
    ]
    body = {
        'render_s': 30.0, 'startup_s': 12.5, 'lead_in_s': 3.0, 'vram_peak_mb': 9100,
        'gpu': {'name': 'Example GPU', 'total_mb': 32607, 'card_key': 'pci:0000:01:00.0'},
        'shots': shots, 'warnings': [], 'summary': 'frames ok',
    }
    body.update(overrides)
    return body


def post_piece(client, job, body):
    return client.post(f'{JOBS}{job.pk}/piece-result/', body, format='json')


def claimed_piece(admin_client, asset, worker, worker_client, index=0, **submit_overrides):
    """Read the shots, submit, measure the key and claim one piece; return ``(submission, piece)``."""
    done_discovery(admin_client, asset, worker, worker_client)
    submission, _ = submit(admin_client, asset, **submit_overrides)
    measure_submission(asset)
    ready_worker(worker)
    piece = pieces_of(submission)[index]
    resp = claim(worker_client, piece, worker)
    assert resp.status_code == 200, resp.data
    piece.refresh_from_db()
    return submission, piece
