# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the QueueSetting API and queue pause
effect on job polling.

Covers:
- GET /api/queue-settings/ returns current state (AC-8, AC-14)
- POST /api/queue-settings/pause/ and resume/ (AC-9, AC-10, AC-24)
- Worker poll returns empty when queue is paused (AC-9)
- Workers resume receiving jobs after resume (AC-10)
- QueueSetting singleton behavior (AC-22)
- Permission enforcement (AC-21)
"""

import pytest

from workers.models import Job, QueueSetting

QUEUE_URL = '/api/queue-settings/'
JOBS_URL = '/api/jobs/'


@pytest.mark.django_db
class TestQueueSettingGet:
    """AC-8/AC-14: GET returns queue_paused state."""

    def test_default_state_is_not_paused(self, admin_client):
        resp = admin_client.get(QUEUE_URL)
        assert resp.status_code == 200
        assert resp.data['queue_paused'] is False

    def test_returns_true_after_pause(self, admin_client):
        admin_client.post(f'{QUEUE_URL}pause/')
        resp = admin_client.get(QUEUE_URL)
        assert resp.status_code == 200
        assert resp.data['queue_paused'] is True


@pytest.mark.django_db
class TestQueueSettingPauseResume:
    """AC-24: Pause and resume use update_or_create for safety."""

    def test_pause_returns_paused_true(self, admin_client):
        resp = admin_client.post(f'{QUEUE_URL}pause/')
        assert resp.status_code == 200
        assert resp.data['queue_paused'] is True

        setting = QueueSetting.objects.get(pk=1)
        assert setting.queue_paused is True

    def test_resume_returns_paused_false(self, admin_client):
        # Pause first
        admin_client.post(f'{QUEUE_URL}pause/')

        resp = admin_client.post(f'{QUEUE_URL}resume/')
        assert resp.status_code == 200
        assert resp.data['queue_paused'] is False

        setting = QueueSetting.objects.get(pk=1)
        assert setting.queue_paused is False

    def test_double_pause_is_idempotent(self, admin_client):
        admin_client.post(f'{QUEUE_URL}pause/')
        resp = admin_client.post(f'{QUEUE_URL}pause/')
        assert resp.status_code == 200
        assert resp.data['queue_paused'] is True
        assert QueueSetting.objects.count() == 1

    def test_double_resume_is_idempotent(self, admin_client):
        resp = admin_client.post(f'{QUEUE_URL}resume/')
        assert resp.status_code == 200
        assert resp.data['queue_paused'] is False


@pytest.mark.django_db
class TestQueueSettingSingleton:
    """AC-22: Only one QueueSetting row ever exists (pk=1)."""

    def test_singleton_always_pk_1(self, db):
        qs = QueueSetting(queue_paused=True)
        qs.pk = 99  # Try to set different pk
        qs.save()
        assert qs.pk == 1

    def test_get_instance_creates_if_missing(self, db):
        assert QueueSetting.objects.count() == 0
        instance = QueueSetting.get_instance()
        assert instance.pk == 1
        assert instance.queue_paused is False

    def test_get_instance_returns_existing(self, db):
        QueueSetting.objects.create(queue_paused=True)
        instance = QueueSetting.get_instance()
        assert instance.pk == 1
        assert instance.queue_paused is True

    def test_delete_is_noop(self, db):
        instance = QueueSetting.get_instance()
        instance.delete()
        assert QueueSetting.objects.filter(pk=1).exists()


@pytest.mark.django_db
class TestQueuePauseInPolling:
    """AC-9/AC-10: Queue pause affects worker job polling."""

    def test_paused_queue_returns_empty_poll(
        self, admin_client, worker_with_token, asset,
    ):
        """AC-9: Worker poll returns empty list when queue paused."""
        _, worker_client = worker_with_token

        admin_client.post(
            JOBS_URL,
            data={
                'name': 'PauseTest1',
                'asset_id': asset.pk,
                'output_file_pattern': '//render/#.png',
            },
            format='json',
        )
        assert Job.objects.filter(name='PauseTest1').exists()

        # Pause the queue
        admin_client.post(f'{QUEUE_URL}pause/')

        # Worker polls
        resp = worker_client.get(
            JOBS_URL,
            {
                'status': 'QUEUED',
                'assigned_worker__isnull': 'True',
                'available_versions': '4.2.19',
            },
        )
        assert resp.status_code == 200
        assert len(resp.data) == 0

    def test_resumed_queue_returns_jobs(
        self, admin_client, worker_with_token, asset,
    ):
        """AC-10: After resume, workers see QUEUED jobs again."""
        _, worker_client = worker_with_token

        admin_client.post(
            JOBS_URL,
            data={
                'name': 'ResumeTs01',
                'asset_id': asset.pk,
                'output_file_pattern': '//render/#.png',
            },
            format='json',
        )

        # Pause then resume
        admin_client.post(f'{QUEUE_URL}pause/')
        admin_client.post(f'{QUEUE_URL}resume/')

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
        assert 'ResumeTs01' in names

    def test_admin_list_not_affected_by_pause(
        self, admin_client, asset,
    ):
        """Admin list (no worker poll params) still shows jobs."""
        admin_client.post(
            JOBS_URL,
            data={
                'name': 'AdminList1',
                'asset_id': asset.pk,
                'output_file_pattern': '//render/#.png',
            },
            format='json',
        )

        admin_client.post(f'{QUEUE_URL}pause/')

        resp = admin_client.get(JOBS_URL)
        assert resp.status_code == 200
        names = [j['name'] for j in resp.data]
        assert 'AdminList1' in names


@pytest.mark.django_db
class TestQueueSettingsPermissions:
    """AC-21: Queue settings require IsAdmin."""

    def test_worker_cannot_get_settings(self, worker_with_token):
        _, worker_client = worker_with_token
        resp = worker_client.get(QUEUE_URL)
        assert resp.status_code == 403

    def test_worker_cannot_pause(self, worker_with_token):
        _, worker_client = worker_with_token
        resp = worker_client.post(f'{QUEUE_URL}pause/')
        assert resp.status_code == 403

    def test_worker_cannot_resume(self, worker_with_token):
        _, worker_client = worker_with_token
        resp = worker_client.post(f'{QUEUE_URL}resume/')
        assert resp.status_code == 403

    def test_unauthenticated_cannot_access(self):
        from rest_framework.test import APIClient
        anon = APIClient()
        resp = anon.get(QUEUE_URL)
        assert resp.status_code in (401, 403)
