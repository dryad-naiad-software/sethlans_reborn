# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the audio job's ``last_output`` through
``POST /api/jobs/{id}/sound-result/`` (#285).

Covers: the worker's real multi-line summary is stored one ``key=value`` per
line after the ``audio_samples=... audio_rate=... audio_channels=...``
header, with control characters removed inside lines and ``\\r\\n`` line
ends handled; the summary's ``warning=`` lines still reach the run's and the
version's warnings (#284), read from the request body.
"""

import pytest

from sethlans_worker_agent.unreal_executor.summary import PREFIX, Summary

from ..unreal_helpers import unreal_asset
from .sound_helpers import claimed_audio, post_sound


@pytest.fixture
def audio(admin_client, db, settings, tmp_path, worker_with_token):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    worker, client = worker_with_token
    submission, job = claimed_audio(admin_client, unreal_asset(), worker, client)
    return submission, job, client


@pytest.mark.django_db
class TestLastOutputLines:

    def test_one_key_per_line_and_warnings_still_read(self, audio):
        submission, job, client = audio
        summary = Summary()
        summary.update({'sound_offset': 'measured', 'note': ''})
        summary.warn('export: mute state unknown')
        expected = summary.text().split('\n')
        text = summary.text().replace('\n', '\r\n').replace('note=', 'note=\x00')
        resp = post_sound(client, job, summary=text)
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        lines = job.last_output.split('\n')
        assert lines[0].startswith('audio_samples=') and 'audio_channels=2' in lines[0]
        assert lines[1:] == expected and len(expected) > 3
        assert f'{PREFIX} note=' in lines and lines[-1] == f'{PREFIX} warning=export: mute state unknown'
        submission.refresh_from_db()
        assert submission.sound_runs[0]['warnings'] == ['export: mute state unknown']
        assert submission.warnings == ['export: mute state unknown']

    def test_no_summary_is_just_the_header(self, audio):
        _submission, job, client = audio
        assert post_sound(client, job).status_code == 200
        job.refresh_from_db()
        assert '\n' not in job.last_output and job.last_output.startswith('audio_samples=')
