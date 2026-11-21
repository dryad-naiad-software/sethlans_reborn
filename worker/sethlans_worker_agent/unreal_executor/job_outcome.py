# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``delivery.Outcome`` of an expected Unreal job failure (Unreal foundation FR-UE-2, FR-UE-17;
moved out of ``job_run`` for the 300-line limit).

``outcome_for(exc, cause, summary, disable_until_restart)``: ``JobFailure`` -> ``error``;
``LocalConflict`` -> ``release`` with its reason; ``Cancelled`` and ``AbortedError`` -> ``cancel``
(the run's cause first); ``ShareUnavailableError`` -> ``share``; ``CacheUnavailableError`` disables
Unreal until restart and is an ``error``; ``SurvivorsLeft`` -> ``survivors``; an unconfined launch
marks the summary's ``job_object`` failed; a refused command line or isolation -> ``error`` with
``"Unreal launch refused: "``; anything else listed in ``EXPECTED`` -> ``error`` with its text.
"""
from typing import Callable

from sethlans_worker_agent.project_archive.errors import (
    AbortedError, ArchiveError, CacheUnavailableError, ShareUnavailableError)
from sethlans_worker_agent.unreal_executor import user_state
from sethlans_worker_agent.unreal_executor.command import CommandError
from sethlans_worker_agent.unreal_executor.delivery import Outcome
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, JobFailure, LocalConflict, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.runner_types import SURVIVORS_MESSAGE, UnconfinedLaunchError

EXPECTED = (JobFailure, LocalConflict, Cancelled, AbortedError, ShareUnavailableError, ArchiveError,
            SurvivorsLeft, UnconfinedLaunchError, CommandError, user_state.IsolationError)


def outcome_for(exc: Exception, cause: Callable[[], str], summary, disable_until_restart: Callable[[str], None]):
    """The ``Outcome`` of one of ``EXPECTED`` (anything else propagates after the cleanup)."""
    if isinstance(exc, JobFailure):
        return Outcome('error', exc.message)
    if isinstance(exc, LocalConflict):
        return Outcome('release', exc.message, exc.reason)
    if isinstance(exc, (Cancelled, AbortedError)):
        return Outcome('cancel', reason=cause() or getattr(exc, 'cause', 'canceled'))
    if isinstance(exc, ShareUnavailableError):
        return Outcome('share', str(exc) or 'share unavailable')
    if isinstance(exc, CacheUnavailableError):
        disable_until_restart(str(exc))
    if isinstance(exc, SurvivorsLeft):
        return Outcome('survivors', SURVIVORS_MESSAGE)
    if isinstance(exc, UnconfinedLaunchError):
        summary.set('job_object', 'failed')
    if isinstance(exc, (CommandError, user_state.IsolationError)):
        return Outcome('error', f"Unreal launch refused: {exc}")
    return Outcome('error', str(exc))
