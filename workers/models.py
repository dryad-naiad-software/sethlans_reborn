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
# workers/models.py
"""
Defines the Django models for the Sethlans Reborn rendering system.

These models represent all the core entities of the application, including
the organizational structure (`Project`), renderable assets (`Asset`),
rendering machines (`Worker`), and the various types of render jobs.
"""

import uuid
import logging
from pathlib import Path
from django.db import models
from django.utils import timezone
from django.db.models import JSONField, Sum
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.files.base import ContentFile

from .constants import TilingConfiguration, RenderEngine, CyclesFeatureSet, RenderDevice

logger = logging.getLogger(__name__)


class Project(models.Model):
    """
    Represents a top-level creative project for organizing assets and jobs.

    Attributes:
        id (UUIDField): A unique identifier for the project.
        name (CharField): The human-readable name of the project.
        created_at (DateTimeField): The date and time the project was created.
        is_paused (BooleanField): If True, all jobs in this project will not be
            picked up by workers.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_paused = models.BooleanField(default=False,
                                    help_text="If true, workers will not pick up jobs from this project.")

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


def asset_upload_path(instance, filename):
    """
    Generates a project-specific path for an uploaded asset file.

    The path is structured as: `media/assets/<project_id>/<asset_uuid><ext>`.
    This ensures files for different projects are isolated and avoids naming collisions.
    """
    extension = Path(filename).suffix
    return f'assets/{instance.project.id}/{uuid.uuid4()}{extension}'


def job_output_upload_path(instance, filename):
    """
    Generates a unique, project-specific path for a single render job's output file.

    The path is structured as: `media/assets/<project_id>/outputs/job_<job_id><ext>`.
    """
    extension = Path(filename).suffix
    project_id = instance.asset.project.id
    return f'assets/{project_id}/outputs/job_{instance.id}{extension}'


def tiled_job_output_upload_path(instance, filename):
    """
    Generates a unique, project-specific path for a TiledJob's final assembled output file.

    The path is structured as: `media/assets/<project_id>/outputs/tiled_<tiled_job_id><ext>`.
    """
    extension = Path(filename).suffix
    project_id = instance.asset.project.id
    return f'assets/{project_id}/outputs/tiled_{instance.id}{extension}'


def animation_frame_output_upload_path(instance, filename):
    """
    Generates a project-specific path for a single, assembled animation frame.

    The path is structured as: `media/assets/<proj_id>/outputs/anim_<anim_id>/frame_<frame_number><ext>`.
    """
    extension = Path(filename).suffix
    project_id = instance.animation.project.id
    return f'assets/{project_id}/outputs/anim_{instance.animation.id}/frame_{instance.frame_number:04d}{extension}'


def thumbnail_upload_path(instance, filename):
    """
    Generates a unique, project-specific path for a thumbnail image.

    The path is structured to place all thumbnails in a centralized `thumbnails`
    directory, named after the object and its ID to ensure uniqueness.
    """
    extension = Path(filename).suffix
    model_name = instance.__class__.__name__.lower()
    project_id = None

    if hasattr(instance, 'project'):
        project_id = instance.project.id
    elif hasattr(instance, 'asset'):
        project_id = instance.asset.project.id
    elif hasattr(instance, 'animation'):
        project_id = instance.animation.project.id

    if not project_id:
        # Fallback for models without a direct project link, though unlikely
        return f'assets/unknown_project/thumbnails/{model_name}_{instance.id}{extension}'

    return f'assets/{project_id}/thumbnails/{model_name}_{instance.id}{extension}'


class Worker(models.Model):
    """
    Represents a single rendering machine in the distributed system.

    Workers send periodic heartbeats to the manager to register and update their
    status and capabilities.

    Attributes:
        hostname (CharField): The unique hostname of the worker.
        ip_address (GenericIPAddressField): The IP address of the worker.
        os (CharField): The operating system of the worker (e.g., 'Windows 11').
        last_seen (DateTimeField): The timestamp of the last successful heartbeat.
        is_active (BooleanField): A flag indicating if the worker is currently online.
        available_tools (JSONField): A dictionary containing detected hardware
            (e.g., CPU cores, GPU devices) and available software versions (e.g., Blender).
    """
    hostname = models.CharField(max_length=255, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    os = models.CharField(max_length=100, blank=True, default='')
    last_seen = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    available_tools = JSONField(default=dict, blank=True)

    def __str__(self):
        return self.hostname

    class Meta:
        ordering = ['hostname']


class Asset(models.Model):
    """
    Represents a `.blend` file asset uploaded to the manager.

    Assets are linked to a `Project` for organizational purposes and are
    downloaded by workers when a job requires them.

    Attributes:
        project (ForeignKey): The project this asset belongs to.
        name (CharField): The unique name of the asset.
        blend_file (FileField): The actual `.blend` file.
        created_at (DateTimeField): The date and time the asset was uploaded.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(max_length=255, unique=True, help_text="A unique name for the asset file.")
    blend_file = models.FileField(upload_to=asset_upload_path, help_text="The uploaded .blend file.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


class JobStatus(models.TextChoices):
    """Enumeration for the possible states of a standard render job."""
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'
    CANCELED = 'CANCELED', 'Canceled'


class TiledJobStatus(models.TextChoices):
    """Enumeration for the possible states of a tiled render job."""
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    ASSEMBLING = 'ASSEMBLING', 'Assembling'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'


class AnimationFrameStatus(models.TextChoices):
    """Enumeration for the possible states of a single frame within an animation."""
    PENDING = 'PENDING', 'Pending'
    RENDERING = 'RENDERING', 'Rendering'
    ASSEMBLING = 'ASSEMBLING', 'Assembling'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'


class Animation(models.Model):
    """
    Represents a multi-frame animation render job.

    This model serves as a parent container that automatically spawns
    individual `Job` objects for each frame.

    Attributes:
        project (ForeignKey): The project this animation belongs to.
        name (CharField): The unique name of the animation job.
        asset (ForeignKey): The `.blend` file asset to be rendered.
        output_file_pattern (CharField): The output file name pattern (e.g., `//renders/####.png`).
        start_frame (IntegerField): The first frame of the animation sequence.
        end_frame (IntegerField): The last frame of the animation sequence.
        frame_step (IntegerField): The number of frames to advance between renders.
        status (CharField): The current status of the overall animation.
        submitted_at (DateTimeField): The date and time the animation was submitted.
        completed_at (DateTimeField): The date and time the animation was fully completed.
        blender_version (CharField): The target Blender version for rendering.
        render_engine (CharField): The render engine to use (e.g., `CYCLES`).
        render_device (CharField): The preferred render device (`CPU`, `GPU`, `ANY`).
        cycles_feature_set (CharField): The Cycles feature set to use (`SUPPORTED`, `EXPERIMENTAL`).
        render_settings (JSONField): A dictionary of optional Blender settings to override.
        total_render_time_seconds (IntegerField): The sum of all child jobs' render times.
        tiling_config (CharField): If set, each frame will be rendered as a grid of tiles.
        thumbnail (ImageField): A small preview image of the most recently completed frame.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='animations')
    name = models.CharField(max_length=255, unique=True)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='animations')
    output_file_pattern = models.CharField(max_length=1024)
    start_frame = models.IntegerField()
    end_frame = models.IntegerField()
    frame_step = models.IntegerField(default=1,
                                     help_text="Number of frames to advance animation between renders (e.g., a step of 2 renders every other frame).")
    status = models.CharField(max_length=50, default='QUEUED')
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.CharField(max_length=100, default="4.5",
                                       help_text="e.g., '4.5' or '4.1.1'")
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY)
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices,
                                          default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True,
                                       help_text="Blender render settings overrides, e.g., {'cycles.samples': 128, 'resolution_x': 1920}")
    total_render_time_seconds = models.IntegerField(default=0,
                                                    help_text="The cumulative render time of all completed frames in this animation.")
    tiling_config = models.CharField(
        max_length=10,
        choices=TilingConfiguration.choices,
        default=TilingConfiguration.NONE,
        help_text="Grid size for tiled rendering of each frame."
    )
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True,
                                  help_text="A preview thumbnail of the latest completed frame.")

    def __str__(self):
        return self.name


class AnimationFrame(models.Model):
    """
    Represents a single frame of a (potentially tiled) animation.

    This model is used as a parent container for the tile jobs of a single
    frame, and stores the final assembled image and render time for that frame.

    Attributes:
        animation (ForeignKey): The parent `Animation` to which this frame belongs.
        frame_number (IntegerField): The number of the frame in the sequence.
        status (CharField): The current status of the frame's rendering and assembly.
        output_file (FileField): The final, assembled output image for this frame.
        render_time_seconds (IntegerField): The total time taken to render and
            assemble all tiles for this frame.
        thumbnail (ImageField): A small preview image of the assembled frame.
    """
    animation = models.ForeignKey(Animation, on_delete=models.CASCADE, related_name='frames')
    frame_number = models.IntegerField()
    status = models.CharField(max_length=50, choices=AnimationFrameStatus.choices, default=AnimationFrameStatus.PENDING)
    output_file = models.FileField(upload_to=animation_frame_output_upload_path, null=True, blank=True,
                                   help_text="The final, assembled output image for this frame.")
    render_time_seconds = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True,
                                  help_text="A preview thumbnail of this assembled frame.")

    def __str__(self):
        return f"{self.animation.name} - Frame {self.frame_number}"

    class Meta:
        ordering = ['animation', 'frame_number']
        unique_together = ('animation', 'frame_number')


class TiledJob(models.Model):
    """
    Represents a single, high-resolution image render that is split into multiple tiles.

    This model serves as a parent container that automatically spawns
    individual `Job` objects for each tile of the render.

    Attributes:
        id (UUIDField): A unique identifier for the tiled job.
        project (ForeignKey): The project this tiled job belongs to.
        name (CharField): The unique name of the tiled job.
        asset (ForeignKey): The `.blend` file asset to be rendered.
        final_resolution_x (IntegerField): The final width in pixels of the assembled image.
        final_resolution_y (IntegerField): The final height in pixels of the assembled image.
        tile_count_x (IntegerField): The number of horizontal tiles in the grid.
        tile_count_y (IntegerField): The number of vertical tiles in the grid.
        status (CharField): The current status of the overall tiled job.
        submitted_at (DateTimeField): The date and time the job was submitted.
        completed_at (DateTimeField): The date and time the job was fully completed.
        blender_version (CharField): The target Blender version for rendering.
        render_engine (CharField): The render engine to use (e.g., `CYCLES`).
        render_device (CharField): The preferred render device (`CPU`, `GPU`, `ANY`).
        cycles_feature_set (CharField): The Cycles feature set to use (`SUPPORTED`, `EXPERIMENTAL`).
        render_settings (JSONField): A dictionary of optional Blender settings to override.
        total_render_time_seconds (IntegerField): The sum of all child jobs' render times.
        output_file (FileField): The final, assembled output image file.
        thumbnail (ImageField): A small preview image of the final assembled output.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tiled_jobs')
    name = models.CharField(max_length=255, unique=True)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='tiled_jobs')
    final_resolution_x = models.IntegerField()
    final_resolution_y = models.IntegerField()
    tile_count_x = models.IntegerField(default=4)
    tile_count_y = models.IntegerField(default=4)
    status = models.CharField(max_length=50, choices=TiledJobStatus.choices, default=TiledJobStatus.QUEUED)
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.CharField(max_length=100, default="4.5")
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY)
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices,
                                          default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True, help_text="Global render settings for all tiles.")
    total_render_time_seconds = models.IntegerField(default=0)
    output_file = models.FileField(upload_to=tiled_job_output_upload_path, null=True, blank=True,
                                   help_text="The final, assembled output image.")
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True,
                                  help_text="A preview thumbnail of the final assembled image.")

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-submitted_at']


