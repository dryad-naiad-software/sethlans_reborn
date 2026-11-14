# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: pilot creation (Unreal foundation FR-PM-2, FR-PM-9,
AC-45, AC-49): the triggers per distinct measurement key, the pilot's
fields, its queue position and the reserved name.
"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from shared.unreal.validation import overrides_sha256, target_sha256

from workers.models import Asset, Job, UnrealMeasurement
from workers.services.unreal_pilot import ProjectLockMissing, Target, ensure_pilot, lock_project

from .unreal_helpers import BUILD, RENDER_SETTINGS, unreal_asset, unreal_project
from .unreal_pilot_helpers import OTHER_SEQUENCE, create_job, created_job, pilots, record_of, settings

CVARS = {'r.Streaming.PoolSize': '3000'}


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.mark.django_db
class TestFirstJob:

    def test_creates_one_pilot_with_the_job_target(self, admin_client, asset):
        job = created_job(admin_client, asset, frame=1000)
        [pilot] = pilots()
        record = record_of(pilot)
        assert pilot.name == f'sethlans-pilot-{record.pk}'
        assert (pilot.unreal_kind, pilot.renderer, pilot.render_device) == ('pilot', 'UNREAL', 'GPU')
        assert (pilot.engine_build, pilot.start_frame, pilot.end_frame, pilot.status) == (BUILD, 1000, 1000, 'QUEUED')
        assert pilot.render_settings == {**RENDER_SETTINGS, 'unreal.output_format': 'PNG'}
        assert pilot.output_file_pattern.endswith('.png')
        assert pilot.unreal_target_sha256 == job.unreal_target_sha256 == record.target_sha256
        assert record.overrides_sha256 == asset.project.unreal_overrides_sha256 == overrides_sha256({})
        assert (record.project_id, record.asset_id, record.remeasure, record.peak_mb) == (
            asset.project_id, asset.pk, False, None)
        assert (record.map, record.sequence, record.preset, record.engine_build) == (
            RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'],
            RENDER_SETTINGS['unreal.preset'], BUILD)

    def test_second_job_on_the_same_key_creates_none(self, admin_client, asset):
        created_job(admin_client, asset, name='Still01', frame=1000)
        created_job(admin_client, asset, name='Still02', frame=200)
        assert len(pilots()) == 1
        assert UnrealMeasurement.objects.count() == 1

    @pytest.mark.parametrize('change', [
        {'render_settings': settings(sequence=OTHER_SEQUENCE)},
        {'render_settings': settings(preset='/Game/Presets/Other.Other')},
        {'render_settings': settings(map='/Game/Maps/Other.Other')},
        {'engine_build': '5.8.3-1'},
    ], ids=['sequence', 'preset', 'map', 'engine_build'])
    def test_other_target_gets_its_own_pilot(self, admin_client, asset, change):
        created_job(admin_client, asset, name='Still01')
        second = created_job(admin_client, asset, name='Still02', **change)
        assert len(pilots()) == 2
        pilot = pilots()[-1]
        assert pilot.engine_build == second.engine_build
        assert pilot.render_settings == {**second.render_settings}
        assert pilot.unreal_target_sha256 == second.unreal_target_sha256
        created_job(admin_client, asset, name='Still03')
        assert len(pilots()) == 2

    def test_after_a_failed_measurement_no_submission_creates_a_pilot(self, admin_client, asset):
        created_job(admin_client, asset, name='Still01')
        Job.objects.filter(unreal_kind='pilot').update(status='ERROR', error_message='boom')
        created_job(admin_client, asset, name='Still02')
        assert len(pilots()) == 1

    def test_client_cannot_create_a_pilot(self, admin_client, asset):
        job = created_job(admin_client, asset, unreal_kind='pilot')
        assert job.unreal_kind == 'render'
        assert len(pilots()) == 1

    @pytest.mark.parametrize('name', ['sethlans-pilot-1', 'Sethlans-Pilot-x', 'SETHLANS-PILOT-'])
    def test_reserved_name_is_rejected(self, admin_client, asset, name):
        resp = create_job(admin_client, asset, name=name)
        assert resp.status_code == 400
        assert resp.data == {'name': ['Names starting with "sethlans-pilot-" are reserved for pilot jobs.']}
        assert pilots() == []

    def test_rename_to_reserved_name_is_rejected(self, admin_client, asset):
        job = created_job(admin_client, asset)
        resp = admin_client.patch(f'/api/jobs/{job.pk}/', {'name': 'sethlans-pilot-9'}, format='json')
        assert resp.status_code == 400
        assert 'name' in resp.data


@pytest.mark.django_db
class TestQueuePosition:

    def test_pilot_takes_the_oldest_held_job_place(self, admin_client, asset):
        old = Job.objects.create(name='OldOrm', asset=asset, output_file_pattern='//r/o.png',
                                 start_frame=5, end_frame=5, render_settings=dict(RENDER_SETTINGS),
                                 submitted_at=timezone.now() - timedelta(hours=3))
        created_job(admin_client, asset, name='Still02')
        [pilot] = pilots()
        assert pilot.submitted_at == old.submitted_at

    def test_without_an_older_job_it_is_now(self, admin_client, asset):
        before = timezone.now()
        job = created_job(admin_client, asset)
        [pilot] = pilots()
        assert before <= pilot.submitted_at <= timezone.now()
        assert pilot.submitted_at <= job.submitted_at + timedelta(seconds=5)


