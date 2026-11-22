# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A pilot's verdict and report (Unreal per-machine memory test D6, FR-MT-V-2, FR-MT-V-3, FR-MT-G-4; AC-4).

``does_not_fit`` when a sample of the card reached ``floor(total * 0.93)`` or when ``peak_mb +
baseline_used_mb`` is over that line (``peak_mb`` may be the CSV LocalUsed figure), else ``fits``;
``lower_bound_mb`` is the peak for ``does_not_fit``. The pilot is reported (it ends DONE) either way. A render
job's summary compares its own peak with the result peak the gate used (``vram_peak_over_result``).
"""
import dataclasses
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import log_summary, pilot
from sethlans_worker_agent.unreal_executor.delivery import WRONG_KIND_MESSAGE, Outcome
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict
from tests.unit.worker.unreal._executor_fakes import KIND, params, phase_run, trace
from tests.unit.worker.unreal.test_delivery_card_key import KEY, _delivery, _flow

LINE = 30324                      # floor(32607 * 0.93)


def _render(max_used, baseline=1000, **kwargs):
    return phase_run('render', {0: baseline}, {0: baseline}, trace({0: max_used}, **kwargs))


class TestCsvTerm:
    """D6: the gate's own figure, ``max(card delta, CSV LocalUsed)``, plus the baseline against the line."""

    def test_the_csv_peak_over_the_line_without_a_saturated_sample_does_not_fit(self):
        metrics = pilot.measure([_render(29000)], LINE - 1000 + 1)    # 29325 + 1000 > 30324
        assert (metrics.peak_mb, metrics.peak_source, metrics.saturated) == (29325, 'csv', False)
        assert (metrics.verdict, metrics.lower_bound_mb) == ('does_not_fit', 29325)

    def test_the_csv_peak_exactly_at_the_line_fits(self):
        metrics = pilot.measure([_render(29000)], LINE - 1000)          # 29324 + 1000 == 30324
        assert (metrics.peak_mb, metrics.peak_source) == (29324, 'csv')
        assert (metrics.verdict, metrics.lower_bound_mb, metrics.saturated) == ('fits', None, False)

    def test_one_mb_under_the_saturation_line_fits_on_the_card_alone(self):
        metrics = pilot.measure([_render(LINE - 1)], None)
        assert (metrics.verdict, metrics.saturated, metrics.peak_mb) == ('fits', False, LINE - 1 - 1000)

    def test_the_baseline_counts(self):
        """The same CSV peak fits over a 900 MB baseline and not over a 1,100 MB one."""
        assert pilot.measure([_render(25000, baseline=900)], 29400).verdict == 'fits'           # 30300
        assert pilot.measure([_render(25000, baseline=1100)], 29400).verdict == 'does_not_fit'  # 30500


class TestSaturation:

    def test_a_saturated_warm_up_is_enough(self):
        """Any phase: the warm-up filled the card, the render did not."""
        runs = [phase_run('warmup', {0: 1000}, {0: 1000}, trace({0: LINE})), _render(9000)]
        metrics = pilot.measure(runs, None)
        assert (metrics.verdict, metrics.saturated, metrics.peak_phase) == ('does_not_fit', True, 'warmup')
        assert metrics.lower_bound_mb == metrics.peak_mb == LINE - 1000

    def test_after_saturation_freed_memory_and_a_gap_do_not_release(self):
        """The card was full, so another program's memory being evicted and a stall are expected."""
        full = phase_run('render', {0: 3000}, {0: 3000}, trace({0: LINE}, {0: 100}, gap=40.0))
        metrics = pilot.measure([full], None)
        assert (metrics.verdict, metrics.max_sample_gap_s) == ('does_not_fit', 40.0)
        with pytest.raises(LocalConflict, match='freed'):
            pilot.measure([phase_run('render', {0: 3000}, {0: 3000}, trace({0: LINE - 1}, {0: 100}))], None)

    def test_after_saturation_a_phase_without_samples_is_left_out(self):
        runs = [phase_run('warmup', {0: 1000}, {0: 1000}, trace({0: LINE})),
                phase_run('render', None, None, trace(samples=0))]
        assert pilot.measure(runs, None).verdict == 'does_not_fit'
        with pytest.raises(LocalConflict, match='stalled'):
            pilot.measure([_render(9000), phase_run('render', None, None, trace(samples=0))], None)

    def test_saturation_on_another_card_size(self):
        """The line is the sampled card's: floor(20464 * 0.93) = 19031 on a 20 GB card."""
        assert pilot.measure([_render(19031, total=20464)], None).saturated is True
        assert pilot.measure([_render(19030, total=20464)], None).saturated is False

    def test_saturated_reads_every_phase(self):
        assert pilot.saturated([_render(9000), phase_run('prep', {0: 0}, {0: 0}, trace({0: LINE}))]) is True
        assert pilot.saturated([_render(9000)]) is False and pilot.saturated([]) is False


