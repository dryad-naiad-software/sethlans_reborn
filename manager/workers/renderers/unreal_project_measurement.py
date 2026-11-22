# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The project's ``unreal_measurement`` summary (Unreal foundation FR-PM-7;
per-machine memory test FR-MT-U-1).

``held_pending``, ``held_failed`` and ``held_none`` count the project's
QUEUED normal Unreal jobs by their key's farm-wide state
(``unreal_measured.key_state_case``); :func:`project_annotations` adds
them as subqueries of the project query, so a project list adds no query.

``keys`` replaces the single latest measurement: the project's
measurement keys under its **current** CVar hash, each with its machine
list (``services/machine_rows.py``)::

    {"asset_id", "target_sha256", "engine_build", "map", "sequence", "preset", "machines": [row]}

First the keys of the project's QUEUED or RENDERING render jobs (newest
submission first), then the keys with a current record (newest record
first), at most ``MAX_KEYS``.  The name fields come from the key's newest
current record, else from its oldest active job.  ``keys`` is built only
for single-object responses (a project list shows null, NF-3) and is null
for worker-token requests.  Cost: the job keys, the record keys, the
records of the chosen keys (one query), the share and the active workers,
and the jobs of keys without a record (one query, only when there are any).
"""

from django.db.models import Count, IntegerField, Max, Min, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from ..constants import UnrealKind
from ..models import Job, JobStatus, Project, UnrealMeasurement
from .unreal_machine import JOB_KEY_REFS
from .unreal_measured import FAILED, NONE, PENDING, key_aliases, key_state_case
from .unreal_routing import _is_worker_request
from .unreal_settings import MAP, PRESET, SEQUENCE

_PREFIX = '_um_'
HELD_STATES = (('held_pending', PENDING), ('held_failed', FAILED), ('held_none', NONE))
MAX_KEYS = 20
_ACTIVE = (JobStatus.QUEUED, JobStatus.RENDERING)


def _held_count(state):
    jobs = Job.objects.filter(
        asset__project=OuterRef('pk'), unreal_kind=UnrealKind.RENDER, status=JobStatus.QUEUED,
    ).exclude(unreal_target_sha256='').alias(**key_aliases(JOB_KEY_REFS, '_hk_')).alias(
        _hk_state=key_state_case('_hk_', Q(unreal_target_sha256='')),
    ).filter(_hk_state=state)
    counted = jobs.order_by().values('asset__project').annotate(_n=Count('pk')).values('_n')[:1]
    return Coalesce(Subquery(counted, output_field=IntegerField()), Value(0))


def project_annotations():
    """``annotate()`` kwargs for the held counts (all subqueries of the project query)."""
    return {f'{_PREFIX}{key}': _held_count(state) for key, state in HELD_STATES}


def _annotated(project):
    if hasattr(project, f'{_PREFIX}held_none'):
        return project
    return Project.objects.filter(pk=project.pk).annotate(**project_annotations()).first()


def _wants_keys(context):
    """Keys for a single-object response to a non-worker caller."""
    request = context.get('request')
    if request is not None and _is_worker_request(request):
        return False
    return getattr(context.get('view'), 'action', None) != 'list'


def _active_keys(project):
    """``{key: oldest active job id}`` of the active render jobs' keys, newest submission first."""
    rows = Job.objects.filter(
        asset__project=project, unreal_kind=UnrealKind.RENDER, status__in=_ACTIVE,
    ).exclude(unreal_target_sha256='').values('asset_id', 'unreal_target_sha256').annotate(
        _first=Min('pk'), _last=Max('submitted_at'),
    ).order_by('-_last', '-_first')[:MAX_KEYS]
    return {(row['asset_id'], row['unreal_target_sha256']): row['_first'] for row in rows}


def _record_keys(project):
    """The keys with a current record under the project's current CVars, newest record first."""
    rows = UnrealMeasurement.objects.filter(
        project=project, current=True, overrides_sha256=project.unreal_overrides_sha256,
    ).values('asset_id', 'target_sha256').annotate(_last=Max('created_at'), _newest=Max('pk')).order_by(
        '-_last', '-_newest')[:MAX_KEYS]
    return [(row['asset_id'], row['target_sha256']) for row in rows]


def _names(cache, key, jobs):
    """``(build, map, sequence, preset)`` of a key: its newest record's, else its job's; ``None`` if neither."""
    records = cache.records(key)
    if records:
        newest = records[-1]
        return newest['engine_build'], newest['map'], newest['sequence'], newest['preset']
    job = jobs.get(key)
    if job is None:
        return None
    settings = job['render_settings'] if isinstance(job['render_settings'], dict) else {}
    return job['engine_build'], settings.get(MAP, ''), settings.get(SEQUENCE, ''), settings.get(PRESET, '')


def project_keys(project, context):
    """The ``keys`` list of ``project`` (see the module docstring)."""
    # Imported here: services import the renderer registry.
    from ..services.machine_rows import machine_cache
    cache = machine_cache(context)
    active = _active_keys(project)
    keys = list(dict.fromkeys([*active, *_record_keys(project)]))[:MAX_KEYS]
    cache.expect(keys)
    bare = [active[key] for key in keys if not cache.records(key) and key in active]
    rows = Job.objects.filter(pk__in=bare).values('asset_id', 'unreal_target_sha256', 'engine_build',
                                                  'render_settings') if bare else ()
    jobs = {(job['asset_id'], job['unreal_target_sha256']): job for job in rows}
    entries = []
    for key in keys:
        names = _names(cache, key, jobs)
        if names is None:
            continue
        build, map_, sequence, preset = names
        entries.append({'asset_id': key[0], 'target_sha256': key[1], 'engine_build': build, 'map': map_,
                        'sequence': sequence, 'preset': preset, 'machines': cache.rows(key, build)})
    return entries


def project_summary(project, context=None):
    """The FR-PM-7 summary of an Unreal ``project`` (annotated or not)."""
    row = _annotated(project)
    if row is None:
        return None
    context = {} if context is None else context
    summary = {key: getattr(row, f'{_PREFIX}{key}') for key, _state in HELD_STATES}
    summary['keys'] = project_keys(row, context) if _wants_keys(context) else None
    return summary
