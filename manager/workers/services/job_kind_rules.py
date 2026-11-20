# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Status rules for manager-created Unreal jobs (Unreal foundation FR-RT-14,
FR-PM-9; sequence rendering D12).

* A pilot, ``shots`` or ``audio`` job becomes DONE only through its own
  result endpoint, never through a PATCH or PUT, whoever sends it.
* A failed pilot or ``shots`` job is never set QUEUED again: the
  measurement retry, or a new discovery, replaces it.  (Its key or its
  discovery has already been settled as failed.)
"""

from ..constants import UnrealKind

# kind -> (label, result endpoint)
RESULT_ACTIONS = {
    UnrealKind.PILOT: ('Pilot', 'pilot-result'),
    UnrealKind.SHOTS: ('Shots', 'shots-result'),
    UnrealKind.AUDIO: ('Audio', 'sound-result'),
}
NO_REQUEUE_KINDS = frozenset({UnrealKind.PILOT, UnrealKind.SHOTS})


def pilot_done_message(job):
    """The one refusal text for setting a manager-created job DONE (FR-RT-14, D12)."""
    label, path = RESULT_ACTIONS.get(job.unreal_kind, RESULT_ACTIONS[UnrealKind.PILOT])
    return f"{label} jobs complete through POST /api/jobs/{job.pk}/{path}/."


def pilot_requeue_message(job):
    """The refusal text for requeuing a pilot (FR-PM-9) or a ``shots`` job."""
    if job.unreal_kind == UnrealKind.SHOTS:
        return "Read the shots again with POST /api/sequence-discoveries/."
    return (
        f"Use POST /api/projects/{job.asset.project_id}/unreal-measurement/retry/ "
        "to measure again."
    )


def done_only_by_result(job):
    """Whether ``job`` becomes DONE only through its result endpoint."""
    return job.unreal_kind in RESULT_ACTIONS


def never_requeued(job):
    """Whether ``job`` may never be set QUEUED again once it failed."""
    return job.unreal_kind in NO_REQUEUE_KINDS
