# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from ..models import Worker

ENROLLMENT_KEY = "test-enrollment-key-12345"


@override_settings()
class WorkerHeartbeatTests(APITestCase):
    """Tests for the worker enrollment/heartbeat endpoint."""

    def setUp(self):
        import os
        os.environ['SETHLANS_SECURITY_ENROLLMENT_KEY'] = ENROLLMENT_KEY

    def tearDown(self):
        import os
        os.environ.pop('SETHLANS_SECURITY_ENROLLMENT_KEY', None)

    def test_heartbeat_creates_new_worker(self):
        worker_data = {
            "hostname": "test-worker-01",
            "ip_address": "192.168.1.101",
            "os": "Windows 11",
            "available_tools": {"blender": ["4.2.0"]},
        }
        url = "/api/heartbeat/"
        response = self.client.post(
            url, worker_data, format='json',
            HTTP_X_ENROLLMENT_KEY=ENROLLMENT_KEY,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Worker.objects.count(), 1)
        created_worker = Worker.objects.get()
        self.assertEqual(
            created_worker.hostname, worker_data['hostname'],
        )
        self.assertEqual(created_worker.os, worker_data['os'])
        self.assertEqual(
            created_worker.available_tools['blender'][0], "4.2.0",
        )

    def test_heartbeat_updates_existing_worker(self):
        # First enroll the worker
        enroll_data = {
            "hostname": "test-worker-01",
            "ip_address": "192.168.1.101",
            "os": "Windows 10",
            "available_tools": {"blender": ["4.1.0"]},
        }
        url = "/api/heartbeat/"
        resp = self.client.post(
            url, enroll_data, format='json',
            HTTP_X_ENROLLMENT_KEY=ENROLLMENT_KEY,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        token = resp.data.get('token')

        # Now send an update as an authenticated worker
        update_data = {
            "hostname": "test-worker-01",
            "os": "Windows 11",
            "available_tools": {"blender": ["4.2.0"]},
        }
        response = self.client.post(
            url, update_data, format='json',
            HTTP_AUTHORIZATION=f"Token {token}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Worker.objects.count(), 1)
        updated_worker = Worker.objects.get(
            hostname="test-worker-01",
        )
        self.assertEqual(updated_worker.os, "Windows 11")
        self.assertEqual(
            updated_worker.available_tools['blender'][0], "4.2.0",
        )
