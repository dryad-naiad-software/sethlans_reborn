# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal routing: the worker-poll filter, the claim check and the R8 routing
note (Unreal foundation FR-RT-2..5, FR-RT-9, FR-MS-5).

A worker gets an Unreal job only when the job's exact engine build is one
the worker reports as supported **and** the worker reports the manager's
Sethlans share (``shares_match``) as reachable. The claim enforces this;
the poll excludes what the claim would refuse and the manager can know,
so the oldest Unreal job never blocks a worker's queue with a 409 on
every cycle (D7 head-of-line).
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError

from shared.unreal.builds import is_supported
from shared.unreal.unc import shares_match
from shared.unreal.vram_gate import caps_sum_mb, caps_within_budget, measured_need_mb, pilot_need_mb, usable_mb

from ..constants import WORKER_STALENESS_SECONDS, Renderer, UnrealKind
from ..models import JobStatus, Worker
from ..models.workers import reported_unreal_builds, reports_share_reachable
from ..services.sethlans_share import configured_share
from .unreal_measured import (
    MEASURED, has_nvidia_reading, held_message, measured_claim_rejection, poll_measured_q,
)

MAX_POLL_BUILDS = 10
SHARE_UNSET_MESSAGE = "The Sethlans share is not configured."
SHARE_MISMATCH_MESSAGE = "Worker's Sethlans share does not match the manager's setting."
SHARE_UNREACHABLE_MESSAGE = "Worker does not report the Sethlans share as reachable."
_CONTEXT_KEY = '_online_unreal'


def _no_jobs():
    return Q(pk__in=[])


def parse_poll_builds(params):
    """Return the requested ``unreal_builds`` set (FR-RT-2).

    Raises:
        rest_framework.exceptions.ValidationError: missing, more than
            ``MAX_POLL_BUILDS``, or a value that is not a supported build.
    """
    raw = params.get('unreal_builds') or ''
    builds = [b.strip() for b in raw.split(',') if b.strip()]
    if not builds:
        raise ValidationError("unreal_builds is required when renderers includes UNREAL.")
    if len(builds) > MAX_POLL_BUILDS:
        raise ValidationError(f"Maximum {MAX_POLL_BUILDS} unreal_builds allowed.")
    for build in builds:
        if not is_supported(build):
            raise ValidationError(f"Invalid unreal_builds value: {build[:40]}")
    return set(builds)


def unreal_poll_q(params, worker):
    """The Unreal part of a worker poll (FR-RT-3, FR-MS-5).

    No Unreal job is offered when the manager's share is unset, or, for a
    worker caller, when its stored capabilities do not report that share
    reachable or report none of the requested builds; the build filter is
    narrowed to the builds the worker reports.  A non-worker caller
    (``worker is None``) gets only the share-unset rule.  For every caller,
    normal jobs of unmeasured keys and stale or superseded pilots are left
    out; a worker without an ``nvidia-smi`` reading gets no pilots (D2).
    """
    builds = parse_poll_builds(params)
    share = configured_share()
    if not share:
        return _no_jobs()
    if worker is not None:
        caps = worker.unreal_capabilities
        if not reports_share_reachable(caps, share):
            return _no_jobs()
        builds &= reported_unreal_builds(caps)
        if not builds:
            return _no_jobs()
    include_pilots = worker is None or has_nvidia_reading(worker)
    return Q(renderer=Renderer.UNREAL, engine_build__in=sorted(builds)) & poll_measured_q(include_pilots)


def unreal_claim_rejection(worker, job):
    """Return why ``worker`` may not claim the Unreal ``job``, or ``None`` (FR-RT-4, FR-MS-5)."""
    if not worker.has_unreal_build(job.engine_build):
        return f"Worker does not report Unreal Engine build {job.engine_build}."
    share = configured_share()
    if not share:
        return SHARE_UNSET_MESSAGE
    caps = worker.unreal_capabilities if isinstance(worker.unreal_capabilities, dict) else {}
    if not shares_match(caps.get('share', ''), share):
        return SHARE_MISMATCH_MESSAGE
    if not reports_share_reachable(caps, share):
        return SHARE_UNREACHABLE_MESSAGE
    return measured_claim_rejection(worker, job)


