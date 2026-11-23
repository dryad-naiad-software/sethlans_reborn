# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Owner decision Q10, band 2 (``pilot_others``): other programs' rise past ``unreal.pilot_others_margin_mb`` while
the pilot's card stayed clear of full adds to the pilot's peak, before D6's verdict, only the part of other
programs' use that ``peak + baseline`` does not already hold.

``added = max(0, top - counted)``: ``top`` is the highest other-programs figure over the checked launches;
``counted`` is other programs' use at the card's peak sample for a card peak (``VramTrace.others_at_max``; the
baseline when that sample was not split by process) and the peak launch's baseline for a CSV peak. The addition
goes on the figure that set the peak, so ``peak_mb = max(card delta, CSV)`` and ``peak_source`` still hold.

End to end through ``UeFlow.run`` (real ``pilot.measure``): a program opened in the warm-up and left open is
counted once (the verdict matches what the card held); a card figure the card could not hold is released
(``check_over_card``); a CSV peak above the card is reported as does_not_fit (the manager accepts it).
"""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import job_flow, pilot, pilot_flow, pilot_others
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from tests.unit.worker.unreal._executor_fakes import phase_run, reading, trace
from tests.unit.worker.unreal.test_job_flow_card import BUSY, IDLE, IDLE_KEY, Chooser, _flow
from tests.unit.worker.unreal.test_pilot_flow_failure import LINE as IDLE_LINE, launch

TOTAL = 16384                     # floor(16384 * 0.93) = 15237, the usable line
LOGGER = pilot_flow.__name__


@pytest.fixture(autouse=True)
def _margin(monkeypatch):
    monkeypatch.setattr(config_unreal, 'PILOT_OTHERS_MARGIN_MB', 512)


def sampled(phase, pre, used, others_top, at_peak=None, post=None):
    """A launch of the 16 GB card: card at ``pre`` before, ``used`` at its peak, others' use at most ``others_top``."""
    run = phase_run(phase, {0: pre}, {0: pre if post is None else post}, trace({0: used}, total=TOTAL))
    run.outcome.vram.max_others = {0: others_top}
    if at_peak is not None:
        run.outcome.vram.others_at_max = {0: at_peak}
    return run


def browser_left_open():
    """Warm-up pre 1500; a browser adds 800 MB and stays open; render pre 2300, own peak 12500, card max 14800."""
    return [sampled('warmup', 1500, 9000, 2300, 2300, post=2300), sampled('render', 2300, 14800, 2300, 2300)]


def measured(runs, csv=None):
    return pilot.measure(runs, csv, pilot_others.top_others(runs))


