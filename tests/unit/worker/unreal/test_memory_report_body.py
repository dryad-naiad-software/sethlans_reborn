# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The memory report's body and when one is due (Unreal per-machine memory test D3, D6, D9, FR-MT-S-3, FR-MT-S-4).

Exactly eight keys: ``measurement_id`` (the gate's result), ``card_kind`` (the gated card), ``card_key`` (the
held card), ``driver_version`` (the render launch's; Unknown -> null), ``spilled`` (bool or null),
``spill_cause`` (null exactly when ``spilled`` is not true), ``shared_peak_mb`` (0..MAX_PEAK_MB or null) and
``job_peak_mb`` (1..MAX_PEAK_MB or null): the job's own dedicated peak on its card over the whole launch,
start-up included, the largest over every warm-up and render launch (Q11), never the card's rise. A figure out of range is
sent as null with a WARNING. A known launch driver other than the result's known driver sets
``driver_changed=1``.
"""
import logging

import pytest

from sethlans_worker_agent.unreal_executor import job_memory, memory_report, spill
from shared.unreal.vram_gate import MAX_PEAK_MB
from tests.unit.worker.unreal._executor_fakes import KIND, params
from tests.unit.worker.unreal._memory_report_fakes import CARD_KEY, DRIVER, job_run, render_run, result, watch
from tests.unit.worker.unreal.sequence._pieces import audio_params, piece_params

KEYS = ['measurement_id', 'card_kind', 'card_key', 'driver_version', 'spilled', 'spill_cause', 'shared_peak_mb',
        'job_peak_mb']


class TestDue:

    @pytest.mark.parametrize('p,due', [
        (params(), True), (piece_params(), True), (audio_params(), True),
        (params(kind='pilot'), False), (params(kind='shots', frame=0), False),
    ])
    def test_render_and_audio_jobs_with_a_render_launch(self, tmp_path, p, due):
        assert memory_report.due(p, job_run([render_run(tmp_path)]).runner) is due

    def test_no_warm_up_or_render_launch_is_not_due(self, tmp_path):
        """Q11: a warm-up launch alone makes it due (``test_warmup_report``); the prep alone never does."""
        assert memory_report.due(params(), job_run([render_run(tmp_path, phase='prep')]).runner) is False
        assert memory_report.due(params(), job_run([]).runner) is False

    def test_no_runner_is_not_due(self):
        assert memory_report.due(params(), None) is False


class TestBody:

    def test_exactly_the_eight_keys_and_their_values(self, tmp_path):
        run = render_run(tmp_path, max_job=7300, spill_watch=watch(spilled=False, shared=640))
        body = memory_report.body(job_run([run]))
        assert list(body) == KEYS
        assert body == {'measurement_id': 7, 'card_kind': KIND, 'card_key': CARD_KEY, 'driver_version': DRIVER,
                        'spilled': False, 'spill_cause': None, 'shared_peak_mb': 640, 'job_peak_mb': 7300}
        assert all(type(body[k]) is int for k in ('measurement_id', 'shared_peak_mb', 'job_peak_mb'))

    @pytest.mark.parametrize('spilled,cause,sent', [
        (True, spill.SELF, 'self'), (True, spill.CONTENTION, 'contention'), (False, None, None), (None, None, None),
    ])
    def test_spill_cause_is_null_exactly_when_spilled_is_not_true(self, tmp_path, spilled, cause, sent):
        run = render_run(tmp_path, max_job=7300, spill_watch=watch(spilled=spilled, cause=cause, shared=2000))
        body = memory_report.body(job_run([run]))
        assert body['spilled'] is spilled and body['spill_cause'] == sent

    def test_no_watch_sends_nulls(self, tmp_path):
        body = memory_report.body(job_run([render_run(tmp_path)]))
        assert (body['spilled'], body['spill_cause'], body['shared_peak_mb'], body['job_peak_mb']) == \
            (None, None, None, None)

    def test_the_held_card_is_the_card_key(self, tmp_path):
        body = memory_report.body(job_run([render_run(tmp_path)], card_key='pci:0000:0a:00.0'))
        assert body['card_key'] == 'pci:0000:0a:00.0'


class TestJobPeak:

    def test_the_jobs_own_dedicated_peak_not_the_cards_rise(self, tmp_path):
        """The card rose 20,000 MB over its baseline (other programs too); the job's own processes held 7,300."""
        run = render_run(tmp_path, max_job=7300, max_used=20500)
        assert memory_report.body(job_run([run]))['job_peak_mb'] == 7300

    def test_the_start_up_spike_above_frame_level_is_reported(self, tmp_path):
        """``max_job`` covers the whole launch; the summary's window figure (from the first file on) is lower."""
        run = render_run(tmp_path, max_job=9500)
        run.outcome.vram.job_peak_mb = 7000
        assert memory_report.body(job_run([run]))['job_peak_mb'] == 9500
        assert job_memory.summary_values([run])['vram_job_peak_mb'] == 7000   # the summary keeps its window

    def test_the_largest_over_every_warm_up_and_render_launch(self, tmp_path):
        """A crashed first launch counts, and so does a warm-up (Q11: a cold warm-up can peak above the render)."""
        runs = [render_run(tmp_path, 1, max_job=9800), render_run(tmp_path, 2, max_job=12000, phase='warmup'),
                render_run(tmp_path, 2, max_job=7100)]
        assert memory_report.body(job_run(runs))['job_peak_mb'] == 12000

    def test_an_audio_job_gets_a_real_figure(self, tmp_path):
        """No output file window for audio: the whole-launch figure still exists."""
        run = render_run(tmp_path, max_job=2100)
        assert memory_report.body(job_run([run], p=audio_params()))['job_peak_mb'] == 2100


class TestRanges:

    @pytest.mark.parametrize('name,value,sent', [
        ('shared_peak_mb', 0, 0), ('shared_peak_mb', MAX_PEAK_MB, MAX_PEAK_MB),
        ('job_peak_mb', 1, 1), ('job_peak_mb', MAX_PEAK_MB, MAX_PEAK_MB),
    ])
    def test_the_bounds_are_sent(self, tmp_path, name, value, sent):
        body = memory_report.body(job_run([self._run(tmp_path, name, value)]))
        assert body[name] == sent

    def test_a_job_peak_of_zero_is_null_without_a_warning(self, tmp_path, caplog):
        """Nothing attributed to the job's processes: unknown, not an out-of-range figure."""
        with caplog.at_level(logging.WARNING, logger=memory_report.__name__):
            body = memory_report.body(job_run([self._run(tmp_path, 'job_peak_mb', 0)]))
        assert body['job_peak_mb'] is None
        assert [r for r in caplog.records if r.name == memory_report.__name__] == []

    @pytest.mark.parametrize('name,value,low', [
        ('shared_peak_mb', MAX_PEAK_MB + 1, 0), ('shared_peak_mb', -1, 0), ('job_peak_mb', -5, 1),
        ('job_peak_mb', MAX_PEAK_MB + 1, 1),
    ])
    def test_out_of_range_is_null_and_warned(self, tmp_path, caplog, name, value, low):
        with caplog.at_level(logging.WARNING, logger=memory_report.__name__):
            body = memory_report.body(job_run([self._run(tmp_path, name, value)]))
        assert body[name] is None
        [line] = [r.getMessage() for r in caplog.records if r.name == memory_report.__name__]
        assert line == (f"The memory report's {name} ({value} MB) is outside [{low}, {MAX_PEAK_MB}]; "
                        "it is sent as null.")

    @staticmethod
    def _run(tmp_path, name, value):
        if name == 'shared_peak_mb':
            return render_run(tmp_path, max_job=7000, spill_watch=watch(spilled=False, shared=value))
        return render_run(tmp_path, max_job=value)


class TestDriver:

    def test_the_last_render_launchs_driver(self, tmp_path):
        runs = [render_run(tmp_path, 1, driver='1.1.1.1'), render_run(tmp_path, 2, driver='2.2.2.2')]
        assert memory_report.body(job_run(runs, gate_result=result(driver_version='2.2.2.2')))['driver_version'] == \
            '2.2.2.2'

    @pytest.mark.parametrize('driver', ['Unknown', None])
    def test_an_unknown_driver_is_null(self, tmp_path, driver):
        jr = job_run([render_run(tmp_path, driver=driver)])
        assert memory_report.body(jr)['driver_version'] is None
        assert jr.summary.get('driver_version') == 'unknown' and jr.summary.get('driver_changed') == '0'

    def test_a_changed_driver_is_noted_for_a_re_test(self, tmp_path, caplog):
        jr = job_run([render_run(tmp_path, driver='33.0.1.2')], gate_result=result(driver_version=DRIVER))
        with caplog.at_level(logging.INFO, logger=memory_report.__name__):
            body = memory_report.body(jr)
        assert body['driver_version'] == '33.0.1.2' and jr.summary.get('driver_changed') == '1'
        assert f'ran with driver 33.0.1.2; its memory result was measured with {DRIVER}' in caplog.text

    @pytest.mark.parametrize('launch,measured', [(DRIVER, DRIVER), (DRIVER, None), ('Unknown', DRIVER)])
    def test_same_or_one_side_unknown_is_no_change(self, tmp_path, launch, measured):
        jr = job_run([render_run(tmp_path, driver=launch)], gate_result=result(driver_version=measured))
        memory_report.body(jr)
        assert jr.summary.get('driver_changed') == '0'


class TestCannotBuild:

    @pytest.mark.parametrize('changes,text', [
        ({'gate_result': None}, 'the gate chose no memory result'),
        ({'card_kind': None}, 'the gated card has no card kind (None)'),
        ({'card_kind': 'not-a-kind'}, "the gated card has no card kind ('not-a-kind')"),
        ({'card_key': None}, 'the held card has no PCI card key (None)'),
        ({'card_key': 'apple-gpu'}, "the held card has no PCI card key ('apple-gpu')"),
        ({'flow': False}, 'no flow for the driver version'),
    ])
    def test_a_report_error(self, tmp_path, changes, text):
        with pytest.raises(memory_report.ReportError) as info:
            memory_report.body(job_run([render_run(tmp_path)], **changes))
        assert str(info.value) == text
