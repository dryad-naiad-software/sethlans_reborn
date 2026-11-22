# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The heartbeat sweep: memory tests for a machine that comes online,
changes its cards or driver (Unreal per-machine memory test D16,
FR-MT-P-3).

``views/heartbeat.py`` calls :func:`ensure_for_worker` after the
heartbeat committed.  A worker without Unreal enabled costs nothing (a
pilot left QUEUED for it is never offered, and the next pilot trigger of
its project cancels it).  Otherwise **one read without locks**
(:func:`sweep_rows`, which also reads the manager's share), at most
``SWEEP_MAX_KEYS`` keys to test, finds:

* ``test`` rows: keys with QUEUED render or audio jobs (an audio job by
  its submission's piece key, D10) of a build the worker reports, where
  fewer of the worker's eligible card kinds have a current, not stale
  record than it has kinds (a pair not tried yet or due a re-test), for a
  worker that serves the manager's share and is under the D5 cap of open
  pilots;
* ``cancel`` rows: QUEUED pilots targeted at the worker whose build or
  card kind it no longer serves (or the share), or whose record is no
  longer current (the rules of ``machine_test_cancel``);
* ``stop`` rows (D16 (b), at most ``SWEEP_MAX_KEYS``): keys with QUEUED
  render or audio jobs of a build the worker reports, not a ``test`` row,
  where no current record fits on any worker, nothing of the key is
  RENDERING, an online worker's current record does not fit and no online
  worker's current record is testing with a QUEUED pilot
  (``fits_nowhere.candidate_q``); failed records, pairs not tried and
  ``retest_due`` records are left to ``fits_nowhere.stop_due``.

The read applies the rules the locked check applies, so a worker with a
mismatched share or at the cap finds nothing after its first sweep: one
query, no lock, no write.  (A key at the cap that a covering
result could serve waits until the worker is under the cap.)  Otherwise
one transaction per project with ``test`` or ``cancel`` rows:
``lock_project`` first, the pilots to
cancel (Job rows, ``SKIP LOCKED``) and, when any were canceled, every
Worker row and their records; then, per key that still has a job to
test, every Worker row (``lock_workers``, once per transaction) and
``ensure_machine_tests`` for this worker only, which re-checks
everything under the locks (eligibility, the pair states, the cap,
coverage).

Each ``stop`` row is then confirmed by ``fits_nowhere.stop_due``, a
lock-free read of the exact D12 rule (eligible online pairs, failed
pairs, pairs not tried, ``retest_due``), and only then
``fits_nowhere.check`` runs, in its own transaction (never inside the
per-project transaction above).  So a key that the read cannot settle (a
failed or untried online pair, a record due a re-test, a machine no
longer eligible) costs a few reads, never a lock.
"""

import logging
from collections import defaultdict

from django.db import transaction
from django.db.models import (
    Case, CharField, Count, Exists, F, IntegerField, Min, OuterRef, Q, Subquery, Value, When,
)
from django.db.models.functions import Coalesce

from ..constants import UnrealKind
from ..models import Job, JobStatus, ManagerSettings, Project, UnrealMeasurement, Worker
from ..models.workers import reported_unreal_builds, reports_share_reachable
from ..renderers.unreal_machine import eligible_kinds, live_pilot
from .fits_nowhere import candidate_q, check, stop_due
from .machine_test_cancel import cancel_ineligible_pilots, release_records
from .machine_tests import MAX_OPEN_PILOTS, ensure_machine_tests
from .sethlans_share import share_of
from .unreal_pilot import lock_project
from .unreal_target import Target
from .worker_locks import lock_workers

logger = logging.getLogger(__name__)

# D16: keys one sweep reads.
SWEEP_MAX_KEYS = 20
TEST, CANCEL, PILOT, STOP = 'test', 'cancel', 'pilot', 'stop'
_OVERRIDES = 'asset__project__unreal_overrides_sha256'
# The read yields a role's rank (its index here) and is ordered by it, so stop candidates never crowd
# test rows out of the read's slice; ``sweep_rows`` maps the rank back to the name.
_ROLES = (CANCEL, PILOT, TEST, STOP)
_KEY_TARGET = Case(
    When(unreal_kind=UnrealKind.PILOT, then=Value('')),
    When(unreal_kind=UnrealKind.AUDIO, then=F('sequence_submission__piece_target_sha256')),
    default=F('unreal_target_sha256'), output_field=CharField(),
)
# The stored share, read with the rows (``sethlans_share.share_of`` checks it).
_MANAGER_SHARE = Subquery(ManagerSettings.objects.filter(pk=1).values('sethlans_share')[:1],
                          output_field=CharField())


def _caps_served(worker):
    """``(builds, kinds)`` the worker's own report could test (D5), before the share is compared."""
    caps = worker.unreal_capabilities
    if not isinstance(caps, dict) or caps.get('share_reachable') is not True:
        return (), ()
    builds, kinds = sorted(reported_unreal_builds(caps)), eligible_kinds(worker)
    return (builds, kinds) if builds and kinds else ((), ())


def _open_pilots(worker):
    """The worker's QUEUED live targeted pilots (``machine_tests.open_pilot_counts``), as one subquery."""
    pilots = Job.objects.filter(
        target_worker=worker.pk, status=JobStatus.QUEUED, unreal_kind=UnrealKind.PILOT,
    ).filter(live_pilot()).order_by().values('target_worker').annotate(_n=Count('pk')).values('_n')
    return Coalesce(Subquery(pilots, output_field=IntegerField()), 0)


def _have(worker, kinds):
    """How many of ``kinds`` have a current, not stale record of the row's key for ``worker``."""
    records = UnrealMeasurement.objects.filter(
        asset=OuterRef('asset_id'), overrides_sha256=OuterRef('asset__project__unreal_overrides_sha256'),
        target_sha256=OuterRef('_key_target'), worker=worker.pk, card_kind__in=list(kinds),
        current=True, stale_reason='',
    ).order_by().values('worker').annotate(_n=Count('card_kind', distinct=True)).values('_n')
    return Coalesce(Subquery(records, output_field=IntegerField()), 0)


def _stop_builds(worker):
    """The builds whose keys the worker's sweep reads for stop candidates (D16 (b)): those it reports."""
    caps = worker.unreal_capabilities
    return sorted(reported_unreal_builds(caps)) if isinstance(caps, dict) else []


def _rank(role):
    return Value(_ROLES.index(role))


def _role(worker, builds, kinds, test):
    """The rank in ``_ROLES`` of: ``cancel`` (a pilot of the worker it no longer serves), ``pilot`` (its other
    pilots), ``test``, else ``stop``."""
    pilot = Q(unreal_kind=UnrealKind.PILOT, target_worker=worker.pk)
    if kinds:
        unserved = (~Q(engine_build__in=builds) | ~Q(unreal_measurement__card_kind__in=list(kinds))
                    | ~Q(_current=True))
        whens = [When(pilot & unserved, then=_rank(CANCEL)), When(pilot, then=_rank(PILOT))]
    else:
        whens = [When(pilot, then=_rank(CANCEL))]
    if test is not None:
        whens.append(When(test, then=_rank(TEST)))
    return Case(*whens, default=_rank(STOP), output_field=IntegerField())


def _query(worker, builds, kinds, stop_builds):
    keyed = Q(unreal_kind__in=(UnrealKind.RENDER, UnrealKind.AUDIO)) & ~Q(_key_target='')
    aliases = {'_current': Exists(UnrealMeasurement.objects.filter(pilot_job=OuterRef('pk'), current=True))}
    wanted, test = Q(unreal_kind=UnrealKind.PILOT, target_worker=worker.pk), None
    if kinds:
        aliases.update(_have=_have(worker, kinds), _open=_open_pilots(worker))
        # A worker at the D5 cap gets no pilot under the lock either: no test rows for it.
        test = keyed & Q(engine_build__in=builds, _have__lt=len(kinds), _open__lt=MAX_OPEN_PILOTS)
        wanted |= test
    if stop_builds:
        wanted |= keyed & Q(engine_build__in=stop_builds) & candidate_q(OuterRef('_key_target'), OuterRef(_OVERRIDES))
    return Job.objects.filter(status=JobStatus.QUEUED).annotate(_key_target=_KEY_TARGET).alias(
        **aliases,
    ).filter(wanted).annotate(_role=_role(worker, builds, kinds, test), _share=_MANAGER_SHARE).values(
        'asset__project_id', 'asset_id', '_key_target', '_role', '_share', _OVERRIDES,
    ).annotate(_first=Min('pk')).order_by('_role', '_first')


def _kept(role, served, counts):
    """Whether a row with ``role`` is acted on; counts the ``test`` and ``stop`` rows kept."""
    if role == PILOT or (role == TEST and not served) or counts.get(role, 0) >= SWEEP_MAX_KEYS:
        return False
    counts[role] = counts.get(role, 0) + (role in (TEST, STOP))
    return True


def sweep_rows(worker):
    """The D16 read, one query: ``[{project, asset, target, overrides, role, first}]``.

    At most ``SWEEP_MAX_KEYS`` ``test`` and as many ``stop`` rows.  The
    manager's share comes with the rows: a worker that does not serve it
    (none configured, another share) gets no ``test`` rows and every pilot
    of its own is a ``cancel`` row, as the locked check decides.
    """
    builds, kinds = _caps_served(worker)
    rows = list(_query(worker, builds, kinds, _stop_builds(worker))[:2 * SWEEP_MAX_KEYS + MAX_OPEN_PILOTS])
    share = share_of(rows[0]['_share']) if rows else ''
    served = bool(kinds) and reports_share_reachable(worker.unreal_capabilities, share)
    found, counts = [], {}
    for row in rows:
        role = _ROLES[row['_role']]
        role = CANCEL if role == PILOT and not served else role
        if _kept(role, served, counts):
            found.append({'project': row['asset__project_id'], 'asset': row['asset_id'], 'target': row['_key_target'],
                          'overrides': row[_OVERRIDES], 'role': role, 'first': row['_first']})
    return found


def _key_job(asset_id, target_hash):
    """Under the lock: the render job whose target builds the key's pilot, or ``None`` when nothing waits."""
    waiting = Job.objects.filter(asset_id=asset_id, status=JobStatus.QUEUED).filter(
        Q(unreal_kind=UnrealKind.RENDER, unreal_target_sha256=target_hash)
        | Q(unreal_kind=UnrealKind.AUDIO, sequence_submission__piece_target_sha256=target_hash),
    )
    if not waiting.exists():
        return None
    return Job.objects.filter(
        asset_id=asset_id, unreal_kind=UnrealKind.RENDER, unreal_target_sha256=target_hash,
    ).select_related('asset').order_by(
        Case(When(status=JobStatus.QUEUED, then=Value(0)), default=Value(1), output_field=IntegerField()),
        'submitted_at', 'pk',
    ).first()


def _sweep_project(project_id, worker_pk, rows):
    """One transaction for one project (D16); the number of new pilots."""
    created = 0
    with transaction.atomic():
        try:
            project = lock_project(Project(pk=project_id))
        except Project.DoesNotExist:
            return 0
        canceled = cancel_ineligible_pilots(project)  # D15: Job rows (SKIP LOCKED) before the Worker rows
        if canceled:
            lock_workers()
            release_records(canceled)  # D15: records after the Worker rows
        for row in rows:
            job = _key_job(row['asset'], row['target']) if row['role'] == TEST else None
            if job is None:
                continue
            lock_workers()  # only once a key has a job to test (D15: before ensure's record locks)
            created += len(ensure_machine_tests(project, job.asset, Target.of(job), reason='sweep',
                                                frame=job.start_frame, workers=[worker_pk]))
    return created


def ensure_for_worker(worker_id, *, worker=None):
    """D16 sweep for one worker after its heartbeat committed; the number of new pilots.

    ``worker`` is the heartbeat's own saved row (no re-read); without it
    the row is read by ``worker_id``.  A worker without Unreal enabled is
    skipped with no query.  A failure in one project is logged at WARNING
    with its traceback and the other projects still run (the next
    heartbeat repeats the sweep); so is a failed fits-nowhere check of a
    ``stop`` row, which runs after the projects' pilot transactions.
    """
    if worker is None:
        worker = Worker.objects.filter(pk=worker_id).first()
        if worker is None:
            return 0
    caps = worker.unreal_capabilities
    if not isinstance(caps, dict) or caps.get('enabled') is not True:
        return 0
    by_project, stops = defaultdict(list), []
    for row in sweep_rows(worker):
        (stops if row['role'] == STOP else by_project[row['project']]).append(row)
    created = 0
    for project_id, rows in by_project.items():
        try:
            created += _sweep_project(project_id, worker.pk, rows)
        except Exception:  # one bad project must not block the later ones on every heartbeat
            logger.warning("Memory-test sweep for worker %s, project %s failed; retried next heartbeat.",
                           worker.hostname, project_id, exc_info=True)
    for row in stops:
        _stop_key(worker, row)
    return created


def _stop_key(worker, row):
    """D16 (b): confirm a stop candidate without locks, then run the fits-nowhere check (own transaction)."""
    try:
        if stop_due(row['asset'], row['overrides'], row['target']):
            check(row['project'], row['asset'], row['overrides'], row['target'])
    except Exception:  # one bad key must not block the others on every heartbeat
        logger.warning("Fits-nowhere check from the sweep of worker %s, project %s failed; retried next heartbeat.",
                       worker.hostname, row['project'], exc_info=True)
