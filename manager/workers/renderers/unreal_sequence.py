# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Poll and claim rules for the Unreal sequence job kinds (sequence rendering
D3, FR-SD-3, FR-SR-5).

Neither kind is held by a measurement key or needs a live card reading on
the manager, so both are offered to every worker that passes the Unreal
build and share rules (``unreal_routing``):

* a ``shots`` job runs the prep commandlet only: no GPU work, no warm-up,
  no render;
* an ``audio`` job renders sound only; the worker gates it on its own card
  with the pilot's need rule (``vram_gate.pilot_need_mb``), so the manager
  adds no memory gate at the claim.

Their routing note never names a measurement.
"""

from django.db.models import Q

from ..constants import UnrealKind

# Kinds offered without a measurement key, a memory gate or a card reading.
UNGATED_KINDS = (UnrealKind.SHOTS, UnrealKind.AUDIO)


def ungated_poll_q():
    """The poll part for the ungated kinds (FR-SD-3, FR-SR-5)."""
    return Q(unreal_kind__in=UNGATED_KINDS)


def skips_measured_gate(job):
    """Whether ``job`` skips the measured-peak gate at claim and in its routing note."""
    return job.unreal_kind in UNGATED_KINDS