class TestCounted:

    def test_a_program_left_open_from_the_warm_up_is_counted_once_card_peak(self):
        metrics = measured(browser_left_open())
        assert (metrics.peak_mb, metrics.baseline_used_mb, metrics.others_added_mb) == (12500, 2300, 0)
        assert metrics.verdict == 'fits'                         # the card held both at 14800, under 15237

    def test_a_program_left_open_from_the_warm_up_is_counted_once_csv_peak(self):
        metrics = measured(browser_left_open(), csv=12600)
        assert (metrics.peak_mb, metrics.peak_source, metrics.others_added_mb, metrics.verdict) == \
            (12600, 'csv', 0, 'fits')

    def test_a_rise_during_the_peak_launch_at_the_peak_sample_is_already_in_the_card_peak(self):
        runs = [sampled('warmup', 1500, 6000, 1500), sampled('render', 1500, 14800, 2300, 2300, post=2300)]
        metrics = measured(runs)
        assert (metrics.peak_mb, metrics.others_added_mb, metrics.verdict) == (13300, 0, 'fits')

    def test_only_the_part_missing_from_the_peak_sample_is_added(self):
        """Others peaked at 2300 but held 1700 at the card's peak: 600 MB more could have coincided."""
        runs = [sampled('warmup', 1500, 6000, 1500), sampled('render', 1500, 14800, 2300, 1700)]
        metrics = measured(runs)
        assert (metrics.card_delta_peak_mb, metrics.others_added_mb) == (13300 + 600, 600)
        assert (metrics.verdict, metrics.lower_bound_mb) == ('does_not_fit', 13900)   # 13900 + 1500 > 15237

    def test_a_csv_peak_counts_the_baseline_so_a_rise_during_its_launch_is_added(self):
        """UE's own figure holds none of theirs: own 13400 plus their 2300 at most is over the 15237 line."""
        runs = [sampled('warmup', 1500, 6000, 1500), sampled('render', 1500, 14800, 2300, 2300, post=2300)]
        metrics = measured(runs, csv=13400)
        assert (metrics.peak_mb, metrics.csv_local_used_peak_mb, metrics.card_delta_peak_mb) == (14200, 14200, 13300)
        assert (metrics.others_added_mb, metrics.baseline_used_mb) == (800, 1500)
        assert (metrics.verdict, metrics.lower_bound_mb) == ('does_not_fit', 14200)

    def test_a_card_peak_sample_not_split_by_process_counts_the_baseline(self):
        runs = [sampled('render', 1500, 14000, 2300)]
        assert measured(runs).others_added_mb == 800

    def test_without_a_top_figure_nothing_is_added(self):
        assert pilot.measure(browser_left_open(), None).others_added_mb == 0
        assert pilot_others.added(None, 12500, None, 2300, browser_left_open()[1], 0) == 0

    def test_the_top_figure_is_the_highest_over_the_checked_launches(self):
        runs = [sampled('prep', 900, 1000, 3100), sampled('warmup', 1500, 9000, 2300), sampled('render', 2300, 9000, 2400)]
        unchecked = phase_run('render', None, None, trace({0: 9000}))
        unchecked.outcome.vram.max_others = {0: 9999}
        assert pilot_others.top_others(runs + [unchecked]) == 3100 and pilot_others.top_others([unchecked]) is None

    @pytest.mark.parametrize('csv', [None, 12000, 13000])
    def test_peak_stays_the_larger_figure_and_keeps_its_source(self, csv):
        runs = [sampled('render', 1500, 14000, 2500, 1500)]
        plain, adjusted = pilot.measure(runs, csv), measured(runs, csv)
        assert adjusted.peak_mb == plain.peak_mb + 1000 and adjusted.peak_source == plain.peak_source
        assert adjusted.peak_mb == max(adjusted.card_delta_peak_mb, adjusted.csv_local_used_peak_mb or 0)

    def test_the_implausible_peak_rule_sees_the_peak_before_the_addition(self):
        with pytest.raises(JobFailure, match=r'implausible GPU memory peak \(500 MB\)'):
            measured([sampled('render', 1000, 1500, 9000, 1000)])

    def test_the_summary_carries_the_added_amount(self):
        runs = [sampled('warmup', 1500, 6000, 1500), sampled('render', 1500, 14800, 2300, 1700)]
        values = pilot.summary_values(measured(runs), [0, 1], 'Card', 'c' * 64)
        assert (values['pilot_others_added_mb'], values['pilot_peak_mb']) == (600, 13900)
        assert pilot.summary_values(pilot.measure(runs, None), [0], 'Card', '')['pilot_others_added_mb'] == 0


def measuring_flow(monkeypatch, tmp_path, rise, render_used, csv=None, render_pre=300, at_peak=300):
    """A pilot on the 24,560 MB card: warm-up pre 300 with others rising by ``rise``; render pre ``render_pre``."""
    warm = launch(tmp_path, 'warmup', 9000)
    warm.outcome.vram.max_others = {1: 300 + rise}
    render = launch(tmp_path, 'render', render_used)
    render.pre = render.post = {1: render_pre}
    render.outcome.vram.max_others = {1: render_pre}
    render.outcome.vram.others_at_max = {1: at_peak}
    flow = _flow(Chooser(None), IDLE_KEY, kind='pilot', gate_reading=reading(gpus=(BUSY, IDLE)), tmp_path=tmp_path)
    flow.choice = Choice(42, IDLE_KEY, 'Radeon Test 24G', 1)
    flow.prep = lambda: SimpleNamespace(pilot_frames=(0, 50))
    flow._warm_if_needed = lambda prep: True
    flow._render_with_retry = lambda prep: (render, SimpleNamespace(scan=None), frozenset())
    flow._render_summary = lambda run, check: None
    flow.runner = SimpleNamespace(runs=[warm, render])
    flow.warmup_logs = [(IDLE_KEY, warm.log_path)]
    monkeypatch.setattr(job_flow, 'render_check_time', lambda check, render_s: 60)
    monkeypatch.setattr(job_flow.output, 'collect_pilot', lambda *args: [])
    monkeypatch.setattr(job_flow.pilot_csv, 'collect', lambda *args: csv)
    return flow


