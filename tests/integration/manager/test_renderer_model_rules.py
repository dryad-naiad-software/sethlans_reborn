# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: renderer rules on the models (spec FR-MR-3, FR-MU-4,
FR-MS-1, FR-MR-12, FR-RT-13; chunk 1).

* ``Job.save()`` derives ``renderer`` and an empty ``engine_build``;
* the check constraints (and their readable ``validate_constraints``
  messages);
* ``Asset.save()`` without a ``.blend`` file;
* the ``ManagerSettings.sethlans_share`` validator (fails closed until the
  share endpoint lands);
* the shared ``requeue_or_fail`` helper;
* the manifest line of a shared-project asset.
"""

from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

from workers.models import Asset, Job, JobStatus, ManagerSettings, Project
from workers.models.manager_settings import validate_canonical_unc_root
from workers.views.stuck_jobs import _MAX_AUTO_REQUEUE, requeue_or_fail

BUILD = '5.8.4-58902052'
SHARED = {
    'source_type': 'shared_project', 'archive_path': 'incoming/scene.zip',
    'uproject_path': 'Scene/Scene.uproject', 'archive_sha256': 'f' * 64,
}


@pytest.fixture
def unreal_project(db):
    return Project.objects.create(name='RulesUnreal', renderer='UNREAL', engine_build=BUILD)


@pytest.fixture
def unreal_asset(unreal_project):
    return Asset.objects.create(name='RulesUnrealAsset', project=unreal_project, **SHARED)


def _blend():
    return ContentFile(b'BLENDER' + bytes(57), name='scene.blend')


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _integrity_error(create):
    with pytest.raises(IntegrityError), transaction.atomic():
        create()


@pytest.mark.django_db
class TestJobSaveDerivation:

    def test_renderer_and_build_come_from_the_project(self, unreal_asset):
        job = Job.objects.create(name='RulesU1', asset=unreal_asset, output_file_pattern='//r/f.png')
        assert (job.renderer, job.engine_build) == ('UNREAL', BUILD)

    def test_explicit_build_is_kept_and_renderer_is_overridden(self, unreal_asset):
        job = Job.objects.create(
            name='RulesU2', asset=unreal_asset, output_file_pattern='//r/f.png',
            renderer='BLENDER', engine_build='5.8.4-1',
        )
        assert (job.renderer, job.engine_build) == ('UNREAL', '5.8.4-1')

    def test_blender_job_defaults(self, asset):
        job = Job.objects.create(name='RulesB1', asset=asset, output_file_pattern='//r/f.png')
        assert (job.renderer, job.engine_build) == ('BLENDER', '')

    def test_update_does_not_rederive(self, unreal_asset, asset):
        job = Job.objects.create(name='RulesB2', asset=asset, output_file_pattern='//r/f.png')
        job.name = 'RulesB2x'
        job.save()
        assert Job.objects.get(pk=job.pk).renderer == 'BLENDER'

    def test_missing_asset_is_still_an_integrity_error(self, db):
        _integrity_error(lambda: Job.objects.create(name='RulesNoAsset', output_file_pattern='//r/f.png'))


@pytest.mark.django_db
class TestConstraints:

    def test_unreal_project_with_blender_version(self, default_version):
        _integrity_error(lambda: Project.objects.create(
            name='RulesBad1', renderer='UNREAL', engine_build=BUILD, blender_version=default_version))

    def test_blender_project_without_version(self, db):
        _integrity_error(lambda: Project.objects.create(name='RulesBad2'))

    def test_unreal_project_without_build(self, db):
        _integrity_error(lambda: Project.objects.create(name='RulesBad3', renderer='UNREAL'))

    @pytest.mark.parametrize('override', [
        {'archive_sha256': ''},
        {'overlay_path': 'fixes'},
        {'overlay_sha256': 'a' * 64},
        {'blend_file': 'with-file'},
    ], ids=['no-pin', 'overlay-without-pin', 'pin-without-overlay', 'with-blend-file'])
    def test_inconsistent_shared_asset(self, unreal_project, media_root, override):
        if 'blend_file' in override:
            override = {'blend_file': _blend()}
        _integrity_error(lambda: Asset.objects.create(
            name='RulesBadAsset', project=unreal_project, **{**SHARED, **override}))

    def test_blend_asset_with_archive_field(self, project, media_root):
        _integrity_error(lambda: Asset.objects.create(
            name='RulesBadBlend', project=project, blend_file=_blend(), archive_path='x.zip'))

    def test_blender_job_with_build(self, asset):
        job = Job.objects.create(name='RulesBadJob', asset=asset, output_file_pattern='//r/f.png')
        _integrity_error(lambda: Job.objects.filter(pk=job.pk).update(engine_build=BUILD))

    def test_validate_constraints_gives_a_readable_message(self, db):
        with pytest.raises(ValidationError) as exc:
            Project(name='RulesForm', renderer='UNREAL').validate_constraints()
        assert 'an Unreal project needs an engine build' in str(exc.value)

    def test_shared_asset_saves_without_a_blend_file(self, unreal_asset):
        unreal_asset.refresh_from_db()
        assert not unreal_asset.blend_file


@pytest.mark.django_db
class TestSethlansShareValidator:

    def test_empty_is_accepted(self):
        validate_canonical_unc_root('')

    @pytest.mark.parametrize('value', [r'\\nas\sethlans', 'x', ' '])
    def test_any_value_fails_closed_until_the_share_endpoint(self, value):
        with pytest.raises(ValidationError):
            validate_canonical_unc_root(value)

    def test_full_clean_runs_the_validator(self, db):
        row = ManagerSettings.objects.get(pk=1)
        row.sethlans_share = r'\\nas\sethlans'
        with pytest.raises(ValidationError) as exc:
            row.full_clean()
        assert 'sethlans_share' in exc.value.message_dict


@pytest.mark.django_db
class TestRequeueOrFail:

    def _rendering(self, asset, worker, count=0):
        return Job.objects.create(
            name=f'RulesRq{count}', asset=asset, output_file_pattern='//r/f.png',
            status=JobStatus.RENDERING, assigned_worker=worker, last_output='Fra:7',
            auto_requeue_count=count,
        )

    def test_requeues_and_keeps_last_output(self, asset, worker_with_token):
        job = self._rendering(asset, worker_with_token[0])
        with transaction.atomic():
            assert requeue_or_fail(job, 'Auto-requeued: test') == JobStatus.QUEUED
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.started_at, job.completed_at) == (
            JobStatus.QUEUED, None, None, None)
        assert (job.auto_requeue_count, job.error_message, job.last_output) == (1, 'Auto-requeued: test', 'Fra:7')

    def test_fails_after_the_limit(self, asset, worker_with_token):
        job = self._rendering(asset, worker_with_token[0], count=_MAX_AUTO_REQUEUE)
        with transaction.atomic():
            assert requeue_or_fail(job, 'unused') == JobStatus.ERROR
        job.refresh_from_db()
        assert job.status == JobStatus.ERROR
        assert job.error_message == 'Max auto-requeue attempts exceeded (3)'
        assert job.completed_at is not None
        assert job.auto_requeue_count == _MAX_AUTO_REQUEUE + 1


@pytest.mark.django_db(transaction=True)
def test_requeue_or_fail_raises_outside_an_atomic_block(asset, worker_with_token):
    """No test transaction wraps this test: the guard sees autocommit."""
    job = Job.objects.create(
        name='RulesRqAuto', asset=asset, output_file_pattern='//r/f.png',
        status=JobStatus.RENDERING, assigned_worker=worker_with_token[0],
    )
    assert not transaction.get_connection().in_atomic_block
    with pytest.raises(RuntimeError, match='inside transaction.atomic'):
        requeue_or_fail(job, 'msg')
    job.refresh_from_db()
    assert (job.status, job.auto_requeue_count, job.assigned_worker_id) == (
        JobStatus.RENDERING, 0, worker_with_token[0].pk)


@pytest.mark.django_db
def test_manifest_describes_shared_project_asset(settings, tmp_path, unreal_project):
    from workers.manifest_generator import update_project_manifest
    settings.MEDIA_ROOT = str(tmp_path)
    Asset.objects.create(name='RulesManifest', project=unreal_project, **SHARED)
    update_project_manifest(str(unreal_project.pk))
    manifest = Path(tmp_path) / 'assets' / str(unreal_project.pk)[:8] / 'manifest.txt'
    text = manifest.read_text(encoding='utf-8')
    assert '- RulesManifest (Archive: incoming/scene.zip (Scene/Scene.uproject))' in text
