# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the per-machine memory test integration tests
(Unreal per-machine memory test chunk 3: results, targeting, poll and
claim).  Not a test module.

A **pair** is (key, worker, card kind).  :func:`kinds_worker` makes a
worker eligible with one ticked ``ok`` card per given kind;
:func:`pair_record` stores one record of a pair in a chosen D2 state.
"""

from django.db import transaction
from django.utils import timezone

from tests.unit.manager.card_report_helpers import card, stored
from workers.models import Job, UnrealMeasurement
from workers.services.unreal_pilot import Target, lock_project

from .unreal_helpers import BUILD, RENDER_SETTINGS, card_key_of
from .unreal_pilot_helpers import KIND, ready_worker

# Kinds other than ``KIND`` (``10de:2b85:00a1``, the one ``ready_worker`` gives).
KIND_AMD = '1002:744c:00c8'
KIND_OTHER = '10de:2684:none'
TARGET = Target(BUILD, RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'],
                RENDER_SETTINGS['unreal.preset'])
NOT_TESTED = "This machine has not tested this render yet."
DOES_NOT_FIT = "This render does not fit this machine's graphics cards."
RETEST = "This machine's memory test is being re-done after a driver change."
OTHER_MACHINE = "This memory test is for another machine."
NO_MACHINE = "This memory test has no machine."
NO_CARD_READING = "Worker cannot measure GPU memory (no live card reading)."
NO_KIND = "This machine has no ticked graphics card with a known PCI id."
TARGET_DELETED = "The machine this memory test was for was deleted."


def ids_of(kind):
    """``{'vendor_id', 'device_id', 'revision_id'}`` of a D1 kind string."""
    vendor, device, revision = kind.split(':')
    return {'vendor_id': int(vendor, 16), 'device_id': int(device, 16),
            'revision_id': None if revision == 'none' else int(revision, 16)}


def kind_card(kind, index, **overrides):
    """A stored card of ``kind`` at slot ``index``; ticked and ``ok`` unless overridden."""
    return card(key=card_key_of(index), **{**ids_of(kind), **overrides})


def set_cards(worker, cards):
    """Store ``cards`` as the worker's ``card_status.cards``."""
    worker.card_status = stored(cards=list(cards), alerts=[], notices=[], warnings=[])
    worker.save(update_fields=['card_status', 'last_seen'])
    return worker


def kinds_worker(worker, *kinds):
    """Make ``worker`` eligible (``ready_worker``) with one ticked ``ok`` card per kind."""
    ready_worker(worker)
    return set_cards(worker, [kind_card(kind, index) for index, kind in enumerate(kinds)])


def pilot_job(asset, worker, status='QUEUED', error='', name=None):
    """A pilot job row targeted at ``worker`` (ORM, no record)."""
    return Job.objects.create(
        name=name or f'sethlans-pilot-h{Job.objects.count()}', asset=asset, unreal_kind='pilot',
        output_file_pattern='//p/p.png', render_settings=dict(RENDER_SETTINGS), status=status,
        error_message=error, target_worker=worker, start_frame=0, end_frame=0,
    )


def pair_record(asset, target_hash, worker, kind=KIND, *, verdict='', stale='', pilot=None, error='',
                peak=None, current=True, overrides=None, **fields):
    """One record of the pair; ``pilot`` is the status of its targeted pilot job, or ``None`` for none."""
    project = asset.project
    job = None if pilot is None else pilot_job(asset, worker, status=pilot, error=error)
    if peak is None and verdict:
        peak = 8102
    return UnrealMeasurement.objects.create(
        project=project, asset=asset, target_sha256=target_hash,
        overrides_sha256=project.unreal_overrides_sha256 if overrides is None else overrides,
        engine_build=BUILD, map=RENDER_SETTINGS['unreal.map'], sequence=RENDER_SETTINGS['unreal.sequence'],
        preset=RENDER_SETTINGS['unreal.preset'], pilot_job=job, peak_mb=peak,
        peak_source='card' if peak else '', measured_at=timezone.now() if peak else None,
        worker=worker, worker_hostname=worker.hostname, card_kind=kind, current=current,
        verdict=verdict, verdict_source='pilot' if verdict else '', stale_reason=stale, **fields,
    )


def render_job(asset, name='MtRender', render_settings=None, **extra):
    """A QUEUED Unreal render job made through the ORM (no pilot is created)."""
    return Job.objects.create(name=name, asset=asset, output_file_pattern='//r/f.png',
                              render_settings=dict(render_settings or RENDER_SETTINGS), start_frame=1,
                              end_frame=1, **extra)


def locked_create(asset, target, worker, kind=KIND):
    """``create_pilot`` for one pair under ``lock_project``; the new record."""
    from workers.services.machine_tests import create_pilot
    with transaction.atomic():
        locked = lock_project(asset.project)
        return create_pilot(locked, asset, target, worker=worker, card_kind=kind, reason='test')


def locked_ensure(asset, target=TARGET):
    """``ensure_machine_tests`` for every worker under ``lock_project``; the new pilot jobs."""
    from workers.services.machine_tests import ensure_machine_tests
    with transaction.atomic():
        locked = lock_project(asset.project)
        return ensure_machine_tests(locked, asset, target, reason='test')


def current_of(asset, target_hash, worker, kind=KIND):
    """The pks of the pair's current records."""
    return list(UnrealMeasurement.objects.filter(
        asset=asset, target_sha256=target_hash, worker=worker, card_kind=kind, current=True,
    ).values_list('pk', flat=True))
