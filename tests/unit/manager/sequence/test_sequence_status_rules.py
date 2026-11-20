# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the sequence state machine ``services/sequence_status_rules.py``
(Unreal sequence spec FR-SS-3, AC-34).

Covers: ``shot_state`` over every mix of 1 to 3 covering piece statuses,
checked against the FR-SS-3 table written out row by row; the explicit
table rows; ``submission_state`` over every mix of DONE, ERROR and CANCELED
for 3 shots, with and without open result items (sound, merged copies and
a failed audio job all reduce to "an item is open" or not once no job is
active), with and without ``cancel_requested``, with active and started
jobs; every mix with nothing active ends final unless an item is open;
``failure_message``.  No database.
"""

from itertools import product

import pytest

from workers.constants_sequence import FINAL_SEQUENCE_STATUSES, SequenceStatus, ShotStatus
from workers.models import JobStatus
from workers.services.sequence_status_rules import (
    SubmissionCounts, failure_message, shot_state, submission_state,
)

JOB_STATUSES = [JobStatus.QUEUED, JobStatus.RENDERING, JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED]
FINAL_SHOT = [ShotStatus.DONE, ShotStatus.ERROR, ShotStatus.CANCELED]


def _table_shot(statuses):
    """FR-SS-3's shot rows, in their order."""
    if all(s == 'DONE' for s in statuses):
        return 'DONE'
    if any(s in ('QUEUED', 'RENDERING') for s in statuses):
        return 'RENDERING' if any(s in ('RENDERING', 'DONE') for s in statuses) else 'PENDING'
    if 'ERROR' in statuses:
        return 'ERROR'
    return 'CANCELED'


def _all_mixes():
    for size in (1, 2, 3):
        yield from product(JOB_STATUSES, repeat=size)


class TestShotState:

    @pytest.mark.parametrize('statuses', list(_all_mixes()), ids=lambda s: '-'.join(s))
    def test_every_mix_follows_the_table(self, statuses):
        assert shot_state(statuses) == _table_shot(statuses)

    @pytest.mark.parametrize('statuses, expected', [
        (['DONE', 'DONE'], 'DONE'),
        (['QUEUED'], 'PENDING'),
        (['QUEUED', 'QUEUED'], 'PENDING'),
        (['QUEUED', 'DONE'], 'RENDERING'),
        (['RENDERING'], 'RENDERING'),
        (['QUEUED', 'ERROR'], 'PENDING'),
        (['ERROR', 'DONE'], 'ERROR'),
        (['ERROR', 'CANCELED'], 'ERROR'),
        (['CANCELED', 'DONE'], 'CANCELED'),
        (['CANCELED'], 'CANCELED'),
    ])
    def test_table_rows(self, statuses, expected):
        assert shot_state(statuses) == expected

    def test_no_covering_piece_is_pending(self):
        assert shot_state([]) == ShotStatus.PENDING

    def test_accepts_an_iterator(self):
        assert shot_state(iter(['DONE', 'DONE'])) == ShotStatus.DONE


def _counts(shots, **kwargs):
    return SubmissionCounts(
        shots=len(shots), done=shots.count('DONE'), error=shots.count('ERROR'),
        canceled=shots.count('CANCELED'), **kwargs,
    )


def _settled(shots):
    if all(s == 'DONE' for s in shots):
        return 'DONE'
    if 'DONE' in shots:
        return 'PARTLY_DONE'
    return 'ERROR' if 'ERROR' in shots else 'CANCELED'


SHOT_MIXES = list(product(FINAL_SHOT, repeat=3))


class TestSubmissionState:
    """AC-34: every mix of final shots and items ends in the listed state."""

    @pytest.mark.parametrize('shots', SHOT_MIXES, ids=lambda s: '-'.join(s))
    @pytest.mark.parametrize('open_items', [False, True], ids=['no-open-item', 'open-item'])
    def test_nothing_active(self, shots, open_items):
        state = submission_state(_counts(list(shots), open_items=open_items))
        if open_items:
            assert state == SequenceStatus.ASSEMBLING
        else:
            assert state == _settled(shots)
            assert state in FINAL_SEQUENCE_STATUSES

    @pytest.mark.parametrize('shots', SHOT_MIXES, ids=lambda s: '-'.join(s))
    @pytest.mark.parametrize('open_items', [False, True])
    def test_cancel_requested_and_nothing_active_is_canceled(self, shots, open_items):
        counts = _counts(list(shots), open_items=open_items, cancel_requested=True)
        assert submission_state(counts) == SequenceStatus.CANCELED

    @pytest.mark.parametrize('cancel_requested', [False, True])
    @pytest.mark.parametrize('open_items', [False, True])
    def test_active_job_wins(self, cancel_requested, open_items):
        shots = ['DONE', 'PENDING', 'PENDING']
        queued = _counts(shots, active_jobs=2, open_items=open_items, cancel_requested=cancel_requested)
        started = _counts(shots, active_jobs=2, started_jobs=1, open_items=open_items,
                          cancel_requested=cancel_requested)
        assert submission_state(queued) == SequenceStatus.QUEUED
        assert submission_state(started) == SequenceStatus.RENDERING

    def test_unfinished_shot_with_nothing_active_reads_queued(self):
        assert submission_state(_counts(['DONE', 'PENDING', 'ERROR'])) == SequenceStatus.QUEUED

    @pytest.mark.parametrize('shots, expected', [
        (['DONE', 'DONE', 'DONE'], 'DONE'),
        (['DONE', 'ERROR', 'CANCELED'], 'PARTLY_DONE'),
        (['ERROR', 'CANCELED', 'CANCELED'], 'ERROR'),
        (['CANCELED', 'CANCELED', 'CANCELED'], 'CANCELED'),
    ])
    def test_table_rows(self, shots, expected):
        assert submission_state(_counts(shots)) == expected

    def test_every_reachable_settled_mix_is_final(self):
        for shots, open_items, cancel in product(SHOT_MIXES, [False, True], [False, True]):
            state = submission_state(_counts(list(shots), open_items=open_items, cancel_requested=cancel))
            assert state in FINAL_SEQUENCE_STATUSES or (open_items and not cancel)


class TestFailureMessage:

    def test_names_the_first_failure(self):
        counts = _counts(['DONE', 'ERROR', 'CANCELED'])
        assert failure_message(counts, ('sh020', 'GPU lost')) == "2 of 3 shots did not finish. sh020: GPU lost"

    def test_without_a_message(self):
        counts = _counts(['ERROR', 'DONE', 'DONE'])
        assert failure_message(counts, ('sh010', '')) == "1 of 3 shots did not finish. First: sh010."

    def test_without_a_failure_row(self):
        assert failure_message(_counts(['ERROR']), None) == "1 of 1 shots did not finish."

    def test_capped(self):
        assert len(failure_message(_counts(['ERROR']), ('sh010', 'x' * 5000))) == 1000
