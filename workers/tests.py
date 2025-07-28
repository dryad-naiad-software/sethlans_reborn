# workers/tests.py

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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# workers/tests.py

import tempfile
import shutil
from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase
from .models import Job, Worker, JobStatus, Animation, Asset


# --- NEW TEST CLASS FOR ASSETS ---
@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AssetViewSetTests(APITestCase):
    """
    Test suite for the AssetViewSet, specifically for file uploads.
    """

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(tempfile.gettempdir(), ignore_errors=True)
        super().tearDownClass()

    def test_upload_asset_file(self):
        """
        Ensure we can upload a .blend file to the Asset endpoint.
        """
        # Create a dummy file in memory
        blend_file_content = b"this is a dummy blend file"
        uploaded_file = SimpleUploadedFile(
            "test_scene.blend",
            blend_file_content,
            content_type="application/octet-stream"
        )

        asset_data = {
            "name": "Test Scene Asset",
            "blend_file": uploaded_file,
        }

        url = "/api/assets/"
        response = self.client.post(url, asset_data, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Asset.objects.count(), 1)

        new_asset = Asset.objects.get()
        self.assertEqual(new_asset.name, "Test Scene Asset")
        self.assertTrue(new_asset.blend_file.name.startswith("assets/"))
        self.assertTrue(new_asset.blend_file.name.endswith("test_scene.blend"))

        # Verify the file content on disk
        with new_asset.blend_file.open('rb') as f:
            content_on_disk = f.read()
            self.assertEqual(content_on_disk, blend_file_content)


class WorkerHeartbeatTests(APITestCase):
    """
    Test suite for the WorkerHeartbeatViewSet.
    """

    def test_heartbeat_creates_new_worker(self):
        """
        Ensure a POST request to the heartbeat endpoint successfully creates a new Worker.
        """
        worker_data = {
            "hostname": "test-worker-01",
            "ip_address": "192.168.1.101",
            "os": "Windows 11",
            "available_tools": {"blender": ["4.2.0"]}
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
        """
        Ensure a heartbeat from an existing worker updates its record.
        """
        Worker.objects.create(
            hostname="test-worker-01",
            os="Windows 10",
            available_tools={"blender": ["4.1.0"]}
        )
        update_data = {
            "hostname": "test-worker-01",
            "os": "Windows 11",
            "available_tools": {"blender": ["4.2.0"]}
        }
        url = "/api/heartbeat/"
        response = self.client.post(url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Worker.objects.count(), 1)
        updated_worker = Worker.objects.get(hostname="test-worker-01")
        self.assertEqual(updated_worker.os, "Windows 11")
        self.assertEqual(updated_worker.available_tools['blender'][0], "4.2.0")


class JobViewSetTests(APITestCase):
    """
    Test suite for the JobViewSet.
    """

    def setUp(self):
        """Create a dummy asset for jobs to link to."""
        self.asset = Asset.objects.create(
            name="Test Asset for Jobs",
            blend_file=SimpleUploadedFile("dummy.blend", b"data")
        )

    def test_list_jobs(self):
        """
        Ensure we can list all job objects.
        """
        Job.objects.create(name="Job One", asset=self.asset)
        Job.objects.create(name="Job Two", asset=self.asset)
        url = "/api/jobs/"
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['name'], 'Job Two')
        self.assertEqual(response.data[1]['name'], 'Job One')

    def test_create_job(self):
        """
        Ensure we can create a new job object.
        """
        job_data = {
            "name": "My New Render",
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/final_shot_####",
            "start_frame": 1,
            "end_frame": 100,
            "render_device": "GPU"
        }
        url = "/api/jobs/"
        response = self.client.post(url, job_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Job.objects.count(), 1)
        new_job = Job.objects.get()
        self.assertEqual(new_job.name, job_data['name'])
        self.assertEqual(new_job.status, 'QUEUED')
        self.assertEqual(new_job.render_device, 'GPU')
        self.assertEqual(new_job.asset, self.asset)

    def test_update_job_status(self):
        """
        Ensure we can update a job's status via a PATCH request.
        """
        job = Job.objects.create(name="Job to Update", asset=self.asset, status='QUEUED')
        update_payload = {'status': 'RENDERING'}
        url = f"/api/jobs/{job.id}/"
        response = self.client.patch(url, update_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, 'RENDERING')

    def test_cancel_job_action(self):
        """
        Ensure a POST to the /cancel/ endpoint sets the job status to CANCELED.
        """
        job = Job.objects.create(name="Job to be Canceled", asset=self.asset, status=JobStatus.RENDERING)
        url = f"/api/jobs/{job.id}/cancel/"
        response = self.client.post(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.CANCELED)
        self.assertEqual(response.data['status'], JobStatus.CANCELED)


