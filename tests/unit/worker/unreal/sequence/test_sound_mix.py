# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The excerpt reader and the mix of a run's sound sources (sequence rendering D23, FR-SA-10, AC-45): start offsets,
volume, looping, a section that starts before ``r0``, linear-interpolation resampling, integer PCM of 8 to 32 bits
and silence outside a wave.
"""
import io
import wave

import pytest

from sethlans_worker_agent.unreal_executor import sound_mix
from tests.unit.worker.unreal.sequence._signals import RATE, section, source, wav_bytes

RAMP = [i / 100_000 for i in range(48_000)]  # 1 s at 48 kHz: sample i holds i / 100,000


def _close(a, b, tol=2e-4):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


class TestSource:

    @pytest.mark.parametrize('width,tol', [(1, 1 / 64), (2, 1e-4), (3, 1e-6), (4, 1e-6)])
    def test_integer_pcm_of_every_width_reads_as_mono_floats(self, width, tol):
        values = [0.5, -0.25, 0.0, 0.75]
        src = source(values * 10, channels=3, width=width)
        assert src.width == width and _close(src.read(0, 4), values, tol)

    def test_stereo_is_the_mean_of_its_channels(self):
        frames = [(1000, -1000), (8000, 16000)]
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as writer:
            writer.setnchannels(2), writer.setsampwidth(2), writer.setframerate(RATE)
            writer.writeframes(b''.join(a.to_bytes(2, 'little', signed=True) + b.to_bytes(2, 'little', signed=True)
                                        for a, b in frames))
        src = sound_mix.Source(wave.open(io.BytesIO(buffer.getvalue()), 'rb'))
        assert _close(src.read(0, 2), [0.0, 12000 / 32768], 1e-9)

    def test_outside_the_wave_is_silence_unless_looping(self):
        src = source([0.1, 0.2, 0.3, 0.4], channels=1)
        assert _close(src.excerpt(-2, 6), [0, 0, 0.1, 0.2, 0.3, 0.4, 0, 0])
        assert _close(src.excerpt(-2, 6, looping=True), [0.3, 0.4, 0.1, 0.2, 0.3, 0.4, 0.1, 0.2])
        assert _close(src.excerpt(3, 5, looping=True), [0.4, 0.1]) and src.excerpt(5, 5) == []
        assert src.largest_read == 4

    def test_a_float_wave_is_refused(self):
        with pytest.raises(ValueError):
            sound_mix.Source(type('W', (), {'getparams': lambda self: (1, 5, RATE, 10), 'getcomptype': lambda self: 'NONE'})())


class TestMix:

    def test_ac45_start_offset_and_volume(self):
        """A section from frame 30 (1 s at 30 fps) with a 4,800-sample start offset and volume 0.5."""
        src = source(RAMP, channels=1)
        out = sound_mix.mix([section(src, start=30, end=90, offset=4800, volume=0.5)], 0, RATE, 30, 1, 47_998, 6)
        assert _close(out, [0.0, 0.0, 0.024, 0.024005, 0.02401, 0.024015], 1e-5)

    def test_ac45_a_looped_one_second_wave_over_three_seconds_is_three_copies(self):
        src = source(RAMP, channels=1)
        out = sound_mix.mix([section(src, start=0, end=90, looping=True)], 0, RATE, 30, 1, 0, 3 * RATE)
        assert _close(out[:RATE], out[RATE:2 * RATE], 1e-9) and _close(out[RATE:2 * RATE], out[2 * RATE:], 1e-9)
        assert _close(out[:RATE], [round(v * 32767) / 32767 for v in RAMP], 1e-4)

    def test_ac45_a_section_that_starts_before_r0_plays_from_the_right_position(self):
        """A music bed from frame 0 heard in a run rendered from frame 60: render sample 0 is the bed's 2 s mark."""
        bed = [i / 300_000 for i in range(4 * RATE)]
        src = source(bed, channels=1)
        out = sound_mix.mix([section(src, start=0, end=200)], 60, RATE, 30, 1, 0, 3)
        assert _close(out, [bed[2 * RATE + k] for k in range(3)], 1e-4)

    def test_a_section_stops_at_its_end_and_values_are_clipped(self):
        src = source([0.9] * RATE, channels=1)
        out = sound_mix.mix([section(src, end=15), section(src, end=15)], 0, RATE, 30, 1, 23_998, 4)
        assert _close(out, [1.0, 1.0, 0.0, 0.0], 1e-9)  # two 0.9 sections clip to 1; frame 15 starts at 24,000

    def test_a_44_1_khz_source_is_resampled_by_linear_interpolation(self):
        """Render sample n reads source position n x 44,100 / 48,000 (here 0.91875 per sample)."""
        ramp = [i / 50_000 for i in range(44_100)]
        src = source(ramp, rate=44_100, channels=1)
        out = sound_mix.mix([section(src, end=60)], 0, RATE, 30, 1, 1000, 3)
        assert _close(out, [n * 44_100 / 48_000 / 50_000 for n in (1000, 1001, 1002)], 2e-5)

    def test_a_frame_rate_of_30000_1001_places_sections_by_their_exact_time(self):
        src = source(RAMP, channels=1)
        out = sound_mix.mix([section(src, start=30, end=90)], 0, RATE, 30000, 1001, 48_048, 2)
        assert _close(out, [0.0, 0.00001], 2e-5)  # frame 30 starts 1.001 s (48,048 samples) in


def test_wav_bytes_round_trips_through_the_reader():
    data = wav_bytes([0.25, -0.5], channels=2)
    with wave.open(io.BytesIO(data), 'rb') as reader:
        assert reader.getparams()[:4] == (2, 2, RATE, 2)
