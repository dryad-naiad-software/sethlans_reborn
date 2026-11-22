# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration test: two projects creating pilots for overlapping workers in
opposite orders finish without a deadlock (Unreal per-machine memory test
D15, NF-1; ``services/worker_locks.lock_workers``).

Project one has key K1 (worker A already fits, so only B needs a pilot)
then key K2 (only A needs one); project two the reverse (A, then B).
Locking each pair's rows as it went, the two transactions would lock A and
B in opposite orders; with every Worker row locked ascending first, they
cannot.  Both run ``ensure_pilots_for_queued`` at once on real threads
with their own connections, on a file SQLite database
(``sqlite_file_db.py``) as in ``test_unreal_pilot_locking.py``.

The repository has no PostgreSQL test database: on SQLite the project
lock's write takes the database write lock, so the two transactions
serialize from their first write and a row-lock deadlock cannot happen
there.  This test therefore checks that both finish, with the right
pilots and no errors; the lock order itself (one all-Worker lock before
any record lock) is checked statement by statement in
``test_machine_worker_locks_integration.py``.
"""

import threading

import pytest
from django.db import connections, transaction

from workers.models import Job, ManagerSettings
from workers.services.unreal_pilot import ensure_pilots_for_queued, lock_project

from .machine_test_helpers import render_job
from .sqlite_file_db import file_backed_sqlite
from .unreal_helpers import SHARE, set_share, unreal_asset, unreal_project
from .unreal_pilot_helpers import fits_record, ready_worker, settings as unreal_settings

ROUNDS = 3


@pytest.fixture
def file_database(transactional_db, tmp_path):
    ManagerSettings.objects.get_or_create(pk=1, defaults={'manager_id': 'm', 'enrollment_key': 'k'})
    set_share(SHARE)
    with file_backed_sqlite(tmp_path):
        yield


def _run_together(*calls):
    """Run each call on its own thread after a barrier; return their results."""
    barrier = threading.Barrier(len(calls))
    results, errors = [None] * len(calls), []

    def _target(index, call):
        try:
            barrier.wait(timeout=10)
            results[index] = call()
        except Exception as exc:  # reported below, so no thread dies silently
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=_target, args=(i, c), daemon=True) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not [t for t in threads if t.is_alive()], "a pilot-creating thread is still running (deadlock?)"
    assert not errors, errors
    return results


def _ensure(project):
    def call():
        with transaction.atomic():
            return [p.pk for p in ensure_pilots_for_queued(lock_project(project), reason='test')]
    return call


def _project(name, first_fits, second_fits):
    """A project with two queued keys; ``first_fits`` / ``second_fits`` already fit the first / second key."""
    asset = unreal_asset(unreal_project(name=name), name=f'{name}Asset')
    first = render_job(asset, name=f'{name}K1')
    second = render_job(asset, name=f'{name}K2', render_settings=unreal_settings(sequence='/Game/Cinematics/Two.Two'))
    fits_record(asset, first.unreal_target_sha256, first_fits)
    fits_record(asset, second.unreal_target_sha256, second_fits)
    return asset.project, first, second


def _targets(job):
    """The worker pks of the pilots of ``job``'s key."""
    return sorted(Job.objects.filter(
        unreal_kind='pilot', asset=job.asset, unreal_target_sha256=job.unreal_target_sha256,
    ).values_list('target_worker_id', flat=True))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('round_', range(ROUNDS))
def test_two_projects_in_opposite_worker_orders(worker_with_token, make_worker_client, file_database, round_):
    worker_a = ready_worker(worker_with_token[0])
    worker_b = ready_worker(make_worker_client('mt-race-b')[0])
    one, one_k1, one_k2 = _project(f'MtRaceOne{round_}', worker_a, worker_b)
    two, two_k1, two_k2 = _project(f'MtRaceTwo{round_}', worker_b, worker_a)
    made_one, made_two = _run_together(_ensure(one), _ensure(two))
    assert (len(made_one), len(made_two)) == (2, 2)
    # Project one needs B then A; project two needs A then B: each pair got its one pilot.
    assert (_targets(one_k1), _targets(one_k2)) == ([worker_b.pk], [worker_a.pk])
    assert (_targets(two_k1), _targets(two_k2)) == ([worker_a.pk], [worker_b.pk])
