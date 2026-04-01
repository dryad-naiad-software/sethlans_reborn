# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.db import models
from django.utils import timezone
from django.core.validators import MinLengthValidator
from django.core.exceptions import ValidationError
from django.db.models import Sum
from ..constants import TilingConfiguration, RenderEngine, CyclesFeatureSet, RenderDevice
from .upload_paths import animation_frame_output_upload_path, thumbnail_upload_path
from .projects import Project, Asset
from .jobs import JobStatus

class AnimationFrameStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    RENDERING = 'RENDERING', 'Rendering'
    ASSEMBLING = 'ASSEMBLING', 'Assembling'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'

class Animation(models.Model):
    """
    Represents a multi-frame animation render job.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='animations')
    name = models.CharField(
        max_length=40,
        validators=[MinLengthValidator(4)]
    )
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='animations')
    output_file_pattern = models.CharField(max_length=1024)
    start_frame = models.IntegerField()
    end_frame = models.IntegerField()
    frame_step = models.IntegerField(default=1, help_text="Number of frames to advance animation between renders (e.g., a step of 2 renders every other frame).")
    status = models.CharField(max_length=50, choices=JobStatus.choices, default=JobStatus.QUEUED)
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.ForeignKey(
        'workers.SupportedBlenderVersion',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='animations',
        help_text="Optional version override; inherits from project when null.",
    )
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY)
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices, default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True, help_text="Blender render settings overrides, e.g., {'cycles.samples': 128, 'resolution_x': 1920}")
    total_render_time_seconds = models.IntegerField(default=0, help_text="The cumulative render time of all completed frames in this animation.")
    tiling_config = models.CharField(max_length=10, choices=TilingConfiguration.choices, default=TilingConfiguration.NONE, help_text="Grid size for tiled rendering of each frame.")
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True, help_text="A preview thumbnail of the latest completed frame.", max_length=512)

    def clean(self):
        if self.end_frame < self.start_frame:
            raise ValidationError({
                'end_frame': 'end_frame must be greater than or equal to start_frame.'
            })
        if self.frame_step <= 0:
            raise ValidationError({
                'frame_step': 'frame_step must be a positive integer.'
            })

    def get_tile_counts(self):
        """
        Parse tiling_config and return (tile_count_x, tile_count_y).

        Validates the format is 'NxM' where both N and M are positive
        integers. Raises ValueError with a clear message on malformed
        input.
        """
        config = self.tiling_config
        parts = config.split('x')
        if len(parts) != 2:
            raise ValueError(
                f"Invalid tiling_config format '{config}': "
                f"expected 'NxM' (e.g., '2x2')."
            )
        try:
            tile_count_x = int(parts[0])
            tile_count_y = int(parts[1])
        except ValueError:
            raise ValueError(
                f"Invalid tiling_config '{config}': "
                f"both values must be integers."
            )
        if tile_count_x <= 0 or tile_count_y <= 0:
            raise ValueError(
                f"Invalid tiling_config '{config}': "
                f"tile counts must be positive integers, "
                f"got ({tile_count_x}, {tile_count_y})."
            )
        return tile_count_x, tile_count_y

    @property
    def effective_blender_version(self):
        """Return explicit override or inherit from project."""
        return self.blender_version or self.asset.project.blender_version

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'name'],
                name='unique_animation_name_per_project'
            ),
        ]

class AnimationFrame(models.Model):
    """
    Represents a single frame of a (potentially tiled) animation.
    """
    animation = models.ForeignKey(Animation, on_delete=models.CASCADE, related_name='frames')
    frame_number = models.IntegerField()
    status = models.CharField(max_length=50, choices=AnimationFrameStatus.choices, default=AnimationFrameStatus.PENDING)
    output_file = models.FileField(upload_to=animation_frame_output_upload_path, null=True, blank=True, help_text="The final, assembled output image for this frame.", max_length=512)
    render_time_seconds = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True, help_text="A preview thumbnail of this assembled frame.", max_length=512)

    def __str__(self):
        return f"{self.animation.name} - Frame {self.frame_number}"

    class Meta:
        ordering = ['animation', 'frame_number']
        unique_together = ('animation', 'frame_number')