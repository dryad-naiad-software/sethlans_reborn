# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Poll and claim rules for the Unreal sequence job kinds (sequence rendering
D3, FR-SD-3).

A ``shots`` job runs the prep commandlet only: no GPU work, no warm-up, no
render.  It is held by no measurement key and needs no live card reading,
so it is offered to every worker that passes the Unreal build and share
rules (``unreal_routing``), and its routing note never names GPU memory.
``audio`` jobs join with their gate in chunk 5.
"""

from django.db.models import Q

from ..constants import UnrealKind

# Kinds offered without a measurement key, a memory gate or a card reading.
UNGATED_KINDS = (UnrealKind.SHOTS,)


def ungated_poll_q():
    """The poll part for the ungated kinds (FR-SD-3)."""
    return Q(unreal_kind__in=UNGATED_KINDS)


def skips_measured_gate(job):
    """Whether ``job`` skips the measured-peak gate at claim and in its routing note."""
    return job.unreal_kind in UNGATED_KINDS
