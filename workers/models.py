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

import uuid
from pathlib import Path
from django.db import models
from django.utils import timezone
from django.db.models import JSONField, Sum
from django.db.models.signals import post_save
from django.dispatch import receiver


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


def asset_upload_path(instance, filename):
    """Generates a project-specific path for an asset file using UUIDs."""
    extension = Path(filename).suffix
    # The file will be saved to MEDIA_ROOT/assets/<project_id>/<asset_uuid><ext>
    return f'assets/{instance.project.id}/{uuid.uuid4()}{extension}'


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


class Asset(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(max_length=255, unique=True, help_text="A unique name for the asset file.")
    blend_file = models.FileField(upload_to=asset_upload_path, help_text="The uploaded .blend file.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


class JobStatus(models.TextChoices):
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'
    CANCELED = 'CANCELED', 'Canceled'


class Animation(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='animations')
    name = models.CharField(max_length=255, unique=True)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='animations')
    output_file_pattern = models.CharField(max_length=1024)
    start_frame = models.IntegerField()
    end_frame = models.IntegerField()
    status = models.CharField(max_length=50, default='QUEUED')
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    blender_version = models.CharField(max_length=100, default="4.5",
                                       help_text="e.g., '4.5' or 'blender-4.5.0-windows-x64'")
    render_engine = models.CharField(max_length=100, default="CYCLES", help_text="e.g., 'CYCLES' or 'BLENDER_EEVEE'")
    render_device = models.CharField(max_length=10, default="CPU")
    render_settings = models.JSONField(default=dict, blank=True, help_text="Blender render settings overrides, e.g., {'cycles.samples': 128, 'resolution_x': 1920}")
    total_render_time_seconds = models.IntegerField(default=0,
                                                    help_text="The cumulative render time of all completed frames in this animation.")

    def __str__(self):
        return self.name


class Job(models.Model):
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
                                       help_text="e.g., '4.5' or 'blender-4.5.0-windows-x64'")
    render_engine = models.CharField(max_length=100, default="CYCLES", help_text="e.g., 'CYCLES' or 'BLENDER_EEVEE'")
    render_device = models.CharField(max_length=10, default="CPU")
    render_settings = models.JSONField(default=dict, blank=True, help_text="Blender render settings overrides, e.g., {'cycles.samples': 128, 'resolution_x': 1920}")
    last_output = models.TextField(blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    animation = models.ForeignKey(
        Animation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='jobs'
    )
    render_time_seconds = models.IntegerField(null=True, blank=True,
                                              help_text="The total time in seconds this job took to render.")

    def __str__(self):
        return f"{self.name} ({self.status})"

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = "Render Job"
        verbose_name_plural = "Render Jobs"


@receiver(post_save, sender=Job)
def update_animation_on_job_complete(sender, instance, **kwargs):
    """
    After a Job is saved, if it's part of an animation, check if the
    animation is complete and update its total render time.
    """
    if not instance.animation:
        return

    animation = instance.animation
    all_jobs = animation.jobs.all()
    total_jobs_count = all_jobs.count()

    if total_jobs_count > 0:
        # Recalculate total render time from all DONE jobs
        time_aggregate = all_jobs.filter(status=JobStatus.DONE).aggregate(total=Sum('render_time_seconds'))
        total_time = time_aggregate['total'] or 0

        # Check if all jobs are finished (DONE, ERROR, or CANCELED)
        finished_jobs_count = all_jobs.filter(status__in=[JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED]).count()

        animation_completed = total_jobs_count == finished_jobs_count

        # Update animation fields without triggering signals
        Animation.objects.filter(pk=animation.pk).update(
            total_render_time_seconds=total_time,
            status="DONE" if animation_completed else "RENDERING",
            completed_at=timezone.now() if animation_completed and not animation.completed_at else None
        )