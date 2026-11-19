# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An output upload that meets another connection's write on ``workers_job``
(issue #272).

The concurrent-upload test failed on CI with "database table is locked:
workers_job" in the upload's first read (``get_object``) while the other
upload was writing.  These tests hold that write from a raw connection and
run one upload against it:

* on the in-memory shared-cache test database the read fails at once with
  SQLITE_LOCKED -- the CI failure, made deterministic;
* on a file database in WAL mode, as production runs, the read passes, the
  write transaction waits on ``busy_timeout`` and the upload succeeds once
  the other write ends.

So the failure comes from the test database only; tests that run real
threads use ``sqlite_file_database`` (see ``sqlite_file_db.py``).
"""

import io
import threading
import time

import pytest
from django.db import OperationalError, connection, connections
from PIL import Image

from workers.models import Job

from .sqlite_file_db import job_row_write_lock, sqlite_file_database  # noqa: F401

JOBS_URL = '/api/jobs/'


def _png_file():
    buf = io.BytesIO()
    Image.new('RGB', (32, 32), color='green').save(buf, format='PNG')
    buf.seek(0)
    buf.name = 'out.png'
    return buf


def _claimed_job(admin_client, asset, worker, worker_client):
    resp = admin_client.post(JOBS_URL, {
        'name': 'lock_job', 'asset_id': asset.pk, 'output_file_pattern': '//render/#.png',
    }, format='json')
    assert resp.status_code == 201, resp.data
    job_id = resp.data['id']
    resp = worker_client.post(f'{JOBS_URL}{job_id}/claim/', {'worker_id': worker.pk}, format='json')
    assert resp.status_code == 200, resp.data
    return job_id


def _upload(worker_client, job_id):
    return worker_client.post(
        f'{JOBS_URL}{job_id}/upload_output/', {'output_file': _png_file()}, format='multipart',
    )


class _StatementSpy:
    """Signals the upload's ``workers_job`` read and its ``BEGIN``."""

    def __init__(self):
        self.job_read = threading.Event()
        self.begin = threading.Event()

    def __call__(self, execute, sql, params, many, context):
        if sql.upper().startswith('BEGIN'):
            self.begin.set()
        result = execute(sql, params, many, context)
        if sql.startswith('SELECT') and 'FROM "workers_job"' in sql:
            self.job_read.set()
        return result


@pytest.mark.django_db(transaction=True)
class TestUploadMeetsAHeldJobWrite:

    def test_shared_cache_test_database_fails_the_read_at_once(
        self, admin_client, asset, worker_with_token,
    ):
        """The default test database raises SQLITE_LOCKED without waiting."""
        if connection.vendor != 'sqlite' or not connection.is_in_memory_db():
            pytest.skip('needs the in-memory SQLite test database')
        worker, worker_client = worker_with_token
        job_id = _claimed_job(admin_client, asset, worker, worker_client)
        with job_row_write_lock(connection.settings_dict['NAME'], job_id):
            started = time.monotonic()
            with pytest.raises(OperationalError, match='database table is locked: workers_job'):
                _upload(worker_client, job_id)
            elapsed = time.monotonic() - started
        # busy_timeout (30 s) does not apply to SQLITE_LOCKED.
        assert elapsed < 5, elapsed

    def test_file_database_upload_waits_for_the_write_and_succeeds(
        self, admin_client, asset, worker_with_token, sqlite_file_database,  # noqa: F811
    ):
        """Production's WAL file: the read passes, the write waits, then 200."""
        if sqlite_file_database is None:
            pytest.skip('SQLite only')
        worker, worker_client = worker_with_token
        job_id = _claimed_job(admin_client, asset, worker, worker_client)
        spy, outcome = _StatementSpy(), {}

        def upload():
            try:
                with connection.execute_wrapper(spy):
                    outcome['status'] = _upload(worker_client, job_id).status_code
            except Exception as exc:  # reported in the assertions below
                outcome['error'] = exc
            finally:
                connections.close_all()

        thread = threading.Thread(target=upload, daemon=True)
        with job_row_write_lock(sqlite_file_database, job_id):
            thread.start()
            assert spy.job_read.wait(10), outcome
            assert spy.begin.wait(10), outcome
            thread.join(timeout=0.5)
            # The write transaction's BEGIN IMMEDIATE waits on busy_timeout.
            assert thread.is_alive(), outcome
        thread.join(timeout=30)
        assert not thread.is_alive(), 'the upload did not finish after the write ended'
        assert outcome == {'status': 200}
        job = Job.objects.get(pk=job_id)
        assert job.output_file.name
        assert job.thumbnail.name