class TestSummaryValues:

    def test_the_verdict_values(self):
        full = pilot.summary_values(pilot.measure([_render(LINE)], None), [0, 1], 'Test Card', 'c' * 64)
        assert (full['pilot_verdict'], full['pilot_lower_bound_mb'], full['pilot_saturated']) == \
            ('does_not_fit', LINE - 1000, 1)
        fits = pilot.summary_values(pilot.measure([_render(9000)], None), [0], 'Test Card', 'c' * 64)
        assert (fits['pilot_verdict'], fits['pilot_lower_bound_mb'], fits['pilot_saturated']) == ('fits', '', 0)


def _body(metrics, **kwargs):
    from sethlans_worker_agent.unreal_executor.vram import GpuMem
    gpu = GpuMem(0, 'Test Card', 32607, 1000, None, None, KEY, card_kind=KIND)
    kwargs.setdefault('driver_version', None)
    return pilot.report_body(metrics, gpu, prep_s=1, warmup_s=1, render_s=1, frames=1, engine_build='5.8.4-58902052',
                             overrides_sha256='c' * 64, summary='s', card_kind=KIND, **kwargs)


class TestReportBody:

    def test_a_does_not_fit_body(self):
        body = _body(pilot.measure([_render(LINE)], None), driver_version='32.0.22029.1019')
        assert (body['verdict'], body['lower_bound_mb'], body['peak_mb']) == ('does_not_fit', LINE - 1000, LINE - 1000)
        assert (body['card_kind'], body['driver_version'], body['shared_peak_mb']) == \
            (KIND, '32.0.22029.1019', None)

    def test_a_fits_body(self):
        body = _body(pilot.measure([_render(9000)], None))
        assert (body['verdict'], body['lower_bound_mb'], body['driver_version']) == ('fits', None, None)

    def test_shared_peak_is_passed_through(self):
        assert _body(pilot.measure([_render(9000)], None), shared_peak_mb=640)['shared_peak_mb'] == 640


class TestDelivery:
    """The pilot ends DONE with its verdict; a card of another kind is a backstop release."""

    def test_a_does_not_fit_pilot_is_reported_and_done(self, caplog):
        metrics = pilot.measure([_render(LINE)], None)
        flow = dataclasses.replace(_flow(KEY), metrics=metrics, driver_version='31.0.15.5222')
        calls = []
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.unreal_executor.delivery'):
            result = _delivery(calls).deliver(Outcome('success', flow=flow), integrity_ok=True)
        [(name, body)] = calls
        assert name == 'pilot' and result.success is True and result.released is True
        assert (body['verdict'], body['lower_bound_mb'], body['card_kind']) == ('does_not_fit', LINE - 1000, KIND)
        assert body['driver_version'] == '31.0.15.5222' and body['shared_peak_mb'] is None
        assert 'does_not_fit' in caplog.text

    @pytest.mark.parametrize('kind,found', [('1002:744c:00c8', '1002:744c:00c8'), (None, 'no kind')])
    def test_a_card_of_another_kind_is_released_not_reported(self, kind, found):
        flow = _flow(KEY)
        flow = dataclasses.replace(flow, card=dataclasses.replace(flow.card, card_kind=kind))
        calls = []
        result = _delivery(calls).deliver(Outcome('success', flow=flow), integrity_ok=True)
        assert calls == [('release', 42, 'local_conflict')] and result.released is True and result.success is False
        assert WRONG_KIND_MESSAGE.format(found=found, wanted=KIND).startswith(f'the measured graphics card ({found})')


class TestVramValues:
    """FR-MT-G-4: the job's own peak against the result peak the gate used."""

    def test_a_peak_over_the_result_is_marked(self):
        values = log_summary.vram_values(params(), 7931, 7930)
        assert values['vram_peak_over_result'] == 1 and values['vram_peak_ratio_pct'] == 100
        assert 'vram_peak_over_measurement' not in values

    def test_a_peak_equal_to_the_result_is_not(self):
        values = log_summary.vram_values(params(), 7930, 7930)
        assert values == {'vram_peak_mb': 7930, 'vram_peak_source': 'card', 'vram_peak_ratio_pct': 100}

    def test_the_ratio_rounds(self):
        assert log_summary.vram_values(params(), 3965, 7930)['vram_peak_ratio_pct'] == 50
        assert log_summary.vram_values(params(), 12000, 7930)['vram_peak_ratio_pct'] == 151

    @pytest.mark.parametrize('kind,peak,result_peak', [
        ('pilot', 9000, 7930), ('render', None, 7930), ('render', 9000, None), ('render', 9000, 0),
    ])
    def test_no_comparison_without_both_figures_or_for_a_pilot(self, kind, peak, result_peak):
        values = log_summary.vram_values(params(kind=kind), peak, result_peak)
        assert values == {'vram_peak_mb': '' if peak is None else peak, 'vram_peak_source': 'card'}

    def test_the_result_peak_defaults_to_none(self):
        assert 'vram_peak_ratio_pct' not in log_summary.vram_values(SimpleNamespace(is_pilot=False), 9000)
