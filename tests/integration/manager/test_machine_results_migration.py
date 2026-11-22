# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``0032_machine_results`` (Unreal per-machine memory
test FR-MT-R-4, D13, OQ-1).

Covers the data step: farm-wide records stay as history (not current, no
card kind, results kept); QUEUED and RENDERING pilots are CANCELED and
finished ones untouched; draft pieces are re-hashed with their draft;
each submission gets ``piece_target_sha256`` from one of its render jobs
(after the re-hash); a submission without render jobs has its QUEUED
audio jobs CANCELED (others kept), while a submission with pieces keeps
its audio jobs; ``measured_ranges`` stays null; the reverse is a no-op
on the data and drops the new columns.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from shared.unreal.validation import target_sha256

BEFORE = [('workers', '0031_unrealmeasurement_ram')]
AFTER = [('workers', '0032_machine_results')]
BUILD = '5.8.4-58902052'
MAP, SEQUENCE, PRESET = '/Game/M.M', '/Game/S.S', '/Game/P.P'
SHOT_SET = 'c' * 64
DRAFT = {'temporal_samples': 2, 'motion_blur': False}
OLD_HASH = 'a' * 64
PILOT_REPLACED = "Replaced by per-machine memory tests."


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _columns(table):
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


@pytest.fixture
def at_0031(transactional_db):
    try:
        yield _migrate(BEFORE)
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


class _Seed:
    """Rows at 0031: a project, records, pilots, two submissions with jobs."""

    def __init__(self, apps):
        self.apps = apps
        model = apps.get_model
        self.project = model('workers', 'Project').objects.create(
            name='MtMigration', renderer='UNREAL', engine_build=BUILD)
        self.asset = model('workers', 'Asset').objects.create(
            name='MtMigrationArchive', project=self.project, source_type='shared_project',
            archive_path='in/x.zip', uproject_path='S/S.uproject', archive_sha256='ab' * 32)
        self.worker = model('workers', 'Worker').objects.create(hostname='mt-migration')
        self.discovery = model('workers', 'SequenceDiscovery').objects.create(
            project=self.project, asset=self.asset, engine_build=BUILD, map=MAP, sequence=SEQUENCE,
            preset=PRESET, target_sha256='d' * 64, status='DONE')

    def job(self, name, kind='render', status='QUEUED', submission=None, settings=None, target=OLD_HASH):
        render_settings = {'unreal.map': MAP, 'unreal.sequence': SEQUENCE, 'unreal.preset': PRESET,
                           **(settings or {})}
        return self.apps.get_model('workers', 'Job').objects.create(
            name=name, asset=self.asset, renderer='UNREAL', engine_build=BUILD, unreal_kind=kind,
            output_file_pattern='//r/f.png', render_settings=render_settings, status=status,
            sequence_submission=submission, unreal_target_sha256=target)

    def record(self, pilot=None, peak=8102):
        return self.apps.get_model('workers', 'UnrealMeasurement').objects.create(
            project=self.project, asset=self.asset, overrides_sha256='e' * 64, target_sha256=OLD_HASH,
            engine_build=BUILD, map=MAP, sequence=SEQUENCE, preset=PRESET, pilot_job=pilot, peak_mb=peak,
            peak_source='card' if peak else '', worker=self.worker, worker_hostname=self.worker.hostname)

    def submission(self, version, draft=None):
        return self.apps.get_model('workers', 'SequenceSubmission').objects.create(
            project=self.project, asset=self.asset, discovery=self.discovery, engine_build=BUILD, map=MAP,
            sequence=SEQUENCE, preset=PRESET, version=version, name=f'v{version}', piece_seconds=30,
            lead_in_ms=1000, draft=draft, shot_set_sha256=SHOT_SET, status='QUEUED')