def _min_total_mb(gpu_memory):
    """Smallest reported ``total_mb``, or ``None`` when the worker reports no GPUs."""
    gpus = gpu_memory.get('gpus') if isinstance(gpu_memory, dict) else None
    if not isinstance(gpus, list):
        return None
    totals = [g['total_mb'] for g in gpus if isinstance(g, dict) and isinstance(g.get('total_mb'), int)]
    return min(totals) if totals else None


def _online_unreal():
    """Build the per-response routing data: two queries at most (FR-RT-9).

    Returns ``(share, online_builds, reachable)`` where ``reachable`` maps
    each build to the smallest GPU ``total_mb`` (or ``None``) of every
    online worker that reports it and passes ``reports_share_reachable``.
    """
    share = configured_share()
    online_builds, reachable = set(), {}
    if not share:
        return share, online_builds, reachable
    cutoff = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS)
    rows = Worker.objects.filter(is_active=True, last_seen__gte=cutoff).values_list(
        'unreal_capabilities', 'gpu_memory',
    )
    for caps, gpu_memory in rows:
        builds = reported_unreal_builds(caps)
        online_builds |= builds
        if builds and reports_share_reachable(caps, share):
            total = _min_total_mb(gpu_memory)
            for build in builds:
                reachable.setdefault(build, []).append(total)
    return share, online_builds, reachable


def _vram_need(job, peak):
    """``(need, caps_sum)``: the pilot need, or the measured need plus the caps (FR-RT-9 note 4)."""
    cvars = job.asset.project.unreal_cvar_overrides or {}
    if job.unreal_kind == UnrealKind.PILOT:
        return pilot_need_mb(cvars), None
    return measured_need_mb(peak), caps_sum_mb(cvars)


def _vram_note(job, totals, peak):
    try:
        need, caps_sum = _vram_need(job, peak)
    except (ValueError, TypeError, AttributeError):
        return None
    for total in totals:
        # A worker without gpu_memory (an older worker) is not counted against the job.
        if total is None:
            return None
        if need <= usable_mb(total, 0) and (caps_sum is None or caps_within_budget(caps_sum, total)):
            return None
    return (
        f"No online worker with Unreal Engine build {job.engine_build} "
        f"has enough GPU memory (needs {need} MB)."
    )


def _is_worker_request(request):
    """A worker-token request (``IsWorker``'s test).

    Checking ``request.auth`` first costs a session (dashboard) request no
    ``worker_profile`` query; worker users have no usable password, so they
    only ever authenticate with their token.
    """
    return isinstance(request.auth, Token) and hasattr(request.user, 'worker_profile')


def unreal_routing_note(job, context):
    """Why a QUEUED Unreal job is not being picked up, or ``None`` (FR-RT-9)."""
    if job.status != JobStatus.QUEUED:
        return None
    request = context.get('request')
    if request is not None and _is_worker_request(request):
        return None
    if _CONTEXT_KEY not in context:
        context[_CONTEXT_KEY] = _online_unreal()
    share, online_builds, reachable = context[_CONTEXT_KEY]
    build = job.engine_build
    if not share:
        return SHARE_UNSET_MESSAGE
    if build not in online_builds:
        return f"No online worker reports Unreal Engine build {build}."
    if build not in reachable:
        return (
            f"No online worker with Unreal Engine build {build} is configured "
            "with the manager's Sethlans share and reports it reachable."
        )
    peak = None
    if job.unreal_kind != UnrealKind.PILOT:
        state, reason, peak = _key_state(job)
        if state != MEASURED:
            return held_message(state, reason, job.asset.project_id)
    return _vram_note(job, reachable[build], peak)


def _key_state(job):
    """``(state, reason, peak)`` from the list annotations, else one query (FR-PM-5)."""
    # Imported here: services import the renderer registry.
    from ..services.unreal_pilot import cached_key_state
    found = cached_key_state(job)
    return found.state, found.reason, found.peak_mb
