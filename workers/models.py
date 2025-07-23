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

    # New field to store available tools and their versions on this worker
    # Example: {'blender': ['4.0.0', '4.1.0'], 'ffmpeg': ['6.0']}
    available_tools = JSONField(default=dict, blank=True)


    def __str__(self):
        return self.hostname

    class Meta:
        ordering = ['hostname']

# Define choices for job status
class JobStatus(models.TextChoices):
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'
    CANCELED = 'CANCELED', 'Canceled'


class Job(models.Model):
    name = models.CharField(max_length=255, unique=True, help_text="A unique name for the render job.")
    blend_file_path = models.CharField(max_length=1024, help_text="Absolute path to the Blender file.")
    output_file_pattern = models.CharField(max_length=1024, help_text="Output file path pattern (e.g., //render/#.png)")
    start_frame = models.IntegerField(default=1)
    end_frame = models.IntegerField(default=1)
    # Use a CharField with choices for status
    status = models.CharField(
        max_length=50,
        choices=JobStatus.choices,
        default=JobStatus.QUEUED
    )

    # Link to the Worker that's assigned this job (optional initially)
    assigned_worker = models.ForeignKey(
        'Worker',
        on_delete=models.SET_NULL, # If a worker is deleted, don't delete the job, just nullify this field
        null=True,
        blank=True,
        related_name='jobs' # Allows access from Worker.jobs.all()
    )

    submitted_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # We'll use a string for blender_version for now. Later we can make this more robust.
    blender_version = models.CharField(max_length=100, default="4.5", help_text="e.g., '4.5' or 'blender-4.5.0-windows-x64'")
    render_engine = models.CharField(max_length=100, default="CYCLES", help_text="e.g., 'CYCLES' or 'BLENDER_EEVEE'")

    # Store basic output from Blender process (for debugging/status)
    last_output = models.TextField(blank=True, default='')
    error_message = models.TextField(blank=True, default='')

    def __str__(self):
        return f"{self.name} ({self.status})"

    class Meta:
        ordering = ['-submitted_at'] # Order by most recently submitted first
        verbose_name = "Render Job"
        verbose_name_plural = "Render Jobs"