class AnimationViewSetTests(APITestCase):
    """Test suite for the new AnimationViewSet."""

    def setUp(self):
        """Create a dummy asset for animations to link to."""
        self.asset = Asset.objects.create(
            name="Test Asset for Animations",
            blend_file=SimpleUploadedFile("dummy_anim.blend", b"data")
        )

    def test_create_animation_spawns_jobs(self):
        """
        Ensure POSTing to /api/animations/ creates a parent Animation
        and the correct number of child Job objects with correct properties.
        """
        animation_data = {
            "name": "My Test Animation",
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/anim_####",
            "start_frame": 1,
            "end_frame": 5,
            "blender_version": "4.1.1",
            "render_device": "GPU"
        }
        url = "/api/animations/"
        response = self.client.post(url, animation_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Animation.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 5)
        parent_animation = Animation.objects.first()
        self.assertEqual(parent_animation.render_device, "GPU")
        first_job = Job.objects.order_by('start_frame').first()
        self.assertEqual(first_job.render_device, "GPU")
        self.assertEqual(first_job.asset, self.asset)

    def test_animation_progress_tracking(self):
        """
        Ensure the serializer correctly calculates and reports progress.
        """
        anim = Animation.objects.create(name="Progress Test", asset=self.asset, start_frame=1, end_frame=10)
        for i in range(1, 11):
            Job.objects.create(animation=anim, name=f"Job_{i}", asset=self.asset, start_frame=i, end_frame=i)
        Job.objects.filter(animation=anim, start_frame__in=[1, 2, 3]).update(status=JobStatus.DONE)
        url = f"/api/animations/{anim.id}/"
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_frames'], 10)
        self.assertEqual(response.data['completed_frames'], 3)
        self.assertEqual(response.data['progress'], "3 of 10 frames complete")

    def test_create_animation_propagates_render_settings(self):
        """
        Ensure that render_settings from an Animation are copied to its child Jobs.
        """
        animation_data = {
            "name": "Render Settings Test",
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/settings_####",
            "start_frame": 1,
            "end_frame": 2,
            "render_settings": {"cycles.samples": 32, "resolution_x": 800}
        }
        url = "/api/animations/"
        response = self.client.post(url, animation_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        parent_animation = Animation.objects.get()
        self.assertEqual(parent_animation.render_settings['cycles.samples'], 32)

        first_job = Job.objects.order_by('start_frame').first()
        self.assertEqual(first_job.render_settings['cycles.samples'], 32)
        self.assertEqual(first_job.render_settings['resolution_x'], 800)


class AnimationSignalTests(APITestCase):
    """Test suite for the Django signals related to animations."""

    def setUp(self):
        """Create a dummy asset for jobs/animations to link to."""
        self.asset = Asset.objects.create(
            name="Test Asset for Signals",
            blend_file=SimpleUploadedFile("dummy_signal.blend", b"data")
        )

    def test_animation_total_time_updates_on_job_completion(self):
        """
        Ensure the post_save signal on the Job model correctly sums and updates
        the parent Animation's total_render_time_seconds.
        """
        # ARRANGE: Create a parent animation and two child jobs
        anim = Animation.objects.create(name="Signal Test Animation", asset=self.asset, start_frame=1, end_frame=2)
        job1 = Job.objects.create(animation=anim, name="Job_1", asset=self.asset, render_time_seconds=100,
                                  status=JobStatus.QUEUED)
        job2 = Job.objects.create(animation=anim, name="Job_2", asset=self.asset, render_time_seconds=50,
                                  status=JobStatus.QUEUED)

        # ACT: Update the first job to DONE. This should trigger the signal.
        job1.status = JobStatus.DONE
        job1.save()

        # ASSERT: The animation's total time should be the time of the first job.
        anim.refresh_from_db()
        self.assertEqual(anim.total_render_time_seconds, 100)

        # ACT: Update the second job to DONE.
        job2.status = JobStatus.DONE
        job2.save()

        # ASSERT: The animation's total time should now be the sum of both jobs.
        anim.refresh_from_db()
        self.assertEqual(anim.total_render_time_seconds, 150)