# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for Blender version management.

Covers: create with resolved patch, delete dry-run preview,
confirmed delete with migration, and cannot-delete-last guard.
"""

import pytest
from unittest.mock import patch

from workers.models import (
    SupportedBlenderVersion, Project, Job, Asset,
)

VERSIONS_URL = '/api/supported-versions/'


@pytest.fixture
def _clean_versions(db):
    """
    Remove all migration-seeded versions so tests control the
    exact set of SupportedBlenderVersion rows.
    """
    SupportedBlenderVersion.objects.all().delete()


@pytest.mark.django_db
class TestVersionCreate:

    @patch(
        'workers.serializers.blender_versions.resolve_latest_patch',
        return_value='4.3.5',
    )
    def test_create_version_resolves_patch(
        self, mock_resolve, admin_client, default_version,
    ):
        """Creating a version resolves series to latest patch."""
        resp = admin_client.post(
            VERSIONS_URL,
            data={'series': '4.3'},
            format='json',
        )
        assert resp.status_code == 201
        assert resp.data['series'] == '4.3'
        assert resp.data['resolved_version'] == '4.3.5'
        assert resp.data['major'] == 4
        assert resp.data['minor'] == 3
        mock_resolve.assert_called_once_with('4.3', timeout=5)

    @patch(
        'workers.serializers.blender_versions.resolve_latest_patch',
        return_value=None,
    )
    def test_create_version_no_patch_returns_400(
        self, mock_resolve, admin_client, default_version,
    ):
        """Series with no patches returns 400."""
        resp = admin_client.post(
            VERSIONS_URL,
            data={'series': '9.9'},
            format='json',
        )
        assert resp.status_code == 400

    def test_create_version_invalid_format(
        self, admin_client, default_version,
    ):
        """Invalid series format is rejected."""
        resp = admin_client.post(
            VERSIONS_URL,
            data={'series': 'not-a-version'},
            format='json',
        )
        assert resp.status_code == 400


@pytest.mark.django_db
@pytest.mark.usefixtures('_clean_versions')
class TestVersionDeleteDryRun:

    def test_delete_dry_run_returns_preview(self, admin_client):
        """DELETE without confirm=true returns affected counts."""
        SupportedBlenderVersion.objects.create(
            series='4.2', resolved_version='4.2.19', is_default=True,
        )
        v43 = SupportedBlenderVersion.objects.create(
            series='4.3', resolved_version='4.3.5',
        )

        Project.objects.create(
            name='DryRunProj1', blender_version=v43,
        )

        resp = admin_client.delete(f'{VERSIONS_URL}{v43.pk}/')
        assert resp.status_code == 200
        assert resp.data['affected_project_count'] == 1
        assert 'migration_target' in resp.data

        # Version should NOT be deleted
        assert SupportedBlenderVersion.objects.filter(
            pk=v43.pk,
        ).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures('_clean_versions')
class TestVersionDeleteConfirmed:

    def test_confirmed_delete_migrates_projects(self, admin_client):
        """Confirmed delete migrates projects and nulls job FKs."""
        v42 = SupportedBlenderVersion.objects.create(
            series='4.2', resolved_version='4.2.19', is_default=True,
        )
        v43 = SupportedBlenderVersion.objects.create(
            series='4.3', resolved_version='4.3.5',
        )

        proj = Project.objects.create(
            name='MigrateProj', blender_version=v43,
        )
        asset = Asset(project=proj, name='MigrateAsst')
        asset.blend_file = b'\x00' * 64
        asset.save()
        job = Job.objects.create(
            name='MigrateJob1',
            asset=asset,
            output_file_pattern='//render/#.png',
            blender_version=v43,
        )

        resp = admin_client.delete(
            f'{VERSIONS_URL}{v43.pk}/?confirm=true',
        )
        assert resp.status_code == 200
        assert resp.data['migrated_project_count'] == 1

        # Version is gone
        assert not SupportedBlenderVersion.objects.filter(
            pk=v43.pk,
        ).exists()

        # Project migrated to the only remaining version (4.2)
        proj.refresh_from_db()
        assert proj.blender_version == v42

        # Job FK nulled
        job.refresh_from_db()
        assert job.blender_version is None

    def test_confirmed_delete_reassigns_default(self, admin_client):
        """Deleting the default version reassigns default to target."""
        v42 = SupportedBlenderVersion.objects.create(
            series='4.2', resolved_version='4.2.19', is_default=True,
        )
        v43 = SupportedBlenderVersion.objects.create(
            series='4.3', resolved_version='4.3.5',
        )

        resp = admin_client.delete(
            f'{VERSIONS_URL}{v42.pk}/?confirm=true',
        )
        assert resp.status_code == 200
        assert resp.data['new_default_version'] == '4.3'

        v43.refresh_from_db()
        assert v43.is_default is True


@pytest.mark.django_db
@pytest.mark.usefixtures('_clean_versions')
class TestVersionDeleteLastGuard:

    def test_cannot_delete_last_version(self, admin_client):
        """Cannot delete the last supported version."""
        v_only = SupportedBlenderVersion.objects.create(
            series='4.2', resolved_version='4.2.19', is_default=True,
        )
        resp = admin_client.delete(f'{VERSIONS_URL}{v_only.pk}/')
        assert resp.status_code == 400
        assert 'Cannot remove' in resp.data['error']
