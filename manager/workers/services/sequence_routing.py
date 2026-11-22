# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The routing note of a sequence version (Unreal sequence rendering
FR-SR-3, FR-SR-5, code-review finding #10; per-machine memory test
FR-MT-U-2, NF-3).

A version's note is the note of its first QUEUED piece (every piece shares
one key), else of its first QUEUED audio job, else of its discovery's
QUEUED ``shots`` job, else null.

No query per row: :func:`routing_job_annotation` picks that job's id as a
subquery of the submission query; :func:`routing_jobs` then loads every
picked job of a response in **one** query, with its piece key target and
its project joined, and :func:`routing_context` names their keys to the
response's machine cache (``machine_rows``), so their current records are
read in one query.  The note itself is ``unreal_routing.unreal_routing_note``
with that shared cache, so a whole page costs the same few queries as one
row.
"""

from django.db.models import IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from ..constants import UnrealKind
from ..models import Job, JobStatus

ROUTING_JOB = 'routing_job_id'
CONTEXT_KEY = 'sequence_routing_jobs'


def _first(queryset):
    return Subquery(queryset.filter(status=JobStatus.QUEUED).order_by('pk').values('pk')[:1],
                    output_field=IntegerField())


def routing_job_annotation():
    """``{routing_job_id: Coalesce(first QUEUED piece, first QUEUED audio job, QUEUED shots job)}``."""
    own = Job.objects.filter(sequence_submission=OuterRef('pk'))
    shots = Job.objects.filter(pk=OuterRef('discovery__job_id'), unreal_kind=UnrealKind.SHOTS)
    return {ROUTING_JOB: Coalesce(
        _first(own.filter(unreal_kind=UnrealKind.RENDER)),
        _first(own.filter(unreal_kind=UnrealKind.AUDIO)),
        _first(shots),
    )}


def routing_jobs(job_ids):
    """``{id: job}`` for the picked jobs, in one query, ready for ``unreal_routing_note``."""
    from ..renderers.unreal_machine import piece_target_annotation
    ids = sorted({pk for pk in job_ids if pk is not None})
    if not ids:
        return {}
    jobs = Job.objects.filter(pk__in=ids).annotate(**piece_target_annotation()).select_related('asset__project')
    return {job.pk: job for job in jobs}


def routing_context(context, rows):
    """Put the routing jobs of ``rows`` (submissions) in ``context`` and name their keys to the machine cache."""
    from .machine_rows import machine_cache, page_keys
    jobs = context[CONTEXT_KEY] = routing_jobs(getattr(row, ROUTING_JOB, None) for row in rows)
    machine_cache(context).expect(page_keys(jobs.values()))
    return context


def routing_note(submission, context):
    """FR-SR-3: the version's note from the response's loaded jobs, or ``None``."""
    from ..renderers.unreal_routing import unreal_routing_note
    jobs = context.get(CONTEXT_KEY)
    job_id = getattr(submission, ROUTING_JOB, None)
    if jobs is None or job_id is None:
        return None
    job = jobs.get(job_id)
    return None if job is None else unreal_routing_note(job, context)
