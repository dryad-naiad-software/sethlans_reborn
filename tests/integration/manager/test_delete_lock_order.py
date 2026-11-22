# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the project delete (issue #287) and the asset delete
(issue #288) take the D15 locks before Django's delete collector runs
(``services.delete_locks.lock_for_delete``).  Row locks are emulated on
SQLite (``delete_lock_helpers.row_locks``).

Checks, through ``DELETE /api/projects/{id}/`` and ``/api/assets/{id}/``:
Project write lock -> Job rows -> the Worker rows of the scope's records
(none when no record has a worker) -> the records -> the scope's
SequenceDiscovery rows -> its SequenceSubmission rows, each by ascending
pk, all before the collector's first write (its ``SET_NULL`` of
``pilot_job`` / ``covered_by``); the project delete
selects its Job rows through a subquery (no join, so no Asset row lock
on MariaDB); the locked sets (read back by re-running the captured
statements after rolling the delete back); a project deleted meanwhile
deletes nothing; Blender deletes take no lock; 204, rows gone, then 404.
"""

import re
from types import SimpleNamespace

import pytest

from tests.row_lock_sql import locked_tables, postgres_lock_error
from workers.models import Asset, Job, Project, SequenceDiscovery, SequenceSubmission, UnrealMeasurement, Worker
from workers.views.assets import AssetViewSet
from workers.views.projects import ProjectViewSet

from .delete_lock_helpers import (
    D15, NO_WORKER_LOCK, PROJECT_WRITE_LOCK, collector_writes, delete_captured, lock_statements, locked_pks,
    project_vanishes, sequence_rows, targets,
)
from .machine_test_helpers import pair_record, render_job
from .unreal_helpers import unreal_asset, unreal_project


@pytest.fixture
def scene(db, make_worker_client):
    """An Unreal project with two assets: records (one derived, ``covered_by``), jobs, sequence rows.

    Asset ``first``: a pilot record (worker ``first``), a derived record (worker ``second``), a render
    job, two discoveries and submissions.  Asset ``second``: a record (worker ``third``), a render job,
    one discovery and submission.  Worker ``idle`` has no record.
    """
    workers = {name: make_worker_client(f'del-{name}')[0] for name in ('first', 'second', 'third', 'idle')}
    project = unreal_project()
    first = unreal_asset(project, name='DelFirst')
    second = unreal_asset(project, name='DelSecond')
    measured = pair_record(first, 'a1' * 32, workers['first'], verdict='fits', pilot='QUEUED')
    derived = pair_record(first, 'a2' * 32, workers['second'], verdict='fits', covered_by=measured)
    UnrealMeasurement.objects.filter(pk=derived.pk).update(verdict_source='covered')
    other = pair_record(second, 'a3' * 32, workers['third'], verdict='fits', pilot='DONE')
    render_job(first, name='DelRender1')
    render_job(second, name='DelRender2')
    first_seq, second_seq = sequence_rows(first, (1, 2)), sequence_rows(second, (3,))
    return SimpleNamespace(project=project, first=first, second=second, measured=measured, derived=derived,
                           other=other, workers=workers, first_seq=first_seq, second_seq=second_seq)


@pytest.mark.django_db
@pytest.mark.parametrize('target', ['project', 'asset'])
class TestUnrealDeleteLocks:
    """Issues #287 (project) and #288 (asset): D15 order before the collector writes."""

    def test_d15_order_before_the_first_collector_write(self, admin_client, scene, target):
        url, _expected = targets(scene)[target]
        status, sqls = delete_captured(admin_client, url)
        assert status == 204
        locks = lock_statements(sqls)
        assert [table for _i, table, _sql in locks] == D15
        for _i, table, sql in locks:
            assert locked_tables(sql) == {table}, sql
            assert postgres_lock_error(sql) is None, sql
        writes = collector_writes(sqls)
        assert any(sqls[i].startswith('UPDATE "workers_unrealmeasurement"') for i in writes), sqls
        assert locks[-1][0] < writes[0], sqls[writes[0]]
        write_lock = next(i for i, sql in enumerate(sqls) if sql.startswith(PROJECT_WRITE_LOCK))
        assert write_lock < locks[0][0]

    def test_every_row_lock_is_by_ascending_pk(self, admin_client, scene, target):
        url, _expected = targets(scene)[target]
        locks = {table: sql for _i, table, sql in lock_statements(delete_captured(admin_client, url)[1])}
        for table in D15[1:]:
            assert re.search(rf'ORDER BY (1|"{table}"\."id") ASC\s*(/\*|$)', locks[table]), locks[table]

    def test_locked_sets(self, admin_client, scene, target):
        """Only the records' workers (not ``idle``); the asset delete leaves the other asset's rows alone."""
        url, expected = targets(scene)[target]
        status, sqls = delete_captured(admin_client, url, undo=True)
        assert status == 204
        locks = {table: sql for _i, table, sql in lock_statements(sqls)}
        assert locked_pks(locks['workers_project']) == [scene.project.pk.hex]
        assert {table: locked_pks(locks[table]) for table in expected} == expected
        assert scene.workers['idle'].pk not in locked_pks(locks['workers_worker'])

    @pytest.mark.parametrize('of', [True, False], ids=['for-update-of', 'no-for-update-of'])
    def test_no_lock_joins_another_table(self, admin_client, scene, target, of):
        url, _expected = targets(scene)[target]
        status, sqls = delete_captured(admin_client, url, of=of)
        assert status == 204
        locks = lock_statements(sqls)
        assert [table for _i, table, _sql in locks] == D15
        for _i, _table, sql in locks:
            assert 'JOIN' not in sql, sql
        if target == 'project':
            job_sql = locks[1][2]
            assert re.search(r'"workers_job"\."asset_id" IN \(SELECT', job_sql), job_sql