class Job(models.Model):
    """
    Represents a single, discrete render job.

    This is the fundamental unit of work processed by a `Worker` agent. It can
    be a standalone image render, a single frame of an animation, or a single
    tile of a tiled job.

    Attributes:
        name (CharField): The unique name for the job.
        asset (ForeignKey): The `.blend` file asset to be rendered.
        output_file_pattern (CharField): The path pattern for the render output.
        start_frame (IntegerField): The starting frame number for the render.
        end_frame (IntegerField): The ending frame number for the render.
        status (CharField): The current status of the job.
        assigned_worker (ForeignKey): The worker currently processing this job.
        submitted_at (DateTimeField): The date and time the job was submitted.
        started_at (DateTimeField): The date and time the worker started the job.
        completed_at (DateTimeField): The date and time the job finished.
        blender_version (CharField): The target Blender version for rendering.
        render_engine (CharField): The render engine to use (e.g., `CYCLES`).
        render_device (CharField): The preferred render device (`CPU`, `GPU`, `ANY`).
        cycles_feature_set (CharField): The Cycles feature set to use (`SUPPORTED`, `EXPERIMENTAL`).
        render_settings (JSONField): A dictionary of optional Blender settings to override.
        last_output (TextField): The final captured output from the Blender process stdout.
        error_message (TextField): Any error message captured from the Blender process.
        animation (ForeignKey): A link to the parent `Animation` object, if this is a frame job.
        tiled_job (ForeignKey): A link to the parent `TiledJob` object, if this is a tile.
        animation_frame (ForeignKey): A link to the parent `AnimationFrame` for tiled animation frames.
        render_time_seconds (IntegerField): The total time the render process took.
        output_file (FileField): The final rendered output file.
        thumbnail (ImageField): A small preview image of the final rendered output.
    """
    name = models.CharField(max_length=255, unique=True, help_text="A unique name for the render job.")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='jobs')
    output_file_pattern = models.CharField(max_length=1024, help_text="Output file path pattern (e.g., //render/#.png)")
    start_frame = models.IntegerField(default=1)
    end_frame = models.IntegerField(default=1)
    status = models.CharField(
        max_length=50,
        choices=JobStatus.choices,
        default=JobStatus.QUEUED
    )
    assigned_worker = models.ForeignKey(
        'Worker',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='jobs'
    )
    submitted_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.CharField(max_length=100, default="4.5",
                                       help_text="e.g., '4.5' or '4.1.1'")
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY)
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices,
                                          default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True,
                                       help_text="Blender render settings overrides, e.g., {'cycles.samples': 128, 'resolution_x': 1920}")
    last_output = models.TextField(blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    animation = models.ForeignKey(
        Animation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='jobs'
    )
    tiled_job = models.ForeignKey(
        TiledJob,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='jobs'
    )
    animation_frame = models.ForeignKey(
        AnimationFrame,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='tile_jobs'
    )
    render_time_seconds = models.IntegerField(null=True, blank=True,
                                              help_text="The total time in seconds this job took to render.")
    output_file = models.FileField(
        upload_to=job_output_upload_path,
        null=True,
        blank=True,
        help_text="The final rendered output file uploaded by the worker."
    )
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True,
                                  help_text="A preview thumbnail of the final render.")

    def __str__(self):
        return f"{self.name} ({self.status})"

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = "Render Job"
        verbose_name_plural = "Render Jobs"


