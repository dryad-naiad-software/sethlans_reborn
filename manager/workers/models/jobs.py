# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import uuid
from django.db import models
from django.utils import timezone
from django.core.validators import MinLengthValidator
from django.core.exceptions import ValidationError
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
    name = models.CharField(
        max_length=40,
        validators=[MinLengthValidator(4)]
    )
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='tiled_jobs')
    final_resolution_x = models.IntegerField()
    final_resolution_y = models.IntegerField()
    tile_count_x = models.IntegerField(default=4)
    tile_count_y = models.IntegerField(default=4)
    status = models.CharField(max_length=50, choices=TiledJobStatus.choices, default=TiledJobStatus.QUEUED)
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.ForeignKey(
        'workers.SupportedBlenderVersion',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tiled_jobs',
        help_text="Optional version override; inherits from project when null.",
    )
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY)
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices, default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True, help_text="Global render settings for all tiles.")
    total_render_time_seconds = models.IntegerField(default=0)
    output_file = models.FileField(upload_to=tiled_job_output_upload_path, null=True, blank=True,
                                   help_text="The final, assembled output image.", max_length=512)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True,
                                  help_text="A preview thumbnail of the final assembled image.", max_length=512)

    @property
    def effective_blender_version(self):
        """Return explicit override or inherit from project."""
        return self.blender_version or self.asset.project.blender_version

    def clean(self):
        if self.tile_count_x <= 0:
            raise ValidationError({
                'tile_count_x': 'tile_count_x must be a positive integer.'
            })
        if self.tile_count_y <= 0:
            raise ValidationError({
                'tile_count_y': 'tile_count_y must be a positive integer.'
            })

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'name'],
                name='unique_tiledjob_name_per_project'
            ),
        ]

class Job(models.Model):
    """
    Represents a single, discrete render job.
    """
    name = models.CharField(
        max_length=40,
        help_text="A unique name for the render job within its asset.",
        validators=[MinLengthValidator(4)]
    )
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='jobs')
    output_file_pattern = models.CharField(max_length=1024, help_text="Output file path pattern (e.g., //render/#.png)")
    start_frame = models.IntegerField(default=1)
    end_frame = models.IntegerField(default=1)
    status = models.CharField(max_length=50, choices=JobStatus.choices, default=JobStatus.QUEUED)
    assigned_worker = models.ForeignKey(Worker, on_delete=models.SET_NULL, null=True, blank=True, related_name='jobs')
    submitted_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.ForeignKey(
        'workers.SupportedBlenderVersion',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='jobs',
        help_text="Optional version override; inherits from project when null.",
    )
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(
        max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY,
        db_index=True,
    )
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
    is_paused = models.BooleanField(
        default=False,
        help_text="If true, this job is skipped during worker polling.",
    )
    auto_requeue_count = models.IntegerField(
        default=0,
        help_text="Number of times this job has been auto-requeued by stuck job detection.",
    )
    output_file = models.FileField(upload_to=job_output_upload_path, null=True, blank=True, help_text="The final rendered output file uploaded by the worker.", max_length=512)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True, help_text="A preview thumbnail of the final render.", max_length=512)

    @property
    def effective_blender_version(self):
        """Return explicit override or inherit from project."""
        return self.blender_version or self.asset.project.blender_version

    def __str__(self):
        return f"{self.name} ({self.status})"

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = "Render Job"
        verbose_name_plural = "Render Jobs"
        indexes = [
            models.Index(fields=['status', 'is_paused']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['asset', 'name'],
                name='unique_job_name_per_asset'
            ),
        ]