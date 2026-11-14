# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``0021_unreal_renderer_foundation`` (spec FR-MR-5/6, AC-1).

The spec calls this migration "0020"; #219's ``0020_parent_error_message``
took that number, so it is 0021 and these tests start from 0020.

* Forward: rows created at 0020 keep every column value, and the new
  columns hold their defaults.
* Reverse: refused with ``RuntimeError`` **before any DDL** while an
  Unreal project, a ``shared_project`` asset or an Unreal job exists;
  allowed otherwise.

Every test migrates back to the latest leaf in a ``finally``.
"""

from datetime import datetime, timezone as dt_timezone

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

BEFORE = [('workers', '0020_parent_error_message')]
AFTER = [('workers', '0021_unreal_renderer_foundation')]
DDL_MARKERS = ('CREATE TABLE', 'ALTER TABLE', 'DROP TABLE', 'CREATE INDEX', 'DROP INDEX')
SUBMITTED = datetime(2025, 5, 1, 12, 0, tzinfo=dt_timezone.utc)


def _migrate(targets):
    """Migrate to *targets*; return the historical apps at that state."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


def _migrate_to_latest():
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.fixture
def at_0020(transactional_db):
    """Historical apps at 0020; restores the latest schema afterwards."""
    try:
        yield _migrate(BEFORE)
    finally:
        _migrate_to_latest()


def _seed_0020(apps):
    """Create pre-0021 Project/Asset/Job/Worker rows; return their pks."""
    version = apps.get_model('workers', 'SupportedBlenderVersion').objects.create(
        major=4, minor=2, series='4.2', resolved_version='4.2.19', is_default=True,
    )
    project = apps.get_model('workers', 'Project').objects.create(
        name='LegacyProject', blender_version=version,
    )
    asset = apps.get_model('workers', 'Asset').objects.create(
        name='LegacyAsset', project=project, blend_file='assets/legacy/scene.blend',
    )
    job = apps.get_model('workers', 'Job').objects.create(
        name='LegacyJob', asset=asset, output_file_pattern='//render/f_####.png',
        status='RENDERING', render_device='GPU', last_output='Fra:1',
        submitted_at=SUBMITTED, auto_requeue_count=2,
    )
    worker = apps.get_model('workers', 'Worker').objects.create(
        hostname='legacy-host', available_tools={'blender': ['4.2.19']},
        cpu_name='Legacy CPU',
    )
    return {'version': version.pk, 'project': project.pk, 'asset': asset.pk,
            'job': job.pk, 'worker': worker.pk}


@pytest.mark.django_db(transaction=True)
class TestForward:

    def test_existing_rows_keep_values_and_get_defaults(self, at_0020):
        pks = _seed_0020(at_0020)
        apps = _migrate(AFTER)

        project = apps.get_model('workers', 'Project').objects.get(pk=pks['project'])
        assert (project.name, project.blender_version_id) == ('LegacyProject', pks['version'])
        assert (project.renderer, project.engine_build, project.unreal_cvar_overrides) == ('BLENDER', '', {})

        asset = apps.get_model('workers', 'Asset').objects.get(pk=pks['asset'])
        assert asset.blend_file.name == 'assets/legacy/scene.blend'
        assert asset.project_id == pks['project']
        assert asset.source_type == 'blend_file'
        for field in ('archive_path', 'uproject_path', 'archive_sha256', 'overlay_path', 'overlay_sha256'):
            assert getattr(asset, field) == ''

        job = apps.get_model('workers', 'Job').objects.get(pk=pks['job'])
        assert (job.status, job.render_device, job.last_output) == ('RENDERING', 'GPU', 'Fra:1')
        assert (job.submitted_at, job.auto_requeue_count) == (SUBMITTED, 2)
        assert (job.renderer, job.engine_build) == ('BLENDER', '')

        worker = apps.get_model('workers', 'Worker').objects.get(pk=pks['worker'])
        assert worker.available_tools == {'blender': ['4.2.19']}
        assert worker.cpu_name == 'Legacy CPU'
        assert (worker.unreal_capabilities, worker.gpu_memory, worker.boot_id) == ({}, {}, '')

        # The 0017 singleton row may already be flushed by an earlier
        # transactional test, so check the field and any row present.
        settings_model = apps.get_model('workers', 'ManagerSettings')
        assert settings_model._meta.get_field('sethlans_share').default == ''
        assert all(s == '' for s in settings_model.objects.values_list('sethlans_share', flat=True))

    def test_guard_is_the_last_forward_operation(self):
        import importlib
        from django.db import migrations
        module = importlib.import_module('workers.migrations.0021_unreal_renderer_foundation')
        last = module.Migration.operations[-1]
        assert isinstance(last, migrations.RunPython)
        assert last.reverse_code is module.reverse_guard


