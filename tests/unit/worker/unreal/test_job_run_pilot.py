# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``execute`` paths without processes for pilots: the ``pilot-result/``
answers and the failure prefix (Unreal foundation FR-UE-25 "Report").
"""
import pytest

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from tests.unit.worker.unreal._job_run_world import World, pilot_flow


@pytest.mark.parametrize('report,released', [('done', True), ('terminal', True), ('failed', True),
                                             ('rejected', False)])
def test_pilot_report_responses(tmp_path, monkeypatch, report, released):
    w = World(tmp_path, monkeypatch, kind='pilot')
    w.flow_action = pilot_flow
    w.pilot_report = api_extras.PilotReport(report, 400 if report == 'rejected' else 200, 'peak_mb: too low')
    result = w.run()
    assert result.released is released and 'upload' not in w.names()
    body = next(call[1] for call in w.calls if call[0] == 'pilot')
    assert body['peak_mb'] == 8102 and body['frames'] == 3 and len(body['overrides_sha256']) == 64
    assert (body['game_overrides'], body['texture_streaming']) == ('implicit', 'off')  # the prep's (FR-GC-8)
    if report == 'rejected':
        assert result.error_message == 'Pilot result rejected by the manager: peak_mb: too low'


def test_pilot_failure_messages_are_prefixed(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, kind='pilot')
    w.flow_action = lambda job: (_ for _ in ()).throw(JobFailure('No camera cut found in /Game/S; cannot measure.'))
    result = w.run()
    assert result.error_message == 'Pilot render failed: No camera cut found in /Game/S; cannot measure.'
