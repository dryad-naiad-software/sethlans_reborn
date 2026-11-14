# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/parent_status_rules.py (issue #219).

Pure rules: how a parent's status follows from its children's statuses,
and how the ERROR reason is worded.  No database access.
"""

import pytest

from workers.parent_status_rules import (
    CHILD_DETAIL_MAX,
    COMPLETE,
    FAILED,
    QUEUED,
    REASON_MAX,
    RENDERING,
    ChildSummary,
    assembly_failure_reason,
    failure_reason,
    is_assembly_failure,
    next_parent_state,
)


class TestChildSummary:

    def test_derived_counts(self):
        s = ChildSummary(total=6, queued=1, done=2, error=1, canceled=1)
        assert s.failed == 2
        assert s.active == 2  # 1 queued + 1 rendering
        assert s.started == 5


class TestNextParentState:

    def test_all_done_is_complete(self):
        s = ChildSummary(total=4, done=4)
        assert next_parent_state(s, 4, RENDERING) == COMPLETE

    def test_failed_child_with_siblings_running_stays_rendering(self):
        """Never ERROR while a sibling is still QUEUED or RENDERING."""
        s = ChildSummary(total=4, queued=1, done=1, error=1)
        assert next_parent_state(s, 4, RENDERING) == RENDERING

    def test_failed_child_with_queued_parent_becomes_rendering(self):
        s = ChildSummary(total=4, queued=3, error=1)
        assert next_parent_state(s, 4, QUEUED) == RENDERING

    def test_all_terminal_with_error_is_failed(self):
        s = ChildSummary(total=4, done=3, error=1)
        assert next_parent_state(s, 4, RENDERING) == FAILED

    def test_all_terminal_with_cancel_is_failed(self):
        s = ChildSummary(total=4, done=3, canceled=1)
        assert next_parent_state(s, 4, RENDERING) == FAILED

    def test_all_failed_is_failed(self):
        s = ChildSummary(total=3, error=3)
        assert next_parent_state(s, 3, QUEUED) == FAILED

    def test_requeued_child_returns_error_parent_to_rendering(self):
        """ERROR parent + a requeued child next to DONE siblings."""
        s = ChildSummary(total=4, queued=1, done=3)
        assert next_parent_state(s, 4, 'ERROR') == RENDERING

    def test_everything_requeued_returns_error_parent_to_queued(self):
        s = ChildSummary(total=4, queued=4)
        assert next_parent_state(s, 4, 'ERROR') == QUEUED

    def test_rendering_parent_is_not_downgraded_to_queued(self):
        """A yield-requeue of the only started child keeps RENDERING."""
        s = ChildSummary(total=4, queued=4)
        assert next_parent_state(s, 4, RENDERING) == RENDERING

    def test_untouched_parent_stays_queued(self):
        s = ChildSummary(total=4, queued=4)
        assert next_parent_state(s, 4, QUEUED) == QUEUED

    def test_no_children_is_never_failed(self):
        assert next_parent_state(ChildSummary(), 4, QUEUED) == QUEUED

    @pytest.mark.parametrize('expected', [0, 5])
    def test_complete_requires_expected_count(self, expected):
        """Fewer DONE children than expected is not COMPLETE."""
        s = ChildSummary(total=4, done=4)
        assert next_parent_state(s, expected, RENDERING) != COMPLETE


class TestFailureReason:

    def test_counts_and_first_failure(self):
        s = ChildSummary(total=4, done=2, error=1, canceled=1)
        reason = failure_reason(s, 'tiles', ('Shot_Tile_0_1', 'CUDA out of memory'))
        assert reason == (
            '2 of 4 tiles did not complete (1 failed, 1 canceled). '
            'First failure: Shot_Tile_0_1: CUDA out of memory'
        )

    def test_without_detail(self):
        s = ChildSummary(total=3, done=2, canceled=1)
        assert failure_reason(s, 'frames') == (
            '1 of 3 frames did not complete (1 canceled).'
        )

    def test_blank_child_message_is_omitted(self):
        s = ChildSummary(total=2, done=1, error=1)
        assert 'First failure' not in failure_reason(s, 'frames', ('F', '   '))

    def test_long_child_message_is_truncated(self):
        s = ChildSummary(total=2, done=1, error=1)
        reason = failure_reason(s, 'frames', ('F', 'x' * 5000))
        assert reason.endswith('...')
        assert len(reason) < CHILD_DETAIL_MAX + 100
        assert len(reason) <= REASON_MAX


class TestAssemblyFailureReason:

    def test_includes_exception_text(self):
        reason = assembly_failure_reason('Tile', ValueError('no file'))
        assert reason == 'Tile assembly failed: no file'

    def test_falls_back_to_exception_type(self):
        reason = assembly_failure_reason('Frame', KeyError())
        assert reason == 'Frame assembly failed: KeyError'

    @pytest.mark.parametrize('what', ['Tile', 'Frame'])
    def test_assembly_reasons_are_recognised(self, what):
        assert is_assembly_failure(assembly_failure_reason(what, ValueError('x')))

    @pytest.mark.parametrize('message', [
        '', None, '1 of 4 tiles did not complete (1 failed).',
        'Canceled: all remaining jobs in the project were canceled.',
    ])
    def test_other_reasons_are_not_assembly_failures(self, message):
        assert not is_assembly_failure(message)
