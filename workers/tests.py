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
# workers/tests.py

import tempfile
import shutil
import os
from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase
from PIL import Image
from unittest.mock import patch, MagicMock
from .models import Job, Worker, JobStatus, Animation, Asset, Project, TiledJob, TiledJobStatus, AnimationFrame, AnimationFrameStatus
from .constants import RenderSettings, TilingConfiguration, RenderEngine, CyclesFeatureSet, RenderDevice
from .image_assembler import assemble_tiled_job_image, assemble_animation_frame_image


class BaseMediaTestCase(APITestCase):
    """
    A base test case that creates a temporary MEDIA_ROOT for tests
    that involve file uploads, and cleans it up afterward. It also
    creates a default Project instance for tests to use.
    """
    _media_root_override = None
    media_root = None

    @classmethod
    def setUpClass(cls):
        """Create a temporary directory and activate the MEDIA_ROOT override."""
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls._media_root_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls._media_root_override.enable()

    @classmethod
    def tearDownClass(cls):
        """Deactivate the MEDIA_ROOT override and remove the temporary directory."""
        cls._media_root_override.disable()
        if cls.media_root and os.path.exists(cls.media_root):
            shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        """Create a default Project for test methods to use."""
        self.project = Project.objects.create(name="Default Test Project")


class ProjectViewSetTests(BaseMediaTestCase):
    """
    Test suite for the ProjectViewSet, covering pause/unpause actions.
    """

    def test_pause_project(self):
        """
        Ensure the /pause/ action sets the project's is_paused flag to True.
        """
        self.assertFalse(self.project.is_paused)
        url = f"/api/projects/{self.project.id}/pause/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_paused'])
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_paused)

    def test_unpause_project(self):
        """
        Ensure the /unpause/ action sets the project's is_paused flag to False.
        """
        self.project.is_paused = True
        self.project.save()
        self.assertTrue(self.project.is_paused)

        url = f"/api/projects/{self.project.id}/unpause/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_paused'])
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_paused)


class AssetViewSetTests(BaseMediaTestCase):
    """
    Test suite for the AssetViewSet, specifically for file uploads.
    """

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
            "project": self.project.id
        }

        url = "/api/assets/"
        response = self.client.post(url, asset_data, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Asset.objects.count(), 1)

        new_asset = Asset.objects.get()
        self.assertEqual(new_asset.name, "Test Scene Asset")
        self.assertEqual(new_asset.project, self.project)

        # --- MODIFIED ASSERTIONS FOR UUID FILENAME AND PROJECT PATH ---
        expected_path_start = f"assets/{self.project.id}/"
        self.assertTrue(new_asset.blend_file.name.startswith(expected_path_start))
        self.assertTrue(new_asset.blend_file.name.endswith(".blend"))
        self.assertNotIn("test_scene", new_asset.blend_file.name)

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


