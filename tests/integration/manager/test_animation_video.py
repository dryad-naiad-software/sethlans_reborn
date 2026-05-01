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
from workers.services.parts_check import registry as parts_registry


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
        # Spec wizard-ffmpeg-rewrite FR §137: closed-vocabulary code.
        assert 'video_settings_immutable' in str(patch_resp.data)

    def test_patch_with_preset_only_payload_succeeds_when_unchanged(
        self, admin_client, animation_payload,
    ):
        """Regression: a no-op PATCH that round-trips the persisted
        video_settings must NOT trip the immutability rejection.

        Pre-fix bug: the immutability check ran BEFORE preset
        expansion, so the raw incoming dict
        ``{"preset": "web_h264", "framerate": 24}`` compared unequal
        to the stored expanded dict
        ``{"preset": "web_h264", "framerate": 24, "container": "mp4",
        "codec": "libx264", "crf": 23}`` and the PATCH was rejected
        even though it was a no-op.  Post-fix: expansion runs first,
        so the expanded incoming dict matches the stored dict and the
        check is a true equality test.
        """
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        create_resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert create_resp.status_code == 201
        anim_id = create_resp.data['id']

        # Re-PATCH with the same preset-only payload (no expanded
        # fields).  After preset expansion this matches what's stored.
        patch_resp = admin_client.patch(
            f'/api/animations/{anim_id}/',
            {'video_settings': {'preset': 'web_h264', 'framerate': 24}},
            format='json',
        )
        assert patch_resp.status_code == 200, patch_resp.data

    def test_patch_immutability_error_propagates_code(
        self, admin_client, animation_payload,
    ):
        """Regression: spec FR §131 — the closed-vocab ``code`` must be
        on the leaf ErrorDetail so non-frontend API consumers can
        introspect it via ``response.data['video_settings'][0].code``.

        Pre-fix bug: ``ValidationError(detail=dict, code=...)`` silently
        dropped the ``code`` kwarg; leaves were tagged with the default
        ``'invalid'``.  Post-fix: we construct ErrorDetail explicitly.
        """
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        create_resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        anim_id = create_resp.data['id']

        patch_resp = admin_client.patch(
            f'/api/animations/{anim_id}/',
            {'video_settings': {'preset': 'hq_h265', 'framerate': 30}},
            format='json',
        )
        assert patch_resp.status_code == 400
        leaf = patch_resp.data['video_settings'][0]
        # Literal-string fallback (frontend match path).
        assert str(leaf) == 'video_settings_immutable'
        # Code propagation (mobile / CLI / integration consumer path).
        assert getattr(leaf, 'code', None) == 'video_settings_immutable'


class TestSystemInfoEndpoint:
    """Tests for GET /api/system-info/.

    The endpoint was removed entirely after the legacy
    ``ffmpeg_available`` field migrated to ``GET /api/ffmpeg-status/``
    (spec wizard-ffmpeg-rewrite FR §114-122).  Frontend's
    ``SystemInfoService`` was deleted in commit 2799d96; no callers
    remain.
    """

    def test_url_is_removed(self, admin_client):
        resp = admin_client.get('/api/system-info/')
        assert resp.status_code == 404


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


class TestVideoAssemblyAvailabilityGuard:
    """Defensive serializer rejection per spec FR §128-133 / AC §478-479.

    When ``parts_check.get_status('ffmpeg').status != 'ready'``, an
    animation create with non-null ``video_settings`` must fail with
    the standard DRF ``ValidationError`` shape and the closed-vocab
    code ``video_assembly_unavailable``.

    These tests seed the in-process registry directly via the
    registry's ``_publish`` helper — the same mechanism the real
    parts-check thread uses — to drive each FFmpeg state without
    invoking the real ``check_ffmpeg``.
    """

    @pytest.fixture(autouse=True)
    def _scoped_parts_state(self):
        # Snapshot the in-process status so the test seeds don't leak
        # into later test classes (TestStartupRecovery exercises the
        # animation-create path expecting the test machine's resolved
        # FFmpeg status to remain in effect).
        prior = parts_registry.get_status("ffmpeg")
        yield
        parts_registry._publish("ffmpeg", prior)

    def _seed(self, status, error=None):
        parts_registry._publish(
            "ffmpeg",
            parts_registry.Status(status=status, error=error),
        )

    def test_create_with_video_settings_while_installing_returns_400(
        self, admin_client, animation_payload,
    ):
        self._seed("installing")
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 400
        # Standard DRF shape: {"video_settings": ["video_assembly_unavailable"]}.
        assert 'video_settings' in resp.data
        assert resp.data['video_settings'] == ['video_assembly_unavailable']
        # No custom ``{"error": "..."}`` envelope (spec AC §478).
        assert 'error' not in resp.data

    def test_create_with_video_settings_while_failed_returns_400(
        self, admin_client, animation_payload,
    ):
        self._seed("failed", error="checksum_mismatch")
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 400
        assert resp.data['video_settings'] == ['video_assembly_unavailable']

    def test_create_with_video_settings_while_ready_succeeds(
        self, admin_client, animation_payload,
    ):
        self._seed("ready")
        animation_payload['video_settings'] = {
            'preset': 'web_h264',
            'framerate': 24,
        }
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 201, resp.data
        assert resp.data['video_status'] == 'PENDING'

    def test_create_without_video_settings_while_installing_succeeds(
        self, admin_client, animation_payload,
    ):
        # Spec AC §479: animations without video_settings are unaffected
        # by the parts-check status.
        self._seed("installing")
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 201
        assert resp.data['video_settings'] is None

    def test_create_with_null_video_settings_while_failed_succeeds(
        self, admin_client, animation_payload,
    ):
        self._seed("failed", error="download_failed")
        animation_payload['video_settings'] = None
        resp = admin_client.post(
            '/api/animations/', animation_payload, format='json',
        )
        assert resp.status_code == 201
        assert resp.data['video_settings'] is None


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


