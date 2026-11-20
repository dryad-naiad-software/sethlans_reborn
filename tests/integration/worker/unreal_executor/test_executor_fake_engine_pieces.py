# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sequence pieces end to end against the fake ``UnrealEditor-Cmd.exe`` (sequence rendering FR-SW-3..11, AC-10,
AC-14): real cache, working copy, Job Objects and command lines; the share and the GPU are fakes. The manager is
a fake that records the frames and the piece result, except in the round trip, where they go to the real
``frames/`` and ``piece-result/`` endpoints.
"""
import json
import sys

import pytest

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.unreal_executor import sequence_flow
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


def _json(h, name):
    return json.loads(_job_dir(h).joinpath(name).read_text(encoding='utf-8'))


def test_a_piece_renders_its_lead_in_throws_it_away_and_uploads_the_kept_frames(tmp_path, monkeypatch):
    """AC-10: frames 100-129 with lead-in 30 render .0070 to .0129; 30 are kept and uploaded, 30 deleted."""
    h = Harness(tmp_path, monkeypatch, opts={'frame_sleep': 0.01})
    shots = [[3, 100, 110], [4, 110, 130]]
    result = h.execute(h.piece(41, settings={'unreal.output_format': 'PNG', 'unreal.piece_shots': shots,
                                             'unreal.output_size': [1, 1]}))
    assert result.success and result.released, result.error_message
    assert [frame for _job, frame, _name, _data in h.manager.frames] == list(range(100, 130))
    assert h.manager.frames[0][2] == 'sethlans_41.0100.png' and h.manager.frames[0][3].startswith(b'\x89PNG')
    (job_id, body), = h.manager.piece_bodies
    assert job_id == 41 and set(body) == {'render_s', 'startup_s', 'lead_in_s', 'vram_peak_mb', 'gpu', 'shots',
                                          'warnings', 'summary'}
    assert [(row['index'], row['frames']) for row in body['shots']] == [(3, 10), (4, 20)]
    assert body['lead_in_s'] is not None and body['gpu']['card_key'] == 'pci:0000:01:00.0'
    params = _json(h, 'params.json')
    assert (params['mode'], params['range'], params['start'], 'frame' in params) == ('prepare_range', [70, 130], 100,
                                                                                     False)
    assert {70, 100, 110} <= set(_json(h, 'warmup_params.json')['frames'])  # FR-SW-6: the lead-in's shot too
    s = summary(result)
    assert (s['piece'], s['lead_in'], s['lead_in_deleted'], s['output']) == ('100-129', '30', '30', '30 PNG frames')
    assert not h.manager.uploads and not h.manager.releases


def test_an_exr_piece_uploads_exr_frames(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    result = h.execute(h.piece(42, start=5, end=7, lead=0, fmt='EXR'))
    assert result.success, result.error_message
    assert [(name, data[:4]) for _job, _frame, name, data in h.manager.frames] == \
        [(f'sethlans_42.000{n}.exr', b'\x76\x2f\x31\x01') for n in (5, 6, 7)]
    assert _json(h, 'params.json')['output_format'] == 'EXR'


def test_the_loads_in_flight_assertion_after_every_frame_is_accepted(tmp_path, monkeypatch):
    """AC-14 (fake engine): exit 3 on the assertion alone, every frame present: DONE with exit_accepted."""
    h = Harness(tmp_path, monkeypatch, opts={'loads_in_flight': True})
    result = h.execute(h.piece(43, start=10, end=12, lead=2))
    assert result.success and result.released, result.error_message
    assert len(h.manager.frames) == 3 and len(h.manager.piece_bodies) == 1
    s = summary(result)
    assert s['exit_accepted'] != '' and 'NumberOfLoadsInFlight' in result.stdout


@pytest.mark.parametrize('opts', [{'loads_in_flight': True, 'skip_frame': 11}, {'skip_frame': 11}])
def test_a_missing_frame_fails_the_piece(tmp_path, monkeypatch, opts):
    h = Harness(tmp_path, monkeypatch, opts=opts)
    result = h.execute(h.piece(44, start=10, end=12, lead=2))
    assert not result.success and not result.released
    assert not h.manager.frames and not h.manager.piece_bodies
    if 'loads_in_flight' not in opts:
        assert result.error_message == ("the render wrote 4 PNG files for 5 frames (missing ['sethlans_44.0011.png'], "
                                        "unexpected []).")


def test_a_piece_that_does_not_fit_on_the_disk_is_released_before_the_prep(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    monkeypatch.setattr(sequence_flow.RangeFlow, 'reserve_gb', staticmethod(lambda: 10 ** 9))
    result = h.execute(h.piece(45, start=0, end=9, lead=0, **{'unreal.output_size': [2560, 1440]}))
    assert result.released and h.manager.releases == [(45, 'local_conflict')]
    assert not _job_dir(h).joinpath('logs', 'prep.1.log').exists() and not h.manager.frames


def test_a_draft_sends_its_cvars_and_reports_the_presets_conflicts(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'draft_conflicts': ['r.ScreenPercentage']})
    draft = {'temporal_samples': 2, 'screen_percentage': 50, 'ray_traced_shadows': False}
    result = h.execute(h.piece(46, start=0, end=1, lead=0, **{'unreal.draft': draft}))
    assert result.success, result.error_message
    render_log = _job_dir(h).joinpath('logs', 'render.1.log').read_text(encoding='utf-8')
    assert '[[r.ScreenPercentage:50]]' in render_log and '[[r.RayTracing.Shadows:0]]' in render_log
    params = _json(h, 'params.json')
    assert params['draft'] == draft and params['draft_cvars'] == ['r.ScreenPercentage', 'r.RayTracing.Shadows']
    (_job, body), = h.manager.piece_bodies
    assert "The preset sets r.ScreenPercentage itself; the draft could not change it." in body['warnings']
    assert summary(result)['draft_conflicts'] == 'r.ScreenPercentage'


def test_a_frame_the_manager_no_longer_wants_stops_the_upload(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    answers = [api_extras.PilotReport('done', 200), api_extras.PilotReport('terminal', 409)]
    h.manager.frame_sink = lambda job_id, frame, handle, name: answers.pop(0)
    result = h.execute(h.piece(47, start=0, end=4, lead=0))
    assert result.success and result.released and answers == [] and not h.manager.piece_bodies


# -- the round trip against the real manager endpoints -----------------------------------------------------------

def _answer(resp):
    if resp.status_code == 200:
        return api_extras.PilotReport('done', 200)
    kind = 'terminal' if resp.status_code in (404, 409) else 'rejected'
    return api_extras.PilotReport(kind, resp.status_code, str(resp.data))


@pytest.fixture
def manager_piece(db, settings, tmp_path):
    """A planned submission on the manager: one 50-frame shot at 5 fps, 5 s pieces, so piece 2 has a lead-in."""
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    from tests._sequence_bodies import shots_body
    from tests.integration.manager.sequence import submission_helpers as sh
    from tests.integration.manager.unreal_helpers import unreal_asset, unreal_project
    from tests.integration.manager.unreal_pilot_helpers import claim, ready_worker
    from workers.models import Worker

    settings.MEDIA_ROOT = str(tmp_path / 'media')
    admin = APIClient()
    admin.force_authenticate(user=User.objects.create_superuser('admin', 'admin@example.com', 'testpass123'))
    user = User.objects.create_user(username='worker_pieces')
    worker = Worker.objects.create(hostname='pieces', user=user, is_active=True, available_tools={'blender': ['4.2.19']})
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
    asset = unreal_asset(unreal_project(unreal_piece_seconds=5, unreal_lead_in_ms=1000))
    body = shots_body(playback_range=[0, 50], frame_rate=[5, 1], shots=[['sh010', 0, 50]])
    body['preset_facts']['resolution'] = [64, 36]
    sh.done_discovery(admin, asset, worker, client, body=body)
    submission, _resp = sh.submit(admin, asset)
    sh.measure_submission(asset)
    ready_worker(worker)
    piece = sh.pieces_of(submission)[1]
    assert claim(client, piece, worker).status_code == 200
    piece.refresh_from_db()
    return submission, piece, client


@pytest.mark.django_db
def test_round_trip_frames_and_piece_result_reach_the_real_endpoints(tmp_path, monkeypatch, manager_piece):
    from workers.models import SequenceFrame
    submission, piece, client = manager_piece
    assert (piece.start_frame, piece.end_frame, piece.unreal_lead_in_frames) == (25, 49, 5)
    h = Harness(tmp_path, monkeypatch, opts={'size': [64, 36]})
    h.manager.frame_sink = lambda job_id, frame, handle, name: _answer(client.post(
        f'/api/jobs/{job_id}/frames/', {'frame': str(frame), 'file': _upload(name, handle.read())}, format='multipart'))
    h.manager.piece_sink = lambda job_id, body: _answer(client.post(f'/api/jobs/{job_id}/piece-result/', body,
                                                                    format='json'))
    job = h.piece(piece.pk, start=piece.start_frame, end=piece.end_frame, lead=piece.unreal_lead_in_frames,
                  settings=dict(piece.render_settings), submission=submission.pk)
    result = h.execute(job)
    assert result.success and result.released, result.error_message
    piece.refresh_from_db()
    assert piece.status == 'DONE', piece.last_output
    frames = sorted(SequenceFrame.objects.filter(job=piece).values_list('frame_number', flat=True))
    assert frames == list(range(25, 50))  # the five lead-in frames 20-24 never reached the manager
    timing, = submission.shots.get().timings  # one R7 timing from this piece (the other piece is still queued)
    assert (timing['job'], timing['frames'], timing['card_key']) == (piece.pk, 25, 'pci:0000:01:00.0')
    assert timing['lead_in_s'] is not None and timing['startup_s'] is not None


def _upload(name, data):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, data, content_type='image/png')
