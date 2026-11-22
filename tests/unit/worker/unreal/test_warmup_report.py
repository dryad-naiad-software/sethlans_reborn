# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The memory report after a warm-up launch, and the summary's ``vram_job_peak_phase`` (Unreal per-machine memory
test v2, Q11, D6).

A render (not pilot) or audio job's memory report is due once a warm-up **or** render launch ran, so a cold
warm-up that fails before any render launch still feeds D6: its ``job_peak_mb`` is the warm-up's, the spill
fields are null (only a render launch has a spill watch) and the driver comes from the warm-up log.
``vram_job_peak_phase`` names the launch that gave ``job_peak_mb`` (``job_memory.job_peak``): ``warmup`` only
when it peaked strictly above every render launch, ``render`` on a tie, ``''`` without a figure (0 included).
"""
import logging

import pytest

from sethlans_worker_agent.unreal_executor import job_memory, memory_report
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.summary import Summary
from tests.unit.worker.unreal._executor_fakes import KIND, params
from tests.unit.worker.unreal._memory_report_fakes import CARD_KEY, DRIVER, job_run, render_run, watch
from tests.unit.worker.unreal.sequence._pieces import audio_params, piece_params
from tests.unit.worker.unreal.test_memory_report_paths import memory_calls, throw, world

WARM_FAILED = "Unreal warm-up failed (exit code 3221225477, exited)."


def warm_up(tmp_path, max_job=12000, attempt=1):
    return render_run(tmp_path, attempt, max_job=max_job, phase='warmup')


def on_the_card(jr, run):
    """The flow's choice and warm-up log on the held card, as ``UeFlow.warm_up`` leaves them."""
    jr.flow.choice = Choice(42, CARD_KEY, 'Radeon Test 24G', 1)
    jr.flow.warmup_logs = [(CARD_KEY, run.log_path)]
    return jr


class TestDue:

    @pytest.mark.parametrize('p', [params(), piece_params(), audio_params()], ids=['render', 'piece', 'audio'])
    def test_a_warm_up_launch_alone_makes_it_due(self, tmp_path, p):
        runs = [render_run(tmp_path, phase='prep'), warm_up(tmp_path)]
        assert memory_report.due(p, job_run(runs).runner) is True

    @pytest.mark.parametrize('p', [params(kind='pilot'), params(kind='shots', frame=0)], ids=['pilot', 'shots'])
    def test_never_for_a_pilot_or_a_shots_job(self, tmp_path, p):
        assert memory_report.due(p, job_run([warm_up(tmp_path), render_run(tmp_path)]).runner) is False

    def test_the_prep_alone_is_not_due(self, tmp_path):
        assert memory_report.due(params(), job_run([render_run(tmp_path, max_job=9000, phase='prep')]).runner) is False


class TestWarmUpOnlyBody:

    def test_the_warm_ups_peak_with_null_spill_fields(self, tmp_path):
        run = warm_up(tmp_path)
        run.outcome.vram.spill = watch(spilled=True, cause='self', shared=4000)  # never a warm-up's figure
        jr = on_the_card(job_run([render_run(tmp_path, max_job=30000, phase='prep'), run]), run)
        assert memory_report.body(jr) == {
            'measurement_id': 7, 'card_kind': KIND, 'card_key': CARD_KEY, 'driver_version': DRIVER,
            'spilled': None, 'spill_cause': None, 'shared_peak_mb': None, 'job_peak_mb': 12000}

    def test_the_driver_comes_from_the_warm_up_log(self, tmp_path):
        run = render_run(tmp_path, max_job=12000, phase='warmup', driver='33.0.1.2')
        jr = on_the_card(job_run([run]), run)
        assert memory_report.body(jr)['driver_version'] == '33.0.1.2'
        assert jr.summary.get('driver_version') == '33.0.1.2' and jr.summary.get('driver_changed') == '1'

    def test_a_warm_up_with_nothing_attributed_sends_null(self, tmp_path):
        run = warm_up(tmp_path, max_job=0)
        assert memory_report.body(on_the_card(job_run([run]), run))['job_peak_mb'] is None


