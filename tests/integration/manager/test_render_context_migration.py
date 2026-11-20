# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``0026_unrealmeasurement_render_context``
(gpu-memory-reader FR-GC-7, FR-GT-12, AC-30): two additive fields, existing
measurements read ``''``, no data step and nothing else pending.
"""

import importlib
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor

BEFORE = [('workers', '0025_worker_card_choice')]
AFTER = [('workers', '0026_unrealmeasurement_render_context')]
NEW_COLUMNS = {'game_overrides', 'texture_streaming'}
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
def at_0025(transactional_db):
    try:
        yield _migrate(BEFORE)
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


def _seed(apps):
    project = apps.get_model('workers', 'Project').objects.create(
        name='LegacyUnreal', renderer='UNREAL', engine_build=BUILD)
    asset = apps.get_model('workers', 'Asset').objects.create(
        name='LegacyArchive', project=project, source_type='shared_project',
        archive_path='in/x.zip', uproject_path='S/S.uproject', archive_sha256='ab' * 32)
    records = apps.get_model('workers', 'UnrealMeasurement').objects
    common = {'project': project, 'asset': asset, 'overrides_sha256': 'a' * 64, 'engine_build': BUILD,
              'map': '/Game/M.M', 'sequence': '/Game/S.S', 'preset': '/Game/P.P'}
    measured = records.create(target_sha256='b' * 64, peak_mb=8102, peak_source='card', **common)
    pending = records.create(target_sha256='c' * 64, **common)
    return measured.pk, pending.pk


@pytest.mark.django_db(transaction=True)
class TestMigration:

    def test_existing_records_read_blank_and_keep_their_results(self, at_0025):
        measured_pk, pending_pk = _seed(at_0025)
        assert not NEW_COLUMNS & _columns()
        apps = _migrate(AFTER)
        assert NEW_COLUMNS <= _columns()
        records = apps.get_model('workers', 'UnrealMeasurement').objects
        for pk in (measured_pk, pending_pk):
            record = records.get(pk=pk)
            assert (record.game_overrides, record.texture_streaming) == ('', '')
        measured = records.get(pk=measured_pk)
        assert (measured.peak_mb, measured.peak_source, measured.target_sha256) == (8102, 'card', 'b' * 64)

    def test_reverse_drops_only_the_two_columns(self, at_0025):
        measured_pk, _pending_pk = _seed(at_0025)
        before = _columns()
        _migrate(AFTER)
        apps = _migrate(BEFORE)
        assert _columns() == before
        assert apps.get_model('workers', 'UnrealMeasurement').objects.get(pk=measured_pk).peak_mb == 8102


def test_only_two_additive_fields_without_a_data_step():
    module = importlib.import_module('workers.migrations.0026_unrealmeasurement_render_context')
    operations = module.Migration.operations
    assert [type(op) for op in operations] == [migrations.AddField, migrations.AddField]
    assert {(op.model_name, op.name) for op in operations} == {
        ('unrealmeasurement', 'game_overrides'), ('unrealmeasurement', 'texture_streaming')}
    for op in operations:
        assert (op.field.default, op.field.max_length, op.field.null) == ('', 16, False)
    assert module.Migration.dependencies == BEFORE


@pytest.mark.django_db
def test_no_migration_is_pending():
    out = StringIO()
    call_command('makemigrations', 'workers', '--check', '--dry-run', stdout=out, stderr=StringIO())
    assert 'No changes detected' in out.getvalue()
