# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the claim decision fails closed, and ``requeue_or_fail``
checks the job's own connection (chunk 1 review follow-ups).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils.translation import gettext_lazy

from workers.models import JobStatus
from workers.views import job_worker_actions, stuck_jobs


def _job(**extra):
    values = {
        'renderer': 'BLENDER', 'status': JobStatus.QUEUED,
        'assigned_worker_id': None, 'is_paused': False,
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _strategy(rejection, reclaim=False):
    strategy = MagicMock()
    strategy.idempotent_reclaim.return_value = reclaim
    strategy.claim_rejection.return_value = rejection
    return strategy


@pytest.mark.parametrize('rejection', [
    gettext_lazy("Lazy rejection"), {'reason': 'dict rejection'}, 0, '',
], ids=['lazy-str', 'dict', 'falsy-int', 'empty-str'])
def test_any_non_none_rejection_is_returned_for_409(rejection):
    """Only ``None`` means "claim"; other values, of any type, reject."""
    with patch.object(job_worker_actions, 'get_strategy', return_value=_strategy(rejection)):
        outcome = job_worker_actions._claim_outcome(_job(), SimpleNamespace(pk=1))
    assert outcome is rejection
    assert outcome is not None and outcome is not job_worker_actions._RECLAIM


def test_none_rejection_claims():
    with patch.object(job_worker_actions, 'get_strategy', return_value=_strategy(None)):
        assert job_worker_actions._claim_outcome(_job(), SimpleNamespace(pk=1)) is None


def test_availability_uses_the_fk_id_not_the_relation():
    """No lazy Worker SELECT while the job row is locked."""
    job = _job(assigned_worker_id=7)
    with patch.object(job_worker_actions, 'get_strategy', return_value=_strategy(None)):
        outcome = job_worker_actions._claim_outcome(job, SimpleNamespace(pk=1))
    assert outcome == "Job is not available for claiming."


def test_reclaim_by_assignee_when_allowed():
    job = _job(status=JobStatus.RENDERING, assigned_worker_id=5)
    with patch.object(job_worker_actions, 'get_strategy', return_value=_strategy(None, reclaim=True)):
        assert job_worker_actions._claim_outcome(job, SimpleNamespace(pk=5)) is job_worker_actions._RECLAIM


@pytest.mark.parametrize('alias, expected', [('replica', 'replica'), (None, 'default')])
def test_requeue_or_fail_checks_the_jobs_own_connection(alias, expected):
    job = MagicMock(auto_requeue_count=0)
    job._state.db = alias
    with patch.object(stuck_jobs, 'transaction') as tx:
        tx.get_connection.return_value.in_atomic_block = False
        with pytest.raises(RuntimeError):
            stuck_jobs.requeue_or_fail(job, 'msg')
    tx.get_connection.assert_called_once_with(using=expected)
    job.save.assert_not_called()
