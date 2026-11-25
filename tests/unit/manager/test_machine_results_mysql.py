# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``0032_machine_results.machine_results_data`` on MySQL (issue #318).

MySQL refused the audio step (error 1093): an ``UPDATE workers_job`` whose
``WHERE NOT EXISTS`` read ``workers_job``. The step now selects the keys
first and updates by a plain list of them, in chunks.

The data step runs on the test database with the real models. Its audio
rows must match the old statement's rule, worked out in Python from the
fixture rows (the old statement is never sent: MySQL refuses it), and
every ``UPDATE``/``DELETE`` it runs, compiled for MySQL, must not read its
own table in a subquery.
"""

import importlib
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.db.models import Exists, OuterRef

from tests.mysql_self_ref_sql import (
    mysql_compile_connection, mysql_write_checks, self_subquery_error, update_sql,
)
from workers.models import (
    Asset, Job, Project, SequenceDiscovery, SequenceSubmission, Worker,
)

MIGRATION = importlib.import_module('workers.migrations.0032_machine_results')
BUILD = '5.8.4-58902052'
MAP, SEQUENCE, PRESET = '/Game/M.M', '/Game/S.S', '/Game/P.P'
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=dt_timezone.utc)
_ROW = ('status', 'error_message', 'completed_at')


def _old_audio_step(now):
    """The pre-#318 statement: one UPDATE with a same-table NOT EXISTS (MySQL 1093)."""
    piece_of_own = Job.objects.filter(sequence_submission=OuterRef('sequence_submission'), unreal_kind='render')
    return Job.objects.filter(unreal_kind='audio', status='QUEUED').filter(~Exists(piece_of_own)), dict(
        status='CANCELED', error_message=MIGRATION.AUDIO_WITHOUT_PIECES, completed_at=now)


class _Seed:
    """A project with three submissions: with pieces, without pieces, and a second empty one."""

    def __init__(self):
        self.project = Project.objects.create(name='Mysql1093', renderer='UNREAL', engine_build=BUILD)
        self.asset = Asset.objects.create(
            name='Mysql1093Archive', project=self.project, source_type='shared_project',
            archive_path='in/x.zip', uproject_path='S/S.uproject', archive_sha256='ab' * 32)
        self.worker = Worker.objects.create(hostname='mysql-1093')
        self.discovery = SequenceDiscovery.objects.create(
            project=self.project, asset=self.asset, engine_build=BUILD, map=MAP, sequence=SEQUENCE,
            preset=PRESET, target_sha256='d' * 64, status='DONE')
        self.jobs = []

    def submission(self, version):
        return SequenceSubmission.objects.create(
            project=self.project, asset=self.asset, discovery=self.discovery, engine_build=BUILD, map=MAP,
            sequence=SEQUENCE, preset=PRESET, version=version, name=f'v{version}', piece_seconds=30,
            lead_in_ms=1000, shot_set_sha256='c' * 64, status='QUEUED')

    def job(self, name, kind='render', status='QUEUED', submission=None):
        """Unsaved; :meth:`save` writes them with ``bulk_create`` (no post_save automation)."""
        self.jobs.append(Job(
            name=name, asset=self.asset, renderer='UNREAL', engine_build=BUILD, unreal_kind=kind,
            output_file_pattern='//r/f.png', status=status, sequence_submission=submission,
            render_settings={'unreal.map': MAP, 'unreal.sequence': SEQUENCE, 'unreal.preset': PRESET},
            unreal_target_sha256='1' * 64))

    def save(self):
        Job.objects.bulk_create(self.jobs)


@pytest.fixture
def seeded(db):
    seed = _Seed()
    with_pieces, empty, empty_2 = seed.submission(1), seed.submission(2), seed.submission(3)
    seed.job('piece', submission=with_pieces)
    seed.job('piece-done', status='DONE', submission=with_pieces)
    seed.job('audio-kept', 'audio', submission=with_pieces)
    for index in range(5):
        seed.job(f'audio-empty-{index}', 'audio', submission=empty)
    seed.job('audio-other-empty', 'audio', submission=empty_2)
    seed.job('audio-done', 'audio', 'DONE', submission=empty)
    seed.job('audio-rendering', 'audio', 'RENDERING', submission=empty)
    seed.job('audio-alone', 'audio')
    seed.job('still', submission=None)
    seed.save()
    return seed


def _rows():
    return {row['name']: tuple(row[field] for field in _ROW)
            for row in Job.objects.exclude(unreal_kind='pilot').values('name', *_ROW)}


def _run_data_step():
    with patch.object(MIGRATION.timezone, 'now', return_value=NOW):
        MIGRATION.machine_results_data(django_apps, None)


def _old_rows():
    """The rows the pre-#318 statement gives, from the fixture rows (SELECTs only).

    QUEUED audio jobs with no render job in the same submission (none for a job
    without a submission: ``NULL = NULL`` never matches) become CANCELED.
    """
    jobs = list(Job.objects.exclude(unreal_kind='pilot').values('name', 'unreal_kind', 'sequence_submission_id', *_ROW))
    with_pieces = {job['sequence_submission_id'] for job in jobs
                   if job['unreal_kind'] == 'render' and job['sequence_submission_id'] is not None}
    canceled = ('CANCELED', MIGRATION.AUDIO_WITHOUT_PIECES, NOW)
    return {job['name']: canceled if (job['unreal_kind'], job['status']) == ('audio', 'QUEUED')
            and job['sequence_submission_id'] not in with_pieces else tuple(job[field] for field in _ROW)
            for job in jobs}


@pytest.mark.django_db
class TestAudioStepRows:

    def test_same_rows_as_the_old_statement(self, seeded):
        expected = _old_rows()
        _run_data_step()
        assert _rows() == expected

    def test_queued_audio_without_pieces_is_canceled(self, seeded):
        _run_data_step()
        rows = _rows()
        canceled = ('CANCELED', MIGRATION.AUDIO_WITHOUT_PIECES, NOW)
        for name in [f'audio-empty-{index}' for index in range(5)] + ['audio-other-empty', 'audio-alone']:
            assert rows[name] == canceled, name
        assert rows['audio-kept'] == ('QUEUED', '', None)
        assert rows['audio-done'][0] == 'DONE'
        assert rows['audio-rendering'][0] == 'RENDERING'
        assert rows['piece'] == ('QUEUED', '', None) and rows['still'] == ('QUEUED', '', None)

    def test_chunks_cover_every_row(self, seeded):
        expected = _old_rows()
        with patch.object(MIGRATION, '_UPDATE_CHUNK', 2):
            _run_data_step()
        assert _rows() == expected

    def test_nothing_to_cancel(self, db):
        seed = _Seed()
        seed.job('piece', submission=seed.submission(1))
        seed.save()
        _run_data_step()
        assert _rows() == {'piece': ('QUEUED', '', None)}


@pytest.mark.django_db
class TestMysqlCompiledSql:

    def test_oracle_flags_the_old_statement(self, db):
        queryset, values = _old_audio_step(NOW)
        error = self_subquery_error(update_sql(queryset, mysql_compile_connection(), **values))
        assert error is not None and 'workers_job' in error

    def test_data_step_never_reads_its_target_table(self, seeded):
        with mysql_write_checks() as errors:
            _run_data_step()
        assert errors == []

    def test_checks_see_the_old_statement(self, seeded):
        with mysql_write_checks(execute=False) as errors:
            queryset, values = _old_audio_step(NOW)
            queryset.update(**values)  # compiled and checked, never sent
        assert len(errors) == 1 and 'workers_job' in errors[0]
        assert _rows()['audio-alone'] == ('QUEUED', '', None)
