# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``pilot_flow`` (Unreal per-machine memory test D6, FR-MT-V-1, FR-MT-G-4).

``after_failure``: a pilot whose warm-up or render launch failed **after a saturated sample** reports
``does_not_fit`` with the figures sampled until the failure and ends DONE; without a saturated sample, for any
other job kind, or when the figures cannot be tied to the card, the original failure is raised again.

``driver``: the render launch's log gives the driver; the warm-up log only when the render log has no adapter
lines and only when that warm-up ran on the render's card; ``driver_version=unknown`` in the summary otherwise.
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import job_flow, pilot_flow
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.summary import Summary
from tests.unit.worker.unreal._executor_fakes import outcome, reading, trace
from tests.unit.worker.unreal._job_run_world import World, pilot_flow as world_pilot_flow, png_flow
from tests.unit.worker.unreal.test_job_flow_card import BUSY, IDLE, IDLE_KEY, Chooser, _flow

P = '[2026.11.17-18.27.59:807][  0]'
DRIVER = '32.0.22029.1019'
FOUND = f'{P}LogD3D12RHI: Found D3D12 adapter 1: Radeon Test 24G (VendorId: 1002, DeviceId: 744c\n'
CHOSEN = f'{P}LogD3D12RHI: Chosen D3D12 Adapter Id = 1\n'


def driver_line(value=DRIVER):
    return f'{P}LogD3D12RHI:   Driver Version: 25.10.1 (internal:{value}, unified:25.10.1)\n'


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return path


LINE = 22840                      # floor(24560 * 0.93), the IDLE card


def launch(tmp_path, phase, max_used, attempt=1, chosen=1, text=FOUND + driver_line() + CHOSEN):
    run = SimpleNamespace(phase=phase, pre={1: 300}, post={1: 300},
                          outcome=outcome(vram=trace({1: max_used}, total=24560)),
                          log_path=write(tmp_path, f'{phase}.{attempt}.log', text))
    run.outcome.adapter_chosen = chosen
    return run


def failing_flow(tmp_path, runs, kind='pilot', choice_key=IDLE_KEY, failure=None):
    """A ``UeFlow`` whose render launch failed (``failure``) after ``runs``; the warm-ups ran on ``choice_key``."""
    flow = _flow(Chooser(None), IDLE_KEY, kind=kind, gate_reading=reading(gpus=(BUSY, IDLE)), tmp_path=tmp_path)
    flow.choice = Choice(42, choice_key, 'Radeon Test 24G', 1)
    flow.prep = lambda: SimpleNamespace(pilot_frames=(0, 50))
    flow.runner = SimpleNamespace(runs=runs)
    flow.warmup_logs = [(choice_key, run.log_path) for run in runs if run.phase == 'warmup']
    error = failure or JobFailure("Unreal render failed: the process exited with code 3.")
    flow.failure = error

    def fail(prep):
        raise error
    flow._warm_if_needed = lambda prep: True
    flow._render_with_retry = fail
    return flow


