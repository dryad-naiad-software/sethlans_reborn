# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: a pilot claim against a running fits-nowhere check
(Unreal per-machine memory test D12, D15).

``fits_nowhere.check`` decides "nothing of the key is RENDERING" under
every Worker row lock.  It stays true until the commit because every
transition to RENDERING writes ``assigned_worker`` from NULL, and that
write's foreign-key check needs the claiming worker's row, which the check
holds ``FOR UPDATE``.

The repository has no PostgreSQL test database, so this is shown in two
parts.  **Statement level** (row locks emulated as in the other
``*_lock_sql`` tests): a pilot claim writes ``assigned_worker_id`` on the
Job row, and the check's Worker lock statement, read back, covers the
claiming worker's row.  **Threads** on a file SQLite database
(``sqlite_file_db.py``): while a check holds its locks (paused after the
pair states were read), a pilot claim on another thread does not finish;
it finishes after the check committed, and the check's stop stands.  On
SQLite the claim waits on the database write lock, which the check took
with its first statement; the statement-level test is what ties the wait
to the Worker rows on a row-locking database.
"""

import re
import threading

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import captured_sql, runnable

from workers.models import ManagerSettings
from workers.services import fits_nowhere
from workers.services.unreal_target import Target

from .fits_nowhere_helpers import check_key, dnf_record, statuses
from .machine_test_helpers import emulated_row_locks, locked_create, render_job
from .sqlite_file_db import file_backed_sqlite
from .unreal_helpers import SHARE, set_share, unreal_asset
from .unreal_pilot_helpers import claim, ready_worker, settings

_ASSIGN = re.compile(r'^UPDATE "workers_job" SET .*"assigned_worker_id" = ', re.S)


def _setup(worker_with_token, make_worker_client):
    """Key K fits nowhere (both machines ``does_not_fit``); machine B has a QUEUED pilot of another key."""
    asset = unreal_asset()
    first = ready_worker(worker_with_token[0])
    second, second_client, _token = make_worker_client('mt-fn-race-b')
    ready_worker(second)
    job = render_job(asset)
    for each in (first, second):
        dnf_record(asset, job.unreal_target_sha256, each)
    other = render_job(asset, name='MtFnRaceOther', render_settings=settings(preset='/Game/P/R.R'))
    pilot = locked_create(asset, Target.of(other), second).pilot_job
    return job, pilot, second, second_client


@pytest.mark.django_db
def test_a_pilot_claim_writes_the_worker_fk_the_check_holds(worker_with_token, make_worker_client):
    job, pilot, second, second_client = _setup(worker_with_token, make_worker_client)
    with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
        assert check_key(job) == 1
    [worker_lock] = [sql for sql in captured_sql(ctx)
                     if 'FOR UPDATE' in sql and 'FROM "workers_worker"' in sql]
    with connection.cursor() as cursor:
        cursor.execute(runnable(worker_lock))
        assert second.pk in [row[0] for row in cursor.fetchall()]
    with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
        assert claim(second_client, pilot, second).status_code == 200
    assert [sql for sql in captured_sql(ctx) if _ASSIGN.match(sql)], ctx.captured_queries


@pytest.fixture
def file_database(transactional_db, tmp_path):
    ManagerSettings.objects.get_or_create(pk=1, defaults={'manager_id': 'm', 'enrollment_key': 'k'})
    set_share(SHARE)
    with file_backed_sqlite(tmp_path):
        yield


def _thread(results, errors, name, call):
    def target():
        try:
            results[name] = call()
        except Exception as exc:  # reported by the test, so no thread dies silently
            errors.append(exc)
        finally:
            connections.close_all()
    return threading.Thread(target=target, daemon=True)


def test_a_pilot_claim_waits_for_a_running_check(file_database, worker_with_token, make_worker_client,
                                                 monkeypatch):
    job, pilot, second, second_client = _setup(worker_with_token, make_worker_client)
    entered, release = threading.Event(), threading.Event()
    real = fits_nowhere.largest_tried

    def held(*args, **kwargs):
        found = real(*args, **kwargs)
        entered.set()
        release.wait(10)
        return found

    monkeypatch.setattr(fits_nowhere, 'largest_tried', held)
    results, errors = {}, []
    checking = _thread(results, errors, 'check', lambda: check_key(job))
    claiming = _thread(results, errors, 'claim', lambda: claim(second_client, pilot, second).status_code)
    checking.start()
    try:
        assert entered.wait(10), errors
        claiming.start()
        claiming.join(0.5)
        assert claiming.is_alive() and 'claim' not in results, results
    finally:
        release.set()
        checking.join(30)
        if claiming.ident is not None:
            claiming.join(30)
    assert not checking.is_alive() and not claiming.is_alive()
    assert not errors, errors
    assert results == {'check': 1, 'claim': 200}
    assert statuses(job, pilot) == ['ERROR', 'RENDERING']
