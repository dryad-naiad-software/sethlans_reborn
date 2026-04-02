# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the Project API endpoint.

Covers: CRUD, pause/unpause actions, and paused-project
exclusion from job polling.
"""

import pytest

from workers.models import Job

PROJECTS_URL = '/api/projects/'
JOBS_URL = '/api/jobs/'


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
        assert resp.data['is_paused'] is False
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
class TestProjectPauseUnpause:

    def test_pause_project(self, admin_client, project):
        """Pause action sets is_paused to True."""
        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/pause/',
        )
        assert resp.status_code == 200
        assert resp.data['is_paused'] is True
        project.refresh_from_db()
        assert project.is_paused is True

    def test_unpause_project(self, admin_client, project):
        """Unpause action sets is_paused to False."""
        project.is_paused = True
        project.save(update_fields=['is_paused'])

        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/unpause/',
        )
        assert resp.status_code == 200
        assert resp.data['is_paused'] is False
        project.refresh_from_db()
        assert project.is_paused is False


@pytest.mark.django_db
class TestPausedProjectJobPolling:

    def test_paused_projects_excluded_from_worker_poll(
        self, admin_client, worker_with_token, project, asset,
    ):
        """Jobs from paused projects are excluded from worker polling."""
        worker, worker_client = worker_with_token

        # Create a job in the project
        admin_client.post(
            JOBS_URL,
            data={
                'name': 'PausedTestJob1',
                'asset_id': asset.pk,
                'output_file_pattern': '//render/#.png',
            },
            format='json',
        )
        assert Job.objects.filter(name='PausedTestJob1').exists()

        # Pause the project
        project.is_paused = True
        project.save(update_fields=['is_paused'])

        # Worker polls for QUEUED jobs (worker poll is identified by
        # both status and assigned_worker__isnull params)
        resp = worker_client.get(
            JOBS_URL,
            {
                'status': 'QUEUED',
                'assigned_worker__isnull': 'True',
                'available_versions': '4.2.19',
            },
        )
        assert resp.status_code == 200
        names = [j['name'] for j in resp.data]
        assert 'PausedTestJob1' not in names

    def test_unpaused_project_jobs_visible_in_poll(
        self, admin_client, worker_with_token, project, asset,
    ):
        """Jobs from unpaused projects appear in worker polling."""
        worker, worker_client = worker_with_token

        admin_client.post(
            JOBS_URL,
            data={
                'name': 'VisibleJob001',
                'asset_id': asset.pk,
                'output_file_pattern': '//render/#.png',
            },
            format='json',
        )

        resp = worker_client.get(
            JOBS_URL,
            {
                'status': 'QUEUED',
                'assigned_worker__isnull': 'True',
                'available_versions': '4.2.19',
            },
        )
        assert resp.status_code == 200
        names = [j['name'] for j in resp.data]
        assert 'VisibleJob001' in names