@pytest.fixture
def seeded(at_0031):
    seed = _Seed(at_0031)
    rows = {
        'pilot_queued': seed.job('sethlans-pilot-1', 'pilot'),
        'pilot_rendering': seed.job('sethlans-pilot-2', 'pilot', 'RENDERING'),
        'pilot_done': seed.job('sethlans-pilot-3', 'pilot', 'DONE'),
        'pilot_error': seed.job('sethlans-pilot-4', 'pilot', 'ERROR'),
    }
    rows['record_measured'] = seed.record(rows['pilot_done'])
    rows['record_open'] = seed.record(rows['pilot_queued'], peak=None)
    plain = seed.submission(1)
    drafted = seed.submission(2, draft=dict(DRAFT))
    empty = seed.submission(3)
    piece_settings = {'unreal.shot_set_sha256': SHOT_SET}
    rows['plain_piece'] = seed.job('p1', submission=plain, settings=piece_settings, target='1' * 64)
    rows['plain_piece_2'] = seed.job('p2', submission=plain, settings=piece_settings, target='1' * 64)
    rows['plain_audio'] = seed.job('a1', 'audio', submission=plain)
    rows['draft_piece'] = seed.job('d1', submission=drafted,
                                   settings={**piece_settings, 'unreal.draft': dict(DRAFT)}, target='2' * 64)
    rows['empty_audio_queued'] = seed.job('e1', 'audio', submission=empty)
    rows['empty_audio_done'] = seed.job('e2', 'audio', 'DONE', submission=empty)
    rows['submissions'] = (plain.pk, drafted.pk, empty.pk)
    rows['standalone'] = seed.job('Still', target='9' * 64)
    return rows


def _get(apps, model, pk):
    return apps.get_model('workers', model).objects.get(pk=pk)


@pytest.mark.django_db(transaction=True)
class TestDataStep:

    def test_records_stay_as_history(self, seeded):
        apps = _migrate(AFTER)
        for name in ('record_measured', 'record_open'):
            record = _get(apps, 'UnrealMeasurement', seeded[name].pk)
            assert (record.current, record.card_kind, record.verdict, record.stale_reason) == (False, '', '', '')
        measured = _get(apps, 'UnrealMeasurement', seeded['record_measured'].pk)
        assert (measured.peak_mb, measured.worker_id, measured.pilot_job_id) == (
            8102, seeded['record_measured'].worker_id, seeded['pilot_done'].pk)

    def test_open_pilots_canceled_finished_untouched(self, seeded):
        apps = _migrate(AFTER)
        for name in ('pilot_queued', 'pilot_rendering'):
            pilot = _get(apps, 'Job', seeded[name].pk)
            assert (pilot.status, pilot.error_message) == ('CANCELED', PILOT_REPLACED)
            assert pilot.completed_at is not None and pilot.target_worker_id is None
        assert _get(apps, 'Job', seeded['pilot_done'].pk).status == 'DONE'
        assert _get(apps, 'Job', seeded['pilot_error'].pk).status == 'ERROR'

    def test_piece_keys_and_draft_rehash(self, seeded):
        apps = _migrate(AFTER)
        plain_pk, drafted_pk, empty_pk = seeded['submissions']
        drafted_key = target_sha256(BUILD, MAP, SEQUENCE, PRESET, None, SHOT_SET, DRAFT)
        assert _get(apps, 'Job', seeded['draft_piece'].pk).unreal_target_sha256 == drafted_key
        assert _get(apps, 'Job', seeded['plain_piece'].pk).unreal_target_sha256 == '1' * 64
        assert _get(apps, 'Job', seeded['standalone'].pk).unreal_target_sha256 == '9' * 64
        assert _get(apps, 'SequenceSubmission', plain_pk).piece_target_sha256 == '1' * 64
        assert _get(apps, 'SequenceSubmission', drafted_pk).piece_target_sha256 == drafted_key
        assert _get(apps, 'SequenceSubmission', empty_pk).piece_target_sha256 == ''
        assert {_get(apps, 'SequenceSubmission', pk).measured_ranges for pk in seeded['submissions']} == {None}

    def test_audio_of_a_submission_without_pieces(self, seeded):
        apps = _migrate(AFTER)
        queued = _get(apps, 'Job', seeded['empty_audio_queued'].pk)
        assert queued.status == 'CANCELED' and 'pieces' in queued.error_message
        assert _get(apps, 'Job', seeded['empty_audio_done'].pk).status == 'DONE'
        assert _get(apps, 'Job', seeded['plain_audio'].pk).status == 'QUEUED'
        assert _get(apps, 'Job', seeded['plain_piece'].pk).status == 'QUEUED'

    def test_reverse_keeps_the_data_and_drops_the_columns(self, seeded):
        _migrate(AFTER)
        assert {'target_worker_id', 'unreal_memory_report'} <= _columns('workers_job')
        apps = _migrate(BEFORE)
        assert not {'target_worker_id', 'unreal_memory_report'} & _columns('workers_job')
        assert not {'current', 'card_kind', 'verdict'} & _columns('workers_unrealmeasurement')
        assert 'piece_target_sha256' not in _columns('workers_sequencesubmission')
        assert _get(apps, 'Job', seeded['pilot_queued'].pk).status == 'CANCELED'
        assert _get(apps, 'UnrealMeasurement', seeded['record_measured'].pk).peak_mb == 8102
