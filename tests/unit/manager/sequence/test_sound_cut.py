# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`workers.services.sound_cut` (Unreal sequence spec
FR-SV-5, D15, AC-17).

Covers: the cut window with the D15 sign (a positive offset = the sound
leads, so the window starts earlier); AC-17, a 3-shot run at 48 kHz and
30 fps cut with +196 ms gives exactly ``frames x 1,600`` samples per shot,
the first starting with 9,408 samples of silence; a negative offset pads
the end; a 30000/1001 rate rounds each boundary once, so the cuts of a run
join without a gap; a window wholly outside the file is silence; joining
in order and refusing mixed formats.  The cut does not depend on whether
MRG skipped its latency compensation (D15: the measured offset covers it),
so nothing here takes that flag.  Files are made-up synthetic signals.
"""

import array
import wave

import pytest

from workers.services.sound_cut import cut_shot, cut_window, join, samples_per_frame, write_cut

RATE = 48_000


def _write(path, samples, channels=1, rate=RATE, width=2):
    """A mono ramp ``0, 1, 2, ...`` (mod 30,000) so every sample says where it came from."""
    data = array.array('h', [i % 30_000 for i in range(samples) for _ in range(channels)])
    with wave.open(str(path), 'wb') as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(data.tobytes())
    return str(path)


def _read(path):
    with wave.open(str(path), 'rb') as reader:
        return reader.getparams(), array.array('h', reader.readframes(reader.getnframes()))


class TestWindow:
    def test_thirty_fps_is_1600_samples_a_frame(self):
        assert samples_per_frame(RATE, 30, 1) == 1600

    def test_positive_offset_moves_the_window_earlier(self):
        assert cut_window(0, 90, 0, RATE, 30, 1, 9408) == (-9408, 144_000 - 9408)

    def test_negative_offset_moves_it_later(self):
        assert cut_window(10, 20, 4, RATE, 30, 1, -216) == (9600 + 216, 25_600 + 216)

    def test_ntsc_rate_rounds_each_boundary_once(self):
        bounds = [(0, 7), (7, 19), (19, 30)]
        windows = [cut_window(s, e, 0, RATE, 30000, 1001, 0) for s, e in bounds]
        assert [w[0] for w in windows[1:]] == [w[1] for w in windows[:-1]]
        assert windows[-1][1] == round(30 * RATE * 1001 / 30000)


class TestAc17:
    """AC-17: a 3-shot run, +196 ms at 48 kHz, 30 fps, the run at the sequence start."""

    SHOTS = [(0, 40), (40, 70), (70, 100)]

    def test_shot_lengths_and_leading_silence(self, tmp_path):
        source = _write(tmp_path / 'run.wav', 100 * 1600)
        lengths = []
        for number, (start, end) in enumerate(self.SHOTS):
            target = tmp_path / f'shot{number}.wav'
            lengths.append(cut_shot(source, str(target), (start, end), 0, (30, 1), 9408))
            params, samples = _read(target)
            assert params.nframes == (end - start) * 1600
            assert (params.nchannels, params.sampwidth, params.framerate) == (1, 2, RATE)
            if number == 0:
                assert set(samples[:9408]) == {0} and samples[9408] == 0 and samples[9409] == 1
            else:
                assert samples[0] == (start * 1600 - 9408) % 30_000
        assert lengths == [64_000, 48_000, 48_000]

    def test_the_cuts_join_back_to_the_shifted_run(self, tmp_path):
        source = _write(tmp_path / 'run.wav', 100 * 1600)
        cuts = []
        for number, part in enumerate(self.SHOTS):
            cuts.append(str(tmp_path / f'shot{number}.wav'))
            cut_shot(source, cuts[-1], part, 0, (30, 1), 9408)
        whole = tmp_path / 'whole.wav'
        assert join(cuts, str(whole)) == 160_000
        _params, samples = _read(whole)
        assert samples[9408:] == array.array('h', [i % 30_000 for i in range(160_000 - 9408)])


class TestEdges:
    def test_negative_offset_pads_the_end(self, tmp_path):
        source = _write(tmp_path / 'run.wav', 1000)
        target = tmp_path / 'cut.wav'
        assert write_cut(source, str(target), 900, 1100) == 200
        _params, samples = _read(target)
        assert samples[:100] == array.array('h', range(900, 1000))
        assert set(samples[100:]) == {0}

    @pytest.mark.parametrize('first, last', [(-500, -100), (2000, 2300)], ids=['before', 'after'])
    def test_window_outside_the_file_is_silence(self, tmp_path, first, last):
        source = _write(tmp_path / 'run.wav', 1000)
        target = tmp_path / 'cut.wav'
        write_cut(source, str(target), first, last)
        params, samples = _read(target)
        assert params.nframes == last - first and set(samples) == {0}

    def test_stereo_keeps_its_channels(self, tmp_path):
        source = _write(tmp_path / 'run.wav', 500, channels=2)
        target = tmp_path / 'cut.wav'
        write_cut(source, str(target), -10, 20)
        params, samples = _read(target)
        assert (params.nchannels, params.nframes) == (2, 30)
        assert list(samples[:20]) == [0] * 20 and list(samples[20:24]) == [0, 0, 1, 1]

    def test_join_refuses_mixed_formats(self, tmp_path):
        first = _write(tmp_path / 'a.wav', 10)
        second = _write(tmp_path / 'b.wav', 10, rate=44_100)
        with pytest.raises(ValueError, match='differ in format'):
            join([first, second], str(tmp_path / 'whole.wav'))

    def test_join_needs_a_source(self, tmp_path):
        with pytest.raises(ValueError):
            join([], str(tmp_path / 'whole.wav'))