@receiver(post_save, sender=Job)
def handle_job_completion(sender, instance, **kwargs):
    """
    Signal receiver for when a Job is saved. It handles:
    - Updating parent Animation/TiledJob status on job completion.
    - Triggering thumbnail generation for the job's output file.
    """
    from .image_assembler import assemble_tiled_job_image, assemble_animation_frame_image
    from .image_utils import generate_thumbnail

    # --- Parent Status Update Logic ---
    if instance.animation_frame and instance.status == JobStatus.DONE:
        frame = instance.animation_frame
        animation = frame.animation
        tile_counts = [int(i) for i in animation.tiling_config.split('x')]
        total_tiles_for_frame = tile_counts[0] * tile_counts[1]
        completed_tiles = frame.tile_jobs.filter(status=JobStatus.DONE).count()
        if completed_tiles >= total_tiles_for_frame:
            logger.info(f"All {total_tiles_for_frame} tiles for {frame} are complete. Triggering assembly.")
            assemble_animation_frame_image(frame.id)
    elif instance.animation and instance.animation.tiling_config == TilingConfiguration.NONE:
        animation = instance.animation
        all_jobs = animation.jobs.all()
        total_jobs_count = all_jobs.count()
        if total_jobs_count > 0:
            time_aggregate = all_jobs.filter(status=JobStatus.DONE).aggregate(total=Sum('render_time_seconds'))
            total_time = time_aggregate['total'] or 0
            finished_jobs_count = all_jobs.filter(
                status__in=[JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED]).count()
            animation_completed = total_jobs_count == finished_jobs_count

            # Also update status to RENDERING if it's the first job completing
            current_status = animation.status
            new_status = current_status
            if current_status == JobStatus.QUEUED and finished_jobs_count > 0:
                new_status = JobStatus.RENDERING
            if animation_completed:
                new_status = JobStatus.DONE

            update_fields = {'total_render_time_seconds': total_time, 'status': new_status}
            if animation_completed and not animation.completed_at:
                update_fields['completed_at'] = timezone.now()

            Animation.objects.filter(pk=animation.pk).update(**update_fields)

    elif instance.tiled_job:
        tiled_job = instance.tiled_job
        all_tile_jobs = tiled_job.jobs.all()
        total_tiles = tiled_job.tile_count_x * tiled_job.tile_count_y
        time_aggregate = all_tile_jobs.filter(status=JobStatus.DONE).aggregate(total=Sum('render_time_seconds'))
        total_time = time_aggregate['total'] or 0
        completed_tiles = all_tile_jobs.filter(status=JobStatus.DONE).count()
        TiledJob.objects.filter(pk=tiled_job.pk).update(
            total_render_time_seconds=total_time, status=TiledJobStatus.RENDERING)
        if completed_tiles == total_tiles:
            logger.info(f"All {total_tiles} tiles for TiledJob {tiled_job.id} are complete. Triggering assembly.")
            assemble_tiled_job_image(tiled_job.id)

    # --- Thumbnail Generation for Standard Jobs ---
    if instance.output_file and not instance.thumbnail:
        # Check that this is a standard job, not a tile (tiles are handled by frame/tiled job assembly)
        if not instance.animation_frame and not instance.tiled_job:
            logger.info(f"Job {instance.id} has an output file. Generating thumbnail.")
            thumb_content = generate_thumbnail(instance.output_file)
            if thumb_content:
                # Disconnect signal to prevent recursion, save, then reconnect.
                post_save.disconnect(handle_job_completion, sender=Job)
                instance.thumbnail.save(thumb_content.name, thumb_content, save=True)
                post_save.connect(handle_job_completion, sender=Job)

                # Update parent animation thumbnail for non-tiled animations
                if instance.animation:
                    post_save.disconnect(handle_job_completion, sender=Job)
                    instance.animation.thumbnail.save(thumb_content.name, ContentFile(thumb_content.getvalue()),
                                                      save=True)
                    post_save.connect(handle_job_completion, sender=Job)


