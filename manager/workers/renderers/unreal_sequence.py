# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Poll and claim rules for the Unreal sequence job kinds (sequence rendering
D3, FR-SD-3; per-machine memory test D10).

* a ``shots`` job runs the prep commandlet only: no GPU work, no warm-up,
  no render.  It is held by no measurement key and needs no card reading,
  so it is offered to every worker that passes the Unreal build and share
  rules (``unreal_routing``).
* an ``audio`` job renders sound only.  It is gated like a piece by its
  submission's piece key (D10): offered only to a worker with a ``fits``
  result for that key (``unreal_machine``).

:func:`output_size_text` gives the output-size part of a memory note
(FR-SR-4).
"""

from ..constants import UnrealKind
from ..constants_sequence import OUTPUT_SIZE_KEY, PILOT_FRAMES_KEY, RESOLUTION_KEY

# Kinds offered without a measurement key, a memory gate or a card reading.
UNGATED_KINDS = (UnrealKind.SHOTS,)


def skips_measured_gate(job):
    """Whether ``job`` skips the measured gate at claim and in its routing note."""
    return job.unreal_kind in UNGATED_KINDS


CANCELED_VERSION_MESSAGE = "This job's sequence version was canceled."


def version_canceled(job):
    """Whether ``job`` belongs to a sequence version with a cancel requested (claim guard, FR-SS-6).

    One read of the submission row, no lock (the claim holds the Job lock;
    D22: Job -> Submission).  ``cancel/`` locks every job of the version
    before it cancels them and sets ``cancel_requested``: a claim that took
    the Job lock first is canceled by that update, and a later claim (for
    example of a piece requeued by ``rerender/``) is refused here.
    """
    submission_id = getattr(job, 'sequence_submission_id', None)
    if submission_id is None:
        return False
    from ..models import SequenceSubmission
    return SequenceSubmission.objects.filter(pk=submission_id, cancel_requested=True).exists()


# FR-SA-2: the sound render's output size.
AUDIO_SIZE = (128, 72)


def _size(value):
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(
            isinstance(v, int) and not isinstance(v, bool) for v in value):
        return value
    return None


def output_size_text(job):
    """FR-SR-4: ``at <w>x<h> (<size source>)`` for the routing note's memory part.

    The size source is "the submission's size" for a size set on a sequence
    piece or pilot (``unreal.resolution``), "the job's size" for one set on
    a standalone job, and "the preset's size" otherwise (a piece carries the
    preset's size from its discovery in ``unreal.output_size``; a job
    without either reads "at the preset's size").
    """
    if job.unreal_kind == UnrealKind.AUDIO:
        return f"at {AUDIO_SIZE[0]}x{AUDIO_SIZE[1]} (the sound render's size)"
    settings = job.render_settings if isinstance(job.render_settings, dict) else {}
    chosen = _size(settings.get(RESOLUTION_KEY))
    if chosen is not None:
        sequence = getattr(job, 'sequence_submission_id', None) is not None or PILOT_FRAMES_KEY in settings
        source = "the submission's size" if sequence else "the job's size"
        return f"at {chosen[0]}x{chosen[1]} ({source})"
    preset = _size(settings.get(OUTPUT_SIZE_KEY))
    if preset is not None:
        return f"at {preset[0]}x{preset[1]} (the preset's size)"
    return "at the preset's size"
