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

from django.db import models
from django.utils import timezone
from django.db.models import JSONField

class Worker(models.Model):
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

class JobStatus(models.TextChoices):
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'
    CANCELED = 'CANCELED', 'Canceled'

class Animation(models.Model):
    name = models.CharField(max_length=255, unique=True)
    blend_file_path = models.CharField(max_length=1024)
    output_file_pattern = models.CharField(max_length=1024)
    start_frame = models.IntegerField()
    end_frame = models.IntegerField()
    status = models.CharField(max_length=50, default='QUEUED')
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.CharField(max_length=100, default="4.5", help_text="e.g., '4.5' or 'blender-4.5.0-windows-x64'")
    render_engine = models.CharField(max_length=100, default="CYCLES", help_text="e.g., 'CYCLES' or 'BLENDER_EEVEE'")
    render_device = models.CharField(max_length=10, default="CPU") # New Field

    def __str__(self):
        return self.name

class Job(models.Model):
    name = models.CharField(max_length=255, unique=True, help_text="A unique name for the render job.")
    blend_file_path = models.CharField(max_length=1024, help_text="Absolute path to the Blender file.")
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
    blender_version = models.CharField(max_length=100, default="4.5", help_text="e.g., '4.5' or 'blender-4.5.0-windows-x64'")
    render_engine = models.CharField(max_length=100, default="CYCLES", help_text="e.g., 'CYCLES' or 'BLENDER_EEVEE'")
    render_device = models.CharField(max_length=10, default="CPU") # New Field
    last_output = models.TextField(blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    animation = models.ForeignKey(
        Animation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='jobs'
    )

    def __str__(self):
        return f"{self.name} ({self.status})"

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = "Render Job"
        verbose_name_plural = "Render Jobs"