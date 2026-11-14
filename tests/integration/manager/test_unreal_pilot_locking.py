# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: lock-first writes (Unreal foundation FR-PM-10, AC-54).

Every write that can create a pilot opens a transaction whose first
statement write-locks the project row, so concurrent writes on one
project serialize and a key gets one pilot.  The concurrency cases run
real threads with separate connections (``transaction=True``).
"""

import sqlite3
import threading

import pytest
from django.db import connection, connections
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from workers.models import Asset, Job, ManagerSettings, UnrealMeasurement

from .unreal_helpers import ARCHIVE_SHA, SHARE, set_share, unreal_asset
from .unreal_pilot_helpers import JOBS, POLL, create_job, created_job, pilots, ready_worker, settings

LOCK_MARK = '__write_lock__'
CVARS = {'r.Streaming.PoolSize': '3000'}


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def file_database(transactional_db, admin_user):
    """Run the test on a file copy of the SQLite test database.

    The test database is an in-memory shared-cache database, where a
    second writer gets SQLITE_LOCKED at once instead of waiting on
    ``busy_timeout``; production runs on a file in WAL mode, where
    ``BEGIN IMMEDIATE`` waits.  The rows created so far are copied into
    the file, the threads connect to it, and the in-memory database (kept
    open) is restored afterwards.
    """
    ManagerSettings.objects.get_or_create(pk=1, defaults={'manager_id': 'm', 'enrollment_key': 'k'})
    set_share(SHARE)
    if connection.vendor != 'sqlite':
        yield
        return
    import tempfile
    from pathlib import Path
    directory = tempfile.TemporaryDirectory()
    path = str(Path(directory.name) / 'race.sqlite3')
    connection.ensure_connection()
    target = sqlite3.connect(path)
    connection.connection.backup(target)
    target.execute('PRAGMA journal_mode=WAL')
    target.close()
    memory, original = connection.connection, connection.settings_dict['NAME']
    connection.connection = None
    connection.settings_dict['NAME'] = path
    try:
        yield
    finally:
        connection.close()
        connection.settings_dict['NAME'] = original
        connection.connection = memory
        directory.cleanup()


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
    assert not [t for t in threads if t.is_alive()], "a writer thread is still running (deadlock?)"
    assert not errors, errors
    return results


def _asset_body(project, name, archive):
    return {'name': name, 'project': str(project.pk), 'source_type': 'shared_project',
            'archive_path': archive, 'uproject_path': 'Scene/Scene.uproject', 'archive_sha256': ARCHIVE_SHA}


def _one_pending_per_key():
    keys = {}
    for record in UnrealMeasurement.objects.filter(pilot_job__status='QUEUED'):
        key = (record.asset_id, record.overrides_sha256, record.target_sha256)
        keys[key] = keys.get(key, 0) + 1
    return keys


@pytest.mark.django_db(transaction=True)
class TestConcurrentWrites:

    def test_two_job_creates_on_one_key_create_one_pilot(self, admin_user, file_database):
        asset = unreal_asset()
        a, b = _client(admin_user), _client(admin_user)
        results = _run_together(lambda: create_job(a, asset, name='RaceOne'),
                                lambda: create_job(b, asset, name='RaceTwo'))
        assert [r.status_code for r in results] == [201, 201]
        assert len(pilots()) == 1

    def test_two_asset_creates_on_one_project(self, admin_user, file_database):
        project = unreal_asset().project
        a, b = _client(admin_user), _client(admin_user)
        results = _run_together(
            lambda: a.post('/api/assets/', _asset_body(project, 'AssetRaceA', 'in/a.zip'), format='multipart'),
            lambda: b.post('/api/assets/', _asset_body(project, 'AssetRaceB', 'in/b.zip'), format='multipart'),
        )
        assert [r.status_code for r in results] == [201, 201], [r.data for r in results]
        assert Asset.objects.filter(project=project).count() == 3
        assert all(n == 1 for n in _one_pending_per_key().values())

    def test_asset_create_racing_a_cvar_patch(self, admin_user, file_database):
        asset = unreal_asset()
        created_job(_client(admin_user), asset)
        a, b = _client(admin_user), _client(admin_user)
        results = _run_together(
            lambda: a.post('/api/assets/', _asset_body(asset.project, 'AssetRaceC', 'in/c.zip'), format='multipart'),
            lambda: b.patch(f'/api/projects/{asset.project_id}/', {'unreal_cvar_overrides': CVARS}, format='json'),
        )
        assert [r.status_code for r in results] == [201, 200]
        assert all(n == 1 for n in _one_pending_per_key().values())

    def test_cvar_patch_racing_a_job_create(self, admin_user, worker_with_token, file_database):
        worker, worker_client = worker_with_token
        ready_worker(worker)
        asset = unreal_asset()
        created_job(_client(admin_user), asset, name='Existing')
        a, b = _client(admin_user), _client(admin_user)
        results = _run_together(
            lambda: create_job(a, asset, name='RaceJob', render_settings=settings()),
            lambda: b.patch(f'/api/projects/{asset.project_id}/', {'unreal_cvar_overrides': CVARS}, format='json'),
        )
        assert [r.status_code for r in results] == [201, 200]
        asset.project.refresh_from_db()
        current = asset.project.unreal_overrides_sha256
        job = Job.objects.get(name='RaceJob')
        pending = UnrealMeasurement.objects.filter(
            asset=asset, overrides_sha256=current, target_sha256=job.unreal_target_sha256,
            pilot_job__status='QUEUED')
        assert pending.count() == 1
        offered = {row['name'] for row in worker_client.get(JOBS, POLL).data}
        assert offered == {pending.get().pilot_job.name}


_OPENS = ('SAVEPOINT', 'BEGIN')
_CLOSES = ('RELEASE SAVEPOINT', 'COMMIT', 'ROLLBACK')
_KEY_TABLES = ('"workers_job"', '"workers_asset"', '"workers_unrealmeasurement"')


def _transactions(ctx):
    """The statements of each top-level transaction (nested savepoints included)."""
    found, stack = [], []
    for query in ctx.captured_queries:
        sql = query['sql']
        upper = sql.upper()
        if upper.startswith(_CLOSES):
            done = stack.pop()
            if stack:
                stack[-1].extend(done)
            else:
                found.append(done)
        elif upper.startswith(_OPENS):
            stack.append([])
        elif stack:
            stack[-1].append(sql)
    return found


def _inserts_key_rows(statements):
    return any(s.upper().startswith('INSERT INTO') and any(t in s for t in _KEY_TABLES) for s in statements)


@pytest.mark.django_db
class TestLockIsTheFirstStatement:

    def _assert_locked_first(self, ctx):
        """Every transaction that inserts a job, asset or record starts with the lock."""
        writing = [t for t in _transactions(ctx) if _inserts_key_rows(t)]
        assert writing, [q['sql'] for q in ctx.captured_queries]
        for statements in writing:
            assert LOCK_MARK in statements[0] and '"workers_project"' in statements[0], statements[:3]
            assert statements[1].upper().startswith('SELECT') and '"workers_project"' in statements[1]

    def test_job_create_update_project_update_and_retry(self, admin_client):
        set_share(SHARE)
        asset = unreal_asset()
        with CaptureQueriesContext(connection) as ctx:
            job = created_job(admin_client, asset)
        self._assert_locked_first(ctx)
        with CaptureQueriesContext(connection) as ctx:
            admin_client.patch(f'{JOBS}{job.pk}/', {'render_settings': settings(sequence='/Game/X/Y.Y')},
                               format='json')
        self._assert_locked_first(ctx)
        with CaptureQueriesContext(connection) as ctx:
            admin_client.patch(f'/api/projects/{asset.project_id}/', {'unreal_cvar_overrides': CVARS}, format='json')
        self._assert_locked_first(ctx)
        Job.objects.filter(unreal_kind='pilot').update(status='ERROR')
        with CaptureQueriesContext(connection) as ctx:
            resp = admin_client.post(f'/api/projects/{asset.project_id}/unreal-measurement/retry/', {},
                                     format='json')
        assert resp.status_code == 201, resp.data
        self._assert_locked_first(ctx)

    def test_asset_create(self, admin_client):
        set_share(SHARE)
        project = unreal_asset().project
        with CaptureQueriesContext(connection) as ctx:
            resp = admin_client.post('/api/assets/', _asset_body(project, 'LockAsset', 'in/l.zip'),
                                     format='multipart')
        assert resp.status_code == 201, resp.data
        self._assert_locked_first(ctx)

    def test_blender_writes_issue_no_lock(self, admin_client, project):
        blend = Asset.objects.create(name='BlendLock', project=project, blend_file=b'BLENDER-v300')
        with CaptureQueriesContext(connection) as ctx:
            resp = admin_client.post(JOBS, {'name': 'BlendJob', 'asset_id': blend.pk,
                                            'output_file_pattern': '//r/b.png'}, format='json')
            assert resp.status_code == 201
            admin_client.patch(f'{JOBS}{resp.data["id"]}/', {'name': 'BlendJob2'}, format='json')
            admin_client.patch(f'/api/projects/{project.pk}/', {'name': 'Renamed'}, format='json')
        assert not [q for q in ctx.captured_queries if LOCK_MARK in q['sql']]

    @pytest.mark.parametrize('write', ['asset_create', 'job_create', 'project_update'])
    def test_blender_creates_open_no_transaction(self, admin_client, project, write):
        """FR-PM-8: a .blend upload must not hold the SQLite write lock during the file write."""
        blend = Asset.objects.create(name='BlendTx', project=project, blend_file=b'BLENDER-v300')
        requests = {
            'asset_create': lambda: admin_client.post('/api/assets/', {
                'name': 'BlendUpload', 'project': str(project.pk),
                'blend_file': SimpleUploadedFile('scene.blend', b'BLENDER-v300' + b'0' * 64),
            }, format='multipart'),
            'job_create': lambda: admin_client.post(JOBS, {'name': 'BlendTxJob', 'asset_id': blend.pk,
                                                           'output_file_pattern': '//r/b.png'}, format='json'),
            'project_update': lambda: admin_client.patch(f'/api/projects/{project.pk}/', {'name': 'TxRenamed'},
                                                         format='json'),
        }
        with CaptureQueriesContext(connection) as ctx:
            resp = requests[write]()
        assert resp.status_code in (200, 201), resp.data
        assert not [q['sql'] for q in ctx.captured_queries if q['sql'].upper().startswith(_OPENS)]