@pytest.mark.django_db
class TestUnrealDeleteResults:

    def test_project_delete_removes_every_row_then_404(self, admin_client, scene):
        """Issue #287: assets, jobs (pilot included) and records (derived included) go with the project."""
        url = f'/api/projects/{scene.project.pk}/'
        workers = Worker.objects.count()
        assert admin_client.delete(url).status_code == 204
        assert not Project.objects.filter(pk=scene.project.pk).exists()
        assert not Asset.objects.filter(pk__in=[scene.first.pk, scene.second.pk]).exists()
        for model in (Job, UnrealMeasurement, SequenceDiscovery, SequenceSubmission):
            assert not model.objects.exists(), model
        assert Worker.objects.count() == workers
        assert admin_client.delete(url).status_code == 404

    def test_asset_delete_removes_only_its_rows_then_404(self, admin_client, scene):
        """Issue #288: the asset's jobs, records and sequence rows go; the other asset's stay."""
        url = f'/api/assets/{scene.first.pk}/'
        assert admin_client.delete(url).status_code == 204
        assert not Asset.objects.filter(pk=scene.first.pk).exists()
        assert not Job.objects.filter(asset_id=scene.first.pk).exists()
        assert list(UnrealMeasurement.objects.values_list('pk', flat=True)) == [scene.other.pk]
        assert list(SequenceDiscovery.objects.values_list('pk', flat=True)) == [scene.second_seq[0][0].pk]
        assert list(SequenceSubmission.objects.values_list('pk', flat=True)) == [scene.second_seq[1][0].pk]
        assert Job.objects.filter(asset=scene.second).count() == 2
        assert Project.objects.filter(pk=scene.project.pk).exists()
        assert admin_client.delete(url).status_code == 404

    @pytest.mark.parametrize('records', ['none', 'null-worker'])
    def test_a_scope_without_record_workers_locks_no_worker(self, admin_client, scene, records):
        """No record, or only records without a worker: no Worker lock; the rest of D15 stays."""
        other = UnrealMeasurement.objects.filter(pk=scene.other.pk)
        if records == 'none':
            other.delete()
        else:
            other.update(worker=None)
        status, sqls = delete_captured(admin_client, f'/api/assets/{scene.second.pk}/')
        assert status == 204
        locks = lock_statements(sqls)
        assert [table for _i, table, _sql in locks] == NO_WORKER_LOCK
        assert locks[-1][0] < collector_writes(sqls)[0]
        assert not Asset.objects.filter(pk=scene.second.pk).exists()


@pytest.mark.django_db
class TestProjectGone:
    """Issues #287, #288: a project deleted meanwhile; the delete returns without deleting."""

    @pytest.mark.parametrize('target', ['project', 'asset'])
    def test_deletes_nothing(self, admin_client, scene, monkeypatch, mocker, target):
        viewset, model = (ProjectViewSet, Project) if target == 'project' else (AssetViewSet, Asset)
        project_vanishes(monkeypatch, viewset)
        url, _expected = targets(scene)[target]
        delete = mocker.spy(model, 'delete')
        status, sqls = delete_captured(admin_client, url)
        assert status == 204
        assert delete.call_count == 0
        locks = lock_statements(sqls)
        assert [table for _i, table, _sql in locks] == ['workers_project']
        assert not [i for i in collector_writes(sqls) if i > locks[0][0]], sqls


@pytest.mark.django_db
class TestBlenderDeletes:
    """Blender keeps the plain delete: no project write lock, no row lock."""

    def test_project_and_asset_delete_take_no_lock(self, admin_client, project, asset):
        second = Asset.objects.create(project=project, name='BlendSecond', blend_file=asset.blend_file.name)
        for owner in (asset, second):
            Job.objects.create(name=f'BlendDel{owner.pk}', asset=owner, output_file_pattern='//r/b.png')
        for url in (f'/api/assets/{asset.pk}/', f'/api/projects/{project.pk}/'):
            status, sqls = delete_captured(admin_client, url)
            assert status == 204
            assert not [sql for sql in sqls if 'FOR UPDATE' in sql or sql.startswith(PROJECT_WRITE_LOCK)]
        assert not Project.objects.filter(pk=project.pk).exists()
        assert not Asset.objects.exists() and not Job.objects.exists()