class JobViewSetTests(BaseMediaTestCase):
    """
    Test suite for the JobViewSet.
    """

    def setUp(self):
        """Create a dummy project and asset for jobs to link to."""
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Jobs",
            project=self.project,
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
            "render_device": RenderDevice.GPU
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

    def test_upload_job_output_file(self):
        """
        Ensure uploading a file populates the output_file field.
        The thumbnail generation is tested separately in ThumbnailSignalTests.
        """
        # Arrange
        job = Job.objects.create(name="Job for Upload", asset=self.asset)
        url = f"/api/jobs/{job.id}/upload_output/"
        file_content = b"fake-png-image-data"
        uploaded_file = SimpleUploadedFile("render_result.png", file_content, content_type="image/png")
        data = {"output_file": uploaded_file}

        # Act
        response = self.client.post(url, data, format='multipart')

        # Assert
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertIsNotNone(job.output_file)
        self.assertTrue(job.output_file.name.startswith(f"assets/{self.project.id}/outputs/job_{job.id}"))

    def test_job_filtering_for_gpu_capability(self):
        """
        Ensure the job list is correctly filtered based on worker GPU capability.
        """
        # Arrange
        Job.objects.create(name="CPU Job", asset=self.asset, render_device=RenderDevice.CPU)
        Job.objects.create(name="GPU Job", asset=self.asset, render_device=RenderDevice.GPU)
        Job.objects.create(name="Any Device Job", asset=self.asset, render_device=RenderDevice.ANY)
        url = "/api/jobs/"

        # Act (CPU-only worker)
        response_cpu_worker = self.client.get(url, {'gpu_available': 'false'})
        # Act (GPU-capable worker)
        response_gpu_worker = self.client.get(url, {'gpu_available': 'true'})
        # Act (No preference)
        response_no_pref = self.client.get(url)

        # Assert (CPU-only worker)
        self.assertEqual(response_cpu_worker.status_code, status.HTTP_200_OK)
        cpu_worker_job_names = {job['name'] for job in response_cpu_worker.data}
        self.assertEqual(len(cpu_worker_job_names), 2)
        self.assertIn("CPU Job", cpu_worker_job_names)
        self.assertIn("Any Device Job", cpu_worker_job_names)
        self.assertNotIn("GPU Job", cpu_worker_job_names)

        # Assert (GPU-capable worker)
        self.assertEqual(response_gpu_worker.status_code, status.HTTP_200_OK)
        gpu_worker_job_names = {job['name'] for job in response_gpu_worker.data}
        self.assertEqual(len(gpu_worker_job_names), 2)
        self.assertIn("GPU Job", gpu_worker_job_names)
        self.assertIn("Any Device Job", gpu_worker_job_names)
        self.assertNotIn("CPU Job", gpu_worker_job_names)

        # Assert (No preference specified)
        self.assertEqual(response_no_pref.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_no_pref.data), 3)

    def test_job_filtering_skips_paused_projects(self):
        """
        Ensure workers polling for jobs do not see jobs from paused projects.
        """
        # Arrange: Create an active project and a paused project
        active_project = self.project
        paused_project = Project.objects.create(name="Paused Project", is_paused=True)
        paused_asset = Asset.objects.create(name="Paused Asset", project=paused_project,
                                            blend_file=SimpleUploadedFile("p.blend", b"d"))

        # Create a job in each project
        Job.objects.create(name="Active Job", asset=self.asset, status=JobStatus.QUEUED)
        Job.objects.create(name="Paused Job", asset=paused_asset, status=JobStatus.QUEUED)

        url = "/api/jobs/"
        # Correctly simulate a worker poll
        params = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}

        # Act 1: Poll for jobs. Only the active one should be returned.
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], "Active Job")

        # Act 2: Unpause the project and poll again.
        paused_project.is_paused = False
        paused_project.save()

        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job_names = {job['name'] for job in response.data}
        self.assertEqual(len(job_names), 2)
        self.assertIn("Active Job", job_names)
        self.assertIn("Paused Job", job_names)