class TestFailedWarmUpEndPath:

    def _warm_then_fail(self, w, tmp_path, max_job=12000):
        def action(job):
            run = warm_up(tmp_path, max_job=max_job)
            w.flow.choice, w.flow.summary = Choice(42, w.card_key, 'Radeon Test 24G', 1), Summary()
            w.flow.warmup_logs = [(w.card_key, run.log_path)]
            w.runner.runs.append(run)
            raise JobFailure(WARM_FAILED)
        return action

    def test_a_job_failing_in_its_warm_up_still_reports_its_peak(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = self._warm_then_fail(w, tmp_path)
        result = w.run()
        assert not result.success and not result.released and result.error_message == WARM_FAILED
        assert w.names()[-2:] == ['finish', 'memory']   # before the ERROR report
        [(_, job_id, body, kwargs)] = memory_calls(w)
        assert job_id == 42 and kwargs == {}
        assert (body['job_peak_mb'], body['spilled'], body['spill_cause'], body['shared_peak_mb']) == \
            (12000, None, None, None)
        assert body['driver_version'] == DRIVER and body['card_key'] == w.card_key
        assert 'memory_report=sent' in result.stdout and 'vram_job_peak_phase=warmup' in result.stdout

    def test_a_pilot_failing_in_its_warm_up_sends_none(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch, kind='pilot')
        w.flow_action = self._warm_then_fail(w, tmp_path)
        w.run()
        assert memory_calls(w) == []

    def test_a_job_failing_before_its_warm_up_sends_none(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = throw(JobFailure('Unreal prep pass failed (exit code 3).'))
        result = w.run()
        assert memory_calls(w) == [] and 'memory_report' not in result.stdout
        assert 'vram_job_peak_phase=' in result.stdout and 'vram_job_peak_phase=warmup' not in result.stdout


class TestPeakPhase:

    @pytest.mark.parametrize('peaks,expected', [
        ([('warmup', 12000), ('render', 7100)], (12000, 'warmup')),
        ([('warmup', 6000), ('render', 7100)], (7100, 'render')),
        ([('warmup', 7100), ('render', 7100)], (7100, 'render')),       # a tie goes to the render
        ([('render', 7100), ('warmup', 7100)], (7100, 'render')),       # whatever the order
        ([('render', 7000), ('warmup', 8000), ('render', 7100)], (8000, 'warmup')),   # the retry's warm-up
        ([('warmup', 9000)], (9000, 'warmup')),                         # no render launch
        ([('prep', 30000), ('render', 7100)], (7100, 'render')),        # the prep never counts
        ([('prep', 30000)], (None, '')),
        ([('warmup', 0), ('render', 0)], (0, '')),                      # nothing attributed: no phase
        ([], (None, '')),
    ])
    def test_the_launch_that_gave_the_peak(self, tmp_path, peaks, expected):
        runs = [render_run(tmp_path, n, max_job=mb, phase=phase) for n, (phase, mb) in enumerate(peaks, start=1)]
        assert job_memory.job_peak(runs) == expected
        assert job_memory.job_peak_mb(runs) == expected[0]
        assert job_memory.summary_values(runs)['vram_job_peak_phase'] == expected[1]

    def test_a_launch_without_samples_has_no_phase(self, tmp_path):
        runs = [render_run(tmp_path, phase='warmup'), render_run(tmp_path)]
        assert job_memory.job_peak(runs) == (None, '')
        assert job_memory.summary_values(runs)['vram_job_peak_phase'] == ''

    def test_every_job_carries_the_key(self):
        assert job_memory.summary_values(())['vram_job_peak_phase'] == ''

    @pytest.mark.parametrize('runs,word', [
        (lambda t: [warm_up(t), render_run(t, max_job=7100)], 'warmup'),
        (lambda t: [warm_up(t, max_job=6000), render_run(t, max_job=7100)], 'render'),
        (lambda t: [render_run(t)], 'none'),
    ])
    def test_the_sent_line_names_the_phase(self, tmp_path, caplog, runs, word):
        jr = job_run(runs(tmp_path))
        with caplog.at_level(logging.INFO, logger=memory_report.__name__):
            memory_report.send(jr)
        assert jr.summary.get('memory_report') == 'sent'
        assert f'Unreal job 42 memory report sent (job peak from the {word} launch): ' in caplog.text
