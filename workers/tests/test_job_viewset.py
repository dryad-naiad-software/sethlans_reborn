# FILENAME: workers/tests/test_job_viewset.py
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
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.text import slugify
from rest_framework import status
from ..models import Job, Asset, Project, JobStatus
from ..constants import RenderDevice
from ._base import BaseMediaTestCase

class JobViewSetTests(BaseMediaTestCase):
    def setUp(self):
        """
        Set up a project, an asset, and a standard set of jobs for testing.
        """
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Jobs", project=self.project, blend_file=SimpleUploadedFile("dummy.blend", b"data")
        )
        # Create a set of jobs with different render devices for filtering tests
        Job.objects.create(name="CPU Job", asset=self.asset, render_device=RenderDevice.CPU, status=JobStatus.QUEUED)
        Job.objects.create(name="GPU Job", asset=self.asset, render_device=RenderDevice.GPU, status=JobStatus.QUEUED)
        Job.objects.create(name="Any Device Job", asset=self.asset, render_device=RenderDevice.ANY, status=JobStatus.QUEUED)
        self.url = "/api/jobs/"

    def test_list_jobs(self):
        """
        Tests that the endpoint lists all jobs correctly.
        """
        response = self.client.get(self.url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)

    def test_create_job(self):
        """
        Tests the creation of a new standalone job.
        """
        job_data = {
            "name": "My New Render",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/final_shot_####",
            "start_frame": 1,
            "end_frame": 100,
            "render_device": RenderDevice.GPU,
        }
        response = self.client.post(self.url, job_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # 3 from setup + 1 new = 4
        self.assertEqual(Job.objects.count(), 4)
        new_job = Job.objects.get(name=job_data['name'])
        self.assertEqual(new_job.status, 'QUEUED')
        self.assertEqual(new_job.render_device, RenderDevice.GPU)
        self.assertEqual(new_job.asset, self.asset)
        self.assertEqual(new_job.asset.project, self.project)

    def test_update_job_status(self):
        """
        Tests that a job's status can be updated via a PATCH request.
        """
        job = Job.objects.get(name="CPU Job")
        update_payload = {'status': 'RENDERING'}
        response = self.client.patch(f"/api/jobs/{job.id}/", update_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, 'RENDERING')

    def test_cancel_job_action(self):
        """
        Tests the custom /cancel/ action on a job.
        """
        job = Job.objects.get(name="GPU Job")
        job.status = JobStatus.RENDERING
        job.save()

        response = self.client.post(f"/api/jobs/{job.id}/cancel/", format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.CANCELED)
        self.assertEqual(response.data['status'], JobStatus.CANCELED)

    def test_upload_job_output_file(self):
        """
        Tests the /upload_output/ action for a job.
        """
        job = Job.objects.create(name="Job for Upload", asset=self.asset)
        url = f"/api/jobs/{job.id}/upload_output/"
        uploaded_file = SimpleUploadedFile("render_result.png", b"fake-png-image-data", content_type="image/png")
        response = self.client.post(url, {"output_file": uploaded_file}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertIsNotNone(job.output_file)
        project_short_id = str(self.project.id)[:8]
        slug = slugify(job.name)
        job_dir = f"{slug}-{job.id}"
        self.assertTrue(job.output_file.name.startswith(f"assets/{project_short_id}/outputs/{job_dir}/"))

    def test_job_filtering_for_cpu_worker(self):
        """
        Tests that a worker polling with gpu_available=false sees only CPU and ANY jobs.
        """
        params = {'gpu_available': 'false', 'status': 'QUEUED'}
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        names = {job['name'] for job in response.data}
        self.assertEqual(len(names), 2)
        self.assertIn("CPU Job", names)
        self.assertIn("Any Device Job", names)
        self.assertNotIn("GPU Job", names)

    def test_job_filtering_for_gpu_worker(self):
        """
        Tests that a worker polling with gpu_available=true sees only GPU and ANY jobs.
        """
        params = {'gpu_available': 'true', 'status': 'QUEUED'}
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        names = {job['name'] for job in response.data}
        self.assertEqual(len(names), 2)
        self.assertIn("GPU Job", names)
        self.assertIn("Any Device Job", names)
        self.assertNotIn("CPU Job", names)

    def test_job_filtering_skips_paused_projects(self):
        """
        Tests that a worker poll correctly excludes jobs from paused projects.
        """
        paused_project = Project.objects.create(name="Paused Project", is_paused=True)
        paused_asset = Asset.objects.create(name="Paused Asset", project=paused_project,
                                            blend_file=SimpleUploadedFile("p.blend", b"d"))
        Job.objects.create(name="Paused Job", asset=paused_asset, status=JobStatus.QUEUED)

        # This parameter combination identifies the request as a worker poll
        params = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}
        response = self.client.get(self.url, params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only get the 3 jobs from the active project created in setUp
        self.assertEqual(len(response.data), 3)
        names = {job['name'] for job in response.data}
        self.assertNotIn("Paused Job", names)