@pytest.mark.django_db
class TestCvarAndAssetTriggers:

    def _two_targets(self, admin_client, asset):
        created_job(admin_client, asset, name='Still01')
        created_job(admin_client, asset, name='Still02')
        created_job(admin_client, asset, name='Still03', render_settings=settings(sequence=OTHER_SEQUENCE))
        assert len(pilots()) == 2

    def test_cvar_change_creates_one_pilot_per_distinct_queued_target(self, admin_client, asset):
        self._two_targets(admin_client, asset)
        resp = admin_client.patch(f'/api/projects/{asset.project_id}/', {'unreal_cvar_overrides': CVARS},
                                  format='json')
        assert resp.status_code == 200, resp.data
        assert len(pilots()) == 4
        new_hash = overrides_sha256(CVARS)
        new = UnrealMeasurement.objects.filter(overrides_sha256=new_hash)
        assert new.count() == 2
        assert {r.target_sha256 for r in new} == {
            target_sha256(BUILD, RENDER_SETTINGS['unreal.map'], s, RENDER_SETTINGS['unreal.preset'])
            for s in (RENDER_SETTINGS['unreal.sequence'], OTHER_SEQUENCE)
        }

    def test_unchanged_cvars_create_none(self, admin_client, asset):
        self._two_targets(admin_client, asset)
        resp = admin_client.patch(f'/api/projects/{asset.project_id}/', {'unreal_cvar_overrides': {}},
                                  format='json')
        assert resp.status_code == 200, resp.data
        assert len(pilots()) == 2

    def test_target_without_queued_job_gets_none_until_its_first_job(self, admin_client, asset):
        created_job(admin_client, asset, name='Still01')
        Job.objects.filter(name='Still01').update(status='CANCELED')
        admin_client.patch(f'/api/projects/{asset.project_id}/', {'unreal_cvar_overrides': CVARS}, format='json')
        assert len(pilots()) == 1
        created_job(admin_client, asset, name='Still02')
        assert len(pilots()) == 2

    def test_new_asset_creates_none(self, admin_client, asset):
        created_job(admin_client, asset)
        unreal_asset(asset.project, name='SecondAsset', archive_path='incoming/other.zip')
        assert len(pilots()) == 1

    def test_render_settings_patch_to_an_unmeasured_target(self, admin_client, asset):
        job = created_job(admin_client, asset)
        resp = admin_client.patch(f'{"/api/jobs/"}{job.pk}/',
                                  {'render_settings': settings(sequence=OTHER_SEQUENCE)}, format='json')
        assert resp.status_code == 200, resp.data
        assert len(pilots()) == 2
        assert pilots()[-1].render_settings['unreal.sequence'] == OTHER_SEQUENCE


@pytest.mark.django_db
class TestLockAssertion:

    def test_ensure_pilot_without_the_lock_raises(self, asset):
        target = Target(BUILD, *(RENDER_SETTINGS[k] for k in ('unreal.map', 'unreal.sequence', 'unreal.preset')))
        with transaction.atomic():
            with pytest.raises(ProjectLockMissing):
                ensure_pilot(asset.project, asset, target, reason='test')
        with pytest.raises(ProjectLockMissing):
            ensure_pilot(asset.project, asset, target, reason='test')

    def test_lock_of_an_earlier_transaction_does_not_count(self, asset):
        target = Target(BUILD, *(RENDER_SETTINGS[k] for k in ('unreal.map', 'unreal.sequence', 'unreal.preset')))
        with transaction.atomic():
            lock_project(asset.project)
        with transaction.atomic():
            with pytest.raises(ProjectLockMissing):
                ensure_pilot(asset.project, asset, target, reason='test')

    def test_with_the_lock_it_creates_the_pilot(self, asset):
        target = Target(BUILD, *(RENDER_SETTINGS[k] for k in ('unreal.map', 'unreal.sequence', 'unreal.preset')))
        with transaction.atomic():
            lock_project(asset.project)
            pilot = ensure_pilot(asset.project, asset, target, reason='test')
            again = ensure_pilot(asset.project, asset, target, reason='test')
        assert pilot is not None and again is None


@pytest.mark.django_db
class TestBlenderUnaffected:

    def test_blender_writes_create_no_record(self, admin_client, project):
        resp = admin_client.post('/api/jobs/', {
            'name': 'sethlans-pilot-blender', 'asset_id': Asset.objects.create(
                name='BlendAsset', project=project, blend_file=b'BLENDER-v300').pk,
            'output_file_pattern': '//r/b.png',
        }, format='json')
        assert resp.status_code == 201, resp.data
        assert resp.data['unreal_kind'] == 'render'
        assert resp.data['unreal_measured_peak_mb'] is None
        assert UnrealMeasurement.objects.count() == 0
        assert pilots() == []


@pytest.mark.django_db
def test_second_project_keys_are_separate(admin_client):
    first = unreal_asset()
    second = unreal_asset(unreal_project(name='OtherProj'), name='OtherAsset')
    created_job(admin_client, first)
    created_job(admin_client, second)
    assert len(pilots()) == 2
