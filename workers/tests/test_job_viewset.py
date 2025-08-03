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
from rest_framework import status
from ..models import Job, Asset, Project, JobStatus
from ..constants import RenderDevice
from ._base import BaseMediaTestCase

class JobViewSetTests(BaseMediaTestCase):
    def setUp(self):
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Jobs", project=self.project, blend_file=SimpleUploadedFile("dummy.blend", b"data")
        )

    def test_list_jobs(self):
        Job.objects.create(name="Job One", asset=self.asset)
        Job.objects.create(name="Job Two", asset=self.asset)
        url = "/api/jobs/"
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['name'], 'Job Two')
        self.assertEqual(response.data[1]['name'], 'Job One')

    def test_create_job(self):
        job_data = {
            "name": "My New Render",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/final_shot_####",
            "start_frame": 1,
            "end_frame": 100,
            "render_device": RenderDevice.GPU,
        }
        url = "/api/jobs/"
        response = self.client.post(url, job_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Job.objects.count(), 1)
        new_job = Job.objects.get()
        self.assertEqual(new_job.name, job_data['name'])
        self.assertEqual(new_job.status, 'QUEUED')
        self.assertEqual(new_job.render_device, RenderDevice.GPU)
        self.assertEqual(new_job.asset, self.asset)
        self.assertEqual(new_job.asset.project, self.project)

    def test_update_job_status(self):
        job = Job.objects.create(name="Job to Update", asset=self.asset, status='QUEUED')
        update_payload = {'status': 'RENDERING'}
        url = f"/api/jobs/{job.id}/"
        response = self.client.patch(url, update_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, 'RENDERING')

    def test_cancel_job_action(self):
        job = Job.objects.create(name="Job to be Canceled", asset=self.asset, status=JobStatus.RENDERING)
        url = f"/api/jobs/{job.id}/cancel/"
        response = self.client.post(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.CANCELED)
        self.assertEqual(response.data['status'], JobStatus.CANCELED)

    def test_upload_job_output_file(self):
        job = Job.objects.create(name="Job for Upload", asset=self.asset)
        url = f"/api/jobs/{job.id}/upload_output/"
        uploaded_file = SimpleUploadedFile("render_result.png", b"fake-png-image-data", content_type="image/png")
        response = self.client.post(url, {"output_file": uploaded_file}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertIsNotNone(job.output_file)
        project_short_id = str(self.project.id)[:8]
        self.assertTrue(job.output_file.name.startswith(f"assets/{project_short_id}/outputs/job_{job.id}"))

    def test_job_filtering_for_gpu_capability(self):
        Job.objects.create(name="CPU Job", asset=self.asset, render_device=RenderDevice.CPU)
        Job.objects.create(name="GPU Job", asset=self.asset, render_device=RenderDevice.GPU)
        Job.objects.create(name="Any Device Job", asset=self.asset, render_device=RenderDevice.ANY)
        url = "/api/jobs/"

        response_cpu_worker = self.client.get(url, {'gpu_available': 'false'})
        response_gpu_worker = self.client.get(url, {'gpu_available': 'true'})
        response_no_pref = self.client.get(url)

        self.assertEqual(response_cpu_worker.status_code, status.HTTP_200_OK)
        cpu_names = {job['name'] for job in response_cpu_worker.data}
        self.assertEqual(len(cpu_names), 2)
        self.assertIn("CPU Job", cpu_names)
        self.assertIn("Any Device Job", cpu_names)
        self.assertNotIn("GPU Job", cpu_names)

        self.assertEqual(response_gpu_worker.status_code, status.HTTP_200_OK)
        gpu_names = {job['name'] for job in response_gpu_worker.data}
        self.assertEqual(len(gpu_names), 2)
        self.assertIn("GPU Job", gpu_names)
        self.assertIn("Any Device Job", gpu_names)
        self.assertNotIn("CPU Job", gpu_names)

        self.assertEqual(response_no_pref.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_no_pref.data), 3)

    def test_job_filtering_skips_paused_projects(self):
        active_project = self.project
        paused_project = Project.objects.create(name="Paused Project", is_paused=True)
        paused_asset = Asset.objects.create(name="Paused Asset", project=paused_project,
                                            blend_file=SimpleUploadedFile("p.blend", b"d"))

        Job.objects.create(name="Active Job", asset=self.asset, status=JobStatus.QUEUED)
        Job.objects.create(name="Paused Job", asset=paused_asset, status=JobStatus.QUEUED)

        url = "/api/jobs/"
        # This parameter is what identifies the request as a worker poll
        params = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}

        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], "Active Job")

        paused_project.is_paused = False
        paused_project.save()
        response = self.client.get(url, params)
        names = {job['name'] for job in response.data}
        self.assertEqual(len(names), 2)
        self.assertIn("Active Job", names)
        self.assertIn("Paused Job", names)