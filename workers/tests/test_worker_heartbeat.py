#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
from rest_framework import status
from rest_framework.test import APITestCase
from ..models import Worker

class WorkerHeartbeatTests(APITestCase):
    def test_heartbeat_creates_new_worker(self):
        worker_data = {
            "hostname": "test-worker-01",
            "ip_address": "192.168.1.101",
            "os": "Windows 11",
            "available_tools": {"blender": ["4.2.0"]},
        }
        url = "/api/heartbeat/"
        response = self.client.post(url, worker_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Worker.objects.count(), 1)
        created_worker = Worker.objects.get()
        self.assertEqual(created_worker.hostname, worker_data['hostname'])
        self.assertEqual(created_worker.os, worker_data['os'])
        self.assertEqual(created_worker.available_tools['blender'][0], "4.2.0")

    def test_heartbeat_updates_existing_worker(self):
        Worker.objects.create(hostname="test-worker-01", os="Windows 10", available_tools={"blender": ["4.1.0"]})
        update_data = {"hostname": "test-worker-01", "os": "Windows 11", "available_tools": {"blender": ["4.2.0"]}}
        url = "/api/heartbeat/"
        response = self.client.post(url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Worker.objects.count(), 1)
        updated_worker = Worker.objects.get(hostname="test-worker-01")
        self.assertEqual(updated_worker.os, "Windows 11")
        self.assertEqual(updated_worker.available_tools['blender'][0], "4.2.0")
