# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A ``shots`` job end to end against the fake ``UnrealEditor-Cmd.exe`` (sequence rendering FR-SD-4, FR-SD-7,
FR-SD-8, AC-2): real cache, working copy, Job Objects and prep command line; the share, the GPU and the manager
are fakes. The fake engine exits 3 if the prep carries ``-dx12``, ``-graphicsadapter`` or ``-RenderOffscreen``.
"""
import json
import sys

import pytest

from sethlans_worker_agent import api_extras
from tests.integration.worker.unreal_executor._harness import Harness, summary

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Job Objects, Windows command lines')


@pytest.fixture(autouse=True)
def _forget_refused_projects():
    from sethlans_worker_agent.unreal_executor import adapter_log
    adapter_log.reset_for_tests()
    yield
    adapter_log.reset_for_tests()


def _logs(h):
    return sorted(p.name for p in next(h.cache.layout.j.iterdir()).joinpath('logs').iterdir() if p.suffix == '.log')


def test_a_shots_job_runs_only_the_prep_and_reports_the_shots(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.sampler = None  # AC-2: no GPU memory reading at all
    result = h.execute(h.job(31, kind='shots', frame=0))
    assert result.success and result.released, result.error_message
    (job_id, body), = h.manager.shots_bodies
    assert job_id == 31 and set(body) == {'preset_kind', 'playback_range', 'frame_rate', 'shot_source', 'shots',
                                          'preset_facts', 'warnings'}
    assert body['shots'] == [['sh010', 0, 99], ['sh_020', 99, 202], ['sh010-2', 202, 400]]  # cleaned, numbered
    assert (body['frame_rate'], body['playback_range'], body['shot_source']) == ([30000, 1001], [0, 400], 'shot_tracks')
    assert body['preset_facts']['temporal_samples'] == 8 and body['preset_facts']['frame_step'] is None
    assert _logs(h) == ['prep.1.log'] and h.choices == []  # no warm-up, no render, no card chosen
    assert not h.manager.uploads and not h.manager.pilot_bodies and not h.manager.releases
    s = summary(result)
    assert (s['kind'], s['vram_gate'], s['warmup'], s['shots']) == ('shots', 'skipped', 'skipped', '3')
    assert s['pristine_integrity'] == 'ok' and s['job_object'] == 'ok' and s['zen'] == f'private:{h.port}'
    params = json.loads(next(h.cache.layout.j.iterdir()).joinpath('params.json').read_text(encoding='utf-8'))
    assert params['mode'] == 'list_shots' and 'frame' not in params and 'output_dir' not in params


@pytest.mark.parametrize('opts,message', [
    ({'prep_error': 'Shot sections sh010 and sh020 overlap; Sethlans cannot tell which one renders.'},
     'Shot sections sh010 and sh020 overlap; Sethlans cannot tell which one renders.'),
    ({'shots_result': {'shots': [['a', 0, 50], ['b', 40, 90]]}},
     'Shot discovery returned an invalid result (shots[1]).'),
    ({'shots_result': {'playback_range': [-24, 400]}}, 'Sequences that start before frame 0 are not supported.'),
])
def test_a_failed_discovery_is_the_jobs_error(tmp_path, monkeypatch, opts, message):
    h = Harness(tmp_path, monkeypatch, opts=opts)
    result = h.execute(h.job(32, kind='shots', frame=0))
    assert not result.success and not result.released and result.error_message == message
    assert not h.manager.shots_bodies and _logs(h) == ['prep.1.log']


def test_a_rejected_result_fails_the_job(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.manager.shots_report = api_extras.PilotReport('rejected', 400, "{'shots': ['Shots overlap.']}")
    result = h.execute(h.job(33, kind='shots', frame=0))
    assert not result.success and len(h.manager.shots_bodies) == 1
    assert result.error_message == "Shot discovery result rejected by the manager: {'shots': ['Shots overlap.']}"
