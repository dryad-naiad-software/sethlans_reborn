# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Other programs' use of the pilot's card (Unreal per-machine memory test, owner decision Q10; D9's ``others``).

``others_rise(runs)``: the largest ``max_others[gpu] - run.pre[gpu]`` over every sampled launch (prep, warm-up,
render) with its phase; ``None`` when no launch has both a pre-launch reading and per-process samples.
``check_others``, three bands against ``unreal.pilot_others_margin_mb`` (default 512, read at call time; the spill
margin ``unreal.spill_margin_mb`` plays no part): (1) a rise up to the margin returns ``None`` (the result stands);
(2) a larger rise while no sample reached the card's 93% line returns the highest other-programs figure over the
checked launches (``pilot_others.top_others``; ``pilot.measure`` adds the part not already counted; an INFO line,
no warning); (3) a larger rise with a saturated sample records no verdict: the pilot is released
(``LocalConflict('local_conflict')``) with a WARNING and the summary warning ``pilot_others_rose``.
``pilot_others_rise_mb`` is set either way (empty when not checked). ``collect`` checks before the CSV capture and
the measurement; ``after_failure`` before a saturated failure becomes ``does_not_fit`` (the card was full, so a
rise past the margin is band 3), and never for an ``IsolationFailure`` or a failure without a saturated sample.
Band 2 end to end (the verdict from the adjusted peak) is ``test_pilot_others_bands``.
"""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import job_flow, pilot_flow
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.outcomes import IsolationFailure, JobFailure, LocalConflict
from sethlans_worker_agent.unreal_executor.summary import Summary
from tests.unit.worker.unreal._executor_fakes import phase_run, reading, trace
from tests.unit.worker.unreal.test_job_flow_card import BUSY, IDLE, IDLE_KEY, METRICS, Chooser, _flow
from tests.unit.worker.unreal.test_pilot_flow_failure import LINE, failing_flow, launch

MARGIN = 512
FULL = 30324                      # floor(32607 * 0.93): the card's usable line, D6's saturated sample
LOGGER = pilot_flow.__name__


@pytest.fixture(autouse=True)
def _margin(monkeypatch):
    """The default margins, whatever the developer's own config says."""
    monkeypatch.setattr(config_unreal, 'PILOT_OTHERS_MARGIN_MB', MARGIN)
    monkeypatch.setattr(config_unreal, 'SPILL_MARGIN_MB', 256)


def run(phase, pre, others, used=5000):
    """A sampled launch whose card was at ``pre`` before it, peaked at ``used`` and others' use at ``others``."""
    result = phase_run(phase, pre, pre, trace({0: used}))
    result.outcome.vram.max_others = others
    return result


def flow_stub():
    return SimpleNamespace(summary=Summary(), params=SimpleNamespace(job_id=42))


def message(rise, phase, margin=MARGIN):
    return (f"other programs' use of the pilot's graphics card rose by {rise} MB during the {phase} (more than the "
            f"{margin} MB margin) and the card came close to full, so the measurement is not the pilot's own; it "
            "runs again later.")


def added_message(rise, phase, margin=MARGIN, top=0):
    return (f"other programs' use of the pilot's graphics card rose by {rise} MB during the {phase} (more than the "
            f"{margin} MB margin) while the card stayed clear of full; the part of their use (up to {top} MB) not "
            "already in the measurement is added to the pilot's peak.")


