# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the pure sequence planner ``services/sequence_plan.py``
(Unreal sequence spec FR-SP-1..7, OQ-1 (a), AC-4, AC-5, AC-37, NF-8).

Covers: the worked example of the spec (AC-4); a seeded property test over
random shot lists with gaps and random selections (AC-5: piece size bound,
every chosen frame in exactly one piece, no piece across two runs,
``piece_shots`` clipped, sorted and contiguous, lead-in bounds);
``even_split``; selection by shot name and by frame range; the lead-in
rules (none at the playback start or after a gap, ``min(L, ...)``
otherwise, ``L = 0`` disables, unchosen neighbours count); ``audio_r0``
(AC-37); ``PlanError`` for an empty selection and above ``MAX_PIECES``;
the NF-8 timing bound.  No database.  Shot names are made up.
"""

import random
import time

import pytest

from workers.services import sequence_plan
from workers.services.sequence_plan import (
    Coverage, PlanError, Selection, Shot, audio_r0, choose_parts, even_split, plan, shots_from, split_runs,
)

RATE = (30, 1)
ALL = Selection()


def _shots(lengths, start=0, gaps=None):
    """Contiguous shots of ``lengths`` from ``start``; ``gaps`` maps a shot position to a gap before it."""
    shots, cursor = [], start
    for i, length in enumerate(lengths):
        cursor += (gaps or {}).get(i, 0)
        shots.append(Shot(i + 1, f'sh{(i + 1) * 10:03d}', cursor, cursor + length))
        cursor += length
    return shots


class TestWorkedExample:
    """AC-4: 30 fps, 30 s pieces, 1 s lead-in, one run from the sequence's first frame."""

    def test_pieces_and_leads(self):
        result = plan(_shots([400, 300, 120, 2100, 60]), ALL, RATE, 30, 1000, 0)
        assert [p.end - p.start for p in result.pieces] == [820, 700, 700, 700, 60]
        assert [p.lead for p in result.pieces] == [0, 30, 30, 30, 30]

    def test_piece_shots(self):
        result = plan(_shots([400, 300, 120, 2100, 60]), ALL, RATE, 30, 1000, 0)
        assert result.pieces[0].piece_shots() == [[1, 0, 400], [2, 400, 700], [3, 700, 820]]
        assert result.pieces[2].piece_shots() == [[4, 1520, 2220]]
        assert result.pieces[4].piece_shots() == [[5, 2920, 2980]]
        assert len(result.runs) == 1 and len(result.audio) == 1


def _random_case(rng):
    lengths = [rng.randint(1, 900) for _ in range(rng.randint(1, 40))]
    gaps = {i: rng.randint(1, 40) for i in range(1, len(lengths)) if rng.random() < 0.2}
    shots = _shots(lengths, start=rng.randint(0, 50), gaps=gaps)
    mode = rng.choice(['all', 'names', 'range'])
    if mode == 'names':
        chosen = rng.sample(shots, rng.randint(1, len(shots)))
        return shots, Selection(shots=frozenset(s.name for s in chosen))
    if mode == 'range':
        low, high = shots[0].start, shots[-1].end - 1
        a, b = sorted(rng.randint(low, high) for _ in range(2))
        return shots, Selection(frame_start=a, frame_end=b)
    return shots, ALL


def _check_case(shots, selection, piece_seconds, lead_ms):
    try:
        result = plan(shots, selection, RATE, piece_seconds, lead_ms, shots[0].start)
    except PlanError:
        assert not choose_parts(shots, selection)
        return
    target = max(1, round(piece_seconds * 30))
    lead = round(lead_ms / 1000 * 30)
    chosen = [f for part in result.parts for f in range(part.start, part.end)]
    covered = [f for piece in result.pieces for f in range(piece.start, piece.end)]
    assert sorted(covered) == sorted(chosen) and len(set(covered)) == len(covered)
    coverage = Coverage(shots)
    runs = [(run[0].start, run[-1].end) for run in result.runs]
    for piece in result.pieces:
        assert piece.end - piece.start <= target + target // 2
        assert any(lo <= piece.start and piece.end <= hi for lo, hi in runs)
        rows = piece.piece_shots()
        assert rows[0][1] == piece.start and rows[-1][2] == piece.end
        assert all(a[2] == b[1] for a, b in zip(rows, rows[1:]))
        assert all(start < end for _i, start, end in rows)
        assert 0 <= piece.lead <= lead
        assert piece.start - piece.lead >= coverage.covered_start(piece.start)


class TestProperties:
    """AC-5 over seeded random shot lists, gaps and selections."""

    @pytest.mark.parametrize('seed', range(40))
    def test_random_plans(self, seed):
        rng = random.Random(seed)
        for _ in range(5):
            shots, selection = _random_case(rng)
            _check_case(shots, selection, rng.choice([5, 7, 10, 20]), rng.choice([0, 500, 1000, 3000]))