class AnimationViewSetTests(BaseMediaTestCase):
    """Test suite for the new AnimationViewSet."""

    def setUp(self):
        """Create a dummy project and asset for animations to link to."""
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Animations",
            project=self.project,
            blend_file=SimpleUploadedFile("dummy_anim.blend", b"data")
        )

    def test_create_animation_spawns_jobs(self):
        """
        Ensure POSTing to /api/animations/ creates a parent Animation
        and the correct number of child Job objects with correct properties.
        """
        animation_data = {
            "name": "My Test Animation",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/anim_####",
            "start_frame": 1,
            "end_frame": 5,
            "blender_version": "4.1.1",
            "render_device": RenderDevice.GPU
        }
        url = "/api/animations/"
        response = self.client.post(url, animation_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Animation.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 5)
        parent_animation = Animation.objects.first()
        self.assertEqual(parent_animation.render_device, RenderDevice.GPU)
        self.assertEqual(parent_animation.project, self.project)
        first_job = Job.objects.order_by('start_frame').first()
        self.assertEqual(first_job.render_device, RenderDevice.GPU)
        self.assertEqual(first_job.asset, self.asset)

    def test_animation_progress_tracking(self):
        """
        Ensure the serializer correctly calculates and reports progress.
        """
        anim = Animation.objects.create(name="Progress Test", project=self.project, asset=self.asset, start_frame=1,
                                        end_frame=10)
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
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/settings_####",
            "start_frame": 1,
            "end_frame": 2,
            "render_settings": {"cycles.samples": 32, "render.resolution_x": 800}
        }
        url = "/api/animations/"
        response = self.client.post(url, animation_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        parent_animation = Animation.objects.get()
        self.assertEqual(parent_animation.render_settings['cycles.samples'], 32)

        first_job = Job.objects.order_by('start_frame').first()
        self.assertEqual(first_job.render_settings['cycles.samples'], 32)
        self.assertEqual(first_job.render_settings['render.resolution_x'], 800)

    def test_create_tiled_animation_spawns_correct_jobs(self):
        """
        Ensure creating a tiled animation spawns the correct number of frames and tile jobs.
        """
        animation_data = {
            "name": "My Tiled Anim",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/tiled_anim_####",
            "start_frame": 1,
            "end_frame": 2,
            "tiling_config": TilingConfiguration.TILE_2X2,
            "render_settings": {RenderSettings.SAMPLES: 16}
        }
        url = "/api/animations/"
        response = self.client.post(url, animation_data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Animation.objects.count(), 1)
        self.assertEqual(AnimationFrame.objects.count(), 2)  # 2 frames
        self.assertEqual(Job.objects.count(), 8)  # 2 frames * 4 tiles each

        # Inspect a specific tile job to verify its settings
        job = Job.objects.get(name="My Tiled Anim_Frame_0002_Tile_1_1")
        self.assertEqual(job.start_frame, 2)
        self.assertEqual(job.end_frame, 2)
        self.assertIsNotNone(job.animation_frame)
        self.assertEqual(job.animation_frame.frame_number, 2)

        settings = job.render_settings
        self.assertEqual(settings[RenderSettings.SAMPLES], 16) # From parent
        self.assertEqual(settings[RenderSettings.USE_BORDER], True) # Injected
        self.assertEqual(settings[RenderSettings.BORDER_MIN_X], 0.5)
        self.assertEqual(settings[RenderSettings.BORDER_MAX_X], 1.0)
        self.assertEqual(settings[RenderSettings.BORDER_MIN_Y], 0.5)
        self.assertEqual(settings[RenderSettings.BORDER_MAX_Y], 1.0)

    def test_create_animation_with_new_render_config(self):
        """
        Ensure new render configuration settings are saved and propagated into
        the child jobs' render_settings dictionary.
        """
        animation_data = {
            "name": "New Config Test",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/new_config_####",
            "start_frame": 1,
            "end_frame": 1,
            "render_engine": RenderEngine.EEVEE,
            "cycles_feature_set": CyclesFeatureSet.EXPERIMENTAL,
            "render_device": RenderDevice.GPU
        }
        url = "/api/animations/"
        response = self.client.post(url, animation_data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Animation.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 1)

        parent_animation = Animation.objects.first()
        self.assertEqual(parent_animation.render_engine, RenderEngine.EEVEE)
        self.assertEqual(parent_animation.cycles_feature_set, CyclesFeatureSet.EXPERIMENTAL)
        self.assertEqual(parent_animation.render_device, RenderDevice.GPU)

        child_job = Job.objects.first()
        self.assertEqual(child_job.render_engine, RenderEngine.EEVEE)
        self.assertEqual(child_job.cycles_feature_set, CyclesFeatureSet.EXPERIMENTAL)
        self.assertEqual(child_job.render_device, RenderDevice.GPU)

        # Verify injection into render_settings
        injected_settings = child_job.render_settings
        self.assertEqual(injected_settings[RenderSettings.RENDER_ENGINE], RenderEngine.EEVEE)
        # Should NOT be present if engine is not CYCLES
        self.assertNotIn(RenderSettings.CYCLES_FEATURE_SET, injected_settings)
        self.assertNotIn(RenderSettings.CYCLES_DEVICE, injected_settings)

    def test_create_animation_with_frame_step(self):
        """
        Ensure creating an animation with a frame_step > 1 spawns the correct jobs.
        """
        animation_data = {
            "name": "Frame Step Test",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/step_####",
            "start_frame": 1,
            "end_frame": 5,
            "frame_step": 2
        }
        url = "/api/animations/"
        response = self.client.post(url, animation_data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Animation.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 3)  # Jobs for frames 1, 3, 5

        spawned_frames = [job.start_frame for job in Job.objects.order_by('start_frame')]
        self.assertEqual(spawned_frames, [1, 3, 5])


class TiledAnimationModelTests(BaseMediaTestCase):
    """Test suite for the new Tiled Animation models."""

    def setUp(self):
        """Create a dummy asset for tests."""
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Tiled Anim Models",
            project=self.project,
            blend_file=SimpleUploadedFile("dummy_tiled_anim.blend", b"data")
        )

    def test_create_tiled_animation_and_frames(self):
        """
        Ensure the new models and their relationships can be created successfully.
        """
        # Create a tiled animation
        anim = Animation.objects.create(
            name="Tiled Animation Model Test",
            project=self.project,
            asset=self.asset,
            start_frame=1,
            end_frame=2,
            tiling_config=TilingConfiguration.TILE_2X2
        )
        self.assertEqual(anim.tiling_config, TilingConfiguration.TILE_2X2)

        # Create a parent frame
        anim_frame = AnimationFrame.objects.create(
            animation=anim,
            frame_number=1
        )
        self.assertEqual(anim_frame.animation, anim)

        # Create a job linked to that frame
        job = Job.objects.create(
            name="Tiled Anim Job",
            asset=self.asset,
            animation=anim,
            animation_frame=anim_frame,
            start_frame=1,
            end_frame=1
        )
        self.assertEqual(job.animation_frame, anim_frame)
        self.assertEqual(job.animation_frame.animation, anim)
        self.assertEqual(AnimationFrame.objects.count(), 1)
        self.assertEqual(anim.frames.count(), 1)
        self.assertEqual(anim_frame.tile_jobs.count(), 1)


