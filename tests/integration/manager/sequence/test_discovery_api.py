# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``/api/sequence-discoveries/`` (Unreal sequence spec
FR-SD-1..3, FR-SD-9, FR-SL-5, D20, AC-1, AC-39).

Covers: create and reuse per key (QUEUED, RUNNING and DONE reused; ERROR
replaced), the ``shots`` job it makes, request validation, list filters,
the reserved job-name prefix, worker tokens refused on every admin route,
and the migration (additive, nothing pending).
"""

import importlib
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import migrations
from rest_framework.test import APIClient

from shared.unreal.validation import target_sha256
from workers.models import Asset, Job, Project, SequenceDiscovery

from ..unreal_helpers import BUILD, unreal_asset, unreal_project
from ..unreal_pilot_helpers import create_job
from .sequence_helpers import DISCOVERIES, MAP, PRESET, SEQUENCE, discovery_body, shots_job_of, start_discovery


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.mark.django_db
class TestCreate:

    def test_creates_a_record_and_its_shots_job(self, admin_client, asset):
        discovery, resp = start_discovery(admin_client, asset)
        assert resp.data['status'] == 'QUEUED'
        assert (resp.data['project'], resp.data['asset'], resp.data['engine_build']) == (
            asset.project_id, asset.pk, BUILD)
        assert (discovery.map, discovery.sequence, discovery.preset) == (MAP, SEQUENCE, PRESET)
        assert discovery.target_sha256 == target_sha256(BUILD, MAP, SEQUENCE, PRESET)
        job = shots_job_of(discovery)
        assert resp.data['job'] == job.pk
        assert job.name == f'sethlans-seq-shots-{discovery.pk}'
        assert (job.unreal_kind, job.status, job.renderer, job.engine_build) == ('shots', 'QUEUED', 'UNREAL', BUILD)
        assert (job.start_frame, job.end_frame, job.render_device) == (0, 0, 'GPU')
        assert job.render_settings == {'unreal.map': MAP, 'unreal.sequence': SEQUENCE, 'unreal.preset': PRESET}

    def test_same_key_twice_returns_the_same_record(self, admin_client, asset):
        first, _ = start_discovery(admin_client, asset)
        again, resp = start_discovery(admin_client, asset, expect=200)
        assert again.pk == first.pk
        assert Job.objects.filter(unreal_kind='shots').count() == 1

    @pytest.mark.parametrize('status', ['RUNNING', 'DONE'])
    def test_running_and_done_records_are_reused(self, admin_client, asset, status):
        first, _ = start_discovery(admin_client, asset)
        if status == 'DONE':
            Job.objects.filter(pk=first.job_id).update(status='DONE')
        else:
            Job.objects.filter(pk=first.job_id).update(status='RENDERING')
        SequenceDiscovery.objects.filter(pk=first.pk).update(status=status)
        again, _ = start_discovery(admin_client, asset, expect=200)
        assert again.pk == first.pk

    def test_failed_record_is_replaced(self, admin_client, asset):
        first, _ = start_discovery(admin_client, asset)
        SequenceDiscovery.objects.filter(pk=first.pk).update(status='ERROR', error_message='x')
        second, _ = start_discovery(admin_client, asset, expect=201)
        assert second.pk != first.pk
        assert shots_job_of(second).pk != first.job_id

    def test_open_record_with_a_failed_job_is_settled_and_replaced(self, admin_client, asset):
        first, _ = start_discovery(admin_client, asset)
        Job.objects.filter(pk=first.job_id).update(status='ERROR', error_message='Prep failed: x')
        second, _ = start_discovery(admin_client, asset, expect=201)
        first.refresh_from_db()
        assert (first.status, first.error_message) == ('ERROR', 'Prep failed: x')
        assert second.pk != first.pk

    @pytest.mark.parametrize('field, value', [
        ('map', '/Game/Maps/Other.Other'), ('sequence', '/Game/Cinematics/Ep02.Ep02'),
        ('preset', '/Game/Presets/Draft.Draft'),
    ])
    def test_each_key_part_makes_its_own_record(self, admin_client, asset, field, value):
        first, _ = start_discovery(admin_client, asset)
        other, _ = start_discovery(admin_client, asset, expect=201, **{field: value})
        assert other.pk != first.pk

    def test_another_asset_makes_its_own_record(self, admin_client, asset):
        first, _ = start_discovery(admin_client, asset)
        other_asset = unreal_asset(project=asset.project, name='Archive2', archive_sha256='ef' * 32)
        other, _ = start_discovery(admin_client, other_asset, expect=201)
        assert other.pk != first.pk

    def test_blender_asset_refused(self, admin_client, default_version):
        project = Project.objects.create(name='BlenderProj', blender_version=default_version)
        blender = Asset(project=project, name='BlendAsset',
                        blend_file=ContentFile(b'BLENDER' + b'\x00' * 57, name='scene.blend'))
        blender.save()
        resp = admin_client.post(DISCOVERIES, discovery_body(blender), format='json')
        assert resp.status_code == 400
        assert resp.data['asset'] == ["Shot discovery needs an asset of an Unreal project."]

    @pytest.mark.parametrize('field, value', [
        ('map', 'Game/Maps/Main'), ('sequence', '/Game/Seq;rm'), ('preset', ''), ('preset', '/' + 'a' * 600),
    ])
    def test_bad_object_path_refused(self, admin_client, asset, field, value):
        resp = admin_client.post(DISCOVERIES, discovery_body(asset, **{field: value}), format='json')
        assert resp.status_code == 400 and field in resp.data
        assert not SequenceDiscovery.objects.exists()

    def test_unknown_key_refused(self, admin_client, asset):
        resp = admin_client.post(DISCOVERIES, discovery_body(asset, engine_build='5.8.4-1'), format='json')
        assert (resp.status_code, resp.data) == (400, {'detail': 'Unknown keys: engine_build.'})

    def test_missing_asset_refused(self, admin_client, asset):
        resp = admin_client.post(DISCOVERIES, dict(discovery_body(asset), asset=999999), format='json')
        assert resp.status_code == 400 and 'asset' in resp.data


@pytest.mark.django_db
class TestRead:

    def test_list_filters_by_project_and_asset(self, admin_client, asset):
        first, _ = start_discovery(admin_client, asset)
        other_asset = unreal_asset(project=unreal_project(name='Second'), name='Other')
        other, _ = start_discovery(admin_client, other_asset)
        ids = [row['id'] for row in admin_client.get(DISCOVERIES).data['results']]
        assert ids == [other.pk, first.pk]
        by_project = admin_client.get(DISCOVERIES, {'project': asset.project_id}).data['results']
        assert [r['id'] for r in by_project] == [first.pk]
        by_asset = admin_client.get(DISCOVERIES, {'asset': other_asset.pk}).data['results']
        assert [r['id'] for r in by_asset] == [other.pk]

    def test_list_rows_are_summaries(self, admin_client, asset):
        first, _ = start_discovery(admin_client, asset)
        SequenceDiscovery.objects.filter(pk=first.pk).update(
            status='DONE', shots=[{'index': 1, 'name': 'a', 'start': 0, 'end': 9}], warnings=['w'])
        row = admin_client.get(DISCOVERIES).data['results'][0]
        assert 'shots' not in row and 'warnings' not in row
        assert row['id'] == first.pk and row['status'] == 'DONE' and 'preset_facts' in row
        detail = admin_client.get(f'{DISCOVERIES}{first.pk}/').data
        assert detail['shots'][0]['name'] == 'a' and detail['warnings'] == ['w']

    def test_list_is_paginated(self, admin_client, asset):
        for sequence in ('/Game/C/A.A', '/Game/C/B.B', '/Game/C/D.D'):
            start_discovery(admin_client, asset, sequence=sequence)
        data = admin_client.get(DISCOVERIES, {'page_size': 2}).data
        assert data['count'] == 3 and len(data['results']) == 2 and data['next']
        assert len(admin_client.get(DISCOVERIES, {'page_size': 2, 'page': 2}).data['results']) == 1

    def test_retrieve(self, admin_client, asset):
        first, _ = start_discovery(admin_client, asset)
        data = admin_client.get(f'{DISCOVERIES}{first.pk}/').data
        assert (data['id'], data['status'], data['shots'], data['warnings']) == (first.pk, 'QUEUED', [], [])


@pytest.mark.django_db
class TestAdminOnly:

    def test_worker_token_gets_403_everywhere(self, admin_client, asset, worker_with_token):
        _worker, client = worker_with_token
        first, _ = start_discovery(admin_client, asset)
        assert client.get(DISCOVERIES).status_code == 403
        assert client.get(f'{DISCOVERIES}{first.pk}/').status_code == 403
        assert client.post(DISCOVERIES, discovery_body(asset), format='json').status_code == 403
        assert SequenceDiscovery.objects.count() == 1

    def test_anonymous_refused(self, asset):
        assert APIClient().get(DISCOVERIES).status_code in (401, 403)

    @pytest.mark.parametrize('method', ['put', 'patch', 'delete'])
    def test_no_write_routes(self, admin_client, asset, method):
        first, _ = start_discovery(admin_client, asset)
        resp = getattr(admin_client, method)(f'{DISCOVERIES}{first.pk}/', {}, format='json')
        assert resp.status_code == 405


@pytest.mark.django_db
class TestReservedJobNames:

    @pytest.mark.parametrize('name', ['sethlans-seq-shots-1', 'Sethlans-Seq-mine'])
    def test_client_job_name_refused(self, admin_client, asset, name):
        resp = create_job(admin_client, asset, name=name)
        assert resp.status_code == 400
        assert resp.data['name'] == ['Names starting with "sethlans-seq-" are reserved for sequence renders.']


class TestMigration:

    def test_additive_with_no_data_step(self):
        module = importlib.import_module('workers.migrations.0027_sequence_discovery')
        operations = module.Migration.operations
        assert [type(op) for op in operations] == [migrations.AlterField, migrations.CreateModel]
        assert (operations[0].model_name, operations[0].name) == ('job', 'unreal_kind')
        assert operations[1].name == 'SequenceDiscovery'
        assert module.Migration.dependencies == [('workers', '0026_unrealmeasurement_render_context')]
        index = operations[1].options['indexes'][0]
        assert (index.name, index.fields) == ('seqdisc_key_idx', ['asset', 'target_sha256', '-created_at', '-id'])

    @pytest.mark.django_db
    def test_no_migration_is_pending(self):
        out = StringIO()
        call_command('makemigrations', 'workers', '--check', '--dry-run', stdout=out, stderr=StringIO())
        assert 'No changes detected' in out.getvalue()