class TestOthersRise:

    def test_the_largest_rise_over_every_phase_with_its_phase(self):
        runs = [run('prep', {0: 400}, {0: 500}), run('warmup', {0: 400}, {0: 900}), run('render', {0: 400}, {0: 700})]
        assert pilot_flow.others_rise(runs) == (500, 'warmup')

    def test_each_launch_counts_from_its_own_pre_launch_figure(self):
        runs = [run('warmup', {0: 2000}, {0: 2400}), run('render', {0: 400}, {0: 1000})]
        assert pilot_flow.others_rise(runs) == (600, 'render')

    def test_a_prep_rise_is_reported_as_prep(self):
        assert pilot_flow.others_rise([run('prep', {0: 0}, {0: 300}), run('render', {0: 0}, {0: 10})]) == (300, 'prep')

    def test_a_fall_is_a_rise_of_zero(self):
        assert pilot_flow.others_rise([run('render', {0: 1000}, {0: 200})]) == (0, 'render')

    def test_a_launch_without_a_pre_launch_reading_is_left_out(self):
        assert pilot_flow.others_rise([run('render', None, {0: 9000})]) is None
        assert pilot_flow.others_rise([run('warmup', None, {0: 9000}), run('render', {0: 400}, {0: 450})]) == \
            (50, 'render')

    @pytest.mark.parametrize('pre,others', [({0: 400}, {}), ({1: 400}, {0: 9000})],
                             ids=['no_per_process_data', 'card_not_in_the_pre_launch_reading'])
    def test_nothing_to_compare_is_unknown(self, pre, others):
        assert pilot_flow.others_rise([run('render', pre, others)]) is None

    def test_a_trace_without_the_field_and_no_runs_are_unknown(self):
        bare = SimpleNamespace(phase='render', pre={0: 400}, outcome=SimpleNamespace(vram=SimpleNamespace()))
        assert pilot_flow.others_rise([bare]) is None and pilot_flow.others_rise([]) is None


class TestCheckOthers:

    @pytest.mark.parametrize('rise', [0, 257, MARGIN - 1, MARGIN], ids=['none', 'desktop_257', 'below', 'exactly'])
    def test_band_1_up_to_the_margin_the_result_stands(self, caplog, rise):
        """A real desktop rise of 257 MB that the old 256 MB margin released."""
        flow = flow_stub()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            assert pilot_flow.check_others(flow, [run('render', {0: 400}, {0: 400 + rise}, used=FULL)]) is None
        assert flow.summary.get('pilot_others_rise_mb') == str(rise) and flow.summary.warnings() == ()
        assert [r for r in caplog.records if r.name == LOGGER] == []

    @pytest.mark.parametrize('rise', [MARGIN + 1, 4000])
    def test_band_2_past_the_margin_with_the_card_clear_returns_the_top_figure(self, caplog, rise):
        """The highest absolute figure (pre 400 + rise), not the rise: ``pilot_others`` takes out what is counted."""
        flow = flow_stub()
        runs = [run('warmup', {0: 400}, {0: 400 + rise}, used=FULL - 1), run('render', {0: 400}, {0: 400})]
        with caplog.at_level(logging.INFO, logger=LOGGER):
            assert pilot_flow.check_others(flow, runs) == 400 + rise
        assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == \
            [(logging.INFO, f"Pilot job 42: {added_message(rise, 'warm-up', top=400 + rise)}")]
        assert flow.summary.get('pilot_others_rise_mb') == str(rise) and flow.summary.warnings() == ()

    def test_band_3_one_mb_over_the_margin_with_the_card_full_releases_with_no_verdict(self, caplog):
        flow = flow_stub()
        runs = [run('warmup', {0: 400}, {0: 400 + MARGIN + 1}), run('render', {0: 0}, {}, used=FULL)]
        with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(LocalConflict) as info:
            pilot_flow.check_others(flow, runs)
        expected = message(MARGIN + 1, 'warm-up')
        assert (info.value.reason, info.value.message) == ('local_conflict', expected)
        assert [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING] == \
            [f"Pilot job 42 records no verdict: {expected}"]
        assert flow.summary.warnings() == (f"pilot_others_rose: {expected}"[:300],)
        assert flow.summary.get('pilot_others_rise_mb') == str(MARGIN + 1)

    @pytest.mark.parametrize('phase', ['render', 'prep'])
    def test_the_message_names_the_phase(self, phase):
        with pytest.raises(LocalConflict) as info:
            pilot_flow.check_others(flow_stub(), [run(phase, {0: 0}, {0: 4000}, used=FULL + 500)])
        assert info.value.message == message(4000, phase)

    @pytest.mark.parametrize('pre,others', [(None, {0: 30000}), ({0: 400}, {})],
                             ids=['no_pre_launch_reading', 'no_per_process_data'])
    def test_an_unknown_rise_is_not_checked_and_left_empty(self, pre, others):
        flow = flow_stub()
        assert pilot_flow.check_others(flow, [run('render', pre, others, used=FULL)]) is None
        assert flow.summary.get('pilot_others_rise_mb', 'unset') == '' and flow.summary.warnings() == ()
        assert '[sethlans-unreal] pilot_others_rise_mb=\n' in flow.summary.text() + '\n'

    @pytest.mark.parametrize('margin,rise,over', [
        (100, 100, False), (100, 101, True), (1000, 900, False), (0, 0, False), (0, 1, True),
    ])
    def test_the_margin_is_read_at_call_time(self, monkeypatch, margin, rise, over):
        monkeypatch.setattr(config_unreal, 'PILOT_OTHERS_MARGIN_MB', margin)
        assert pilot_flow.check_others(flow_stub(), [run('render', {0: 400}, {0: 400 + rise})]) == \
            (400 + rise if over else None)
        full = [run('render', {0: 400}, {0: 400 + rise}, used=FULL)]
        if not over:
            assert pilot_flow.check_others(flow_stub(), full) is None
            return
        with pytest.raises(LocalConflict) as info:
            pilot_flow.check_others(flow_stub(), full)
        assert info.value.message == message(rise, 'render', margin)

    @pytest.mark.parametrize('spill', [0, 1, 256, 65536])
    def test_the_spill_margin_plays_no_part(self, monkeypatch, spill):
        """``unreal.spill_margin_mb`` stays D9's spill margin; the pilot reads its own setting."""
        monkeypatch.setattr(config_unreal, 'SPILL_MARGIN_MB', spill)
        assert pilot_flow.check_others(flow_stub(), [run('render', {0: 400}, {0: 400 + 300}, used=FULL)]) is None
        with pytest.raises(LocalConflict):
            pilot_flow.check_others(flow_stub(), [run('render', {0: 400}, {0: 400 + MARGIN + 1}, used=FULL)])


