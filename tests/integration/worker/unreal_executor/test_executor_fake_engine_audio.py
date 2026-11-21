# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Audio jobs end to end against the fake ``UnrealEditor-Cmd.exe`` (sequence rendering FR-SA-2..10, D14, D23, D24):
real cache, working copy, Job Objects and command lines; the share and the GPU are fakes. The fake render writes a
``.wav`` holding a noise source 4,800 samples ahead, and the fake export commandlet writes that source; the worker
must find the lag, upload the ``.wav`` with its offset, and never fail the job on the measurement. The round trip
sends the result to the real ``sound-result/`` endpoint.
"""
import json
import sys

import pytest

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.unreal_executor import wav_check
from shared.unreal import sound_result
from tests.integration.worker.unreal_executor._harness import Harness, summary

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Job Objects, Windows command lines')


@pytest.fixture(autouse=True)
def _forget_refused_projects():
    from sethlans_worker_agent.unreal_executor import adapter_log
    adapter_log.reset_for_tests()
    yield
    adapter_log.reset_for_tests()


def _job_dir(h):
    return next(h.cache.layout.j.iterdir())


def test_an_audio_job_renders_measures_and_uploads_its_wav(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    result = h.execute(h.audio(51, start=8, end=299, lead=8))
    assert result.success and result.released, result.error_message
    (job_id, data, offset, skipped), = h.manager.sounds
    assert job_id == 51 and data[:4] == b'RIFF' and skipped is False
    assert sound_result.check_offset(offset, 48_000) is offset
    assert offset['status'] == 'measured' and abs(offset['offset_samples'] - 4800) <= 32, offset
    assert [row['shot'] for row in offset['shots']] == [1, 2, 3] and offset['unsupported_sections'] == 1
    params = json.loads(_job_dir(h).joinpath('params.json').read_text(encoding='utf-8'))
    assert (params['mode'], params['range'], params['start']) == ('prepare_audio', [0, 300], 8)
    logs = _job_dir(h) / 'logs'
    assert (logs / 'prep.2.log').is_file()  # the export commandlet, after the render
    assert not (_job_dir(h) / 'audio_src').exists()
    s = summary(result)
    assert (s['kind'], s['audio_samples'], s['audio_rate'], s['audio_channels'], s['sound_offset']) == \
        ('audio', str(300 * 1600), '48000', '2', 'measured')
    assert s['vram_gate'] == 'audio' and not h.manager.frames and not h.manager.uploads


def test_the_streaming_fault_fails_the_job(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'audio_fault': True})
    result = h.execute(h.audio(52, end=99))
    assert not result.success and result.error_message == wav_check.FAULT_MESSAGE and not h.manager.sounds


def test_a_failed_export_still_delivers_the_sound(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'export_error': 'A sound wave could not be exported.'})
    result = h.execute(h.audio(53, end=99))
    assert result.success and result.released, result.error_message
    (_job, _data, offset, _skipped), = h.manager.sounds
    assert offset['status'] == 'unavailable' and offset['reason'] == ("the export step failed: A sound wave could "
                                                                      "not be exported.")


def test_a_rejected_sound_fails_the_job(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.manager.sound_sink = lambda *args: api_extras.PilotReport('rejected', 400, 'offset: bad')
    result = h.execute(h.audio(54, end=99))
    assert not result.success and result.error_message == "Sound result rejected by the manager: offset: bad"


# -- the round trip against the real manager endpoint ------------------------------------------------------------

def _answer(resp):
    if resp.status_code == 200:
        return api_extras.PilotReport('done', 200)
    kind = 'terminal' if resp.status_code in (404, 409) else 'rejected'
    return api_extras.PilotReport(kind, resp.status_code, str(resp.data))


@pytest.fixture
def manager_audio(db, settings, tmp_path):
    """A planned submission with sound and its first audio job claimed (frames 0-9 at 30 fps)."""
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    from tests.integration.manager.sequence.sound_helpers import claimed_audio
    from tests.integration.manager.unreal_helpers import unreal_asset
    from workers.models import Worker

    settings.MEDIA_ROOT = str(tmp_path / 'media')
    admin = APIClient()
    admin.force_authenticate(user=User.objects.create_superuser('admin', 'admin@example.com', 'testpass123'))
    user = User.objects.create_user(username='worker_audio')
    worker = Worker.objects.create(hostname='audio', user=user, is_active=True, available_tools={'blender': ['4.2.19']})
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
    submission, job = claimed_audio(admin, unreal_asset(), worker, client)
    return submission, job, client


@pytest.mark.django_db
def test_round_trip_the_sound_reaches_the_real_endpoint(tmp_path, monkeypatch, manager_audio):
    from django.core.files.uploadedfile import SimpleUploadedFile
    submission, job, client = manager_audio
    h = Harness(tmp_path, monkeypatch)

    def post(job_id, handle, offset, skipped, render_s, text):
        body = {'file': SimpleUploadedFile('sound.wav', handle.read(), content_type='audio/wav'),
                'offset': sound_result.offset_text(offset), 'latency_compensation_skipped': 'true' if skipped else 'false',
                'render_s': f'{render_s:.3f}', 'summary': text}
        return _answer(client.post(f'/api/jobs/{job_id}/sound-result/', body, format='multipart'))
    h.manager.sound_sink = post
    data = h.audio(job.pk, start=job.start_frame, end=job.end_frame, lead=job.unreal_lead_in_frames,
                   submission=submission.pk)
    data['render_settings'] = dict(job.render_settings)
    result = h.execute(data)
    assert result.success and result.released, result.error_message
    job.refresh_from_db()
    submission.refresh_from_db()
    assert job.status == 'DONE', job.last_output
    run, = submission.sound_runs
    assert run['job'] == job.pk and run['offset']['status'] == 'unavailable'  # its shots are shorter than 2 s
    assert run['applied_source'] in ('none', 'override')
