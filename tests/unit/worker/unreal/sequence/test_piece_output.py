# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A piece's output (sequence rendering FR-SW-7, D5, AC-10, AC-11), its per-shot timings (FR-SW-9, R7, a piece
down to one frame) and its render check (FR-SW-8, AC-14).
"""
import os

import pytest

from sethlans_worker_agent.unreal_executor import frame_timing, output, render_check
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from tests.unit.worker.unreal._executor_fakes import outcome

P = '[2026.11.20-10.00.00:000][  0]'
DONE = [P + 'LogMovieRenderPipeline: MoviePipelineLinearExecutorBase finished 1 jobs in +00:00:09.0.',
        P + 'LogMovieRenderPipeline: Movie Graph Render completed. Duration: +00:00:05.250']
EXIT = P + 'LogCore: FPlatformMisc::RequestExitWithStatus(0, 0, <NoCallSiteInfo>)'
CRASH = [P + 'LogWindows: Error: === Critical error: ===',
         P + 'LogWindows: Error: Assertion failed: NumberOfLoadsInFlight == 0 [File:AudioStreamingCache.cpp]']


def _write(directory, names):
    for name in names:
        (directory / name).write_bytes(b'x')


class TestCollectRange:

    def test_the_lead_in_is_checked_then_deleted_and_the_kept_frames_come_in_order(self, tmp_path):
        """AC-10: frames 100-129 with lead-in 30 render 0070-0129; 30 kept, 30 deleted."""
        _write(tmp_path, [f'sethlans_9.{n:04d}.png' for n in range(70, 130)] + ['notes.txt'])
        out = output.collect_range(tmp_path, 9, 100, 129, 30, 'png')
        assert [frame for frame, _path, _mtime in out.kept] == list(range(100, 130)) and len(out.lead_mtimes) == 30
        assert sorted(os.listdir(tmp_path)) == sorted([f'sethlans_9.{n:04d}.png' for n in range(100, 130)]
                                                      + ['notes.txt'])

    @pytest.mark.parametrize('width', [5, 7])
    def test_frames_above_9999_are_collected(self, tmp_path, width):
        """AC-11: 5 to 7 digits, whatever the zero-pad."""
        _write(tmp_path, [f'sethlans_9.{n:0{width}d}.exr' for n in range(71_998, 72_001)])
        out = output.collect_range(tmp_path, 9, 71_999, 72_000, 1, 'exr')
        assert [frame for frame, _p, _m in out.kept] == [71_999, 72_000] and len(os.listdir(tmp_path)) == 2

    @pytest.mark.parametrize('names,message', [
        (['sethlans_9.0010.png'], "the render wrote 1 PNG files for 2 frames (missing ['sethlans_9.0011.png'], "
                                  "unexpected [])."),
        (['sethlans_9.0010.png', 'sethlans_9.0011.png', 'sethlans_9.0012.png'],
         "the render wrote 3 PNG files for 2 frames (missing [], unexpected ['sethlans_9.0012.png'])."),
        (['sethlans_9.0010.png', 'sethlans_9.0011.png', 'sethlans_9.00011.png'],
         "the render wrote 3 PNG files for 2 frames (missing [], unexpected ['sethlans_9.0011.png'])."),
        (['sethlans_9.0010.png', 'sethlans_8.0011.png'],
         "the render wrote 2 PNG files for 2 frames (missing ['sethlans_9.0011.png'], unexpected "
         "['sethlans_8.0011.png'])."),
        (['sethlans_9.0010.png', 'sethlans_9.011.png'],
         "the render wrote 2 PNG files for 2 frames (missing ['sethlans_9.0011.png'], unexpected "
         "['sethlans_9.011.png'])."),
    ])
    def test_missing_and_extra_files_fail_and_nothing_is_deleted(self, tmp_path, names, message):
        _write(tmp_path, names)
        with pytest.raises(JobFailure) as caught:
            output.collect_range(tmp_path, 9, 10, 11, 0, 'png')
        assert caught.value.message == message and len(os.listdir(tmp_path)) == len(names)

    def test_a_frame_in_a_subdirectory_is_extra(self, tmp_path):
        (tmp_path / 'sub').mkdir()
        _write(tmp_path, ['sethlans_9.0010.png'])
        _write(tmp_path / 'sub', ['sethlans_9.0011.png'])
        with pytest.raises(JobFailure, match=r"unexpected \['sub/sethlans_9.0011.png'\]"):
            output.collect_range(tmp_path, 9, 10, 11, 0, 'png')

    def test_an_unsafe_output_folder_names_no_path(self, tmp_path, monkeypatch):
        """Review S6: the manager gets the class name, never the worker's cache path."""
        monkeypatch.setattr(output.fs_safe, 'walk_tree', lambda root: (_ for _ in ()).throw(OSError(f'link in {root}')))
        with pytest.raises(JobFailure) as caught:
            output.collect_range(tmp_path, 9, 10, 11, 0, 'png')
        assert caught.value.message == "Unreal render output is unsafe (OSError)."

    def test_range_complete_never_deletes(self, tmp_path):
        _write(tmp_path, ['sethlans_9.0009.exr', 'sethlans_9.0010.exr'])
        assert output.range_complete(tmp_path, 9, 9, 10, 'exr') and len(os.listdir(tmp_path)) == 2
        assert not output.range_complete(tmp_path, 9, 9, 11, 'exr')

    def test_a_pilot_takes_frame_names_of_more_than_four_digits(self, tmp_path):
        _write(tmp_path, ['sethlans_5.00000.png', 'sethlans_5.12345.png'])
        assert [p.name for p in output.collect_pilot(tmp_path, 5, [0, 12345])] == \
            ['sethlans_5.00000.png', 'sethlans_5.12345.png']


