# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for output format validation in serializers and views.

Covers: tiled job format restriction, animation tiled format restriction,
output_file_pattern extension validation, render_settings field validation,
and tile output pattern extensions.
"""

import pytest

from workers.constants import RenderSettings
from workers.models import Job, TiledJob

TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'
JOBS_URL = '/api/jobs/'


@pytest.mark.django_db
class TestTiledJobFormatValidation:
    """FR-15: Tiled jobs must use Pillow-compatible formats."""

    def test_png_accepted(self, admin_client, project, asset):
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'FmtPng001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'PNG',
            },
        }, format='json')
        assert resp.status_code == 201

    def test_jpeg_accepted(self, admin_client, project, asset):
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'FmtJpg001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
            },
        }, format='json')
        assert resp.status_code == 201

    def test_exr_rejected(self, admin_client, project, asset):
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'FmtExr001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'OPEN_EXR',
            },
        }, format='json')
        assert resp.status_code == 400
        assert 'not supported for tiled rendering' in str(resp.data)

    def test_hdr_rejected(self, admin_client, project, asset):
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'FmtHdr001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'HDR',
            },
        }, format='json')
        assert resp.status_code == 400
        assert 'not supported for tiled rendering' in str(resp.data)

    def test_no_format_defaults_png_accepted(self, admin_client, project, asset):
        """No format specified defaults to PNG, which is Pillow-compatible."""
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'FmtDfl001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
        }, format='json')
        assert resp.status_code == 201


@pytest.mark.django_db
class TestTiledJobOutputPattern:
    """FR-17: Tile output patterns use format-derived extension."""

    def test_default_png_extension(self, admin_client, project, asset):
        admin_client.post(TILED_JOBS_URL, data={
            'name': 'PatPng001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
        }, format='json')
        tj = TiledJob.objects.get(name='PatPng001')
        for job in Job.objects.filter(tiled_job=tj):
            assert job.output_file_pattern.endswith('.png')

    def test_jpeg_extension(self, admin_client, project, asset):
        admin_client.post(TILED_JOBS_URL, data={
            'name': 'PatJpg001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
            },
        }, format='json')
        tj = TiledJob.objects.get(name='PatJpg001')
        for job in Job.objects.filter(tiled_job=tj):
            assert job.output_file_pattern.endswith('.jpg')

    def test_tiff_extension(self, admin_client, project, asset):
        admin_client.post(TILED_JOBS_URL, data={
            'name': 'PatTif001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'TIFF',
            },
        }, format='json')
        tj = TiledJob.objects.get(name='PatTif001')
        for job in Job.objects.filter(tiled_job=tj):
            assert job.output_file_pattern.endswith('.tif')


@pytest.mark.django_db
class TestRenderSettingsValidation:
    """FR-43: render_settings field validation."""

    def test_invalid_format_rejected(self, admin_client, project, asset):
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'BadFmt001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'INVALID',
            },
        }, format='json')
        assert resp.status_code == 400
        assert 'Invalid output format' in str(resp.data)

    def test_invalid_quality_rejected(self, admin_client, project, asset):
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'BadQ001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
                RenderSettings.IMAGE_QUALITY: 200,
            },
        }, format='json')
        assert resp.status_code == 400
        assert 'JPEG quality' in str(resp.data)

    def test_invalid_color_depth_rejected(self, admin_client, project, asset):
        resp = admin_client.post(TILED_JOBS_URL, data={
            'name': 'BadCD001',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'PNG',
                RenderSettings.IMAGE_COLOR_DEPTH: '64',
            },
        }, format='json')
        assert resp.status_code == 400
        assert 'Color depth' in str(resp.data)


@pytest.mark.django_db
class TestJobOutputPatternValidation:
    """FR-42: Job output_file_pattern extension must match format."""

    def test_matching_extension_passes(self, admin_client, project, asset):
        resp = admin_client.post(JOBS_URL, data={
            'name': 'ExtMatch01',
            'asset_id': asset.pk,
            'output_file_pattern': 'render_####.jpg',
            'start_frame': 1,
            'end_frame': 1,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
            },
        }, format='json')
        assert resp.status_code == 201

    def test_mismatched_extension_rejected(self, admin_client, project, asset):
        resp = admin_client.post(JOBS_URL, data={
            'name': 'ExtMis01',
            'asset_id': asset.pk,
            'output_file_pattern': 'render_####.png',
            'start_frame': 1,
            'end_frame': 1,
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
            },
        }, format='json')
        assert resp.status_code == 400
        assert 'does not match' in str(resp.data)


@pytest.mark.django_db
class TestAnimationTiledFormatValidation:
    """FR-16: Tiled animations must use Pillow-compatible formats."""

    def test_tiled_animation_exr_rejected(self, admin_client, project, asset):
        resp = admin_client.post(ANIMATIONS_URL, data={
            'name': 'AnimExr01',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'output_file_pattern': 'anim_####.exr',
            'start_frame': 1,
            'end_frame': 5,
            'frame_step': 1,
            'tiling_config': '2x2',
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'OPEN_EXR',
                RenderSettings.RESOLUTION_X: 1920,
                RenderSettings.RESOLUTION_Y: 1080,
            },
        }, format='json')
        assert resp.status_code == 400
        assert 'not supported for tiled rendering' in str(resp.data)

    def test_untiled_animation_exr_accepted(
        self, admin_client, project, asset,
    ):
        resp = admin_client.post(ANIMATIONS_URL, data={
            'name': 'AnimExr02',
            'project': str(project.pk),
            'asset_id': asset.pk,
            'output_file_pattern': 'anim_####.exr',
            'start_frame': 1,
            'end_frame': 3,
            'frame_step': 1,
            'tiling_config': 'NONE',
            'render_settings': {
                RenderSettings.IMAGE_FILE_FORMAT: 'OPEN_EXR',
            },
        }, format='json')
        assert resp.status_code == 201
