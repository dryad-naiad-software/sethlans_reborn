# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The state machine of Unreal sequence submissions (spec FR-SS-3), as pure
functions over statuses and counts.  ``sequence_status`` applies them to
the database.

Shot, from the statuses of the pieces that cover it, in this order:

* every covering piece DONE -> DONE;
* a covering piece QUEUED or RENDERING -> RENDERING if one is RENDERING or
  DONE, else PENDING;
* none active, at least one ERROR -> ERROR;
* none active, no ERROR, at least one CANCELED -> CANCELED.

Submission, from aggregate counts, in this order:

* ``cancel_requested`` and nothing active -> CANCELED;
* a piece or audio job active -> RENDERING if one is RENDERING or DONE,
  else QUEUED;
* nothing active, a requested item of a DONE shot or a whole item PENDING
  or ASSEMBLING -> ASSEMBLING;
* all shots DONE -> DONE;
* some DONE, the others ERROR or CANCELED -> PARTLY_DONE;
* none DONE, at least one ERROR -> ERROR;
* none DONE, the rest CANCELED -> CANCELED.

Nothing active and every shot final always gives a final state.  A shot
that is not final while nothing is active cannot be reached (every shot
has covering pieces); it reads QUEUED so the next refresh settles it.
"""

from dataclasses import dataclass

from ..constants_sequence import SequenceStatus, ShotStatus
from ..constants_video import VideoStatus
from ..models import JobStatus

ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RENDERING)
STARTED_JOB_STATUSES = (JobStatus.RENDERING, JobStatus.DONE)
OPEN_ITEM_STATUSES = (VideoStatus.PENDING, VideoStatus.ASSEMBLING)


def shot_state(piece_statuses):
    """The FR-SS-3 shot state from its covering pieces' statuses."""
    statuses = list(piece_statuses)
    if not statuses:
        return ShotStatus.PENDING
    if all(status == JobStatus.DONE for status in statuses):
        return ShotStatus.DONE
    if any(status in ACTIVE_JOB_STATUSES for status in statuses):
        started = any(status in STARTED_JOB_STATUSES for status in statuses)
        return ShotStatus.RENDERING if started else ShotStatus.PENDING
    if JobStatus.ERROR in statuses:
        return ShotStatus.ERROR
    return ShotStatus.CANCELED


@dataclass(frozen=True)
class SubmissionCounts:
    """What the submission rules read: shot counts, job counts and open items."""

    shots: int
    done: int = 0
    error: int = 0
    canceled: int = 0
    active_jobs: int = 0
    started_jobs: int = 0
    open_items: bool = False
    cancel_requested: bool = False


def _settled_state(counts):
    if counts.shots and counts.done == counts.shots:
        return SequenceStatus.DONE
    if counts.done + counts.error + counts.canceled != counts.shots:
        return SequenceStatus.QUEUED
    if counts.done:
        return SequenceStatus.PARTLY_DONE
    return SequenceStatus.ERROR if counts.error else SequenceStatus.CANCELED


def submission_state(counts):
    """The FR-SS-3 submission state from :class:`SubmissionCounts`."""
    if counts.cancel_requested and not counts.active_jobs:
        return SequenceStatus.CANCELED
    if counts.active_jobs:
        return SequenceStatus.RENDERING if counts.started_jobs else SequenceStatus.QUEUED
    if counts.open_items:
        return SequenceStatus.ASSEMBLING
    return _settled_state(counts)


def failure_message(counts, first_failure):
    """The submission's error text for ERROR or PARTLY_DONE; ``first_failure`` is ``(name, message)``."""
    failed = counts.error + counts.canceled
    text = f"{failed} of {counts.shots} shots did not finish."
    if first_failure:
        name, message = first_failure
        text += f" {name}: {message}" if message else f" First: {name}."
    return text[:1000]
