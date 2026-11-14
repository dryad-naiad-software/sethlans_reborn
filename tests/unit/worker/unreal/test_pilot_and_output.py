# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FR-UE-25 pilot metrics, CSV capture and report body; FR-UE-13 output collection; FR-UE-10/13 render checks."""
import pytest

from sethlans_worker_agent.unreal_executor import output, pilot, pilot_csv, render_check
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from tests.unit.worker.unreal._executor_fakes import outcome, phase_run, reading, trace

PILOT_FIELDS = {'peak_mb', 'peak_source', 'card_delta_peak_mb', 'baseline_used_mb', 'baseline_drift_mb', 'peak_phase',
                'max_sample_gap_s', 'prep_s', 'warmup_s', 'warmup_ran', 'render_s', 'frames', 'seconds_per_frame',
                'per_shot', 'gpu', 'engine_build', 'csv_local_used_peak_mb', 'overrides_sha256', 'summary'}


def runs():
    return [phase_run('prep', {0: 950}, {0: 960}, trace({0: 980})),
            phase_run('warmup', {0: 990}, {0: 1010}, trace({0: 7405}, {0: 900})),
            phase_run('render', {0: 1000}, {0: 945}, trace({0: 8904}, {0: 945}))]


class TestMeasure:

    def test_per_phase_min_baseline_and_card_peak(self):
        metrics = pilot.measure(runs(), None)
        assert (metrics.card_delta_peak_mb, metrics.peak_phase, metrics.baseline_used_mb) == (7959, 'render', 945)
        assert metrics.peak_mb == 7959 and metrics.peak_source == 'card' and metrics.baseline_drift_mb == 55
        assert metrics.csv_local_used_peak_mb is None and metrics.gpu_index == 0

    def test_csv_wins_when_larger_and_card_on_a_tie(self):
        assert pilot.measure(runs(), 8102).peak_source == 'csv' and pilot.measure(runs(), 8102).peak_mb == 8102
        assert pilot.measure(runs(), 7959).peak_source == 'card'

    def test_saturation_names_the_card(self):
        bad = runs()[:2] + [phase_run('render', {0: 1000}, {0: 1000}, trace({0: 30324}))]
        with pytest.raises(JobFailure, match=r'saturated \(30324 of 30324 MB usable on a 32607 MB card\)'):
            pilot.measure(bad, None)

    def test_freed_memory_releases(self):
        bad = runs()[:2] + [phase_run('render', {0: 3000}, {0: 3000}, trace({0: 9000}, {0: 2400}))]
        with pytest.raises(LocalConflict) as info:
            pilot.measure(bad, None)
        assert info.value.reason == 'local_conflict' and 'freed' in info.value.message

    def test_sampling_gap_and_missing_samples_release(self):
        with pytest.raises(LocalConflict, match='stalled for 12 s'):
            pilot.measure([phase_run('render', {0: 1000}, {0: 1000}, trace({0: 9000}, gap=12.5))], None)
        with pytest.raises(LocalConflict):
            pilot.measure([phase_run('render', {0: 1000}, {0: 1000}, trace({0: 9000}, samples=0))], None)

    def test_implausible_peak(self):
        with pytest.raises(JobFailure, match=r'implausible GPU memory peak \(500 MB\)'):
            pilot.measure([phase_run('render', {0: 1000}, {0: 1000}, trace({0: 1500}))], 200)


class TestAdapter:
    LINES = ('[t][0]LogD3D12RHI: Found D3D12 adapter 0: NVIDIA GeForce RTX 5090 (VendorId: 10de, DeviceId: 2b85\n'
             '[t][0]LogD3D12RHI: Found D3D12 adapter 1: AMD Radeon(TM) Graphics (VendorId: 1002, DeviceId: 13c0\n')

    def test_chosen_adapter_must_be_an_nvidia_smi_gpu(self, tmp_path):
        log = tmp_path / 'render.1.log'
        log.write_text(self.LINES + '[t][0]LogD3D12RHI: Chosen D3D12 Adapter Id = 0\n', encoding='utf-8')
        assert pilot.check_adapter(log, reading()) == 'NVIDIA GeForce RTX 5090'
        log.write_text(self.LINES + '[t][0]LogD3D12RHI: Chosen D3D12 Adapter Id = 1\n', encoding='utf-8')
        with pytest.raises(JobFailure, match=r'adapter \(AMD Radeon\(TM\) Graphics\) that nvidia-smi'):
            pilot.check_adapter(log, reading())
        log.write_text(self.LINES, encoding='utf-8')
        with pytest.raises(JobFailure, match='unknown'):
            pilot.check_adapter(log, reading())


