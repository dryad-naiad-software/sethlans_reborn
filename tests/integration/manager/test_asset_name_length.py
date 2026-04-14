# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Regression tests for Asset model name field constraints.

Bug #38: The Asset name field was max_length=40 with a
MinLengthValidator(4), preventing legitimate long filenames
and short but valid names like "a.blend". The fix increased
max_length to 255 and removed the MinLengthValidator.
"""

import pytest

from workers.models import Asset

ASSETS_URL = '/api/assets/'


@pytest.mark.django_db
class TestAssetNameLength:
    """Verify Asset name field accepts names up to 255 characters."""

    def _create_asset_via_api(self, admin_client, project, name):
        """Helper: POST an asset with the given name via the API."""
        blend_content = b'BLENDER' + b'\x00' * 57
        from django.core.files.uploadedfile import SimpleUploadedFile
        blend_file = SimpleUploadedFile(
            'test.blend', blend_content,
            content_type='application/octet-stream',
        )
        return admin_client.post(
            ASSETS_URL,
            data={
                'name': name,
                'project': str(project.pk),
                'blend_file': blend_file,
            },
            format='multipart',
        )

    def test_49_character_name_succeeds(self, admin_client, project):
        """A 49-character name (exceeded old limit of 40) is accepted."""
        name = 'a' * 45 + '.blnd'
        assert len(name) == 50
        # Adjust to exactly 49
        name = 'a' * 44 + '.blnd'
        assert len(name) == 49
        resp = self._create_asset_via_api(admin_client, project, name)
        assert resp.status_code == 201, resp.data
        assert resp.data['name'] == name

    def test_255_character_name_succeeds(self, admin_client, project):
        """A 255-character name (the new max) is accepted."""
        name = 'x' * 249 + '.blend'
        assert len(name) == 255
        resp = self._create_asset_via_api(admin_client, project, name)
        assert resp.status_code == 201, resp.data
        assert resp.data['name'] == name

    def test_short_name_succeeds(self, admin_client, project):
        """A short name like 'a.blend' (7 chars) is accepted."""
        resp = self._create_asset_via_api(
            admin_client, project, 'a.blend',
        )
        assert resp.status_code == 201, resp.data
        assert resp.data['name'] == 'a.blend'

    def test_256_character_name_rejected(self, admin_client, project):
        """A 256-character name exceeds max_length and is rejected."""
        name = 'y' * 250 + '.blend'
        assert len(name) == 256
        resp = self._create_asset_via_api(admin_client, project, name)
        assert resp.status_code == 400

    def test_single_char_name_succeeds(self, admin_client, project):
        """Even a 1-character name is accepted (no min length)."""
        resp = self._create_asset_via_api(admin_client, project, 'Z')
        assert resp.status_code == 201, resp.data
        assert resp.data['name'] == 'Z'


@pytest.mark.django_db
class TestAssetNameLengthModel:
    """Direct model-level tests for the Asset name field."""

    def test_max_length_is_255(self):
        """The name field max_length must be 255."""
        field = Asset._meta.get_field('name')
        assert field.max_length == 255

    def test_no_min_length_validator(self):
        """The name field must NOT have a MinLengthValidator."""
        field = Asset._meta.get_field('name')
        validator_types = [type(v).__name__ for v in field.validators]
        assert 'MinLengthValidator' not in validator_types