class TestCollectBand2:

    def test_a_rise_at_the_margin_adds_nothing(self, monkeypatch, tmp_path):
        result = measuring_flow(monkeypatch, tmp_path, 512, 22000).run()
        assert (result.metrics.peak_mb, result.metrics.verdict, result.metrics.others_added_mb) == (21700, 'fits', 0)

    def test_a_program_open_in_the_warm_up_and_gone_at_the_peak_is_added(self, monkeypatch, tmp_path, caplog):
        """21700 + 300 fits under the 22840 line; with the 1500 MB the program held added, 23200 + 300 does not."""
        flow = measuring_flow(monkeypatch, tmp_path, 1500, 22000)
        with caplog.at_level(logging.INFO, logger=LOGGER):
            metrics = flow.run().metrics
        assert (metrics.peak_mb, metrics.verdict, metrics.lower_bound_mb, metrics.others_added_mb) == \
            (23200, 'does_not_fit', 23200, 1500)
        assert flow.summary.get('pilot_others_added_mb') == '1500' and flow.summary.get('pilot_others_rise_mb') == '1500'
        assert flow.summary.warnings() == () and [r.levelno for r in caplog.records if r.name == LOGGER] == [logging.INFO]

    def test_a_program_left_open_into_the_render_is_counted_once(self, monkeypatch, tmp_path):
        """Render pre 1800 holds it: card 22000 = pilot 20200 + 1800, under the line, so the verdict stays fits."""
        metrics = measuring_flow(monkeypatch, tmp_path, 1500, 22000, render_pre=1800, at_peak=1800).run().metrics
        assert (metrics.peak_mb, metrics.baseline_used_mb, metrics.others_added_mb, metrics.verdict) == \
            (20200, 1800, 0, 'fits')

    def test_a_program_left_open_into_the_render_is_counted_once_csv_peak(self, monkeypatch, tmp_path):
        flow = measuring_flow(monkeypatch, tmp_path, 1500, 22000, csv=20500, render_pre=1800, at_peak=1800)
        metrics = flow.run().metrics
        assert (metrics.peak_mb, metrics.peak_source, metrics.others_added_mb, metrics.verdict) == \
            (20500, 'csv', 0, 'fits')

    def test_a_card_figure_exactly_filling_the_card_is_reported(self, monkeypatch, tmp_path):
        metrics = measuring_flow(monkeypatch, tmp_path, IDLE.total_mb - 22000, 22000).run().metrics
        assert (metrics.card_delta_peak_mb + metrics.baseline_used_mb, metrics.verdict) == (IDLE.total_mb, 'does_not_fit')

    def test_a_card_figure_the_card_could_not_hold_is_released(self, monkeypatch, tmp_path, caplog):
        flow = measuring_flow(monkeypatch, tmp_path, 3000, 22000)  # 21700 + 3000 + 300 = 25000 > 24560
        with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(LocalConflict) as info:
            flow.run()
        expected = pilot_flow.OVER_CARD_MESSAGE.format(added=3000, card=24700, baseline=300, total=IDLE.total_mb)
        assert (info.value.reason, info.value.message) == ('local_conflict', expected)
        assert flow.summary.warnings() == (f"pilot_others_rose: {expected}"[:300],)
        assert [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING] == \
            [f"Pilot job 42 records no verdict: {expected}"]
        assert flow.summary.get('pilot_verdict') == ''

    def test_a_csv_peak_above_the_card_is_reported_as_does_not_fit(self, monkeypatch, tmp_path):
        """The card delta stays the card's reading (19700 + 300); the manager accepts an over-full CSV peak."""
        flow = measuring_flow(monkeypatch, tmp_path, 1500, 20000, csv=24500)
        metrics = flow.run().metrics
        assert (metrics.peak_mb, metrics.peak_source, metrics.card_delta_peak_mb) == (26000, 'csv', 19700)
        assert metrics.peak_mb + metrics.baseline_used_mb > IDLE.total_mb
        assert (metrics.verdict, metrics.lower_bound_mb, metrics.others_added_mb) == ('does_not_fit', 26000, 1500)
        assert flow.summary.get('pilot_verdict') == 'does_not_fit' and flow.summary.warnings() == ()

    def test_a_card_that_came_close_to_full_is_released_not_added(self, monkeypatch, tmp_path):
        with pytest.raises(LocalConflict) as info:
            measuring_flow(monkeypatch, tmp_path, 1500, IDLE_LINE).run()
        assert 'the card came close to full' in info.value.message