@receiver(post_save, sender=AnimationFrame)
def handle_animation_frame_completion(sender, instance, **kwargs):
    """
    Signal receiver for when an AnimationFrame is saved. It handles:
    - Updating the parent Animation's status after all frames are complete.
    - Triggering thumbnail generation for the frame's output file.
    """
    from .image_utils import generate_thumbnail

    animation = instance.animation

    # --- Thumbnail Generation ---
    if instance.output_file and not instance.thumbnail:
        logger.info(f"AnimationFrame {instance.id} has an output file. Generating thumbnail.")
        thumb_content = generate_thumbnail(instance.output_file)
        if thumb_content:
            # Disconnect to prevent recursion
            post_save.disconnect(handle_animation_frame_completion, sender=AnimationFrame)
            instance.thumbnail.save(thumb_content.name, ContentFile(thumb_content.getvalue()), save=True)
            # Also update the parent animation's thumbnail
            animation.thumbnail.save(thumb_content.name, ContentFile(thumb_content.getvalue()), save=True)
            post_save.connect(handle_animation_frame_completion, sender=AnimationFrame)

    # --- Parent Animation Status Update ---
    if instance.status == AnimationFrameStatus.DONE and animation.status == JobStatus.QUEUED:
        animation.status = JobStatus.RENDERING
        animation.save(update_fields=['status'])

    all_frames = animation.frames.all()
    total_frames_count = all_frames.count()
    completed_frames_count = all_frames.filter(status=AnimationFrameStatus.DONE).count()

    if total_frames_count > 0 and completed_frames_count == total_frames_count:
        logger.info(f"All {total_frames_count} frames for Animation '{animation.name}' are complete.")
        time_aggregate = all_frames.aggregate(total=Sum('render_time_seconds'))
        total_time = time_aggregate['total'] or 0

        animation.status = "DONE"
        animation.completed_at = timezone.now()
        animation.total_render_time_seconds = total_time
        animation.save(update_fields=['status', 'completed_at', 'total_render_time_seconds'])


@receiver(post_save, sender=TiledJob)
def handle_tiled_job_completion(sender, instance, **kwargs):
    """
    Signal receiver for TiledJob to generate a thumbnail upon completion.
    """
    from .image_utils import generate_thumbnail
    if instance.output_file and not instance.thumbnail:
        logger.info(f"TiledJob {instance.id} has an output file. Generating thumbnail.")
        thumb_content = generate_thumbnail(instance.output_file)
        if thumb_content:
            # Disconnect to prevent recursion
            post_save.disconnect(handle_tiled_job_completion, sender=TiledJob)
            instance.thumbnail.save(thumb_content.name, thumb_content, save=True)
            post_save.connect(handle_tiled_job_completion, sender=TiledJob)