class TestTimings:
    SHOTS = ((1, 10, 12), (2, 12, 14))

    def test_frame_times_startup_and_shots(self):
        kept = [(10, 1010.0), (11, 1012.0), (12, 1015.0), (13, 1019.0)]
        timing = frame_timing.piece_timing(kept, [], 1000.0, self.SHOTS)
        # times: median(2, 3, 4) = 3 for the first frame, then 2, 3, 4
        assert [dict(r) for r in timing.shots] == [{'index': 1, 'frames': 2, 'seconds': 5.0, 's_per_frame': 2.5},
                                                   {'index': 2, 'frames': 2, 'seconds': 7.0, 's_per_frame': 3.5}]
        assert (timing.startup_s, timing.lead_in_s, timing.warnings) == (7.0, None, ())

    def test_with_a_lead_in_the_startup_ends_at_the_first_rendered_file(self):
        timing = frame_timing.piece_timing([(10, 1030.0), (11, 1032.0)], [1020.0, 1025.0], 1000.0, ((1, 10, 12),))
        assert (timing.startup_s, timing.lead_in_s) == (18.0, 25.0)
        assert timing.shots[0]['seconds'] == 4.0

    def test_one_kept_frame_after_a_lead_in_uses_the_last_lead_in_file(self):
        timing = frame_timing.piece_timing([(10, 1030.0)], [1020.0, 1026.0], 1000.0, ((1, 10, 11),))
        assert timing.shots[0]['seconds'] == 4.0 and timing.startup_s == 16.0 and timing.warnings == ()

    def test_one_frame_and_no_lead_in_is_an_upper_bound_with_unknown_startup(self):
        timing = frame_timing.piece_timing([(10, 1060.0)], [], 1000.0, ((1, 10, 11),))
        assert timing.shots[0] == {'index': 1, 'frames': 1, 'seconds': 60.0, 's_per_frame': 60.0}
        assert timing.startup_s is None and timing.warnings == (frame_timing.ONE_FRAME_WARNING,)

    def test_a_clock_step_backwards_never_gives_a_negative_time(self):
        timing = frame_timing.piece_timing([(10, 1010.0), (11, 1005.0), (12, 1009.0)], [], 1000.0, ((1, 10, 13),))
        assert timing.shots[0]['seconds'] >= 0.0 and timing.startup_s >= 0.0
        assert all(value >= 0.0 for value in (timing.shots[0]['seconds'], timing.shots[0]['s_per_frame']))


class TestRangeCheck:

    def _log(self, tmp_path, *lines):
        path = tmp_path / 'render.1.log'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    @pytest.mark.parametrize('result', [
        outcome(exit_code=3), outcome(reason='fatal', exit_code=1, fatal_rule='critical_error')])
    def test_the_loads_in_flight_assertion_alone_is_accepted_when_every_frame_exists(self, tmp_path, result):
        """AC-14."""
        log = self._log(tmp_path, *DONE, *CRASH)
        check = render_check.check_range(result, log, lambda: True)
        assert check.accepted and check.accepted_exit == result.exit_code and not check.retry

    @pytest.mark.parametrize('lines,complete', [
        (DONE + CRASH, False),                                                         # a frame is missing
        (DONE + [CRASH[0]], True),                                                     # another crash
        (DONE + CRASH + [P + 'LogD3D12RHI: Error: DXGI_ERROR_DEVICE_REMOVED'], True),  # a second fatal rule
        ([DONE[1]] + CRASH, True),                                                     # MRQ did not finish its job
    ])
    def test_otherwise_the_non_zero_exit_fails(self, tmp_path, lines, complete):
        log = self._log(tmp_path, *lines)
        with pytest.raises(JobFailure):
            render_check.check_range(outcome(exit_code=3), log, lambda: complete)

    def test_a_clean_render_is_checked_as_one_mrq_job(self, tmp_path):
        log = self._log(tmp_path, *DONE, EXIT)
        check = render_check.check_range(outcome(), log, lambda: pytest.fail('not asked on a clean exit'))
        assert not check.accepted and check.accepted_exit is None

    def test_cancelled_compilations_still_ask_for_the_retry(self, tmp_path):
        log = self._log(tmp_path, *DONE, EXIT, P + 'LogComputeFramework: Warning: X: Compilation cancelled.')
        assert render_check.check_range(outcome(), log, lambda: True).retry == 'cancelled'