def with_others(launch_run, rise):
    """The launch's card rose by ``rise`` MB of other programs' use over its pre-launch 300 MB."""
    launch_run.outcome.vram.max_others = {1: 300 + rise}
    return launch_run


def collecting_flow(monkeypatch, tmp_path, rise, calls, warm_used=9000):
    """A pilot whose render succeeded; the warm-up saw others' use rise by ``rise``; CSV and measure recorded."""
    warm = with_others(launch(tmp_path, 'warmup', warm_used), rise)
    render = with_others(launch(tmp_path, 'render', 9000), 0)
    flow = _flow(Chooser(None), IDLE_KEY, kind='pilot', gate_reading=reading(gpus=(BUSY, IDLE)), tmp_path=tmp_path)
    flow.choice = Choice(42, IDLE_KEY, 'Radeon Test 24G', 1)
    flow.prep = lambda: SimpleNamespace(pilot_frames=(0, 50))
    flow._warm_if_needed = lambda prep: True
    flow._render_with_retry = lambda prep: (render, SimpleNamespace(scan=None), frozenset())
    flow._render_summary = lambda run, check: None
    flow.runner = SimpleNamespace(runs=[warm, render])
    monkeypatch.setattr(job_flow, 'render_check_time', lambda check, render_s: 60)
    monkeypatch.setattr(job_flow.output, 'collect_pilot', lambda *args: [])
    monkeypatch.setattr(job_flow.pilot_csv, 'collect', lambda *args: calls.append('csv'))
    monkeypatch.setattr(job_flow.pilot, 'measure',
                        lambda runs, csv_peak, others_top: calls.append(('measure', others_top)) or METRICS)
    return flow


