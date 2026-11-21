# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the Unreal sequence sound tests (chunk 5: audio jobs,
``sound-result/``, sound items).  Not a test module.

With :data:`submission_helpers.SMALL_SHOTS` at 30 fps the plan has two
runs, ``sh010``+``sh020`` [0, 10) and ``sh030`` [20, 24), so two audio
jobs, each starting right after a gap (no audio lead-in).  A run's
``.wav`` at 48 kHz has 1,600 samples per frame.
"""

import io
import json
import wave

from django.core.files.uploadedfile import SimpleUploadedFile

from workers.models import Job

from ..unreal_pilot_helpers import JOBS, claim, ready_worker
from .submission_helpers import done_discovery, submit

RATE = 48_000
SAMPLES_PER_FRAME = 1_600


def audio_jobs_of(submission):
    return list(Job.objects.filter(sequence_submission=submission, unreal_kind='audio').order_by('start_frame'))


def run_samples(job):
    """The samples a run's ``.wav`` must have (lead-in included)."""
    return (job.end_frame - job.start_frame + 1 + job.unreal_lead_in_frames) * SAMPLES_PER_FRAME


def wav_bytes(samples, channels=2, width=2, rate=RATE, silent=False):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        frame = bytes(channels * width) if silent else (b'\x01' * channels * width)
        writer.writeframes(frame * samples)
    return buffer.getvalue()


def wav_file(samples, name='run.wav', **kwargs):
    return SimpleUploadedFile(name, wav_bytes(samples, **kwargs), content_type='audio/wav')


def shot_row(shot, lag, peak=0.95, ambiguous=False):
    return {'shot': shot, 'lag_samples': lag, 'envelope_peak': 0.9, 'envelope_margin': 0.3, 'peak': peak,
            'ambiguous': ambiguous}


def offset(**overrides):
    """A measured ``offset`` object (+9,418 samples, two agreeing shots)."""
    value = {
        'status': 'measured', 'offset_samples': 9418, 'reason': '', 'shots_tried': 2, 'shots_confident': 2,
        'shots_agreeing': 2, 'min_peak': 0.93, 'spread_samples': 3, 'unsupported_sections': 0,
        'export_s': 21.5, 'correlate_s': 4.2, 'shots': [shot_row(1, 9417), shot_row(2, 9420)],
    }
    value.update(overrides)
    return value


def unmeasured(status='unavailable', reason='no enabled audio section'):
    return offset(status=status, offset_samples=None, reason=reason, shots_tried=0, shots_confident=0,
                  shots_agreeing=0, min_peak=None, spread_samples=None, shots=[])


def post_sound(client, job, file_obj=None, offset_value=None, skipped='false', **extra):
    data = {
        'file': file_obj if file_obj is not None else wav_file(run_samples(job)),
        'offset': offset_value if isinstance(offset_value, str) else json.dumps(offset_value or offset()),
        'latency_compensation_skipped': skipped,
    }
    data.update(extra)
    return client.post(f'{JOBS}{job.pk}/sound-result/', data, format='multipart')


def claimed_audio(admin_client, asset, worker, worker_client, index=0, **submit_overrides):
    """Read the shots, submit with sound and claim one audio job (no measurement, no card reading)."""
    done_discovery(admin_client, asset, worker, worker_client)
    submission, _ = submit(admin_client, asset, sound=True, **submit_overrides)
    ready_worker(worker, source='override')
    job = audio_jobs_of(submission)[index]
    resp = claim(worker_client, job, worker)
    assert resp.status_code == 200, resp.data
    job.refresh_from_db()
    return submission, job
