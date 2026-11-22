# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration test: two ``pilot-result/`` calls of one pair at once (Unreal
per-machine memory test D15, NF-1, D3).

One machine and one card kind test two keys; both pilots are RENDERING on
it and report at the same moment, with different drivers, on real
threads with their own connections, on a file SQLite database
(``sqlite_file_db.py``) as in ``test_machine_lock_concurrency_integration.py``.
Each locks the pair's current records (D15), so both must finish without
an error and the second must see the first's stored driver: exactly one
of the two records ends ``driver_changed`` (the one stored first), and a
third record of the pair with an unknown driver is staled once, by the
second.

The repository has no PostgreSQL test database: on SQLite the two
transactions serialize from their first write, so a row-lock deadlock
cannot happen here; the lock order itself is checked statement by
statement in ``test_machine_pilot_lock_sql_integration.py``.
"""

import threading

import pytest
from django.db import connections
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Job, ManagerSettings, UnrealMeasurement

from .machine_test_helpers import pair_record
from .machine_verdict_helpers import NEW_DRIVER, OLD_DRIVER
from .sqlite_file_db import file_backed_sqlite
from .unreal_helpers import SHARE, set_share, unreal_asset
from .unreal_pilot_helpers import OTHER_SEQUENCE, created_job, pilot_body, pilots, post_result, ready_worker, settings

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
    assert not [t for t in threads if t.is_alive()], "a pilot-result thread is still running (deadlock?)"
    assert not errors, errors
    return results


def _report(token, job, driver):
    def call():
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        resp = post_result(client, job.pk, pilot_body(driver_version=driver))
        return resp.status_code, resp.data
    return call


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('round_', range(ROUNDS))
def test_two_results_of_one_pair_at_once(admin_client, worker_with_token, file_database, round_):
    worker = ready_worker(worker_with_token[0])
    asset = unreal_asset(name=f'MtPilotRace{round_}')
    created_job(admin_client, asset, name='RaceOne')
    created_job(admin_client, asset, name='RaceTwo', render_settings=settings(sequence=OTHER_SEQUENCE))
    first, second = pilots(asset)
    Job.objects.filter(pk__in=[first.pk, second.pk]).update(status='RENDERING', assigned_worker=worker)
    unknown = pair_record(asset, 'b1' * 32, worker, verdict='fits', driver_version='')
    token = Token.objects.get(user=worker.user).key
    answers = _run_together(_report(token, first, OLD_DRIVER), _report(token, second, NEW_DRIVER))
    assert answers == [(200, {'status': 'DONE', 'peak_mb': 8102})] * 2
    assert set(Job.objects.filter(pk__in=[first.pk, second.pk]).values_list('status', flat=True)) == {'DONE'}
    records = {r.pilot_job_id: r for r in UnrealMeasurement.objects.filter(pilot_job__in=[first, second])}
    assert (records[first.pk].driver_version, records[second.pk].driver_version) == (OLD_DRIVER, NEW_DRIVER)
    assert sorted(r.stale_reason for r in records.values()) == ['', 'driver_changed']
    assert UnrealMeasurement.objects.get(pk=unknown.pk).stale_reason == 'driver_changed'
