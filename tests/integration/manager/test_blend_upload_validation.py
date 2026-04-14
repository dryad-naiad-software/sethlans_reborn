# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for .blend file upload validation on the Asset endpoint.

Fixes #34: No backend validation on .blend file uploads. The
AssetSerializer now validates file extension, size, and magic bytes.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

ASSETS_URL = '/api/assets/'

# Valid .blend file content (starts with BLENDER magic bytes)
VALID_BLEND_CONTENT = b'BLENDER' + b'\x00' * 57


def _upload(admin_client, project, filename, content, content_type=None):
    """POST an asset upload and return the response."""
    ct = content_type or 'application/octet-stream'
    blend_file = SimpleUploadedFile(filename, content, content_type=ct)
    return admin_client.post(
        ASSETS_URL,
        data={
            'name': 'test-asset',
            'project': str(project.pk),
            'blend_file': blend_file,
        },
        format='multipart',
    )


@pytest.mark.django_db
class TestBlendFileExtensionValidation:
    """Reject uploads whose filename does not end with .blend."""

    def test_non_blend_extension_rejected(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'scene.png', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 400
        assert 'Only .blend files' in str(resp.data)

    def test_blend_extension_accepted(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'scene.blend', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 201

    def test_uppercase_blend_extension_accepted(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'scene.BLEND', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 201

    def test_mixed_case_blend_extension_accepted(
        self, admin_client, project,
    ):
        resp = _upload(
            admin_client, project, 'scene.Blend', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 201

    def test_no_extension_rejected(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'scenefile', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 400
        assert 'Only .blend files' in str(resp.data)

    def test_blend_substring_not_accepted(self, admin_client, project):
        """A filename like 'scene.blend.zip' must be rejected."""
        resp = _upload(
            admin_client, project, 'scene.blend.zip',
            VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 400

    def test_exe_extension_rejected(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'malware.exe', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestBlendFileMagicBytesValidation:
    """Reject files that don't start with BLENDER magic bytes."""

    def test_wrong_magic_bytes_rejected(self, admin_client, project):
        bad_content = b'\x00' * 64
        resp = _upload(
            admin_client, project, 'scene.blend', bad_content,
        )
        assert resp.status_code == 400
        assert 'valid .blend file' in str(resp.data)

    def test_partial_magic_bytes_rejected(self, admin_client, project):
        partial = b'BLEND' + b'\x00' * 59
        resp = _upload(
            admin_client, project, 'scene.blend', partial,
        )
        assert resp.status_code == 400
        assert 'valid .blend file' in str(resp.data)

    def test_empty_file_rejected(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'scene.blend', b'',
        )
        assert resp.status_code == 400

    def test_valid_magic_bytes_accepted(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'scene.blend', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 201


@pytest.mark.django_db
class TestBlendFileSizeValidation:
    """Reject files that exceed the 500 MB asset-specific limit."""

    def test_size_limit_error_message(self, admin_client, project):
        """
        Verify the error message mentions 500MB.

        We cannot actually create a 500 MB+ file in tests, so we
        monkeypatch the constant instead.
        """
        from workers.serializers import assets as assets_module
        original = assets_module.MAX_BLEND_FILE_SIZE
        try:
            # Temporarily set limit to 32 bytes
            assets_module.MAX_BLEND_FILE_SIZE = 32
            content = b'BLENDER' + b'\x00' * 57  # 64 bytes
            resp = _upload(
                admin_client, project, 'scene.blend', content,
            )
            assert resp.status_code == 400
            assert 'exceeds' in str(resp.data).lower()
        finally:
            assets_module.MAX_BLEND_FILE_SIZE = original

    def test_file_under_limit_accepted(self, admin_client, project):
        resp = _upload(
            admin_client, project, 'scene.blend', VALID_BLEND_CONTENT,
        )
        assert resp.status_code == 201
