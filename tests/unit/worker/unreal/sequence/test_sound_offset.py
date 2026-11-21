# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The sound-offset measurement on synthetic signals with a known lag (sequence rendering D23, D24, FR-SA-10, AC-44,
AC-45b, AC-45c, AC-45d, AC-46, AC-49). A render is built as the source mix shifted by the lag (rendered sample
``p`` holds mix sample ``p + lag``), optionally degraded; ``sound_offset.measure`` must find the lag and apply the
confidence rules. Pure stdlib, no real audio file.
"""
import math
import random
import time

import pytest

from sethlans_worker_agent.unreal_executor import sound_offset
from shared.unreal import sound_result
from tests.unit.worker.unreal.sequence._signals import (
    FPS, RATE, SPF, program, section, shifted, shots, source, tone)

SHOT_FRAMES = 90                       # 3 s at 30 fps
RUN = 8 * SHOT_FRAMES                  # 8 shots
N = RUN * SPF


@pytest.fixture(scope='module')
def bed():
    """The raw source: 48 kHz noise bursts with a tone, long enough for every lag the tests use."""
    return program(N + 2 * RATE)


def _measure(src_samples, render_samples, parts=None, src_rate=RATE, looping=False, run=RUN):
    src = source(src_samples, rate=src_rate)
    render = source(render_samples)
    sec = section(src, 0, run + 100, looping=looping)
    result = sound_offset.measure(render, [sec], parts or shots(run // SHOT_FRAMES, SHOT_FRAMES), 0, run, FPS, 1)
    return sound_result.check_offset(result, RATE), src, render


def _piecewise(src, lags):
    """A render whose shot ``i`` (of ``SHOT_FRAMES``) lies at ``lags[i]``."""
    out = []
    for i, lag in enumerate(lags):
        out += shifted(src, lag, N)[i * SHOT_FRAMES * SPF:(i + 1) * SHOT_FRAMES * SPF]
    return out


class TestKnownLag:

    @pytest.mark.parametrize('lag', [9418, -216])
    def test_ac44_the_lag_is_found_through_25_db_of_noise(self, bed, lag):
        result, _src, _render = _measure(bed, shifted(bed, lag, N, snr_db=25))
        assert result['status'] == 'measured' and abs(result['offset_samples'] - lag) <= 32, result
        assert (result['shots_tried'], result['shots_confident'], result['shots_agreeing']) == (8, 8, 8)
        assert [row['shot'] for row in result['shots']] == list(range(1, 9)) and result['reason'] == ''
        assert result['min_peak'] >= 0.80 and result['spread_samples'] <= 32
        assert result['export_s'] is None and result['correlate_s'] >= 0

    def test_ac44_a_64_sample_step_between_shots_is_still_measured(self, bed):
        result, _src, _render = _measure(bed, _piecewise(bed, [9418] * 4 + [9418 + 64] * 4))
        assert result['status'] == 'measured' and result['offset_samples'] in (9418, 9418 + 64)
        assert sound_result.disagreeing_shots(result) == []

    def test_ac45d_shots_far_from_the_median_are_named(self, bed):
        result, _src, _render = _measure(bed, _piecewise(bed, [9418] * 6 + [9418 + 2000] * 2))
        assert result['status'] == 'measured' and result['offset_samples'] == 9418
        assert sound_result.disagreeing_shots(result) == [7, 8]
        assert result['shots_agreeing'] == 6 and result['spread_samples'] == 2000

    def test_a_dc_shifted_render_is_still_lined_up(self, bed):
        """AC-45c: every correlation is zero-mean, so a constant offset in the samples changes nothing."""
        result, _src, _render = _measure(bed, shifted(bed, 4000, N, dc=0.25))
        assert result['status'] == 'measured' and abs(result['offset_samples'] - 4000) <= 32


_RANDOM = random.Random(11)
_LEVELS = [_RANDOM.uniform(0.1, 0.5) for _ in range(400)]
_PARTIALS = [(2 * math.pi * _RANDOM.uniform(80, 6000), _RANDOM.uniform(0, 2 * math.pi)) for _ in range(12)]
RESAMPLED_RUN = 4 * SHOT_FRAMES  # 4 shots: the analytic signal is slow to sample


def _wave(t):
    """Band-limited noise as a continuous function (12 seeded partials below 6 kHz) whose level steps every 0.1 s,
    so it can be sampled at 44.1 kHz for the source and at 48 kHz for the render."""
    level = _LEVELS[int(t * 10) % len(_LEVELS)]
    return level * sum(math.sin(w * t + phase) for w, phase in _PARTIALS) / 6


def _resampled(src_rate):
    """The source sampled at 44.1 kHz (read as ``src_rate``) and the render at 48 kHz, 9,418 samples ahead."""
    src = [_wave(i / 44_100) for i in range(int((RESAMPLED_RUN / FPS + 1) * 44_100))]
    render = [_wave((p + 9418) / RATE) for p in range(RESAMPLED_RUN * SPF)]
    return _measure(src, render, src_rate=src_rate, run=RESAMPLED_RUN)


class TestResampling:

    def test_ac45b_a_44_1_khz_source_in_a_48_khz_render(self):
        result, src_reader, _render = _resampled(44_100)
        assert src_reader.rate == 44_100
        assert result['status'] == 'measured' and abs(result['offset_samples'] - 9418) <= 32, result
        assert result['shots_agreeing'] == 4 and result['spread_samples'] <= 32

    def test_without_resampling_the_shots_would_not_line_up(self):
        """The old formula (the source read at the render's rate) drifts 8.8 % per second: never measured."""
        result, _src, _render = _resampled(RATE)
        assert result['status'] != 'measured' or result['spread_samples'] > 1000, result


class TestNoLagInformation:

    def test_ac45c_a_steady_tone_is_ambiguous(self):
        bed = tone(N + 2 * RATE)
        result, _src, _render = _measure(bed, shifted(bed, 9418, N))
        assert result['status'] == 'low_confidence' and result['offset_samples'] is None
        assert all(row['ambiguous'] for row in result['shots']) and 'ambiguous' in result['reason']

    def test_ac45c_a_half_second_loop_is_ambiguous(self):
        loop = program(RATE // 2, seed=3, burst_s=0.05)
        mix = [loop[i % len(loop)] for i in range(N + 2 * RATE)]
        result, _src, _render = _measure(loop, shifted(mix, 9418, N), looping=True)
        assert result['status'] == 'low_confidence', result
        assert sum(row['ambiguous'] for row in result['shots']) >= 6


class TestEligibilityAndRules:

    def test_ac46_shots_shorter_than_2_s_are_not_correlated(self, bed):
        result, _src, _render = _measure(bed, shifted(bed, 100, N), parts=shots(12, 59))
        assert result['status'] == 'unavailable' and result['reason'] == "no shot of the run lasts 2 s"
        assert result['shots'] == [] and result['offset_samples'] is None

    def test_ac46_silent_shots_are_left_out(self, bed):
        quiet = bed[:3 * SHOT_FRAMES * SPF] + [0.0] * (2 * SHOT_FRAMES * SPF) + bed[5 * SHOT_FRAMES * SPF:]
        result, _src, _render = _measure(quiet, shifted(quiet, 500, N))
        assert [row['shot'] for row in result['shots']] == [1, 2, 3, 6, 7, 8]
        assert result['status'] == 'measured' and result['offset_samples'] == 500

    def test_ac46_a_run_with_no_sound_is_unavailable(self):
        silent = [0.0] * (N + RATE)
        result, _src, _render = _measure(silent, silent[:N])
        assert result['status'] == 'unavailable' and result['reason'] == "no eligible shot has sound in the mix"

    def test_ac46_three_disagreeing_shots_are_low_confidence(self, bed):
        result, _src, _render = _measure(bed, _piecewise(bed, [1000, 1500, 2000]), parts=shots(3, SHOT_FRAMES))
        assert result['status'] == 'low_confidence' and result['shots_confident'] == 3, result
        assert result['reason'].startswith("the shots disagree (1 of 3")

    def test_one_or_two_shots_need_a_peak_of_0_9(self, bed):
        result, _src, _render = _measure(bed, shifted(bed, 700, N), parts=shots(2, SHOT_FRAMES))
        assert result['status'] == 'measured' and result['offset_samples'] == 700 and result['shots_tried'] == 2
        noisy, _src, _render = _measure(bed, shifted(bed, 700, N, snr_db=3), parts=shots(2, SHOT_FRAMES))
        assert noisy['status'] == 'low_confidence' and noisy['offset_samples'] in (None, 700)

    def test_no_section_and_only_unsupported_sections(self):
        render = source([0.1] * RATE)
        none = sound_offset.measure(render, [], shots(2, SHOT_FRAMES), 0, 180, FPS, 1)
        assert none['status'] == 'unavailable' and none['reason'] == "no enabled audio section in the run"
        cue = sound_offset.measure(render, [], shots(2, SHOT_FRAMES), 0, 180, FPS, 1, unsupported=2, export_s=3.0)
        assert cue['reason'] == "every audio section is unsupported (2)" and cue['unsupported_sections'] == 2
        assert sound_result.check_offset(cue, RATE)['export_s'] == 3.0


class TestShotChoice:

    def test_buckets_spread_16_with_first_and_last_tried_first(self):
        items = list(range(40))
        chosen = sound_offset.buckets(items)
        assert len(chosen) == 16 and chosen[0][0] == 0 and chosen[-1][0] == 39
        assert all(1 <= len(bucket) <= 3 for bucket in chosen)
        assert sound_offset.buckets(list(range(5))) == [[0], [1], [2], [3], [4]]

    def test_eligible_needs_2_s_and_60_frames_inside_the_run(self):
        parts = [(1, 0, 59), (2, 59, 119), (3, 119, 300), (4, 300, 400)]
        assert sound_offset.eligible(parts, 0, 400, 30, 1) == [(2, 59, 119), (3, 119, 300), (4, 300, 400)]
        assert sound_offset.eligible(parts, 0, 400, 24, 1) == [(2, 59, 119), (3, 119, 300), (4, 300, 400)]
        assert sound_offset.eligible(parts, 0, 400, 60, 1) == [(3, 119, 300)]  # 2 s is 120 frames at 60 fps
        assert sound_offset.eligible(parts, 60, 350, 30, 1) == [(3, 119, 300)]  # only shots inside [r0, end)


def test_ac49_sixteen_shots_stay_inside_the_cpu_and_memory_bounds():
    run = 16 * 60  # 16 shots of 2 s
    bed = program(run * SPF + 2 * RATE, seed=5)
    src, render = source(bed), source(shifted(bed, 9418, run * SPF))
    started = time.process_time()
    result = sound_offset.measure(render, [section(src, 0, run + 10)], shots(16, 60), 0, run, FPS, 1)
    cpu = time.process_time() - started
    assert result['status'] == 'measured' and result['shots_tried'] == 16 and abs(result['offset_samples'] - 9418) <= 32
    assert cpu < 30, cpu
    largest = max(src.largest_read, render.largest_read)
    assert largest * 2 * 32 < 50 * 1024 * 1024  # stereo samples held as Python floats, well under 50 MB
    assert largest <= 5 * RATE
