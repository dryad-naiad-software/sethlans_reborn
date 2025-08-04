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
# workers/models/jobs.py
import uuid
from django.db import models
from django.utils import timezone
from ..constants import TilingConfiguration, RenderEngine, CyclesFeatureSet, RenderDevice
from .upload_paths import job_output_upload_path, tiled_job_output_upload_path, thumbnail_upload_path
from .projects import Asset
from .workers import Worker


class JobStatus(models.TextChoices):
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'
    CANCELED = 'CANCELED', 'Canceled'

class TiledJobStatus(models.TextChoices):
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    ASSEMBLING = 'ASSEMBLING', 'Assembling'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'

class TiledJob(models.Model):
    """
    Represents a single, high-resolution image render that is split into multiple tiles.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('workers.Project', on_delete=models.CASCADE, related_name='tiled_jobs')
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
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices, default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True, help_text="Global render settings for all tiles.")
    total_render_time_seconds = models.IntegerField(default=0)
    output_file = models.FileField(upload_to=tiled_job_output_upload_path, null=True, blank=True,
                                   help_text="The final, assembled output image.", max_length=512)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True,
                                  help_text="A preview thumbnail of the final assembled image.", max_length=512)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-submitted_at']

class Job(models.Model):
    """
    Represents a single, discrete render job.
    """
    name = models.CharField(max_length=255, unique=True, help_text="A unique name for the render job.")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='jobs')
    output_file_pattern = models.CharField(max_length=1024, help_text="Output file path pattern (e.g., //render/#.png)")
    start_frame = models.IntegerField(default=1)
    end_frame = models.IntegerField(default=1)
    status = models.CharField(max_length=50, choices=JobStatus.choices, default=JobStatus.QUEUED)
    assigned_worker = models.ForeignKey(Worker, on_delete=models.SET_NULL, null=True, blank=True, related_name='jobs')
    submitted_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.CharField(max_length=100, default="4.5", help_text="e.g., '4.5' or '4.1.1'")
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY)
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices, default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True,
                                       help_text="Blender render settings overrides, e.g., {'cycles.samples': 128, 'resolution_x': 1920}")
    last_output = models.TextField(blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    # Linked in animation.py via FK targets; using string models to avoid circular imports
    animation = models.ForeignKey('workers.Animation', on_delete=models.CASCADE, null=True, blank=True, related_name='jobs')
    tiled_job = models.ForeignKey(TiledJob, on_delete=models.CASCADE, null=True, blank=True, related_name='jobs')
    animation_frame = models.ForeignKey('workers.AnimationFrame', on_delete=models.CASCADE, null=True, blank=True, related_name='tile_jobs')
    render_time_seconds = models.IntegerField(null=True, blank=True, help_text="The total time in seconds this job took to render.")
    output_file = models.FileField(upload_to=job_output_upload_path, null=True, blank=True, help_text="The final rendered output file uploaded by the worker.", max_length=512)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True, help_text="A preview thumbnail of the final render.", max_length=512)

    def __str__(self):
        return f"{self.name} ({self.status})"

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = "Render Job"
        verbose_name_plural = "Render Jobs"