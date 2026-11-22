# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration test: ``pilot-result/`` and ``memory-report/`` of one pair at
once (Unreal per-machine memory test D15, NF-1, D3; chunk 6 concurrency).

One machine and one card kind: a pilot of one key and a render job of
another key are both RENDERING on it; the pilot reports the new driver
and the render job's memory report reports the same new driver for its
older-driver result, at the same moment, on real threads with their own
connections, on a file SQLite database (``sqlite_file_db.py``) as in
``test_machine_pilot_result_concurrency_integration.py``.  Both lock the
pair's records after Job and Worker (D15), so both finish without an
error, in either order with the same end state: the pilot is DONE with
the new driver, the render's result is ``driver_changed`` and its report
answered ``retest_due``.

The repository has no PostgreSQL test database: on SQLite the two
transactions serialize from their first write, so a row-lock deadlock
cannot happen here; the lock order itself is checked statement by
statement in ``test_machine_memory_report_lock_sql_integration.py``.
"""

import threading

import pytest
from django.db import connections
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Job, ManagerSettings, UnrealMeasurement

from .machine_test_helpers import render_job
from .machine_verdict_helpers import NEW_DRIVER
from .memory_report_helpers import keyed_record, post_report, report_body, stored_report
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
    assert not [t for t in threads if t.is_alive()], "a report thread is still running (deadlock?)"
    assert not errors, errors
    return results


def _as(token, post, job, body):
    def call():
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        resp = post(client, job.pk, body)
        return resp.status_code, resp.data
    return call


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('round_', range(ROUNDS))
def test_pilot_result_and_memory_report_of_one_pair_at_once(admin_client, worker_with_token, file_database, round_):
    worker = ready_worker(worker_with_token[0])
    asset = unreal_asset(name=f'MtReportRace{round_}')
    created_job(admin_client, asset, name='RacePilotKey')
    [pilot] = pilots(asset)
    job = render_job(asset, name='RaceRender', render_settings=settings(sequence=OTHER_SEQUENCE))
    record = keyed_record(asset, job.unreal_target_sha256, worker)
    Job.objects.filter(pk__in=[pilot.pk, job.pk]).update(status='RENDERING', assigned_worker=worker)
    token = Token.objects.get(user=worker.user).key
    answers = _run_together(
        _as(token, post_result, pilot, pilot_body(driver_version=NEW_DRIVER)),
        _as(token, post_report, job, report_body(record, driver_version=NEW_DRIVER, job_peak_mb=9000)),
    )
    assert answers == [(200, {'status': 'DONE', 'peak_mb': 8102}), (200, {'result': 'retest_due'})]
    assert Job.objects.get(pk=pilot.pk).status == 'DONE'
    assert UnrealMeasurement.objects.get(pilot_job=pilot).driver_version == NEW_DRIVER
    staled = UnrealMeasurement.objects.get(pk=record.pk)
    assert (staled.stale_reason, staled.job_peak_max_mb, staled.verdict) == ('driver_changed', None, 'fits')
    assert stored_report(job)['result'] == 'retest_due'
