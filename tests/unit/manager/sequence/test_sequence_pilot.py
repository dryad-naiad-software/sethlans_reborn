# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the pilot frames of a sequence submission
``services/sequence_pilot.py`` (Unreal sequence spec FR-SR-1, OQ-2 (b),
AC-43).

Covers: 800 chosen shots give 256 evenly spaced frames, first and last
included, out of 800; the first frame of each unchosen shot a piece's
lead-in reaches into is added; audio lead-in shots only with sound; a
chosen shot is never added twice; ``sample_evenly`` edge cases.  No
database.  Shot names are made up.
"""

import pytest

from workers.services.sequence_pilot import pilot_frames, sample_evenly
from workers.services.sequence_plan import Selection, Shot, plan

RATE = (30, 1)


def _shots(lengths, start=0):
    shots, cursor = [], start
    for i, length in enumerate(lengths):
        shots.append(Shot(i + 1, f'sh{i + 1:04d}', cursor, cursor + length))
        cursor += length
    return shots


def _choose(*names):
    return Selection(shots=frozenset(names))


class TestSampling:
    """AC-43: more than 256 frames are sampled evenly."""

    def test_800_shots(self):
        shots = _shots([90] * 800)
        result = plan(shots, Selection(), RATE, 30, 1000)
        frames, total = pilot_frames(result, shots, sound=False)
        assert total == 800
        assert len(frames) == 256
        assert frames[0] == 0 and frames[-1] == shots[-1].start
        assert frames == sorted(set(frames))
        gaps = {b - a for a, b in zip(frames, frames[1:])}
        assert gaps <= {270, 360}

    def test_under_the_cap_kept_whole(self):
        shots = _shots([10, 20, 30])
        frames, total = pilot_frames(plan(shots, Selection(), RATE, 30, 1000), shots, sound=True)
        assert (frames, total) == ([0, 10, 30], 3)


class TestLeadInShots:
    """OQ-2 (b): the shots a lead-in renders are measured too."""

    def test_unchosen_shot_reached_by_a_piece_lead_in(self):
        # sh0002 starts at 100; its 30-frame lead-in renders frames 70-99 of sh0001.
        shots = _shots([100, 50, 50])
        result = plan(shots, _choose('sh0002'), RATE, 30, 1000)
        frames, total = pilot_frames(result, shots, sound=False)
        assert (frames, total) == ([0, 100], 2)

    def test_lead_in_across_two_short_shots(self):
        shots = _shots([100, 10, 10, 50])
        result = plan(shots, _choose('sh0004'), RATE, 30, 1000)
        frames, _total = pilot_frames(result, shots, sound=False)
        # The lead-in [90, 120) touches the tail of sh0001 and all of sh0002 and sh0003.
        assert frames == [0, 100, 110, 120]

    def test_unreached_shot_not_added(self):
        shots = _shots([100, 100, 50])
        result = plan(shots, _choose('sh0003'), RATE, 30, 1000)
        frames, _total = pilot_frames(result, shots, sound=False)
        assert frames == [100, 200]

    def test_audio_lead_in_only_with_sound(self):
        # Lead-in 0 ms: no piece lead-in, but the audio job starts 8 frames early.
        shots = _shots([100, 50])
        result = plan(shots, _choose('sh0002'), RATE, 30, 0)
        assert result.pieces[0].lead == 0 and result.audio[0].r0 == 92
        assert pilot_frames(result, shots, sound=False)[0] == [100]
        assert pilot_frames(result, shots, sound=True)[0] == [0, 100]

    def test_chosen_shot_not_added_twice(self):
        # A frame range inside sh0001: its lead-in reaches the unrendered head of the same shot.
        shots = _shots([1000])
        result = plan(shots, Selection(frame_start=500, frame_end=599), RATE, 30, 1000)
        assert result.pieces[0].lead == 30
        assert pilot_frames(result, shots, sound=True) == ([500], 1)


class TestSampleEvenly:

    @pytest.mark.parametrize('count', [2, 3, 10, 256])
    def test_first_and_last_kept(self, count):
        values = list(range(1000))
        sample = sample_evenly(values, count)
        assert len(sample) == count
        assert sample[0] == 0 and sample[-1] == 999
        assert sample == sorted(set(sample))

    def test_short_list_unchanged(self):
        assert sample_evenly([3, 5], 256) == [3, 5]
        assert sample_evenly([], 256) == []

    def test_exactly_the_cap(self):
        values = list(range(256))
        assert sample_evenly(values, 256) == values

    def test_one_over_the_cap(self):
        values = list(range(257))
        sample = sample_evenly(values, 256)
        assert len(sample) == 256 and sample[0] == 0 and sample[-1] == 256


class TestMeasuredRanges:
    """Review round (D8, OQ-2 (b)): the key's shot set is the measured set."""

    def test_reached_shots_join_the_chosen_parts(self):
        from workers.services.sequence_pilot import measured_ranges
        shots = _shots([4, 6, 10])
        result = plan(shots, _choose('sh0002'), RATE, 30, 0)
        assert measured_ranges(result, shots, sound=False) == [(4, 10)]
        assert measured_ranges(result, shots, sound=True) == [(4, 10), (0, 4)]