class TestAfterFailure:

    def test_a_render_that_failed_after_a_saturated_sample_does_not_fit(self, tmp_path):
        runs = [launch(tmp_path, 'warmup', 9000), launch(tmp_path, 'render', LINE)]
        flow = failing_flow(tmp_path, runs)
        result = flow.run()
        assert (result.metrics.verdict, result.metrics.saturated) == ('does_not_fit', True)
        assert result.metrics.lower_bound_mb == result.metrics.peak_mb == LINE - 300
        assert result.card is IDLE and result.adapter == 'Radeon Test 24G' and result.driver_version == DRIVER
        assert result.warmup_ran is True and result.outputs == []
        assert flow.summary.get('pilot_verdict') == 'does_not_fit' and flow.summary.get('driver_source') == 'render'
        assert f'pilot_failed_saturated: {flow.failure}' in flow.summary.warnings()

    def test_a_warm_up_that_failed_after_a_saturated_sample_does_not_fit(self, tmp_path):
        """No render launch: the adapter and the driver come from the warm-up on the same card."""
        flow = failing_flow(tmp_path, [launch(tmp_path, 'warmup', LINE + 5)])
        result = flow.run()
        assert result.metrics.verdict == 'does_not_fit' and result.metrics.peak_phase == 'warmup'
        assert result.adapter == 'Radeon Test 24G' and result.driver_version == DRIVER
        assert flow.summary.get('driver_source') == 'warmup'

    def test_without_a_saturated_sample_the_failure_stands(self, tmp_path):
        flow = failing_flow(tmp_path, [launch(tmp_path, 'warmup', 9000), launch(tmp_path, 'render', LINE - 1)])
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert info.value is flow.failure and flow.summary.get('pilot_verdict') == ''

    def test_a_render_job_is_never_measured(self, tmp_path):
        flow = failing_flow(tmp_path, [launch(tmp_path, 'render', LINE)], kind='render')
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert info.value is flow.failure

    def test_a_launch_without_the_chosen_line_keeps_the_failure(self, tmp_path):
        """The failed launch's samples must be the card's too: without its adapter line they are not tied to it."""
        runs = [launch(tmp_path, 'warmup', 9000), launch(tmp_path, 'render', LINE, chosen=None)]
        flow = failing_flow(tmp_path, runs)
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert info.value is flow.failure and info.value.__cause__ is None

    def test_no_adapter_name_in_any_log_keeps_the_failure(self, tmp_path):
        runs = [launch(tmp_path, 'warmup', 9000, text='nothing\n'), launch(tmp_path, 'render', LINE, text='x\n')]
        with pytest.raises(JobFailure) as info:
            failing_flow(tmp_path, runs).run()
        assert str(info.value) == "Unreal render failed: the process exited with code 3."

    def test_a_card_not_in_the_reading_keeps_the_failure(self, tmp_path):
        flow = failing_flow(tmp_path, [launch(tmp_path, 'render', LINE)], choice_key='pci:0000:c1:00.0')
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert info.value is flow.failure

    def test_an_implausible_peak_keeps_the_failure(self, tmp_path):
        """A full card whose rise over the baseline is below 1,024 MB cannot be a render's figure."""
        run = launch(tmp_path, 'render', LINE)
        run.pre = run.post = {1: LINE - 500}
        flow = failing_flow(tmp_path, [run])
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert info.value is flow.failure and flow.summary.get('pilot_verdict') == ''

    def test_a_retry_failure_after_saturation_also_does_not_fit(self, tmp_path):
        """The FR-UE-10 second attempt's failure is a ``JobFailure`` like any launch failure."""
        runs = [launch(tmp_path, 'render', LINE), launch(tmp_path, 'warmup', 9000, attempt=2),
                launch(tmp_path, 'render', 12000, attempt=2)]
        flow = failing_flow(tmp_path, runs, failure=JobFailure("Unreal render failed after its retry."))
        result = flow.run()
        assert result.metrics.verdict == 'does_not_fit' and result.metrics.peak_mb == LINE - 300


class _Stop(Exception):
    """Ends a warm-up at its launch."""


def _stop(*args, **kwargs):
    raise _Stop()


