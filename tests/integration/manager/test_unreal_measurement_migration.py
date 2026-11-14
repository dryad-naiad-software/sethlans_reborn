# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``0022_unreal_measurement`` (Unreal foundation FR-PM-1,
FR-MR-6): forward fills the key hashes of existing Unreal rows and creates
no pilot; reverse is refused before any DDL while a measurement or a pilot
job exists.
"""

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

from shared.unreal.validation import overrides_sha256, target_sha256

BEFORE = [('workers', '0021_unreal_renderer_foundation')]
AFTER = [('workers', '0022_unreal_measurement')]
DDL_MARKERS = ('CREATE', 'ALTER', 'DROP')
BUILD = '5.8.4-58902052'
SETTINGS = {'unreal.map': '/Game/M.M', 'unreal.sequence': '/Game/S.S', 'unreal.preset': '/Game/P.P'}


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def at_0021(transactional_db):
    try:
        yield _migrate(BEFORE)
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


def _seed(apps):
    Project = apps.get_model('workers', 'Project')
    Asset = apps.get_model('workers', 'Asset')
    Job = apps.get_model('workers', 'Job')
    project = Project.objects.create(name='LegacyUnreal', renderer='UNREAL', engine_build=BUILD,
                                     unreal_cvar_overrides={'r.Streaming.PoolSize': '3000'})
    asset = Asset.objects.create(name='LegacyArchive', project=project, source_type='shared_project',
                                 archive_path='in/x.zip', uproject_path='S/S.uproject', archive_sha256='ab' * 32)
    job = Job.objects.create(name='LegacyStill', asset=asset, renderer='UNREAL', engine_build=BUILD,
                             output_file_pattern='//r/f.png', render_settings=SETTINGS)
    return project.pk, job.pk


@pytest.mark.django_db(transaction=True)
class TestForward:

    def test_fills_hashes_and_creates_no_pilot(self, at_0021):
        project_pk, job_pk = _seed(at_0021)
        apps = _migrate(AFTER)
        project = apps.get_model('workers', 'Project').objects.get(pk=project_pk)
        job = apps.get_model('workers', 'Job').objects.get(pk=job_pk)
        assert project.unreal_overrides_sha256 == overrides_sha256({'r.Streaming.PoolSize': '3000'})
        assert job.unreal_target_sha256 == target_sha256(BUILD, '/Game/M.M', '/Game/S.S', '/Game/P.P')
        assert job.unreal_kind == 'render'
        assert apps.get_model('workers', 'UnrealMeasurement').objects.count() == 0

    def test_guard_is_the_last_forward_operation(self):
        module = importlib.import_module('workers.migrations.0022_unreal_measurement')
        last = module.Migration.operations[-1]
        assert last.code.__name__ == 'noop' and last.reverse_code is module.reverse_guard


@pytest.mark.django_db(transaction=True)
class TestReverse:

    def _pilot(self, apps):
        Job = apps.get_model('workers', 'Job')
        Job.objects.filter(name='LegacyStill').update(unreal_kind='pilot')

    def _record(self, apps):
        Asset = apps.get_model('workers', 'Asset')
        asset = Asset.objects.get(name='LegacyArchive')
        apps.get_model('workers', 'UnrealMeasurement').objects.create(
            project_id=asset.project_id, asset=asset, overrides_sha256='a' * 64, target_sha256='b' * 64,
            engine_build=BUILD, map='/Game/M.M', sequence='/Game/S.S', preset='/Game/P.P')

    @pytest.mark.parametrize('make', ['_pilot', '_record'])
    def test_refused_before_any_ddl(self, at_0021, make):
        _seed(at_0021)
        apps = _migrate(AFTER)
        getattr(self, make)(apps)
        with CaptureQueriesContext(connection) as ctx:
            with pytest.raises(RuntimeError, match='Cannot unapply 0022_unreal_measurement'):
                _migrate(BEFORE)
        assert [q['sql'] for q in ctx.captured_queries if q['sql'].upper().startswith(DDL_MARKERS)] == []
        if make == '_record':
            apps.get_model('workers', 'UnrealMeasurement').objects.all().delete()
        else:
            apps.get_model('workers', 'Job').objects.update(unreal_kind='render')

    def test_reverse_succeeds_without_measurements(self, at_0021):
        project_pk, _job_pk = _seed(at_0021)
        _migrate(AFTER)
        apps = _migrate(BEFORE)
        assert apps.get_model('workers', 'Project').objects.filter(pk=project_pk).exists()
        columns = {c.name for c in connection.introspection.get_table_description(
            connection.cursor(), 'workers_project')}
        assert 'unreal_overrides_sha256' not in columns

    def test_reverse_on_a_sqlite_without_drop_column(self, at_0021, monkeypatch):
        """SQLite < 3.35.5: the column is dropped by rebuilding the table."""
        if connection.vendor != 'sqlite':
            pytest.skip('SQLite feature flag')
        project_pk, _job_pk = _seed(at_0021)
        _migrate(AFTER)
        monkeypatch.setattr(connection.features, 'can_alter_table_drop_column', False)
        apps = _migrate(BEFORE)
        project = apps.get_model('workers', 'Project').objects.get(pk=project_pk)
        assert project.unreal_cvar_overrides == {'r.Streaming.PoolSize': '3000'}
        columns = {c.name for c in connection.introspection.get_table_description(
            connection.cursor(), 'workers_project')}
        assert 'unreal_overrides_sha256' not in columns and 'engine_build' in columns
