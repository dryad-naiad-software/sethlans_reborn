# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``0028_sequence_submissions`` (Unreal sequence spec
FR-SQ-1, FR-SS-1, FR-SS-2, D2, D18; additive migration).

Covers: forward from 0027 keeps existing projects and jobs and gives the
new columns their defaults (piece length 30 s, lead-in 1000 ms, sound on,
no lead-in frames, no submission); backward removes the columns and the
new tables; ``makemigrations --check`` finds nothing to do.
"""

import pytest
from django.core.management import call_command
from django.db import DataError, IntegrityError, connection, transaction

from ..migration_helpers import migrate_to, migrate_to_latest

BEFORE = [('workers', '0027_sequence_discovery')]
AFTER = [('workers', '0028_sequence_submissions')]
BUILD = '5.8.4-58902052'
SETTINGS = {'unreal.map': '/Game/M.M', 'unreal.sequence': '/Game/S.S', 'unreal.preset': '/Game/P.P'}


def _columns(table):
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


@pytest.fixture
def at_0027(transactional_db):
    try:
        yield migrate_to(BEFORE)
    finally:
        migrate_to_latest()


def _seed(apps):
    Project = apps.get_model('workers', 'Project')
    Asset = apps.get_model('workers', 'Asset')
    Job = apps.get_model('workers', 'Job')
    project = Project.objects.create(name='MigrationShow', renderer='UNREAL', engine_build=BUILD)
    asset = Asset.objects.create(name='MigrationArchive', project=project, source_type='shared_project',
                                 archive_path='in/x.zip', uproject_path='S/S.uproject', archive_sha256='ab' * 32)
    job = Job.objects.create(name='MigrationStill', asset=asset, renderer='UNREAL', engine_build=BUILD,
                             output_file_pattern='//r/f.png', render_settings=SETTINGS)
    return project.pk, job.pk


@pytest.mark.django_db(transaction=True)
class TestMigration:

    def test_forward_gives_defaults(self, at_0027):
        project_pk, job_pk = _seed(at_0027)
        apps = migrate_to(AFTER)
        project = apps.get_model('workers', 'Project').objects.get(pk=project_pk)
        job = apps.get_model('workers', 'Job').objects.get(pk=job_pk)
        assert (project.unreal_piece_seconds, project.unreal_lead_in_ms, project.unreal_sound) == (30, 1000, True)
        assert project.unreal_sound_offsets is None
        assert (job.unreal_lead_in_frames, job.sequence_submission_id, job.name) == (0, None, 'MigrationStill')
        assert apps.get_model('workers', 'SequenceSubmission').objects.count() == 0

    def test_backward_removes_columns_and_tables(self, at_0027):
        project_pk, job_pk = _seed(at_0027)
        migrate_to(AFTER)
        assert 'unreal_lead_in_frames' in _columns('workers_job')
        apps = migrate_to(BEFORE)
        assert not {'unreal_lead_in_frames', 'sequence_submission_id'} & _columns('workers_job')
        assert not {'unreal_piece_seconds', 'unreal_lead_in_ms', 'unreal_sound', 'unreal_sound_offsets'} & _columns(
            'workers_project')
        assert 'workers_sequencesubmission' not in connection.introspection.table_names()
        assert apps.get_model('workers', 'Job').objects.filter(pk=job_pk).exists()
        assert apps.get_model('workers', 'Project').objects.filter(pk=project_pk).exists()

    def test_lead_in_column_refuses_negative(self, at_0027):
        _project_pk, job_pk = _seed(at_0027)
        apps = migrate_to(AFTER)
        # SQLite and PostgreSQL: the CHECK constraint (IntegrityError); MySQL and MariaDB: the
        # UNSIGNED column in strict mode (DataError 1264). Either way the row is refused.
        with pytest.raises((IntegrityError, DataError)), transaction.atomic():
            apps.get_model('workers', 'Job').objects.filter(pk=job_pk).update(unreal_lead_in_frames=-1)


@pytest.mark.django_db
def test_makemigrations_is_clean():
    call_command('makemigrations', check=True, dry_run=True, verbosity=0)