class TiledJobViewSetTests(BaseMediaTestCase):
    """Test suite for the TiledJobViewSet."""

    def setUp(self):
        """Create a dummy project and asset for tiled jobs to link to."""
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Tiled Jobs",
            project=self.project,
            blend_file=SimpleUploadedFile("dummy_tiled.blend", b"data")
        )

    def test_create_tiled_job_spawns_child_jobs(self):
        """
        Ensure creating a TiledJob spawns the correct number of child Jobs
        with correctly calculated border render settings.
        """
        tiled_job_data = {
            "name": "My Tiled Render",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "final_resolution_x": 800,
            "final_resolution_y": 600,
            "tile_count_x": 2,
            "tile_count_y": 2,
            "render_settings": {RenderSettings.SAMPLES: 64}
        }
        url = "/api/tiled-jobs/"
        response = self.client.post(url, tiled_job_data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TiledJob.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 4)  # 2x2 grid

        # Check the first tile (0, 0)
        job_tile_0_0 = Job.objects.get(name="My Tiled Render_Tile_0_0")
        settings = job_tile_0_0.render_settings
        self.assertEqual(settings[RenderSettings.SAMPLES], 64)
        self.assertEqual(settings[RenderSettings.RESOLUTION_X], 800)
        self.assertEqual(settings[RenderSettings.USE_BORDER], True)
        self.assertEqual(settings[RenderSettings.BORDER_MIN_X], 0.0)
        self.assertEqual(settings[RenderSettings.BORDER_MAX_X], 0.5)
        self.assertEqual(settings[RenderSettings.BORDER_MIN_Y], 0.0)
        self.assertEqual(settings[RenderSettings.BORDER_MAX_Y], 0.5)

        # Check the last tile (1, 1)
        job_tile_1_1 = Job.objects.get(name="My Tiled Render_Tile_1_1")
        settings = job_tile_1_1.render_settings
        self.assertEqual(settings[RenderSettings.SAMPLES], 64)
        self.assertEqual(settings[RenderSettings.BORDER_MIN_X], 0.5)
        self.assertEqual(settings[RenderSettings.BORDER_MAX_X], 1.0)
        self.assertEqual(settings[RenderSettings.BORDER_MIN_Y], 0.5)
        self.assertEqual(settings[RenderSettings.BORDER_MAX_Y], 1.0)


class ImageAssemblerTests(BaseMediaTestCase):
    """Test suite for the image assembly utility."""

    def setUp(self):
        """Create a TiledJob and mock child jobs with dummy image files."""
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Assembler",
            project=self.project,
            blend_file=SimpleUploadedFile("dummy_assembler.blend", b"data")
        )
        self.tiled_job = TiledJob.objects.create(
            name="Test Assembly Job",
            project=self.project,
            asset=self.asset,
            final_resolution_x=200,
            final_resolution_y=200,
            tile_count_x=2,
            tile_count_y=2,
        )

        # Create 4 dummy tile images with different colors
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]  # R, G, B, Y
        for y in range(2):
            for x in range(2):
                img = Image.new('RGB', (100, 100), color=colors[y * 2 + x])
                # Save the dummy file to a path the job model can use
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', dir=self.media_root)
                img.save(temp_file, 'PNG')
                temp_file.close()

                job = Job.objects.create(
                    tiled_job=self.tiled_job,
                    name=f"{self.tiled_job.name}_Tile_{y}_{x}",
                    asset=self.asset,
                    status=JobStatus.DONE,
                )
                # Assign the saved file to the job's FileField
                job.output_file.name = os.path.relpath(temp_file.name, self.media_root)
                job.save()

    def test_assemble_tiled_job_image(self):
        """
        Ensure the assembler correctly stitches tiles into a final image.
        """
        # Act
        assemble_tiled_job_image(self.tiled_job.id)

        # Assert
        self.tiled_job.refresh_from_db()
        self.assertEqual(self.tiled_job.status, TiledJobStatus.DONE)
        self.assertIsNotNone(self.tiled_job.completed_at)
        self.assertTrue(self.tiled_job.output_file.name)

        # Verify the assembled image
        final_image = Image.open(self.tiled_job.output_file.path)
        self.assertEqual(final_image.size, (200, 200))
        self.assertEqual(final_image.getpixel((50, 50)), (0, 0, 255, 255))
        self.assertEqual(final_image.getpixel((150, 50)), (255, 255, 0, 255))
        self.assertEqual(final_image.getpixel((50, 150)), (255, 0, 0, 255))
        self.assertEqual(final_image.getpixel((150, 150)), (0, 255, 0, 255))


