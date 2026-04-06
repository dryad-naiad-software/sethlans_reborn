# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the Project API endpoint.

Covers: CRUD and cancel_all_jobs. Project-level pause was removed in
favor of job-level pause (see test_job_pause.py).
"""

import pytest

PROJECTS_URL = '/api/projects/'


@pytest.mark.django_db
class TestProjectCreate:

    def test_create_project_returns_201(
        self, admin_client, default_version,
    ):
        """Create project returns 201 with correct fields."""
        resp = admin_client.post(
            PROJECTS_URL,
            data={
                'name': 'NewProject1',
                'blender_version': default_version.pk,
            },
            format='json',
        )
        assert resp.status_code == 201
        assert resp.data['name'] == 'NewProject1'
        assert 'is_paused' not in resp.data
        assert 'id' in resp.data
        assert 'created_at' in resp.data

    def test_create_project_uses_default_version(
        self, admin_client, default_version,
    ):
        """Omitting blender_version uses the default version."""
        resp = admin_client.post(
            PROJECTS_URL,
            data={'name': 'DefaultVerProj'},
            format='json',
        )
        assert resp.status_code == 201
        details = resp.data.get('blender_version_details', {})
        assert details['series'] == '4.2'

    def test_create_project_name_too_short(
        self, admin_client, default_version,
    ):
        """Project name under 4 chars is rejected."""
        resp = admin_client.post(
            PROJECTS_URL,
            data={'name': 'abc', 'blender_version': default_version.pk},
            format='json',
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestProjectPauseRemoved:
    """Project-level pause/unpause actions have been removed (AC-3, AC-4)."""

    def test_pause_endpoint_returns_404(self, admin_client, project):
        """POST /api/projects/{id}/pause/ no longer exists."""
        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/pause/',
        )
        assert resp.status_code == 404

    def test_unpause_endpoint_returns_404(self, admin_client, project):
        """POST /api/projects/{id}/unpause/ no longer exists."""
        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/unpause/',
        )
        assert resp.status_code == 404

    def test_project_response_excludes_is_paused(
        self, admin_client, project,
    ):
        """GET /api/projects/{id}/ does not include is_paused (AC-2)."""
        resp = admin_client.get(f'{PROJECTS_URL}{project.pk}/')
        assert resp.status_code == 200
        assert 'is_paused' not in resp.data