class TestDriver:

    def _flow(self, choice_key=IDLE_KEY, warmups=()):
        return SimpleNamespace(choice=Choice(42, choice_key, 'Radeon Test 24G', 1), warmup_logs=list(warmups),
                               summary=Summary())

    def test_the_render_log_gives_the_driver(self, tmp_path):
        render = write(tmp_path, 'render.1.log', FOUND + driver_line() + CHOSEN)
        warm = write(tmp_path, 'warmup.1.log', FOUND + driver_line('1.2.3.4') + CHOSEN)
        flow = self._flow(warmups=[(IDLE_KEY, warm)])
        assert pilot_flow.driver(flow, render) == DRIVER
        assert (flow.summary.get('driver_version'), flow.summary.get('driver_source')) == (DRIVER, 'render')

    def test_a_render_log_without_adapter_lines_reads_the_same_cards_warm_up(self, tmp_path):
        """Card keys compare by address: the warm-up's padded spelling is the same card."""
        render = write(tmp_path, 'render.1.log', 'no adapter lines\n')
        warm = write(tmp_path, 'warmup.1.log', FOUND + driver_line() + CHOSEN)
        flow = self._flow(warmups=[('pci:00000000:41:00.0', warm)])
        assert pilot_flow.driver(flow, render) == DRIVER and flow.summary.get('driver_source') == 'warmup'

    def test_a_warm_up_on_another_card_is_never_read(self, tmp_path):
        render = write(tmp_path, 'render.1.log', 'no adapter lines\n')
        warm = write(tmp_path, 'warmup.1.log', FOUND + driver_line() + CHOSEN)
        flow = self._flow(warmups=[('pci:0000:0a:00.0', warm)])
        assert pilot_flow.driver(flow, render) is None
        assert (flow.summary.get('driver_version'), flow.summary.get('driver_source')) == ('unknown', '')

    def test_the_last_warm_up_on_the_card_is_read(self, tmp_path):
        render = write(tmp_path, 'render.1.log', 'no adapter lines\n')
        first = write(tmp_path, 'warmup.1.log', FOUND + driver_line('1.1.1.1') + CHOSEN)
        second = write(tmp_path, 'warmup.2.log', FOUND + driver_line('2.2.2.2') + CHOSEN)
        flow = self._flow(warmups=[(IDLE_KEY, first), (IDLE_KEY, second), ('pci:0000:0a:00.0', render)])
        assert pilot_flow.driver(flow, render) == '2.2.2.2'

    @pytest.mark.parametrize('text,source', [
        (FOUND + CHOSEN, 'render'), (FOUND + driver_line('Unknown') + CHOSEN, 'render'), ('nothing\n', ''),
    ])
    def test_a_missing_or_unknown_driver_is_unknown_in_the_summary(self, tmp_path, text, source):
        flow = self._flow()
        assert pilot_flow.driver(flow, write(tmp_path, 'render.1.log', text)) is None
        assert (flow.summary.get('driver_version'), flow.summary.get('driver_source')) == ('unknown', source)

    def test_no_log_at_all_is_unknown_and_warned(self, caplog):
        flow = self._flow()
        assert pilot_flow.driver(flow, None) is None
        assert flow.summary.get('driver_version') == 'unknown'
        assert 'The graphics driver version is unknown (no warm-up or render log)' in caplog.text

    def test_the_flow_records_each_warm_up_with_its_card(self, monkeypatch, tmp_path):
        """``UeFlow.warm_up`` lists ``(card key, log)`` before the launch (the driver's fallback log)."""
        flow = _flow(Chooser(Choice(42, IDLE_KEY, 'Radeon Test 24G', 1)), IDLE_KEY, kind='pilot', tmp_path=tmp_path)
        flow.job = SimpleNamespace(warmup_params=tmp_path / 'w.json', warmup_result=tmp_path / 'r.json',
                                   script=lambda name: tmp_path / name,
                                   log=lambda phase, attempt: tmp_path / f'{phase}.{attempt}.log')
        flow.config = SimpleNamespace(step_frames=1, step_seconds=1, hold_seconds=1)
        flow.builders = SimpleNamespace(build_warmup=lambda *args: 'launch')
        flow.runner = SimpleNamespace(launch=_stop)
        monkeypatch.setattr(job_flow.queue_prep, 'warmup_params', lambda *args, **kwargs: {})
        monkeypatch.setattr(job_flow.queue_prep, 'write_json', lambda path, data: None)
        with pytest.raises(_Stop):
            flow.warm_up(SimpleNamespace(pilot_frames=(0,)), 1)
        assert flow.warmup_logs == [(IDLE_KEY, tmp_path / 'warmup.1.log')]


class TestResultPeakWiring:
    """FR-MT-G-4: ``job_run`` hands the flow the peak of the result the gate chose."""

    def test_a_render_gets_its_gate_results_peak(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch)
        w.flow_action = png_flow
        assert w.run().success and w.flow.result_peak_mb == 7930

    def test_a_pilot_gets_none(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch, kind='pilot')
        w.flow_action = world_pilot_flow
        w.run()
        assert w.flow.result_peak_mb is None

    def test_the_render_summary_uses_it(self, monkeypatch):
        seen = []
        monkeypatch.setattr(job_flow.log_summary, 'render_values', lambda scan, merged: ({}, []))
        monkeypatch.setattr(job_flow.log_summary, 'vram_values',
                            lambda params, peak, result_peak: seen.append((peak, result_peak)) or {})
        flow = _flow(Chooser(None), IDLE_KEY)
        flow.result_peak_mb = 7930
        flow._render_summary(SimpleNamespace(outcome=outcome(peak=8100)), SimpleNamespace(scan=None))
        assert seen == [(8100, 7930)]
