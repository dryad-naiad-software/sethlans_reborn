# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The admin measurement retry (Unreal foundation FR-PM-6).

``POST /api/projects/{id}/unreal-measurement/retry/`` creates new
measurement records and targeted pilots for one key of the project: the
key of the job named by ``job_id``, or of the project's oldest held
QUEUED normal job whose key is failed or none.  Per machine (per-machine
memory test D5, FR-MT-P-4): one pilot for every eligible (worker, card
kind) pair whose current record failed, is due a re-test or is missing;
with ``remeasure``, also every pair with a result.  ``worker`` narrows
this to that worker's eligible kinds (400 when it has none for the key's
build now).  A pair whose pilot is still QUEUED or RENDERING is never
tested again, and the D5 cap of open pilots per worker applies.  The retry
is refused only when no pilot was created; the response names the first
new pilot.  The retry always creates pilots (an admin asks for a new
test), never a derived record.

The transaction locks the project first (FR-PM-10).  Refusals raise
:class:`RetryRefused`, which the view maps to its status and body.
"""

import logging

from django.db import transaction

from ..constants import UnrealKind
from ..models import Asset, Job, JobStatus
from ..renderers import get_strategy
from ..renderers.unreal_machine import JOB_KEY_REFS
from ..renderers.unreal_measured import FAILED, NONE, key_aliases, key_state_case
from .machine_tests import MAX_OPEN_PILOTS, create_pilots, eligible_pairs, pair_states
from .unreal_target import Target
from .worker_locks import lock_workers

logger = logging.getLogger(__name__)

NOT_UNREAL = "Not an Unreal project."
NO_ASSET = "This project has no Unreal asset yet."
NOT_A_JOB = "Not an Unreal job of this project."
NO_HELD_JOB = "No held Unreal job needs a measurement; pass job_id."
ALREADY_PENDING = "A pilot render is already queued or running."
ALREADY_MEASURED = 'This target is already measured; send "remeasure": true to measure it again.'
NO_MACHINE = "No machine that is on can run this memory test now."
AT_LIMIT = (f"Every machine that needs this memory test already has {MAX_OPEN_PILOTS} queued memory tests; "
            "retry when some of them have run.")
NOT_ELIGIBLE = "This machine cannot run this memory test now."
# D2 pair states a retry without ``remeasure`` tests again (``None``: no current record).
_RETRIED_STATES = (None, 'failed', 'retest_due')
# Pairs with a result: their new record is a re-measurement (``remeasure`` true).
_MEASURED_STATES = ('fits', 'does_not_fit')


class RetryRefused(Exception):
    """The retry is refused with HTTP ``status`` and response ``body``."""

    def __init__(self, status, body):
        super().__init__(body)
        self.status = status
        self.body = body


def _normal_jobs(project):
    return Job.objects.filter(
        asset__project=project, unreal_kind=UnrealKind.RENDER,
    ).exclude(unreal_target_sha256='').select_related('asset')


def _chosen_job(project, job_id):
    if job_id is not None:
        job = _normal_jobs(project).filter(pk=job_id).first()
        if job is None:
            raise RetryRefused(400, {'job_id': [NOT_A_JOB]})
        return job
    held = _normal_jobs(project).filter(status=JobStatus.QUEUED).alias(
        **key_aliases(JOB_KEY_REFS, '_rk_'),
    ).alias(_rk_state=key_state_case('_rk_')).filter(_rk_state__in=(FAILED, NONE))
    job = held.order_by('submitted_at', 'id').first()
    if job is None:
        raise RetryRefused(409, {'error': NO_HELD_JOB})
    return job


def _refusal(chosen, found, remeasure):
    """Why no pilot was created (``chosen``: the pairs that wanted one; ``found``: the D2 states)."""
    if chosen:
        # Every pair that needed a pilot was refused by the D5 cap (the pairs are within the Worker locks).
        return AT_LIMIT
    if not remeasure and found & set(_MEASURED_STATES):
        return ALREADY_MEASURED
    if 'testing' in found:
        return ALREADY_PENDING
    return NO_MACHINE


def _create(project, job, target, remeasure, worker):
    """The pilots of the pairs that need one (D5 cap applies); 409 when none was created."""
    pairs = eligible_pairs(target.engine_build, None if worker is None else [worker], within=lock_workers())
    if worker is not None and not pairs:
        raise RetryRefused(400, {'worker': [NOT_ELIGIBLE]})
    states = pair_states(job.asset, project.unreal_overrides_sha256, target.sha256(), pairs)
    wanted = _RETRIED_STATES + (_MEASURED_STATES if remeasure else ())
    chosen = [(machine, kind) for machine, kind in pairs if states.get((machine.pk, kind)) in wanted]
    measured = frozenset(pair for pair, state in states.items() if state in _MEASURED_STATES)
    records = create_pilots(project, job.asset, target, chosen, reason='retry', frame=job.start_frame,
                            remeasured=measured if remeasure else frozenset())
    if not records:
        found = {states.get((machine.pk, kind)) for machine, kind in pairs}
        raise RetryRefused(409, {'error': _refusal(chosen, found, remeasure)})
    return records


def retry_measurement(project, *, job_id=None, remeasure=False, worker=None, username=''):
    """Create a new record and pilot for one key of ``project`` (FR-PM-6, FR-MT-P-4).

    Returns:
        The first new ``UnrealMeasurement`` (its ``pilot_job`` set).

    Raises:
        RetryRefused: 400 for a Blender project, a bad ``job_id`` or a
            ``worker`` with no eligible kind for the key; 409 with no
            Unreal asset, no held job, or no pilot created (every pair
            testing, measured without ``remeasure``, at the open-test
            limit, or no eligible machine).
    """
    strategy = get_strategy(project.renderer)
    with transaction.atomic():
        locked = strategy.lock_for_write(project)
        if not strategy.supports('pilot_measurement'):
            raise RetryRefused(400, {'detail': NOT_UNREAL})
        if not Asset.objects.filter(project=locked).exists():
            raise RetryRefused(409, {'error': NO_ASSET})
        job = _chosen_job(locked, job_id)
        target = Target.of(job)
        records = _create(locked, job, target, remeasure, worker)
        record = records[0]
    logger.warning(
        "Unreal measurement retry by %s for project %s, asset %s, target %s %s %s %s, worker %s "
        "(remeasure=%s, %d pilots).", username, project.pk, job.asset_id, target.map, target.sequence,
        target.preset, target.engine_build, 'any' if worker is None else worker, record.remeasure, len(records),
    )
    return record