class TestEvenSplit:

    @pytest.mark.parametrize('start, end, count', [(0, 2100, 3), (10, 1001, 4), (5, 6, 1), (0, 7, 7), (3, 100, 9)])
    def test_contiguous_and_balanced(self, start, end, count):
        ranges = even_split(start, end, count)
        assert len(ranges) == count
        assert ranges[0][0] == start and ranges[-1][1] == end
        assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:]))
        sizes = [b - a for a, b in ranges]
        assert max(sizes) - min(sizes) <= 1


class TestSelection:

    def test_shot_names(self):
        shots = _shots([10, 20, 30])
        parts = choose_parts(shots, Selection(shots=frozenset({'sh010', 'sh030'})))
        assert [(p.index, p.start, p.end) for p in parts] == [(1, 0, 10), (3, 30, 60)]
        assert len(split_runs(parts)) == 2

    def test_frame_range_clips_inclusive_end(self):
        parts = choose_parts(_shots([10, 20, 30]), Selection(frame_start=5, frame_end=34))
        assert [(p.index, p.start, p.end) for p in parts] == [(1, 5, 10), (2, 10, 30), (3, 30, 35)]

    def test_range_in_a_gap_is_empty(self):
        with pytest.raises(PlanError, match='No frame'):
            plan(_shots([10, 10], gaps={1: 20}), Selection(frame_start=12, frame_end=25), RATE, 30, 1000)

    def test_shots_from_discovery_rows(self):
        rows = [{'index': 1, 'name': 'sh010', 'start': 0, 'end': 5}]
        assert shots_from(rows) == [Shot(1, 'sh010', 0, 5)]


class TestLeadIn:
    """OQ-1 (a): every piece above its stretch start gets the lead-in; gaps stop it."""

    def test_none_at_playback_start_and_after_a_gap(self):
        shots = _shots([100, 100], start=10, gaps={1: 50})
        result = plan(shots, ALL, RATE, 30, 1000, 10)
        assert [(p.start, p.lead) for p in result.pieces] == [(10, 0), (160, 0)]

    def test_capped_by_the_stretch_start(self):
        # Chosen shot 2 starts 10 frames into the stretch: only 10 frames of lead-in fit.
        shots = _shots([10, 100])
        result = plan(shots, Selection(shots=frozenset({'sh020'})), RATE, 30, 1000)
        assert [(p.start, p.lead) for p in result.pieces] == [(10, 10)]

    def test_unchosen_neighbour_gives_full_lead(self):
        result = plan(_shots([200, 100]), Selection(shots=frozenset({'sh020'})), RATE, 30, 1000)
        assert result.pieces[0].lead == 30

    def test_zero_disables(self):
        result = plan(_shots([400, 300, 120, 2100, 60]), ALL, RATE, 30, 0)
        assert {p.lead for p in result.pieces} == {0}

    def test_frame_range_inside_a_shot(self):
        result = plan(_shots([1000]), Selection(frame_start=500, frame_end=599), RATE, 30, 1000)
        assert [(p.start, p.end, p.lead) for p in result.pieces] == [(500, 600, 30)]


class TestAudioR0:
    """FR-SP-5, AC-37."""

    def test_after_a_gap_no_audio_lead(self):
        coverage = Coverage(_shots([100, 100], gaps={1: 20}))
        assert audio_r0(coverage, 120, 30) == 120

    def test_short_first_shot_at_playback_start(self):
        shots = _shots([5, 100])
        result = plan(shots, ALL, RATE, 30, 1000, 0)
        assert result.audio[0].r0 == 0

    def test_moves_back_to_eight_frames_before_a_shot_end(self):
        shots = [Shot(1, 'shA', 0, 20), Shot(2, 'shB', 20, 25), Shot(3, 'shC', 25, 100)]
        assert audio_r0(Coverage(shots), 25, 0) == 12
        result = plan(shots, Selection(shots=frozenset({'shC'})), RATE, 30, 0)
        assert result.audio[0] == sequence_plan.AudioRun(25, 100, 12)

    def test_lead_at_least_eight_frames(self):
        assert audio_r0(Coverage(_shots([100, 100])), 100, 3) == 92


class TestLimits:

    def test_too_many_pieces(self, monkeypatch):
        monkeypatch.setattr(sequence_plan, 'MAX_PIECES', 3)
        with pytest.raises(PlanError, match='4 pieces; at most 3'):
            plan(_shots([10, 10, 10, 10], gaps={1: 5, 2: 5, 3: 5}), ALL, RATE, 30, 1000)

    def test_too_many_shots(self, monkeypatch):
        monkeypatch.setattr(sequence_plan, 'MAX_SUBMISSION_SHOTS', 2)
        with pytest.raises(PlanError, match='3 shots'):
            plan(_shots([10, 10, 10]), ALL, RATE, 30, 1000)


class TestSpeed:
    """NF-8: an 800-shot, 72,000-frame submission plans in under 2 s."""

    def test_episode(self):
        shots = _shots([90] * 800)
        began = time.perf_counter()
        result = plan(shots, ALL, (30000, 1001), 30, 1000, 0)
        assert time.perf_counter() - began < 2.0
        assert sum(p.end - p.start for p in result.pieces) == 72_000
