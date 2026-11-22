# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The machine list and the routing note's memory part of a measurement key
(Unreal per-machine memory test D2, D5, FR-MT-U-1, FR-MT-U-2, NF-3).

**Rows** (:meth:`MachineCache.rows`): one per (worker, card kind) pair, as::

    {"worker", "hostname", "online", "card_kind", "card_name", "card_total_mb", "state",
     "peak_mb", "lower_bound_mb", "driver_version", "reason", "covered", "ram"}

for every current record of the key (whatever its worker's state), plus a
``not_tried`` row for every eligible pair without one.  Eligible pairs are
D5's: an active worker that serves the key's build (reports it, and the
manager's share as reachable) with a ticked ``ok`` card of the kind and a
live card reading, all read from its stored heartbeat.  A worker seen
within ``WORKER_STALENESS_SECONDS`` is online; an active worker seen
earlier is offline and its untried pairs are listed with ``online``
false, judged on the last heartbeat it sent.  Inactive (disabled) workers
give no untried rows; their records are listed with ``online`` false.
``state`` is a D2 state (``unreal_machine.PAIR_STATES``); ``peak_mb`` is
the result peak (D2) once measured; ``reason`` is the failure reason of a
``failed`` pair only; ``covered`` marks a derived record (D8); ``ram`` is
the record's RAM figures (Q8, the ``UnrealRam`` object) once measured,
else null (also for ``not_tried``).  The card
name and size come from the record once measured, else from the worker's
stored card of that kind.

**Memory note** (:meth:`MachineCache.memory_note`, over the eligible
online pairs): null when one fits; ``WAITING_NOTE`` while any is testing,
not tried or ``retest_due`` (``<n>`` = distinct machines); the failed note
when the rest include a failed pair; ``TOO_BIG_NOTE`` when every pair does
not fit (the fits-nowhere stop then ends the jobs once nothing of the key
renders); ``NO_MACHINE_NOTE`` when no online worker is eligible.

**Cost (NF-3):** one cache per response (:func:`machine_cache`): the share
(one query) and the active workers (one query) on first use, and the
current records of every expected key in one query (:meth:`expect` names
the response's keys before the first row is built; a key not expected
costs one more query).
"""

from datetime import timedelta
from functools import cached_property

from django.db.models import F
from django.utils import timezone

from shared.unreal.card_kind import card_kind

from ..constants import WORKER_STALENESS_SECONDS, Renderer, UnrealKind
from ..models import JobStatus, UnrealMeasurement, Worker
from ..models.workers import reported_unreal_builds, reports_share_reachable
from ..renderers.unreal_machine import FAILED, FITS, NOT_TRIED, PIECE_TARGET, RETEST_DUE, TESTING, pair_state_case
from ..renderers.unreal_measured import TOO_BIG_NOTE, failed_note, record_reason
from .machine_tests import pairs_of
from .sethlans_share import configured_share

CONTEXT_KEY = '_unreal_machine_cache'
WAITING_NOTE = "Waiting for memory tests on {n} machine(s)."
NO_MACHINE_NOTE = ("No online worker with Unreal Engine build {build} has a ticked graphics card "
                   "with a live memory reading.")
_KEYED = (UnrealKind.RENDER, UnrealKind.AUDIO)
_WAITING = (TESTING, NOT_TRIED, RETEST_DUE)
_WORKER_FIELDS = ('pk', 'hostname', 'is_active', 'last_seen', 'unreal_capabilities', 'gpu_memory', 'card_status')
_RECORD_FIELDS = (
    'pk', 'asset_id', 'target_sha256', 'worker_id', 'worker_hostname', 'card_kind', 'engine_build', 'map',
    'sequence', 'preset', 'gpu_name', 'gpu_total_mb', 'peak_mb', 'job_peak_max_mb', 'lower_bound_mb',
    'driver_version', 'verdict_source', 'ram',
)


def machine_cache(context):
    """The response's :class:`MachineCache`, kept in the serializer ``context``."""
    cache = context.get(CONTEXT_KEY)
    if cache is None:
        cache = context[CONTEXT_KEY] = MachineCache()
    return cache


def page_keys(jobs):
    """The keys of the QUEUED Unreal render and audio ``jobs`` (no query; audio needs the piece annotation)."""
    keys = set()
    for job in jobs:
        if job.renderer != Renderer.UNREAL or job.unreal_kind not in _KEYED or job.status != JobStatus.QUEUED:
            continue
        if job.unreal_kind == UnrealKind.AUDIO:
            if hasattr(job, PIECE_TARGET):
                keys.add((job.asset_id, getattr(job, PIECE_TARGET) or ''))
        else:
            keys.add((job.asset_id, job.unreal_target_sha256))
    return keys


def _total_mb(worker, card_key):
    reading = worker.gpu_memory if isinstance(worker.gpu_memory, dict) else {}
    for gpu in reading.get('gpus') or ():
        if isinstance(gpu, dict) and card_key is not None and gpu.get('card_key') == card_key:
            return gpu.get('total_mb') if isinstance(gpu.get('total_mb'), int) else None
    return None


def _stored_card(worker, kind):
    """``(name, total MB)`` of the worker's first stored card of ``kind``, else ``(None, None)``."""
    status = worker.card_status if worker is not None else None
    cards = status.get('cards') if isinstance(status, dict) else None
    for card in cards if isinstance(cards, list) else ():
        if isinstance(card, dict) and card_kind(card.get('vendor_id'), card.get('device_id'),
                                                card.get('revision_id')) == kind:
            return card.get('name') or None, _total_mb(worker, card.get('key'))
    return None, None


class MachineCache:
    """One response's workers and current records (see the module docstring)."""

    def __init__(self):
        self.cutoff = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS)
        self._records, self._loaded, self._expected, self._pairs = {}, set(), set(), {}

    @cached_property
    def share(self):
        return configured_share()

    @cached_property
    def workers(self):
        """``{pk: worker}`` of every active worker (one query)."""
        rows = Worker.objects.filter(is_active=True).only(*_WORKER_FIELDS).order_by('pk')
        return {worker.pk: worker for worker in rows}

    def is_online(self, worker):
        return worker is not None and worker.last_seen is not None and worker.last_seen >= self.cutoff

    def routing_builds(self):
        """``(online builds, builds of online workers that report the share reachable)`` (FR-RT-9)."""
        online, reachable = set(), set()
        for worker in self.workers.values():
            if not self.is_online(worker):
                continue
            builds = reported_unreal_builds(worker.unreal_capabilities)
            online |= builds
            if builds and reports_share_reachable(worker.unreal_capabilities, self.share):
                reachable |= builds
        return online, reachable

    def pairs(self, build, online=True):
        """The eligible pairs of online (or offline) active workers for ``build``, by worker pk then kind."""
        if not self.share:
            return []
        if (build, online) not in self._pairs:
            workers = [worker for worker in self.workers.values() if self.is_online(worker) == online]
            self._pairs[build, online] = pairs_of(workers, build, self.share)
        return self._pairs[build, online]

    def expect(self, keys):
        """Name keys the response will read, so the first read loads them all in one query."""
        self._expected |= {key for key in keys if key[1]} - self._loaded

    def records(self, key):
        """The key's current records (as values rows, by pk), loading the expected keys with it."""
        if key not in self._loaded:
            self._load(self._expected | {key})
        return self._records.get(key, [])

    def _load(self, keys):
        wanted = {key for key in keys if key[1]} - self._loaded
        self._loaded |= keys
        self._expected -= keys
        if not wanted:
            return
        rows = UnrealMeasurement.objects.filter(
            current=True, asset_id__in=sorted({asset for asset, _target in wanted}),
            target_sha256__in=sorted({target for _asset, target in wanted}),
            overrides_sha256=F('project__unreal_overrides_sha256'),
        ).annotate(_pair_state=pair_state_case(), _pair_reason=record_reason()).order_by('pk').values(
            *_RECORD_FIELDS, '_pair_state', '_pair_reason')
        for row in rows:
            key = (row['asset_id'], row['target_sha256'])
            if key in wanted:
                self._records.setdefault(key, []).append(row)

    def _record_row(self, record):
        worker = self.workers.get(record['worker_id'])
        name, total = _stored_card(worker, record['card_kind'])
        state, peak = record['_pair_state'], record['peak_mb']
        return {
            'worker': record['worker_id'],
            'hostname': worker.hostname if worker is not None else record['worker_hostname'],
            'online': self.is_online(worker), 'card_kind': record['card_kind'],
            'card_name': record['gpu_name'] or name,
            'card_total_mb': record['gpu_total_mb'] if record['gpu_total_mb'] is not None else total,
            'state': state,
            'peak_mb': None if peak is None else max(peak, record['job_peak_max_mb'] or 0),
            'lower_bound_mb': record['lower_bound_mb'], 'driver_version': record['driver_version'] or None,
            'reason': (record['_pair_reason'] or '')[:200] if state == FAILED else None,
            'covered': record['verdict_source'] == 'covered',
            'ram': None if peak is None else record['ram'],
        }

    @staticmethod
    def _untried_row(worker, kind, online):
        name, total = _stored_card(worker, kind)
        return {
            'worker': worker.pk, 'hostname': worker.hostname, 'online': online, 'card_kind': kind,
            'card_name': name, 'card_total_mb': total, 'state': NOT_TRIED, 'peak_mb': None,
            'lower_bound_mb': None, 'driver_version': None, 'reason': None, 'covered': False, 'ram': None,
        }

    def rows(self, key, build):
        """FR-MT-U-1: the machine rows of ``key`` (``(asset id, target hash)``) for ``build``."""
        records = self.records(key)
        rows = [self._record_row(record) for record in records]
        seen = {(record['worker_id'], record['card_kind']) for record in records}
        for online in (True, False):
            rows += [self._untried_row(worker, kind, online) for worker, kind in self.pairs(build, online)
                     if (worker.pk, kind) not in seen]
        return sorted(rows, key=lambda row: (row['hostname'].lower(), row['card_kind'], row['worker'] or 0))

    def memory_note(self, key, build, project_id):
        """FR-MT-U-2: the routing note's memory part for ``key``, or ``None`` when an online pair fits."""
        pairs = self.pairs(build)
        if not pairs:
            return NO_MACHINE_NOTE.format(build=build)
        by_pair = {(record['worker_id'], record['card_kind']): record for record in self.records(key)}
        found = [(worker, by_pair.get((worker.pk, kind))) for worker, kind in pairs]
        states = [(worker, NOT_TRIED if record is None else record['_pair_state'], record)
                  for worker, record in found]
        if any(state == FITS for _worker, state, _record in states):
            return None
        waiting = {worker.pk for worker, state, _record in states if state in _WAITING}
        if waiting:
            return WAITING_NOTE.format(n=len(waiting))
        failed = [record for _worker, state, record in states if state == FAILED]
        if failed:
            return failed_note(failed[0]['_pair_reason'], project_id)
        return TOO_BIG_NOTE
