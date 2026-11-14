# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The project's ``unreal_measurement`` summary (Unreal foundation FR-PM-7).

One project has one measurement key per target, so the summary counts the
project's QUEUED normal Unreal jobs by key state (``held_pending``,
``held_failed``, ``held_none``) and shows the most recent record made
under the project's **current** CVar hash (``latest``).

:func:`project_annotations` adds every value as a subquery of the project
query, so a project list adds no query; a single object without them
(a create or update response) reads them in one query.
"""

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from rest_framework.fields import DateTimeField

from shared.unreal.vram_gate import measured_need_mb

from ..constants import UnrealKind
from ..models import Job, JobStatus, Project, UnrealMeasurement
from .unreal_measured import (
    FAILED, JOB_KEY_REFS, NONE, PENDING, key_aliases, key_state_case, record_reason,
    record_state,
)

_PREFIX = '_um_'
HELD_STATES = (('held_pending', PENDING), ('held_failed', FAILED), ('held_none', NONE))
# ``latest`` keys read straight from the record (API name -> model field).
_RECORD_FIELDS = (
    ('measurement_id', 'id'), ('remeasure', 'remeasure'), ('asset_id', 'asset_id'),
    ('engine_build', 'engine_build'), ('map', 'map'), ('sequence', 'sequence'),
    ('preset', 'preset'), ('peak_mb', 'peak_mb'), ('peak_source', 'peak_source'),
    ('measured_at', 'measured_at'), ('worker_hostname', 'worker_hostname'),
    ('gpu_name', 'gpu_name'), ('gpu_total_mb', 'gpu_total_mb'), ('prep_s', 'prep_s'),
    ('warmup_s', 'warmup_s'), ('seconds_per_frame', 'seconds_per_frame'),
    ('pilot_job_id', 'pilot_job_id'),
)
# Results that read ``null`` until the record is measured.
_RESULT_KEYS = frozenset({
    'peak_mb', 'peak_source', 'measured_at', 'worker_hostname', 'gpu_name',
    'gpu_total_mb', 'prep_s', 'warmup_s', 'seconds_per_frame',
})


def _held_count(state):
    jobs = Job.objects.filter(
        asset__project=OuterRef('pk'), unreal_kind=UnrealKind.RENDER, status=JobStatus.QUEUED,
    ).exclude(unreal_target_sha256='').alias(**key_aliases(JOB_KEY_REFS, '_hk_')).alias(
        _hk_state=key_state_case('_hk_', Q(unreal_target_sha256='')),
    ).filter(_hk_state=state)
    counted = jobs.order_by().values('asset__project').annotate(_n=Count('pk')).values('_n')[:1]
    return Coalesce(Subquery(counted, output_field=IntegerField()), Value(0))


def project_annotations():
    """``annotate()`` kwargs for the summary (all subqueries of the project query)."""
    latest = UnrealMeasurement.objects.filter(
        project=OuterRef('pk'), overrides_sha256=OuterRef('unreal_overrides_sha256'),
    ).order_by('-created_at', '-id').annotate(_state=record_state(), _reason=record_reason())
    annotations = {f'{_PREFIX}{key}': _held_count(state) for key, state in HELD_STATES}
    for key, field in _RECORD_FIELDS:
        annotations[f'{_PREFIX}{key}'] = Subquery(latest.values(field)[:1])
    annotations[f'{_PREFIX}status'] = Subquery(latest.values('_state')[:1])
    annotations[f'{_PREFIX}reason'] = Subquery(latest.values('_reason')[:1])
    return annotations


def _annotated(project):
    if hasattr(project, f'{_PREFIX}held_none'):
        return project
    return Project.objects.filter(pk=project.pk).annotate(**project_annotations()).first()


def _latest(row):
    if getattr(row, f'{_PREFIX}measurement_id') is None:
        return None
    latest = {key: getattr(row, f'{_PREFIX}{key}') for key, _field in _RECORD_FIELDS}
    status = getattr(row, f'{_PREFIX}status')
    latest['status'] = status
    latest['reason'] = getattr(row, f'{_PREFIX}reason') if status == FAILED else None
    peak = latest['peak_mb']
    if peak is None:
        for key in _RESULT_KEYS:
            latest[key] = None
    latest['gate_need_mb'] = measured_need_mb(peak) if isinstance(peak, int) and peak > 0 else None
    if latest['measured_at'] is not None:
        latest['measured_at'] = DateTimeField().to_representation(latest['measured_at'])
    return latest


def project_summary(project):
    """The FR-PM-7 summary of an Unreal ``project`` (annotated or not)."""
    row = _annotated(project)
    if row is None:
        return None
    summary = {key: getattr(row, f'{_PREFIX}{key}') for key, _state in HELD_STATES}
    summary['latest'] = _latest(row)
    return summary