def test_report_body_matches_the_manager_schema():
    metrics = pilot.measure(runs(), 8102)
    gpu = pilot.gpu_of(reading(), 0)
    body = pilot.report_body(metrics, gpu, prep_s=24.06, warmup_s=800.2, render_s=1380.5, frames=23,
                             engine_build='5.8.4-58902052', overrides_sha256='c' * 64, summary='s')
    assert set(body) == PILOT_FIELDS and body['seconds_per_frame'] == 60.02 and body['warmup_ran'] is True
    assert body['gpu'] == {'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607}
    assert pilot.gpu_of(reading(), 3) is None


class TestCsv:

    def _csv(self, path, rows):
        path.write_text('EventsCol,GPUMem/LocalUsedMB,Other\n' + ''.join(rows), encoding='utf-8')

    def test_line_names_the_file(self, tmp_path):
        profile, logs = tmp_path / 'ueprofile', tmp_path / 'logs'
        csv_dir = pilot_csv.csv_dir(profile)
        csv_dir.mkdir(parents=True)
        logs.mkdir()
        self._csv(csv_dir / 'a.csv', ['x,7000.5,1\n', 'x,8102.4,1\n', 'x,99'])
        log = tmp_path / 'render.log'
        log.write_text(f"[t]LogCsvProfiler: Display: Capture Ended. Writing CSV to file : {csv_dir / 'a.csv'}\n",
                       encoding='utf-8')
        assert pilot_csv.collect(log, profile, frozenset(), logs) == 8102
        assert (logs / 'pilot.a.csv').exists() and not (csv_dir / 'a.csv').exists()

    def test_line_outside_the_profile_is_ignored(self, tmp_path):
        log = tmp_path / 'render.log'
        log.write_text(f"LogCsvProfiler: Display: Capture Ended. Writing CSV to file : {tmp_path / 'x.csv'}\n",
                       encoding='utf-8')
        pilot_csv.csv_dir(tmp_path / 'ueprofile').mkdir(parents=True)
        assert pilot_csv.collect(log, tmp_path / 'ueprofile', frozenset(), tmp_path) is None

    def test_without_the_line_the_single_new_file_is_used(self, tmp_path):
        profile = tmp_path / 'ueprofile'
        csv_dir = pilot_csv.csv_dir(profile)
        csv_dir.mkdir(parents=True)
        self._csv(csv_dir / 'old.csv', ['x,1,1\n'])
        before = pilot_csv.listing(profile)
        self._csv(csv_dir / 'new.csv', ['x,5000,1\n'])
        log = tmp_path / 'render.log'
        log.write_text('nothing\n', encoding='utf-8')
        assert pilot_csv.collect(log, profile, before, tmp_path) == 5000
        self._csv(csv_dir / 'n1.csv', ['x,1,1\n'])
        self._csv(csv_dir / 'n2.csv', ['x,1,1\n'])
        assert pilot_csv.collect(log, profile, before | {'new.csv'}, tmp_path) is None

    def test_unparseable_or_missing_column(self, tmp_path):
        path = tmp_path / 'a.csv'
        path.write_text('a,b\n1,2\n', encoding='utf-8')
        assert pilot_csv.peak_local_used_mb(path) is None
        assert pilot_csv.collect(tmp_path / 'missing.log', tmp_path / 'CSV', frozenset(), tmp_path) is None


class TestOutput:

    def test_single(self, tmp_path):
        with pytest.raises(JobFailure, match='no PNG output'):
            output.collect_single(tmp_path)
        (tmp_path / 'sethlans_1.1000.PNG').write_bytes(b'x')
        (tmp_path / 'notes.txt').write_bytes(b'x')
        assert output.collect_single(tmp_path).name == 'sethlans_1.1000.PNG'
        (tmp_path / 'b.png').write_bytes(b'x')
        with pytest.raises(JobFailure, match='produced 2 PNG files; expected 1'):
            output.collect_single(tmp_path)

    def test_subdirectory(self, tmp_path):
        (tmp_path / 'shot').mkdir()
        (tmp_path / 'shot' / 'a.png').write_bytes(b'x')
        with pytest.raises(JobFailure, match=r'subdirectory \(shot/a.png\)'):
            output.collect_single(tmp_path)

    def test_pilot_names(self, tmp_path):
        for frame in (0, 99):
            (tmp_path / output.pilot_name(5, frame)).write_bytes(b'x')
        names = [p.name for p in output.collect_pilot(tmp_path, 5, [0, 99])]
        assert names == ['sethlans_5.0000.png', 'sethlans_5.0099.png']
        with pytest.raises(JobFailure, match='missing'):
            output.collect_pilot(tmp_path, 5, [0, 99, 202])


class TestRenderCheck:

    def _log(self, tmp_path, *lines):
        path = tmp_path / 'render.1.log'
        path.write_text(''.join(f'[t][0]{line}\n' for line in lines), encoding='utf-8')
        return path

    GOOD = ('LogMovieRenderPipeline: Movie Graph Render completed. Duration: +00:00:57.095',
            'MoviePipelineLinearExecutorBase finished 1 jobs in +00:01:00.0', 'RequestExitWithStatus(0, 0, x)')

    def test_success_and_render_time(self, tmp_path):
        check = render_check.check_render(outcome(), self._log(tmp_path, *self.GOOD), 1)
        assert check.retry == '' and render_check.log_validation.render_time_seconds(check.scan, 99) == 57

    def test_cancellations_after_the_exit_line_retry(self, tmp_path):
        log = self._log(tmp_path, *self.GOOD, 'LogComputeFramework: Warning: K: Compilation cancelled.')
        check = render_check.check_render(outcome(), log, 1)
        assert check.retry == 'cancelled' and check.cancelled == 1
        assert str(render_check.retry_failure(check, 60)).startswith('Unreal render had 1 cancelled kernel')

    def test_forced_quit_retries(self, tmp_path):
        check = render_check.check_render(outcome(reason='exited_after_grace', quit_forced=True),
                                          self._log(tmp_path, *self.GOOD), 1)
        assert check.retry == 'quit_forced'
        assert 'did not exit within 60 s after the render finished, twice' in str(render_check.retry_failure(check, 60))

    @pytest.mark.parametrize('kwargs,lines,jobs,text', [
        ({'reason': 'timeout'}, (), 1, 'timed out'), ({'reason': 'hang', 'detail': 'quiet'}, (), 1, 'hung'),
        ({'reason': 'fatal', 'fatal_rule': 'gpu_device_lost', 'detail': 'DXGI'}, (), 1, 'fatal log line'),
        ({'exit_code': 3}, GOOD, 1, 'exit code 3'), ({}, GOOD, 23, 'finished 1 jobs; expected 23'),
        ({}, GOOD[:2], 1, 'RequestExitWithStatus'),
    ])
    def test_failures(self, tmp_path, kwargs, lines, jobs, text):
        with pytest.raises(JobFailure, match=text):
            render_check.check_render(outcome(**kwargs), self._log(tmp_path, *lines), jobs)

    def test_prep_and_warmup_checks(self):
        render_check.check_prep(outcome(exit_code=-1))          # the result file carries the error
        render_check.check_prep(outcome(reason='exited_after_grace'))
        with pytest.raises(JobFailure, match='prep pass timed out'):
            render_check.check_prep(outcome(reason='timeout'))
        render_check.check_warmup(outcome())
        with pytest.raises(JobFailure, match='without its'):
            render_check.check_warmup(outcome(marker=False))
        with pytest.raises(JobFailure, match='log flood'):
            render_check.check_warmup(outcome(reason='fatal', fatal_rule='log_flood', detail=''))
