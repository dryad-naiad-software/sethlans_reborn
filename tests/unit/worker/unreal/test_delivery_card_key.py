# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""A pilot whose measured card has no card key is released, never reported or blamed on the project.

A backstop: the reserver refuses such a pilot before the render (FR-GU-10).
"""
import dataclasses
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


def _flow(card_key, game_overrides='preset', texture_streaming='fully_load'):
    metrics = PilotMetrics(peak_mb=8102, peak_source='card', card_delta_peak_mb=8102, csv_local_used_peak_mb=None,
                           baseline_used_mb=900, baseline_drift_mb=0, peak_phase='render', gpu_index=0,
                           max_sample_gap_s=1.0)
    gpu = GpuMem(0, 'Test Card A', 16384, 900, None, None, card_key)
    prep = SimpleNamespace(pilot_frames=(0, 1), game_overrides=game_overrides, texture_streaming=texture_streaming)
    return FlowResult(prep=prep, render_s=60.0, prep_s=10.0, warmup_s=30.0,
                      warmup_ran=True, render_time_seconds=60, metrics=metrics, gate_reading=reading(gpus=(gpu,)),
                      card=gpu)


@pytest.fixture()
def calls():
    return []


def _delivery(calls):
    deps = DeliveryDeps(release_job=lambda job_id, reason: calls.append(('release', job_id, reason)),
                        mark_unreachable=lambda reason: calls.append(('unreachable', reason)),
                        upload=lambda *args: calls.append(('upload',)),
                        report_pilot=lambda job_id, body: (calls.append(('pilot', body)),
                                                           api_extras.PilotReport('done', 200, ''))[1],
                        report_shots=lambda job_id, body: calls.append(('shots', body)),
                        upload_frame=lambda *args: calls.append(('frame',)),
                        report_piece=lambda job_id, body: calls.append(('piece', body)), upload_sound=None)
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


@pytest.mark.parametrize('game_overrides,texture_streaming', [
    ('preset', 'fully_load'), ('implicit', 'off'), ('none', 'unchanged'), ('unknown', 'unknown')])
def test_the_report_carries_what_the_prep_said_the_render_ran_with(calls, game_overrides, texture_streaming):
    """FR-GC-8: both values always reach pilot-result/, ``unknown`` included."""
    _delivery(calls).deliver(Outcome('success', flow=_flow(KEY, game_overrides, texture_streaming)), integrity_ok=True)
    [(_name, body)] = calls
    assert (body['game_overrides'], body['texture_streaming']) == (game_overrides, texture_streaming)


def test_the_report_names_the_flows_card_never_a_gate_reading_lookup(calls):
    """D22: ``FlowResult.card`` is the job's card; the gate reading's card at the peak's index is another one."""
    job_card = GpuMem(1, 'Radeon Test 24G', 24560, 300, None, None, 'pci:0000:41:00.0')
    flow = dataclasses.replace(_flow(KEY), card=job_card)   # gate reading: index 0 is 'Test Card A' (KEY)
    assert flow.metrics.gpu_index == 0 and flow.gate_reading.gpus[0].card_key == KEY
    _delivery(calls).deliver(Outcome('success', flow=flow), integrity_ok=True)
    [(_name, body)] = calls
    assert body['gpu'] == {'index': 1, 'name': 'Radeon Test 24G', 'total_mb': 24560,
                           'card_key': 'pci:0000:41:00.0'}


def test_no_card_fails_the_pilot_in_brand_neutral_words(calls):
    flow = dataclasses.replace(_flow(KEY), card=None)
    result = _delivery(calls).deliver(Outcome('success', flow=flow), integrity_ok=True)
    assert calls == [] and result.success is False and result.released is False
    assert result.error_message == 'Pilot render failed: the card of the measured peak is not in the GPU memory reading.'
    assert all('nvidia' not in text.lower() for text in (result.error_message, NO_CARD_KEY_MESSAGE))
