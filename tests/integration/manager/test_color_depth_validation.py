# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: color depth must suit the job's file format (#243).

Every API path that accepts ``render_settings`` (jobs, tiled jobs and
animations, create and update) shares one field validator, so the full
table is exercised on the single-job endpoint and the rule once on each
other path. A rejection is a field error under ``render_settings``.
"""

import pytest

from workers.constants import FORMAT_EXTENSIONS, RenderSettings
from workers.models import Animation, Job, JobStatus, TiledJob

JOBS_URL = '/api/jobs/'
TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'
FMT = RenderSettings.IMAGE_FILE_FORMAT
DEPTH = RenderSettings.IMAGE_COLOR_DEPTH

TABLE = {
    'PNG': ('8', '16'),
    'JPEG': ('8',),
    'OPEN_EXR': ('16', '32'),
    'OPEN_EXR_MULTILAYER': ('16', '32'),
    'TIFF': ('8', '16'),
    'BMP': ('8',),
    'HDR': ('32',),
    'TARGA': ('8',),
}
ALL_COMBINATIONS = [
    (fmt, depth, depth in allowed)
    for fmt, allowed in TABLE.items()
    for depth in ('8', '16', '32')
]


def _post_job(client, asset, render_settings, fmt='PNG'):
    return client.post(JOBS_URL, data={
        'name': 'DepthJob01',
        'asset_id': asset.pk,
        'output_file_pattern': f'render_####{FORMAT_EXTENSIONS[fmt]}',
        'start_frame': 1,
        'end_frame': 1,
        'render_settings': render_settings,
    }, format='json')


def _field_error(resp):
    """The single render_settings error message of a 400 response."""
    assert resp.status_code == 400, resp.data
    assert list(resp.data) == ['render_settings']
    return str(resp.data['render_settings'][0])


@pytest.mark.django_db
class TestSingleJobTable:
    """Every row of the table, allowed and rejected, on POST /api/jobs/."""

    @pytest.mark.parametrize('fmt,depth,allowed', ALL_COMBINATIONS)
    def test_combination(self, admin_client, asset, fmt, depth, allowed):
        resp = _post_job(admin_client, asset, {FMT: fmt, DEPTH: depth}, fmt)
        if allowed:
            assert resp.status_code == 201, resp.data
            assert Job.objects.get().render_settings[DEPTH] == depth
            return
        allowed_text = ', '.join(f"'{d}'" for d in TABLE[fmt])
        assert _field_error(resp) == (
            f"Color depth '{depth}' is not supported for output format "
            f"'{fmt}'. Allowed color depths for '{fmt}': {allowed_text}."
        )
        assert not Job.objects.exists()


@pytest.mark.django_db
class TestSingleJobEdgeCases:

    @pytest.mark.parametrize('depth', ['8', '16'])
    def test_depth_without_format_allowed_for_png(self, admin_client, asset, depth):
        resp = _post_job(admin_client, asset, {DEPTH: depth})
        assert resp.status_code == 201, resp.data

    def test_depth_without_format_checked_against_png(self, admin_client, asset):
        resp = _post_job(admin_client, asset, {DEPTH: '32'})
        assert _field_error(resp) == (
            "Color depth '32' is not supported for output format 'PNG' "
            "(the default when no file format is set). "
            "Allowed color depths for 'PNG': '8', '16'."
        )

    def test_unknown_format_keeps_format_error(self, admin_client, asset):
        resp = _post_job(admin_client, asset, {FMT: 'GIF', DEPTH: '16'})
        message = _field_error(resp)
        assert message.startswith("Invalid output format 'GIF'.")
        assert 'Color depth' not in message

    def test_integer_depth_rejected(self, admin_client, asset):
        resp = _post_job(admin_client, asset, {FMT: 'OPEN_EXR', DEPTH: 32}, 'OPEN_EXR')
        assert _field_error(resp) == "Color depth must be one of '8', '16', '32' (a string)."

    def test_patch_rejects_combination(self, admin_client, asset):
        job_id = _post_job(admin_client, asset, {FMT: 'PNG', DEPTH: '16'}).data['id']
        resp = admin_client.patch(
            f'{JOBS_URL}{job_id}/', data={'render_settings': {FMT: 'PNG', DEPTH: '32'}},
            format='json',
        )
        assert "Color depth '32' is not supported for output format 'PNG'" in _field_error(resp)
        assert Job.objects.get(pk=job_id).render_settings[DEPTH] == '16'

    def test_patch_accepts_depth_8(self, admin_client, asset):
        job_id = _post_job(admin_client, asset, {FMT: 'PNG', DEPTH: '16'}).data['id']
        resp = admin_client.patch(
            f'{JOBS_URL}{job_id}/', data={'render_settings': {FMT: 'PNG', DEPTH: '8'}},
            format='json',
        )
        assert resp.status_code == 200, resp.data
        assert Job.objects.get(pk=job_id).render_settings[DEPTH] == '8'


def _tiled_payload(project, asset, render_settings):
    return {
        'name': 'DepthTile01',
        'project': str(project.pk),
        'asset_id': asset.pk,
        'final_resolution_x': 800,
        'final_resolution_y': 600,
        'tile_count_x': 2,
        'tile_count_y': 2,
        'render_settings': render_settings,
    }


@pytest.mark.django_db
class TestTiledJob:

    def test_rejects_combination(self, admin_client, project, asset):
        resp = admin_client.post(
            TILED_JOBS_URL, data=_tiled_payload(project, asset, {FMT: 'JPEG', DEPTH: '16'}),
            format='json',
        )
        assert _field_error(resp) == (
            "Color depth '16' is not supported for output format 'JPEG'. "
            "Allowed color depths for 'JPEG': '8'."
        )
        assert not TiledJob.objects.exists()

    def test_accepts_depth_8(self, admin_client, project, asset):
        resp = admin_client.post(
            TILED_JOBS_URL, data=_tiled_payload(project, asset, {FMT: 'PNG', DEPTH: '8'}),
            format='json',
        )
        assert resp.status_code == 201, resp.data

    def test_patch_rejects_combination(self, admin_client, project, asset):
        created = admin_client.post(
            TILED_JOBS_URL, data=_tiled_payload(project, asset, {FMT: 'PNG', DEPTH: '8'}),
            format='json',
        )
        resp = admin_client.patch(
            f"{TILED_JOBS_URL}{created.data['id']}/",
            data={'render_settings': {FMT: 'BMP', DEPTH: '16'}}, format='json',
        )
        assert "Color depth '16' is not supported for output format 'BMP'" in _field_error(resp)
        assert TiledJob.objects.get().render_settings == {FMT: 'PNG', DEPTH: '8'}


def _animation_payload(project, asset, render_settings):
    return {
        'name': 'DepthAnim01',
        'project': str(project.pk),
        'asset_id': asset.pk,
        'output_file_pattern': 'anim_####.exr',
        'start_frame': 1,
        'end_frame': 2,
        'frame_step': 1,
        'tiling_config': 'NONE',
        'render_settings': render_settings,
    }


@pytest.mark.django_db
class TestAnimation:

    def test_rejects_combination(self, admin_client, project, asset):
        resp = admin_client.post(
            ANIMATIONS_URL,
            data=_animation_payload(project, asset, {FMT: 'OPEN_EXR', DEPTH: '8'}),
            format='json',
        )
        assert _field_error(resp) == (
            "Color depth '8' is not supported for output format 'OPEN_EXR'. "
            "Allowed color depths for 'OPEN_EXR': '16', '32'."
        )
        assert not Animation.objects.exists()

    def test_accepts_allowed_depth(self, admin_client, project, asset):
        resp = admin_client.post(
            ANIMATIONS_URL,
            data=_animation_payload(project, asset, {FMT: 'OPEN_EXR', DEPTH: '32'}),
            format='json',
        )
        assert resp.status_code == 201, resp.data

    def test_patch_rejects_combination(self, admin_client, project, asset):
        created = admin_client.post(
            ANIMATIONS_URL,
            data=_animation_payload(project, asset, {FMT: 'OPEN_EXR', DEPTH: '32'}),
            format='json',
        )
        resp = admin_client.patch(
            f"{ANIMATIONS_URL}{created.data['id']}/",
            data={'render_settings': {FMT: 'OPEN_EXR', DEPTH: '8'}}, format='json',
        )
        assert "Color depth '8' is not supported for output format 'OPEN_EXR'" in _field_error(resp)
        assert Animation.objects.get().render_settings == {FMT: 'OPEN_EXR', DEPTH: '32'}


@pytest.mark.django_db
class TestStoredRows:
    """Rows stored before #243 are not re-validated by unrelated writes."""

    def test_worker_report_on_now_invalid_row(self, worker_with_token, asset):
        worker, client = worker_with_token
        stored = {FMT: 'PNG', DEPTH: '32'}
        job = Job.objects.create(
            name='LegacyDepth', asset=asset, output_file_pattern='render_####.png',
            start_frame=1, end_frame=1, render_settings=dict(stored),
            status=JobStatus.RENDERING, assigned_worker=worker,
        )
        # The fields the worker's final report sends (job_lifecycle.py).
        resp = client.patch(f'{JOBS_URL}{job.pk}/', data={
            'status': 'DONE', 'completed_at': '2026-11-16T00:00:00Z',
            'last_output': 'Fra:1 Saved render_0001.png', 'error_message': '',
            'render_time_seconds': 12,
        }, format='json')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert job.status == JobStatus.DONE
        assert job.render_settings == stored


@pytest.mark.django_db
def test_null_key_rejected_by_api(admin_client, asset):
    resp = _post_job(admin_client, asset, {FMT: None, DEPTH: '16'})
    assert _field_error(resp) == (
        f"'{FMT}' must not be null; leave the key out to use the default."
    )