class AnimationSignalTests(BaseMediaTestCase):
    """Test suite for the Django signals related to animations."""

    def setUp(self):
        """Create a dummy project and asset for jobs/animations to link to."""
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Signals",
            project=self.project,
            blend_file=SimpleUploadedFile("dummy_signal.blend", b"data")
        )

    def test_animation_total_time_updates_on_job_completion(self):
        """
        Ensure the post_save signal on the Job model correctly sums and updates
        the parent Animation's total_render_time_seconds.
        """
        # ARRANGE: Create a parent animation and two child jobs
        anim = Animation.objects.create(name="Signal Test Animation", project=self.project, asset=self.asset,
                                        start_frame=1, end_frame=2)
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


class TiledAnimationAssemblyTests(BaseMediaTestCase):
    """Test suite for the tiled animation assembly logic and signals."""

    def setUp(self):
        """Create models for a tiled animation with one frame and four tile jobs."""
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Tiled Assembly",
            project=self.project,
            blend_file=SimpleUploadedFile("dummy_assembly.blend", b"data")
        )
        self.animation = Animation.objects.create(
            name="Tiled Assembly Animation",
            project=self.project,
            asset=self.asset,
            start_frame=1,
            end_frame=2, # Two frames for the final signal test
            tiling_config=TilingConfiguration.TILE_2X2
        )
        self.frame1 = AnimationFrame.objects.create(animation=self.animation, frame_number=1)
        self.frame2 = AnimationFrame.objects.create(animation=self.animation, frame_number=2)

        # Create 4 dummy tile images with different colors for frame 1
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)] # R, G, B, Y
        for y in range(2):
            for x in range(2):
                img = Image.new('RGB', (50, 50), color=colors[y * 2 + x])
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', dir=self.media_root)
                img.save(temp_file, 'PNG')
                temp_file.close()

                job = Job.objects.create(
                    animation=self.animation,
                    animation_frame=self.frame1,
                    name=f"{self.animation.name}_Frame_1_Tile_{y}_{x}",
                    asset=self.asset,
                    status=JobStatus.DONE,
                    render_time_seconds=10,
                    render_settings={
                        RenderSettings.RESOLUTION_X: 100,
                        RenderSettings.RESOLUTION_Y: 100
                    }
                )
                job.output_file.name = os.path.relpath(temp_file.name, self.media_root)
                job.save()

    def test_assemble_animation_frame(self):
        """
        Ensure the assembler stitches tiles and saves the final image.
        Thumbnail generation is now tested via the signal test.
        """
        # Act
        assemble_animation_frame_image(self.frame1.id)

        # Assert
        self.frame1.refresh_from_db()
        self.assertEqual(self.frame1.status, AnimationFrameStatus.DONE)
        self.assertTrue(self.frame1.output_file.name)
        self.assertEqual(self.frame1.render_time_seconds, 40)

        # Verify the assembled image content
        final_image = Image.open(self.frame1.output_file.path)
        self.assertEqual(final_image.size, (100, 100))
        self.assertEqual(final_image.getpixel((25, 25)), (0, 0, 255, 255))
        self.assertEqual(final_image.getpixel((75, 75)), (0, 255, 0, 255))

    def test_animation_status_updates_after_all_frames_assemble(self):
        """
        Ensure the parent Animation status updates to DONE only after all its
        AnimationFrame children are marked as DONE.
        """
        # Arrange: Assemble the first frame and set its render time.
        assemble_animation_frame_image(self.frame1.id)
        self.frame1.refresh_from_db()
        self.assertEqual(self.frame1.status, AnimationFrameStatus.DONE)

        # The animation should still be in progress
        self.animation.refresh_from_db()
        self.assertNotEqual(self.animation.status, "DONE")

        # Arrange: Mock the completion of the second frame's jobs and assemble it
        self.frame2.render_time_seconds = 60 # Simulate a different render time
        self.frame2.status = AnimationFrameStatus.DONE
        self.frame2.save() # This triggers the post_save signal on AnimationFrame

        # Assert: Now the animation should be complete
        self.animation.refresh_from_db()
        self.assertEqual(self.animation.status, "DONE")
        self.assertIsNotNone(self.animation.completed_at)
        self.assertEqual(self.animation.total_render_time_seconds, 100) # 40s from frame 1 + 60s from frame 2