# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``0031_unrealmeasurement_ram`` (per-card spec
FR-PC-MGR-1): three additive nullable fields, no constraint operations,
existing records read null, and the migration reverses.
"""

import importlib

import pytest
from django.db import connection, migrations, models
from django.db.migrations.executor import MigrationExecutor

BEFORE = [('workers', '0030_sequence_videos')]
AFTER = [('workers', '0031_unrealmeasurement_ram')]
NEW_COLUMNS = {'ram', 'ram_process_commit_peak_mb', 'shared_machine'}
BUILD = '5.8.4-58902052'


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _columns():
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, 'workers_unrealmeasurement')}


@pytest.fixture
def at_0030(transactional_db):
    try:
        yield _migrate(BEFORE)
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


def _seed(apps):
    project = apps.get_model('workers', 'Project').objects.create(
        name='LegacyRam', renderer='UNREAL', engine_build=BUILD)
    asset = apps.get_model('workers', 'Asset').objects.create(
        name='LegacyRamArchive', project=project, source_type='shared_project',
        archive_path='in/x.zip', uproject_path='S/S.uproject', archive_sha256='ab' * 32)
    return apps.get_model('workers', 'UnrealMeasurement').objects.create(
        project=project, asset=asset, overrides_sha256='a' * 64, target_sha256='b' * 64,
        engine_build=BUILD, map='/Game/M.M', sequence='/Game/S.S', preset='/Game/P.P',
        peak_mb=8102, peak_source='card').pk


@pytest.mark.django_db(transaction=True)
class TestMigration:

    def test_existing_records_read_null_and_keep_their_results(self, at_0030):
        pk = _seed(at_0030)
        assert not NEW_COLUMNS & _columns()
        apps = _migrate(AFTER)
        assert NEW_COLUMNS <= _columns()
        record = apps.get_model('workers', 'UnrealMeasurement').objects.get(pk=pk)
        assert (record.ram, record.ram_process_commit_peak_mb, record.shared_machine) == (None, None, None)
        assert (record.peak_mb, record.peak_source) == (8102, 'card')

    def test_reverse_drops_only_the_three_columns(self, at_0030):
        pk = _seed(at_0030)
        before = _columns()
        _migrate(AFTER)
        apps = _migrate(BEFORE)
        assert _columns() == before
        assert apps.get_model('workers', 'UnrealMeasurement').objects.get(pk=pk).peak_mb == 8102


def test_three_nullable_additive_fields_without_a_data_step():
    module = importlib.import_module('workers.migrations.0031_unrealmeasurement_ram')
    operations = module.Migration.operations
    assert [type(op) for op in operations] == [migrations.AddField] * 3
    fields = {op.name: op.field for op in operations}
    assert {op.model_name for op in operations} == {'unrealmeasurement'}
    assert (type(fields['ram']), type(fields['ram_process_commit_peak_mb']), type(fields['shared_machine'])) == (
        models.JSONField, models.IntegerField, models.BooleanField)
    assert all(field.null and field.blank for field in fields.values())
    assert module.Migration.dependencies == BEFORE
