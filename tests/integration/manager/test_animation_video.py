# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for animation video assembly features.

Covers: serializer validation, video_settings immutability,
system-info endpoint, retry-video and download-video actions,
and startup recovery.
"""

import pytest

from workers.models import Animation


@pytest.fixture
def animation_payload(project, asset):
    """Base payload for creating an animation."""
    return {
        'name': 'TestVideoAnim',
        'project': project.pk,
        'asset_id': asset.pk,
        'output_file_pattern': 'frame_####.png',
        'start_frame': 1,
        'end_frame': 3,
        'frame_step': 1,
        'render_settings': {
            'render.image_settings.file_format': 'PNG',
        },
    }


class TestAnimationVideoSettingsValidation:
    """Serializer validation for video_settings on creation."""

    def test_create_with_preset_expands_settings(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 201, resp.data
        data = resp.data
        assert data['video_settings']['container'] == 'mp4'
        assert data['video_settings']['codec'] == 'libx264'
        assert data['video_settings']['crf'] == 23
        assert data['video_settings']['framerate'] == 24
        assert data['video_status'] == 'PENDING'

    def test_create_without_video_settings(
        self, admin_client, animation_payload,
    ):
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 201
        assert resp.data['video_settings'] is None
        assert resp.data['video_status'] is None

    def test_create_with_null_video_settings(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = None
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 201
        assert resp.data['video_settings'] is None

    def test_rejects_unknown_preset(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'nonexistent',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 400
        assert 'Unknown video preset' in str(resp.data)

    def test_rejects_invalid_framerate(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 0,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 400
        assert 'Framerate' in str(resp.data)

    def test_rejects_hdr_format_with_video(
        self, admin_client, animation_payload,
    ):
        animation_payload['render_settings'] = {
            'render.image_settings.file_format': 'OPEN_EXR',
        }
        animation_payload['output_file_pattern'] = 'frame_####.exr'
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 400
        assert 'not available for OPEN_EXR' in str(resp.data)

    def test_custom_rejects_codec_container_mismatch(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'custom',
            'container': 'mp4',
            'codec': 'libvpx-vp9',
            'framerate': 24,
            'crf': 31,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 400
        assert 'not valid for' in str(resp.data)

    def test_custom_valid_settings_accepted(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'custom',
            'container': 'webm',
            'codec': 'libvpx-vp9',
            'framerate': 30,
            'crf': 31,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 201
        assert resp.data['video_status'] == 'PENDING'


class TestVideoSettingsImmutability:
    """video_settings cannot be modified after creation."""

    def test_patch_video_settings_rejected(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        create_resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert create_resp.status_code == 201
        anim_id = create_resp.data['id']

        patch_resp = admin_client.patch(
            f'/api/animations/{anim_id}/',
            {'video_settings': {'preset': 'hq_h265', 'framerate': 30}},
            format='json',
        )
        assert patch_resp.status_code == 400
        assert 'cannot be modified after creation' in str(patch_resp.data)


class TestSystemInfoEndpoint:
    """Tests for GET /api/system-info/."""

    def test_returns_ffmpeg_status(self, admin_client):
        resp = admin_client.get('/api/system-info/')
        assert resp.status_code == 200
        assert 'ffmpeg_available' in resp.data
        assert isinstance(resp.data['ffmpeg_available'], bool)

    def test_requires_auth(self, db):
        from rest_framework.test import APIClient
        anon_client = APIClient()
        resp = anon_client.get('/api/system-info/')
        assert resp.status_code in (401, 403)


class TestRetryVideoAction:
    """Tests for POST /api/animations/{id}/retry-video/."""

    def test_retry_rejects_non_error_status(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        create_resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        anim_id = create_resp.data['id']

        resp = admin_client.post(f'/api/animations/{anim_id}/retry-video/')
        assert resp.status_code == 400
        assert 'ERROR' in str(resp.data)

    def test_retry_on_error_status(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        create_resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        anim_id = create_resp.data['id']

        # Force status to ERROR
        Animation.objects.filter(pk=anim_id).update(
            video_status='ERROR',
            video_error='test error',
        )

        resp = admin_client.post(f'/api/animations/{anim_id}/retry-video/')
        assert resp.status_code == 200
        assert resp.data['status'] == 'retry_started'

        anim = Animation.objects.get(pk=anim_id)
        assert anim.video_status == 'ASSEMBLING'
        assert anim.video_error == ''


class TestDownloadVideoAction:
    """Tests for GET /api/animations/{id}/download-video/."""

    def test_returns_404_when_no_video(
        self, admin_client, animation_payload,
    ):
        create_resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        anim_id = create_resp.data['id']
        resp = admin_client.get(f'/api/animations/{anim_id}/download-video/')
        assert resp.status_code == 404

    def test_returns_404_when_status_not_done(
        self, admin_client, animation_payload,
    ):
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        create_resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        anim_id = create_resp.data['id']
        resp = admin_client.get(f'/api/animations/{anim_id}/download-video/')
        assert resp.status_code == 404


class TestStartupRecovery:
    """Tests for stuck ASSEMBLING recovery on startup."""

    def _run_recovery(self):
        """Call the recovery logic directly via the registered app config."""
        from django.apps import apps
        config = apps.get_app_config('workers')
        config._reset_stuck_assemblies()

    def test_reset_stuck_assembling(self, db, animation_payload, admin_client):
        # Create animation with video settings
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        anim_id = resp.data['id']

        # Simulate stuck ASSEMBLING state
        Animation.objects.filter(pk=anim_id).update(
            video_status='ASSEMBLING',
        )

        self._run_recovery()

        anim = Animation.objects.get(pk=anim_id)
        assert anim.video_status == 'ERROR'
        assert 'interrupted by server restart' in anim.video_error

    def test_does_not_affect_other_statuses(
        self, db, animation_payload, admin_client,
    ):
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        anim_id = resp.data['id']

        # Set to DONE (should not be affected)
        Animation.objects.filter(pk=anim_id).update(video_status='DONE')

        self._run_recovery()

        anim = Animation.objects.get(pk=anim_id)
        assert anim.video_status == 'DONE'