def _unreal_project(apps):
    return apps.get_model('workers', 'Project').objects.create(
        name='GuardUnreal', renderer='UNREAL', engine_build='5.8.4-58902052',
    )


def _shared_asset(apps, project):
    return apps.get_model('workers', 'Asset').objects.create(
        name='GuardShared', project=project, source_type='shared_project',
        archive_path='incoming/x.zip', uproject_path='X/X.uproject', archive_sha256='b' * 64,
    )


def _unreal_job_on_blender_asset(apps):
    """An UNREAL job row without any Unreal project (ORM/admin can do it)."""
    version = apps.get_model('workers', 'SupportedBlenderVersion').objects.get(series='4.2')
    project = apps.get_model('workers', 'Project').objects.create(
        name='GuardBlender', blender_version=version,
    )
    asset = apps.get_model('workers', 'Asset').objects.create(
        name='GuardBlenderAsset', project=project, blend_file='assets/g/s.blend',
    )
    apps.get_model('workers', 'Job').objects.create(
        name='GuardUnrealJob', asset=asset, output_file_pattern='//r/f.png',
        renderer='UNREAL', engine_build='5.8.4-58902052',
    )


def _make_unreal_project(apps):
    _unreal_project(apps)


def _make_shared_asset(apps):
    # The asset's project is Blender: the guard checks each model on its own.
    project = apps.get_model('workers', 'Project').objects.get(name='LegacyProject')
    _shared_asset(apps, project)


def _blender_project_without_version(apps):
    """A BLENDER project with a NULL version, which CHECK would forbid.

    MySQL < 8.0.16 parses but does not enforce CHECK constraints, so such a
    row can exist there.  SQLite's ``ignore_check_constraints`` pragma
    reproduces that here.
    """
    if connection.vendor != 'sqlite':
        pytest.skip('emulates an unenforced CHECK through a SQLite pragma')
    Project = apps.get_model('workers', 'Project')
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA ignore_check_constraints = ON')
        try:
            Project.objects.filter(name='LegacyProject').update(blender_version=None)
        finally:
            cursor.execute('PRAGMA ignore_check_constraints = OFF')
    assert Project.objects.filter(renderer='BLENDER', blender_version__isnull=True).exists()


@pytest.mark.django_db(transaction=True)
class TestReverse:

    @pytest.mark.parametrize('make_unreal_row', [
        _make_unreal_project, _make_shared_asset, _unreal_job_on_blender_asset,
        _blender_project_without_version,
    ], ids=['unreal-project', 'shared-project-asset', 'unreal-job', 'blender-project-without-version'])
    def test_reverse_refused_before_any_ddl(self, at_0020, make_unreal_row):
        _seed_0020(at_0020)
        apps = _migrate(AFTER)
        make_unreal_row(apps)

        with CaptureQueriesContext(connection) as ctx:
            with pytest.raises(RuntimeError, match='Cannot unapply 0021: Unreal data exists'):
                _migrate(BEFORE)
        ddl = [q['sql'] for q in ctx.captured_queries if q['sql'].upper().startswith(DDL_MARKERS)]
        assert ddl == []

        columns = {c.name for c in connection.introspection.get_table_description(
            connection.cursor(), 'workers_project')}
        assert {'renderer', 'engine_build'} <= columns
        assert MigrationExecutor(connection).loader.applied_migrations.keys() >= set(AFTER)

    def test_reverse_succeeds_without_unreal_data(self, at_0020):
        pks = _seed_0020(at_0020)
        _migrate(AFTER)
        apps = _migrate(BEFORE)

        columns = {c.name for c in connection.introspection.get_table_description(
            connection.cursor(), 'workers_project')}
        assert 'renderer' not in columns
        project = apps.get_model('workers', 'Project').objects.get(pk=pks['project'])
        assert project.blender_version_id == pks['version']
