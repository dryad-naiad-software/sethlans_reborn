# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A job's ``unreal_machine_results`` (Unreal per-machine memory test D2,
D10, D11, FR-MT-T-3).

For a worker-token request on an Unreal render or audio job: the
caller's own current, not stale, ``fits`` records of the job's key (an
audio job: its submission's piece key), one per card kind the caller is
eligible for (at most four), as::

    [{"measurement_id", "card_kind", "peak_mb", "ram", "shared_peak_mb", "driver_version"}]

``peak_mb`` is the result peak, ``max(peak_mb, job_peak_max_mb)`` (D2);
``driver_version`` is null when unknown.  ``[]`` when the caller has no
fits result; null for other callers and for Blender, pilot and ``shots``
jobs.

**One query per response:** the first row reads the caller's records of
every asset in the response (the page a list serializer holds) and keeps
them in the serializer context; later rows read the cache.
"""

from django.db.models import F, QuerySet
from rest_framework import serializers

from ..constants import Renderer, UnrealKind
from ..models import Job, SequenceSubmission, UnrealMeasurement
from ..renderers.unreal_machine import FITS, PIECE_TARGET, eligible_kinds
from .pilot_ram import UnrealRamSerializer

CONTEXT_KEY = '_unreal_machine_results'
_KEYED_KINDS = (UnrealKind.RENDER, UnrealKind.AUDIO)


class MachineResultSerializer(serializers.Serializer):
    """One ``unreal_machine_results`` entry (OpenAPI schema only)."""

    measurement_id = serializers.IntegerField()
    card_kind = serializers.CharField()
    peak_mb = serializers.IntegerField(help_text="The result peak: max(pilot peak, largest reported job peak).")
    ram = UnrealRamSerializer(allow_null=True)
    shared_peak_mb = serializers.IntegerField(allow_null=True)
    driver_version = serializers.CharField(allow_null=True)


def key_target(job):
    """The target hash of the job's key: its own, or (audio) its submission's piece key."""
    if job.unreal_kind != UnrealKind.AUDIO:
        return job.unreal_target_sha256
    if hasattr(job, PIECE_TARGET):
        return getattr(job, PIECE_TARGET) or ''
    return SequenceSubmission.objects.filter(pk=job.sequence_submission_id).values_list(
        'piece_target_sha256', flat=True).first() or ''


def response_rows(serializer, job):
    """Every job in the response (a list serializer's page, read without a query), ``job`` included."""
    rows = [job]
    parent = serializer.parent
    if isinstance(parent, serializers.ListSerializer):
        instance = parent.instance
        # A queryset is the one the list serializer is iterating: already fetched, no query.
        if isinstance(instance, (list, tuple)) or (isinstance(instance, QuerySet) and instance._result_cache is not None):
            rows = [row for row in instance if isinstance(row, Job)] + [job]
    return rows


def _response_assets(serializer, job):
    """The asset ids of every job in the response, ``job``'s included."""
    return {row.asset_id for row in response_rows(serializer, job)}


def _entry(row):
    return {
        'measurement_id': row['pk'], 'card_kind': row['card_kind'],
        'peak_mb': max(row['peak_mb'], row['job_peak_max_mb'] or 0), 'ram': row['ram'],
        'shared_peak_mb': row['shared_peak_mb'], 'driver_version': row['driver_version'] or None,
    }


def _load(worker, asset_ids):
    """``{(asset id, target hash): [entry]}`` of the worker's fits records of ``asset_ids`` (one query)."""
    kinds = eligible_kinds(worker)
    if not kinds or not asset_ids:
        return {}
    records = UnrealMeasurement.objects.filter(
        worker=worker, current=True, verdict=FITS, stale_reason='', card_kind__in=list(kinds),
        asset_id__in=sorted(asset_ids), overrides_sha256=F('project__unreal_overrides_sha256'),
        peak_mb__isnull=False,
    ).order_by('card_kind').values(
        'pk', 'asset_id', 'target_sha256', 'card_kind', 'peak_mb', 'job_peak_max_mb', 'ram', 'shared_peak_mb',
        'driver_version',
    )
    results = {}
    for row in records:
        results.setdefault((row['asset_id'], row['target_sha256']), []).append(_entry(row))
    return results


def machine_results(serializer, job, worker):
    """The ``unreal_machine_results`` of ``job`` for the calling ``worker`` (see the module docstring)."""
    if job.renderer != Renderer.UNREAL or job.unreal_kind not in _KEYED_KINDS:
        return None
    cache = serializer.context.setdefault(CONTEXT_KEY, {'assets': set(), 'results': {}})
    if job.asset_id not in cache['assets']:
        assets = _response_assets(serializer, job) - cache['assets']
        cache['results'].update(_load(worker, assets))
        cache['assets'] |= assets
    return list(cache['results'].get((job.asset_id, key_target(job)), []))
