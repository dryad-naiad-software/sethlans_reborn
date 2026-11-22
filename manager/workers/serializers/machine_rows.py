# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Machine lists in API responses (Unreal per-machine memory test FR-MT-U-1).

``MachineRowSerializer`` is the schema of one row (built by
``services/machine_rows.py``).  The lists appear on the job detail as
``unreal_machines`` (Unreal render and audio jobs; ``GET /api/jobs/{id}/``
only, null in lists and other actions and for worker-token requests, so no
worker sees other machines' hostnames or drivers), on the sequence
submission detail as ``machines`` (its piece key; the endpoint is admin
only) and per key in a project's ``unreal_measurement.keys``.
"""

from rest_framework import serializers

from ..constants import UnrealKind
from ..renderers import UnsupportedRendererError, get_strategy
from ..renderers.unreal_machine import PAIR_STATES
from ..renderers.unreal_routing import _is_worker_request
from ..services.machine_rows import machine_cache, page_keys
from .machine_results import key_target, response_rows
from .pilot_ram import UnrealRamSerializer

_KEYED = (UnrealKind.RENDER, UnrealKind.AUDIO)


class MachineRowSerializer(serializers.Serializer):
    """One (machine, card kind) pair of a measurement key (OpenAPI schema only)."""

    worker = serializers.IntegerField(allow_null=True, help_text="The worker's id; null when the record has no worker.")
    hostname = serializers.CharField()
    online = serializers.BooleanField(help_text="Seen within the worker staleness window.")
    card_kind = serializers.CharField(help_text="vvvv:dddd:rrrr (PCI vendor, device, revision or 'none').")
    card_name = serializers.CharField(allow_null=True)
    card_total_mb = serializers.IntegerField(allow_null=True)
    state = serializers.ChoiceField(choices=PAIR_STATES)
    peak_mb = serializers.IntegerField(
        allow_null=True, help_text="The result peak: max(pilot peak, largest reported job peak); null until measured.")
    lower_bound_mb = serializers.IntegerField(
        allow_null=True, help_text="does_not_fit: the need is at least this many MB.")
    driver_version = serializers.CharField(allow_null=True)
    reason = serializers.CharField(allow_null=True, help_text="Why the memory test failed (failed only).")
    covered = serializers.BooleanField(
        help_text="The result was derived from a covering result of the same machine (D8).")
    ram = UnrealRamSerializer(
        allow_null=True, help_text="The result's RAM figures (Q8); null until measured and for not_tried.")


def _admin_request(context):
    request = context.get('request')
    return request is None or not _is_worker_request(request)


def job_machines(serializer, job):
    """``unreal_machines`` of ``job``: the rows of its key on ``GET /api/jobs/{id}/``, else ``None``."""
    context = serializer.context
    view = context.get('view')
    if getattr(view, 'action', None) != 'retrieve' or not _admin_request(context):
        return None
    try:
        measured = get_strategy(job.renderer).supports('pilot_measurement')
    except UnsupportedRendererError:
        return None
    if not measured or job.unreal_kind not in _KEYED:
        return None
    target = key_target(job)
    return machine_cache(context).rows((job.asset_id, target), job.engine_build) if target else None


def submission_machines(context, submission):
    """``machines`` of a submission: the rows of its piece key, or ``None`` before it has one."""
    if not submission.piece_target_sha256 or not _admin_request(context):
        return None
    key = (submission.asset_id, submission.piece_target_sha256)
    return machine_cache(context).rows(key, submission.engine_build)


def expect_page_keys(serializer, job):
    """Name the keys of the response's jobs to the machine cache once (the routing note reads them)."""
    context = serializer.context
    if '_unreal_machine_page' not in context:
        context['_unreal_machine_page'] = True
        machine_cache(context).expect(page_keys(response_rows(serializer, job)))
