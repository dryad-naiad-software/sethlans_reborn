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
# workers/serializers.py
"""
Django REST Framework serializers for the workers application.

These serializers define the data format for all API requests and responses,
ensuring data integrity and providing a clear contract for frontend and
worker agent interactions.
"""

from rest_framework import serializers
from .models import Worker, Job, JobStatus, Animation, Asset, Project, TiledJob, TiledJobStatus, AnimationFrame

class ProjectSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Project` model.
    """
    class Meta:
        model = Project
        fields = ['id', 'name', 'created_at', 'is_paused']
        read_only_fields = ['id', 'created_at', 'is_paused']


class WorkerSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Worker` model.
    """
    class Meta:
        model = Worker
        fields = ['id', 'hostname', 'ip_address', 'os', 'last_seen', 'is_active', 'available_tools']
        read_only_fields = ['last_seen']


class AssetSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Asset` model, handling file uploads.
    """
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    project_details = ProjectSerializer(source='project', read_only=True)

    class Meta:
        model = Asset
        fields = ['id', 'name', 'blend_file', 'created_at', 'project', 'project_details']
        read_only_fields = ['created_at', 'project_details']
        extra_kwargs = {
            'project': {'write_only': True}
        }


class AnimationFrameSerializer(serializers.ModelSerializer):
    """
    Serializer for the individual, assembled frames of a tiled animation.
    """
    class Meta:
        model = AnimationFrame
        fields = ['id', 'frame_number', 'status', 'output_file', 'thumbnail', 'render_time_seconds']


class AnimationSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Animation` model.

    This serializer includes custom fields to report progress and links to the
    child `AnimationFrame` objects for tiled animations.
    """
    progress = serializers.SerializerMethodField(help_text="Human-readable progress string (e.g., '3 of 10 frames complete').")
    total_frames = serializers.SerializerMethodField(help_text="The total number of frames in the animation.")
    completed_frames = serializers.SerializerMethodField(help_text="The number of frames that are in a 'DONE' status.")
    frames = AnimationFrameSerializer(many=True, read_only=True, help_text="List of child frames for tiled animations.")

    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    project_details = ProjectSerializer(source='project', read_only=True)
    asset = AssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )

    class Meta:
        model = Animation
        fields = [
            'id', 'name', 'status', 'progress', 'total_frames', 'completed_frames',
            'project', 'project_details', 'asset', 'asset_id', 'output_file_pattern', 'start_frame', 'end_frame',
            'frame_step',
            'blender_version', 'render_engine', 'render_device', 'cycles_feature_set',
            'render_settings', 'tiling_config',
            'submitted_at', 'completed_at',
            'total_render_time_seconds', 'thumbnail', 'frames'
        ]
        read_only_fields = ('status', 'progress', 'total_frames', 'completed_frames', 'submitted_at', 'completed_at',
                            'total_render_time_seconds', 'asset', 'project_details', 'thumbnail', 'frames')
        extra_kwargs = {
            'project': {'write_only': True}
        }

    def validate(self, data):
        """
        Custom validation to ensure the selected `Asset` belongs to the `Project`.
        """
        project = data.get('project')
        asset = data.get('asset')
        if project and asset and asset.project != project:
            raise serializers.ValidationError("The selected Asset does not belong to the selected Project.")
        return data

    def get_total_frames(self, obj):
        """
        Calculates the total number of frames in the animation.
        """
        return (obj.end_frame - obj.start_frame) + 1

    def get_completed_frames(self, obj):
        """
        Counts the number of completed frames. This logic differs based on
        whether the animation is tiled or a standard sequence.
        """
        if obj.tiling_config != 'NONE':
            return obj.frames.filter(status='DONE').count()
        return obj.jobs.filter(status=JobStatus.DONE).count()

    def get_progress(self, obj):
        """
        Generates a human-readable string representing the progress of the animation.
        """
        completed = self.get_completed_frames(obj)
        total = self.get_total_frames(obj)
        if total == 0:
            return "0 of 0 frames complete"
        return f"{completed} of {total} frames complete"


class TiledJobSerializer(serializers.ModelSerializer):
    """
    Serializer for the `TiledJob` model.

    Includes calculated fields for progress and tile counts.
    """
    progress = serializers.SerializerMethodField(help_text="Human-readable progress string (e.g., '3 of 10 tiles complete').")
    total_tiles = serializers.SerializerMethodField(help_text="The total number of tiles in the job.")
    completed_tiles = serializers.SerializerMethodField(help_text="The number of tiles that are in a 'DONE' status.")

    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    project_details = ProjectSerializer(source='project', read_only=True)
    asset = AssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )

    class Meta:
        model = TiledJob
        fields = [
            'id', 'name', 'status', 'progress', 'total_tiles', 'completed_tiles',
            'project', 'project_details', 'asset', 'asset_id',
            'final_resolution_x', 'final_resolution_y', 'tile_count_x', 'tile_count_y',
            'blender_version', 'render_engine', 'render_device', 'cycles_feature_set', 'render_settings',
            'submitted_at', 'completed_at', 'total_render_time_seconds', 'output_file', 'thumbnail'
        ]
        read_only_fields = (
            'id', 'status', 'progress', 'total_tiles', 'completed_tiles',
            'submitted_at', 'completed_at', 'total_render_time_seconds',
            'asset', 'project_details', 'output_file', 'thumbnail'
        )
        extra_kwargs = {
            'project': {'write_only': True}
        }

    def validate(self, data):
        """
        Custom validation to ensure the selected `Asset` belongs to the `Project`.
        """
        project = data.get('project')
        asset = data.get('asset')
        if project and asset and asset.project != project:
            raise serializers.ValidationError("The selected Asset does not belong to the selected Project.")
        return data

    def get_total_tiles(self, obj):
        """
        Calculates the total number of tiles for the job.
        """
        return obj.tile_count_x * obj.tile_count_y

    def get_completed_tiles(self, obj):
        """
        Counts the number of completed child jobs.
        """
        return obj.jobs.filter(status=JobStatus.DONE).count()

    def get_progress(self, obj):
        """
        Generates a human-readable string representing the progress of the tiled job.
        """
        completed = self.get_completed_tiles(obj)
        total = self.get_total_tiles(obj)
        if total == 0:
            return "0 of 0 tiles complete"
        return f"{completed} of {total} tiles complete"


class JobSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Job` model.

    This serializer is used by the API for creating, viewing, and updating jobs.
    It includes read-only fields for human-readable status and worker hostname.
    Fields updated by the worker agent during its lifecycle (e.g., `assigned_worker`,
    `started_at`, `completed_at`, `last_output`, `error_message`) are now
    writable to allow status updates via PATCH requests.
    """
    assigned_worker_hostname = serializers.CharField(source='assigned_worker.hostname', read_only=True, help_text="The hostname of the worker assigned to this job.")
    status_display = serializers.CharField(source='get_status_display', read_only=True, help_text="The human-readable status of the job.")
    asset = AssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )


    class Meta:
        model = Job
        fields = [
            'id',
            'name',
            'asset',
            'asset_id',
            'output_file_pattern',
            'start_frame',
            'end_frame',
            'status',
            'status_display',
            'assigned_worker',
            'assigned_worker_hostname',
            'animation',
            'tiled_job',
            'animation_frame',
            'submitted_at',
            'started_at',
            'completed_at',
            'blender_version',
            'render_engine',
            'render_device',
            'cycles_feature_set',
            'render_settings',
            'last_output',
            'error_message',
            'render_time_seconds',
            'output_file',
            'thumbnail',
        ]
        read_only_fields = [
            'submitted_at',
            'status_display',
            'assigned_worker_hostname',
            'asset',
            'output_file',
            'thumbnail',
            'tiled_job',
            'animation_frame'
        ]
        extra_kwargs = {
            'status': {'required': False},
            'assigned_worker': {'required': False},
            'animation': {'required': False},
            'render_time_seconds': {'required': False},
            'render_settings': {'required': False},
            'started_at': {'required': False},
            'completed_at': {'required': False},
            'last_output': {'required': False},
            'error_message': {'required': False},
        }