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
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

from rest_framework import status
from rest_framework.test import APITestCase

from .models import Job, Worker, JobStatus



class WorkerHeartbeatTests(APITestCase):
    """
    Test suite for the WorkerHeartbeatViewSet.
    """

    def test_heartbeat_creates_new_worker(self):
        """
        Ensure a POST request to the heartbeat endpoint successfully creates a new Worker.
        """
        # Define the payload a new worker would send
        worker_data = {
            "hostname": "test-worker-01",
            "ip_address": "192.168.1.101",
            "os": "Windows 11",
            "available_tools": {"blender": ["4.2.0"]}
        }

        # Make the API call to the heartbeat endpoint
        url = "/api/heartbeat/"
        response = self.client.post(url, worker_data, format='json')

        # --- Assertions ---
        # 1. Check for a successful response code
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 2. Check that a worker was actually created in the database
        self.assertEqual(Worker.objects.count(), 1)

        # 3. Verify the details of the created worker
        created_worker = Worker.objects.get()
        self.assertEqual(created_worker.hostname, worker_data['hostname'])
        self.assertEqual(created_worker.os, worker_data['os'])
        self.assertEqual(created_worker.available_tools['blender'][0], "4.2.0")

    def test_heartbeat_updates_existing_worker(self):
        """
        Ensure a heartbeat from an existing worker updates its record.
        """
        # First, create an existing worker in the database
        initial_worker = Worker.objects.create(
            hostname="test-worker-01",
            os="Windows 10",
            available_tools={"blender": ["4.1.0"]}
        )

        # Define the payload for the update heartbeat
        update_data = {
            "hostname": "test-worker-01",
            "os": "Windows 11",  # OS has been updated
            "available_tools": {"blender": ["4.2.0"]}  # Tools have changed
        }

        # Make the API call
        url = "/api/heartbeat/"
        response = self.client.post(url, update_data, format='json')

        # --- Assertions ---
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 1. Ensure no new worker was created
        self.assertEqual(Worker.objects.count(), 1)

        # 2. Verify the worker's data was updated
        updated_worker = Worker.objects.get(hostname="test-worker-01")
        self.assertEqual(updated_worker.os, "Windows 11")
        self.assertEqual(updated_worker.available_tools['blender'][0], "4.2.0")


class JobViewSetTests(APITestCase):
    """
    Test suite for the JobViewSet.
    """

    def test_list_jobs(self):
        """
        Ensure we can list all job objects.
        """
        # Create a couple of jobs to be listed
        Job.objects.create(name="Job One", blend_file_path="/path/1")
        Job.objects.create(name="Job Two", blend_file_path="/path/2")

        # Make the API call to the list endpoint
        url = "/api/jobs/"
        response = self.client.get(url, format='json')

        # --- Assertions ---
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Check that the response contains the correct number of jobs
        self.assertEqual(len(response.data), 2)

        # Check that the data is correct
        self.assertEqual(response.data[0]['name'], 'Job Two')  # Jobs are ordered by -submitted_at by default
        self.assertEqual(response.data[1]['name'], 'Job One')

    def test_create_job(self):
        """
        Ensure we can create a new job object.
        """
        # Data for the new job
        job_data = {
            "name": "My New Render",
            "blend_file_path": "/path/to/my_scene.blend",
            "output_file_pattern": "//renders/final_shot_####",
            "start_frame": 1,
            "end_frame": 100
        }

        # Make the API call to the create endpoint
        url = "/api/jobs/"
        response = self.client.post(url, job_data, format='json')

        # --- Assertions ---
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Job.objects.count(), 1)

        # Verify the created job's details
        new_job = Job.objects.get()
        self.assertEqual(new_job.name, job_data['name'])
        self.assertEqual(new_job.status, 'QUEUED')  # Check default status

    def test_update_job_status(self):
        """
        Ensure we can update a job's status via a PATCH request.
        """
        # Create an initial job
        job = Job.objects.create(
            name="Job to Update",
            blend_file_path="/path/update",
            status='QUEUED'
        )

        # Data for the PATCH request
        update_payload = {'status': 'RENDERING'}

        # Make the API call to the detail endpoint
        url = f"/api/jobs/{job.id}/"
        response = self.client.patch(url, update_payload, format='json')

        # --- Assertions ---
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Refresh the object from the database to get the updated value
        job.refresh_from_db()
        self.assertEqual(job.status, 'RENDERING')

    def test_cancel_job_action(self):
        """
        Ensure a POST to the /cancel/ endpoint sets the job status to CANCELED.
        """
        # ARRANGE: Create a job that is in a state that can be canceled (e.g., RENDERING)
        job = Job.objects.create(
            name="Job to be Canceled",
            blend_file_path="/path/cancel_me.blend",
            status=JobStatus.RENDERING
        )

        # ACT: Make the API call to the new 'cancel' action endpoint
        url = f"/api/jobs/{job.id}/cancel/"
        response = self.client.post(url, format='json')

        # ASSERT
        # 1. Check for a successful response
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 2. Refresh the object from the database to see the change
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.CANCELED)

        # 3. Check that the response data also reflects the change
        self.assertEqual(response.data['status'], JobStatus.CANCELED)
