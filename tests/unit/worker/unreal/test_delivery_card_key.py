# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""A pilot whose measured card has no card key is released, never reported or blamed on the project."""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.unreal_executor.delivery import NO_CARD_KEY_MESSAGE, Delivery, DeliveryDeps, Outcome
from sethlans_worker_agent.unreal_executor.job_flow import FlowResult
from sethlans_worker_agent.unreal_executor.pilot import PilotMetrics
from sethlans_worker_agent.unreal_executor.summary import Summary
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.unreal._executor_fakes import params, reading

KEY = 'pci:0000:0a:00.0'


def _flow(card_key):
    metrics = PilotMetrics(peak_mb=8102, peak_source='card', card_delta_peak_mb=8102, csv_local_used_peak_mb=None,
                           baseline_used_mb=900, baseline_drift_mb=0, peak_phase='render', gpu_index=0,
                           max_sample_gap_s=1.0)
    gpu = GpuMem(0, 'Test Card A', 16384, 900, None, None, card_key)
    return FlowResult(prep=SimpleNamespace(pilot_frames=(0, 1)), render_s=60.0, prep_s=10.0, warmup_s=30.0,
                      warmup_ran=True, render_time_seconds=60, metrics=metrics, gate_reading=reading(gpus=(gpu,)))


@pytest.fixture()
def calls():
    return []


def _delivery(calls):
    deps = DeliveryDeps(release_job=lambda job_id, reason: calls.append(('release', job_id, reason)),
                        mark_unreachable=lambda reason: calls.append(('unreachable', reason)),
                        upload=lambda *args: calls.append(('upload',)),
                        report_pilot=lambda job_id, body: (calls.append(('pilot', body)),
                                                           api_extras.PilotReport('done', 200, ''))[1])
    return Delivery(params(kind='pilot'), deps, Summary(), after_report=None)


def test_no_card_key_releases_the_job_without_reporting(calls, caplog):
    with caplog.at_level(logging.WARNING, logger='sethlans_worker_agent.unreal_executor.delivery'):
        result = _delivery(calls).deliver(Outcome('success', flow=_flow(None)), integrity_ok=True)
    assert calls == [('release', 42, 'local_conflict')]
    assert result.released is True and result.success is False and result.error_message == ''
    [line] = [record.getMessage() for record in caplog.records]
    assert NO_CARD_KEY_MESSAGE in line and 'project' not in NO_CARD_KEY_MESSAGE.lower()


def test_a_card_key_reports_the_result_with_it(calls):
    result = _delivery(calls).deliver(Outcome('success', flow=_flow(KEY)), integrity_ok=True)
    [(name, body)] = calls
    assert name == 'pilot' and body['gpu']['card_key'] == KEY
    assert result.success is True and result.released is True
