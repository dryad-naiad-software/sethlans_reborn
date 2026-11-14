# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: ``BlenderStrategy`` is a pure delegation of the pre-renderer
behavior (spec FR-MR-9, FR-T-4 "Blender strategy parity").

The pre-change ``JobViewSet._apply_version_filter`` and the claim view's
version check are reproduced below as oracles; the strategy must give
the same filter, the same errors and the same messages on a table of
inputs.  Database-backed behavior (``prepare_project_create``) is covered
by the integration tests.
"""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.models import Q
from django.http import QueryDict
from rest_framework.exceptions import ValidationError

from workers.renderers import get_strategy

_OLD_VERSION_REGEX = re.compile(r'^\d+\.\d+\.\d+$')


def _old_version_filter_q(param):
    """Oracle: the pre-change ``_apply_version_filter`` as a ``Q`` (or None)."""
    if not param:
        return None
    versions = [v.strip() for v in param.split(',') if v.strip()]
    if len(versions) > 20:
        raise ValidationError("Maximum 20 versions allowed.")
    for v in versions:
        if not _OLD_VERSION_REGEX.match(v):
            raise ValidationError(f"Invalid version format: {v}")
    series_list = list({'.'.join(v.split('.')[:2]) for v in versions})
    return (
        Q(blender_version__series__in=series_list)
        | Q(blender_version__isnull=True, asset__project__blender_version__series__in=series_list)
    )


def _old_claim_message(job, worker):
    """Oracle: the pre-change claim view's version check."""
    effective = job.effective_blender_version
    if effective and not worker.has_blender_version(effective.resolved_version):
        return (
            "Worker does not have required Blender "
            f"version {effective.resolved_version} installed."
        )
    return None


def _errors(func, *args):
    try:
        return ('ok', func(*args))
    except ValidationError as exc:
        return ('error', exc.detail)


@pytest.fixture
def blender():
    return get_strategy('BLENDER')


POLL_INPUTS = [
    None, '', '4.2.19', '4.2.19,4.5.8', ' 4.2.19 , 4.2.1 ', '4.2.19,,',
    '4.2', 'not-a-version', '4.2.19,bad',
    ','.join(f'4.{n}.0' for n in range(20)),
    ','.join(f'4.{n}.0' for n in range(21)),
    ','.join(['x'] * 21),
]


class TestPollParity:

    @pytest.mark.parametrize('param', POLL_INPUTS)
    def test_poll_q_matches_old_filter(self, blender, param):
        params = QueryDict(mutable=True)
        if param is not None:
            params['available_versions'] = param
        old = _errors(_old_version_filter_q, param)
        new = _errors(blender.poll_q, params)
        if old[0] == 'error':
            assert new == old
            return
        expected = Q(renderer='BLENDER') if old[1] is None else Q(renderer='BLENDER') & old[1]
        assert new == ('ok', expected)

    def test_other_params_are_ignored(self, blender):
        params = QueryDict('device_prefs=BANANA&unreal_builds=junk&renderers=BLENDER')
        assert blender.poll_q(params) == Q(renderer='BLENDER')


class TestClaimParity:

    @pytest.mark.parametrize('effective, installed', [
        (None, []),
        (SimpleNamespace(resolved_version='4.2.19'), ['4.2.19']),
        (SimpleNamespace(resolved_version='4.5.8'), ['4.2.19']),
        (SimpleNamespace(resolved_version='4.5.8'), []),
    ])
    def test_claim_rejection_matches_old_check(self, blender, effective, installed):
        job = SimpleNamespace(effective_blender_version=effective)
        worker = SimpleNamespace(has_blender_version=lambda v: v in installed)
        assert blender.claim_rejection(worker, job) == _old_claim_message(job, worker)


class TestValidation:

    def test_project_without_unreal_keys_passes_unchanged(self, blender):
        attrs = {'name': 'Proj'}
        assert blender.validate_project(attrs, None) is attrs

    @pytest.mark.parametrize('attrs, field', [
        ({'engine_build': '5.8.4-58902052'}, 'engine_build'),
        ({'unreal_cvar_overrides': {'r.X.Y': '1'}}, 'unreal_cvar_overrides'),
        ({'unreal_cvar_overrides': []}, 'unreal_cvar_overrides'),
    ])
    def test_project_rejects_unreal_keys(self, blender, attrs, field):
        with pytest.raises(ValidationError) as exc:
            blender.validate_project(attrs, None)
        assert exc.value.detail == {field: ["Not applicable to Blender projects."]}

    @pytest.mark.parametrize('attrs', [{'engine_build': ''}, {'unreal_cvar_overrides': {}}])
    def test_project_accepts_empty_unreal_keys(self, blender, attrs):
        assert blender.validate_project(attrs, None) == attrs

    def test_job_rejects_engine_build(self, blender):
        with pytest.raises(ValidationError) as exc:
            blender.validate_job({'engine_build': '5.8.4-1'}, MagicMock(), None)
        assert exc.value.detail == {'engine_build': ["Not applicable to Blender jobs."]}

    def test_job_keeps_output_pattern_extension_check(self, blender):
        attrs = {'output_file_pattern': '//r/f.jpg', 'render_settings': {
            'render.image_settings.file_format': 'PNG'}}
        with pytest.raises(ValidationError) as exc:
            blender.validate_job(attrs, MagicMock(), None)
        assert exc.value.detail == [
            "Output file pattern extension '.jpg' does not match the selected "
            "format 'PNG' (expected '.png')."
        ]

    def test_prepare_job_create_is_identity(self, blender):
        data = {'name': 'J'}
        assert blender.prepare_job_create(data, MagicMock()) is data

    def test_asset_rejects_archive_fields(self, blender):
        project = SimpleNamespace(renderer='BLENDER')
        with pytest.raises(ValidationError) as exc:
            blender.validate_asset({'project': project, 'archive_path': 'x.zip', 'overlay_sha256': 'a'}, None)
        assert exc.value.detail == {
            'archive_path': ["Not applicable to .blend file assets."],
            'overlay_sha256': ["Not applicable to .blend file assets."],
        }

    def test_asset_on_unreal_project_is_rejected_on_source_type(self, blender):
        project = SimpleNamespace(renderer='UNREAL')
        with pytest.raises(ValidationError) as exc:
            blender.validate_asset({'project': project}, None)
        assert exc.value.detail == {
            'source_type': ["Uploaded .blend file assets require a Blender project."],
        }

    def test_asset_update_uses_instance_project(self, blender):
        instance = SimpleNamespace(project=SimpleNamespace(renderer='BLENDER'))
        assert blender.validate_asset({'name': 'n'}, instance) == {'name': 'n'}


class TestFlagsAndDescriptions:

    def test_flags(self, blender):
        assert blender.supports('tiled_job') and blender.supports('animation')
        assert not blender.supports('something_else')
        assert blender.idempotent_reclaim() is False
        assert blender.worker_patch_allowed_fields() is None
        assert blender.requeue_on_worker_restart() is False
        assert blender.supports_release() is False
        assert blender.routing_note(MagicMock(), {}) is None

    def test_describe_asset_matches_old_manifest_text(self, blender):
        asset = SimpleNamespace(blend_file=SimpleNamespace(name='assets/aabbccdd/abc12345.blend'))
        assert blender.describe_asset(asset) == 'File: abc12345.blend'