class TestTiledAnimationVideoTrigger:
    """Spec FR §141 — video assembly trigger MUST fire for tiled
    animations once all frames complete.

    The trigger lives in two places:
      * ``handle_job_completion`` — for non-tiled animations
        (Job post_save → all child Jobs DONE).
      * ``handle_animation_frame_completion`` — for tiled animations
        (AnimationFrame post_save → all frames DONE).

    The latter ONLY fires if the tile assembler transitions
    AnimationFrame to DONE via ``frame.save()`` (which fires
    post_save).  If the assembler ever switches to
    ``AnimationFrame.objects.filter(...).update(status=DONE)``, the
    signal is silently bypassed and tiled animations never produce
    video.  This regression test locks the contract in.
    """

    def test_frame_save_fires_video_assembly_trigger(
        self, db, project, asset, mocker,
    ):
        """Saving the LAST AnimationFrame as DONE must trigger
        ``_trigger_video_assembly`` (which transitions
        ``video_status`` PENDING -> ASSEMBLING and spawns the worker
        thread).
        """
        from workers.models import AnimationFrame, AnimationFrameStatus

        # Mock the actual assembly worker so no ffmpeg fork.
        # ``_trigger_video_assembly`` does a function-local
        # ``from .video_assembler import assemble_animation_video``
        # so we patch the source module, not workers.signals.
        mock_assemble = mocker.patch(
            'workers.video_assembler.assemble_animation_video',
        )
        # Replace the threading.Thread used inside
        # _trigger_video_assembly so the (mocked) assembler is invoked
        # synchronously and we don't leak background threads in tests.
        mocker.patch(
            'workers.signals.threading.Thread',
            side_effect=lambda target, args, daemon: type(
                'FakeThread', (), {
                    'start': lambda self_=None, t=target, a=args: t(*a),
                },
            )(),
        )
        # Under the default ``db`` fixture, the test runs inside an
        # uncommitted transaction so ``transaction.on_commit``
        # callbacks never fire.  Patch it to invoke the callback
        # immediately so we can observe the trigger path.
        mocker.patch(
            'workers.signals.transaction.on_commit',
            side_effect=lambda fn: fn(),
        )

        anim = Animation.objects.create(
            name='TiledTrigger',
            project=project,
            asset=asset,
            output_file_pattern='frame_####.png',
            start_frame=1,
            end_frame=2,
            frame_step=1,
            tiling_config='2x2',
            render_settings={
                'render.image_settings.file_format': 'PNG',
            },
            video_settings={
                'preset': 'web_h264',
                'framerate': 24,
                'container': 'mp4',
                'codec': 'libx264',
                'crf': 23,
            },
            video_status='PENDING',
        )
        f1 = AnimationFrame.objects.create(animation=anim, frame_number=1)
        f2 = AnimationFrame.objects.create(animation=anim, frame_number=2)

        # Transition both frames to DONE via ``frame.save()``.  The
        # post_save handler should observe both DONE and fire
        # ``_trigger_video_assembly`` after the second save.
        f1.status = AnimationFrameStatus.DONE
        f1.save(update_fields=['status'])

        # After the first frame, animation is not yet complete.
        anim.refresh_from_db()
        assert anim.video_status == 'PENDING', (
            "video_status must remain PENDING until all frames are DONE."
        )

        f2.status = AnimationFrameStatus.DONE
        f2.save(update_fields=['status'])

        # CAS update inside _trigger_video_assembly flips PENDING ->
        # ASSEMBLING.  If the trigger never fired (e.g. because the
        # assembler used .update() instead of frame.save()),
        # video_status would still be PENDING.
        anim.refresh_from_db()
        assert anim.video_status == 'ASSEMBLING', (
            "video_status must transition to ASSEMBLING once all "
            "frames are DONE — _trigger_video_assembly was not "
            "invoked.  Did the tile assembler switch to a queryset "
            ".update() that bypasses post_save?"
        )
        mock_assemble.assert_called_once_with(anim.pk)