class TestCollect:

    def test_others_past_the_margin_with_the_card_full_release_before_the_measurement(self, monkeypatch, tmp_path):
        calls = []
        flow = collecting_flow(monkeypatch, tmp_path, MARGIN + 1, calls, warm_used=LINE)
        with pytest.raises(LocalConflict) as info:
            flow.run()
        assert info.value.message == message(MARGIN + 1, 'warm-up') and calls == []
        assert flow.summary.get('pilot_verdict') == '' and flow.summary.get('driver_version') == ''

    def test_others_at_the_margin_are_measured_with_nothing_added(self, monkeypatch, tmp_path):
        calls = []
        flow = collecting_flow(monkeypatch, tmp_path, MARGIN, calls, warm_used=LINE)
        result = flow.run()
        assert result.metrics is METRICS and calls == ['csv', ('measure', None)]
        assert flow.summary.get('pilot_others_rise_mb') == str(MARGIN)

    def test_others_past_the_margin_with_the_card_clear_are_measured_with_the_rise(self, monkeypatch, tmp_path):
        calls = []
        flow = collecting_flow(monkeypatch, tmp_path, MARGIN + 1, calls)
        result = flow.run()
        assert result.metrics is METRICS and calls == ['csv', ('measure', 300 + MARGIN + 1)]
        assert flow.summary.warnings() == () and flow.summary.get('pilot_others_rise_mb') == str(MARGIN + 1)


class TestAfterFailure:

    def test_a_saturated_failure_with_others_rising_is_released_never_does_not_fit(self, tmp_path):
        runs = [with_others(launch(tmp_path, 'warmup', 9000), 0), with_others(launch(tmp_path, 'render', LINE), 900)]
        flow = failing_flow(tmp_path, runs)
        with pytest.raises(LocalConflict) as info:
            flow.run()
        assert (info.value.reason, info.value.message) == ('local_conflict', message(900, 'render'))
        assert flow.summary.get('pilot_verdict') == '' and flow.summary.get('pilot_others_rise_mb') == '900'
        assert not [w for w in flow.summary.warnings() if w.startswith('pilot_failed_saturated')]
        assert [w for w in flow.summary.warnings() if w.startswith('pilot_others_rose: ')]

    def test_a_saturated_failure_one_mb_past_the_margin_is_released(self, tmp_path):
        """The card was full: a rise past the margin is band 3, never band 2's addition."""
        flow = failing_flow(tmp_path, [with_others(launch(tmp_path, 'render', LINE), MARGIN + 1)])
        with pytest.raises(LocalConflict) as info:
            flow.run()
        assert info.value.message == message(MARGIN + 1, 'render') and flow.summary.get('pilot_verdict') == ''

    @pytest.mark.parametrize('rise', [257, MARGIN])
    def test_a_saturated_failure_with_others_up_to_the_margin_does_not_fit(self, tmp_path, rise):
        flow = failing_flow(tmp_path, [with_others(launch(tmp_path, 'render', LINE), rise)])
        result = flow.run()
        assert result.metrics.verdict == 'does_not_fit' and flow.summary.get('pilot_others_rise_mb') == str(rise)
        assert (result.metrics.others_added_mb, result.metrics.peak_mb) == (0, LINE - 300)
        assert flow.summary.get('pilot_others_added_mb') == '0'

    @pytest.mark.parametrize('rise', [0, 900])
    def test_an_isolation_failure_is_always_raised_again(self, tmp_path, rise):
        """Saturated or not, a Zen/ini guard or Zen log failure is never a measurement nor a release."""
        failure = IsolationFailure("the farm-private Zen data path was not confirmed")
        flow = failing_flow(tmp_path, [with_others(launch(tmp_path, 'render', LINE), rise)], failure=failure)
        with pytest.raises(IsolationFailure) as info:
            flow.run()
        assert info.value is failure and isinstance(failure, JobFailure)
        assert 'pilot_others_rise_mb' not in flow.summary.text() and flow.summary.get('pilot_verdict') == ''

    def test_a_failure_without_a_saturated_sample_keeps_the_original(self, tmp_path):
        flow = failing_flow(tmp_path, [with_others(launch(tmp_path, 'render', LINE - 1), 5000)])
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert info.value is flow.failure and 'pilot_others_rise_mb' not in flow.summary.text()

    def test_a_render_job_with_others_rising_keeps_the_original(self, tmp_path):
        flow = failing_flow(tmp_path, [with_others(launch(tmp_path, 'render', LINE), 5000)], kind='render')
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert info.value is flow.failure
