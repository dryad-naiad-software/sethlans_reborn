# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pure rules for deriving a parent's status from its children (issue #219).

A *parent* is a ``TiledJob``, an ``Animation`` or an ``AnimationFrame``.
Its children are render ``Job`` rows (tiles or frames), or, for a tiled
``Animation``, its ``AnimationFrame`` rows.  This module holds no ORM
code so the rules can be unit-tested in isolation; the DB-facing side
lives in ``parent_status.py``.

Rules
-----
* **COMPLETE** — every expected child is DONE.  The caller then assembles
  (tiled job / tiled frame) or marks the parent DONE (animation).
* **FAILED** — every child is terminal (DONE/ERROR/CANCELED) and at least
  one is not DONE.  The parent goes to ERROR with a reason.  A child that
  is still QUEUED or RENDERING keeps the parent active, so the parent never
  flips to ERROR while siblings render.  Stuck-job auto-requeue only acts
  on RENDERING children (non-terminal), so a child that may still be
  auto-requeued keeps the parent active too.  CANCELED counts like ERROR,
  matching ``projects.cancel_all_jobs`` (``TiledJobStatus`` has no
  CANCELED value).
* **Active** — anything else.  ``RENDERING`` once any child has left
  QUEUED (or the parent was already RENDERING), else ``QUEUED``.  A parent
  in ERROR returns to an active state as soon as a failed child is
  requeued, so the retry can still complete it.
"""

from dataclasses import dataclass

COMPLETE = 'COMPLETE'
FAILED = 'FAILED'
QUEUED = 'QUEUED'
RENDERING = 'RENDERING'

# Cap for a single child's error text copied into the parent's reason.
CHILD_DETAIL_MAX = 500
REASON_MAX = 2000


@dataclass(frozen=True)
class ChildSummary:
    """Counts of a parent's children by status."""

    total: int = 0
    queued: int = 0
    done: int = 0
    error: int = 0
    canceled: int = 0
    render_time: int = 0

    @property
    def failed(self):
        """Children that ended without producing output."""
        return self.error + self.canceled

    @property
    def active(self):
        """Children that are not terminal (QUEUED, RENDERING, ...)."""
        return self.total - self.done - self.failed

    @property
    def started(self):
        """Children that have left QUEUED."""
        return self.total - self.queued


def next_parent_state(summary, expected, current_status):
    """Return COMPLETE, FAILED, QUEUED or RENDERING for a parent.

    Args:
        summary: ``ChildSummary`` of the parent's children.
        expected: number of children the parent needs DONE to complete.
        current_status: the parent's current status string.
    """
    if expected > 0 and summary.done >= expected:
        return COMPLETE
    if summary.total > 0 and summary.active == 0 and summary.failed > 0:
        return FAILED
    if current_status == RENDERING or summary.started > 0:
        return RENDERING
    return QUEUED


def _truncate(text, limit):
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + '...'


def failure_reason(summary, noun, first_failure=None):
    """Build a human-readable reason for a FAILED parent.

    Args:
        summary: ``ChildSummary`` of the children.
        noun: plural child noun, e.g. ``'tiles'`` or ``'frames'``.
        first_failure: optional ``(label, message)`` of one failed child.

    Example:
        ``1 of 4 tiles did not complete (1 failed). First failure:
        Shot_Tile_0_1: CUDA out of memory``
    """
    parts = []
    if summary.error:
        parts.append(f"{summary.error} failed")
    if summary.canceled:
        parts.append(f"{summary.canceled} canceled")
    reason = (
        f"{summary.failed} of {summary.total} {noun} did not complete"
        f" ({', '.join(parts)})."
    )
    if first_failure:
        label, message = first_failure
        detail = _truncate(message, CHILD_DETAIL_MAX)
        if detail:
            reason += f" First failure: {label}: {detail}"
    return _truncate(reason, REASON_MAX)


ASSEMBLY_FAILURE_PREFIXES = ('Tile assembly failed: ', 'Frame assembly failed: ')


def assembly_failure_reason(what, exc):
    """Reason stored when image assembly raises (``what``: Tile or Frame)."""
    message = _truncate(str(exc), CHILD_DETAIL_MAX) or type(exc).__name__
    return f"{what} assembly failed: {message}"


def is_assembly_failure(error_message):
    """True when an ERROR reason was written by a failed image assembly.

    A parent in that state has every child DONE; it must not be claimed
    for assembly again by a later child refresh.
    """
    return (error_message or '').startswith(ASSEMBLY_FAILURE_PREFIXES)
