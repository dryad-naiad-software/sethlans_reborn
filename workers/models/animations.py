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
# workers/models/animation.py
from django.db import models
from django.utils import timezone
from django.db.models import Sum
from ..constants import TilingConfiguration, RenderEngine, CyclesFeatureSet, RenderDevice
from .upload_paths import animation_frame_output_upload_path, thumbnail_upload_path
from .projects import Project, Asset

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
    name = models.CharField(max_length=255, unique=True)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='animations')
    output_file_pattern = models.CharField(max_length=1024)
    start_frame = models.IntegerField()
    end_frame = models.IntegerField()
    frame_step = models.IntegerField(default=1, help_text="Number of frames to advance animation between renders (e.g., a step of 2 renders every other frame).")
    status = models.CharField(max_length=50, default='QUEUED')
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.CharField(max_length=100, default="4.5", help_text="e.g., '4.5' or '4.1.1'")
    render_engine = models.CharField(max_length=50, choices=RenderEngine.choices, default=RenderEngine.CYCLES)
    render_device = models.CharField(max_length=10, choices=RenderDevice.choices, default=RenderDevice.ANY)
    cycles_feature_set = models.CharField(max_length=50, choices=CyclesFeatureSet.choices, default=CyclesFeatureSet.SUPPORTED)
    render_settings = models.JSONField(default=dict, blank=True, help_text="Blender render settings overrides, e.g., {'cycles.samples': 128, 'resolution_x': 1920}")
    total_render_time_seconds = models.IntegerField(default=0, help_text="The cumulative render time of all completed frames in this animation.")
    tiling_config = models.CharField(max_length=10, choices=TilingConfiguration.choices, default=TilingConfiguration.NONE, help_text="Grid size for tiled rendering of each frame.")
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True, help_text="A preview thumbnail of the latest completed frame.")

    def __str__(self):
        return self.name

class AnimationFrame(models.Model):
    """
    Represents a single frame of a (potentially tiled) animation.
    """
    animation = models.ForeignKey(Animation, on_delete=models.CASCADE, related_name='frames')
    frame_number = models.IntegerField()
    status = models.CharField(max_length=50, choices=AnimationFrameStatus.choices, default=AnimationFrameStatus.PENDING)
    output_file = models.FileField(upload_to=animation_frame_output_upload_path, null=True, blank=True, help_text="The final, assembled output image for this frame.")
    render_time_seconds = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_path, null=True, blank=True, help_text="A preview thumbnail of this assembled frame.")

    def __str__(self):
        return f"{self.animation.name} - Frame {self.frame_number}"

    class Meta:
        ordering = ['animation', 'frame_number']
        unique_together = ('animation', 'frame_number')

