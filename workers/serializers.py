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

from rest_framework import serializers
from .models import Worker, Job, JobStatus, Animation, Asset, Project

class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['id', 'name', 'created_at']
        read_only_fields = ['id', 'created_at']


class WorkerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Worker
        fields = ['id', 'hostname', 'ip_address', 'os', 'last_seen', 'is_active', 'available_tools']
        read_only_fields = ['last_seen']


class AssetSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    project_details = ProjectSerializer(source='project', read_only=True)

    class Meta:
        model = Asset
        fields = ['id', 'name', 'blend_file', 'created_at', 'project', 'project_details']
        read_only_fields = ['created_at', 'project_details']
        extra_kwargs = {
            'project': {'write_only': True}
        }


class AnimationSerializer(serializers.ModelSerializer):
    progress = serializers.SerializerMethodField()
    total_frames = serializers.SerializerMethodField()
    completed_frames = serializers.SerializerMethodField()

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
            'blender_version', 'render_engine', 'render_device',
            'render_settings',
            'submitted_at', 'completed_at',
            'total_render_time_seconds'
        ]
        read_only_fields = ('status', 'progress', 'total_frames', 'completed_frames', 'submitted_at', 'completed_at',
                            'total_render_time_seconds', 'asset', 'project_details')
        extra_kwargs = {
            'project': {'write_only': True}
        }

    def validate(self, data):
        """Check that the chosen Asset belongs to the chosen Project."""
        project = data.get('project')
        asset = data.get('asset')
        if project and asset and asset.project != project:
            raise serializers.ValidationError("The selected Asset does not belong to the selected Project.")
        return data

    def get_total_frames(self, obj):
        return (obj.end_frame - obj.start_frame) + 1

    def get_completed_frames(self, obj):
        return obj.jobs.filter(status=JobStatus.DONE).count()

    def get_progress(self, obj):
        completed = self.get_completed_frames(obj)
        total = self.get_total_frames(obj)
        if total == 0:
            return "0 of 0 frames complete"
        return f"{completed} of {total} frames complete"


class JobSerializer(serializers.ModelSerializer):
    assigned_worker_hostname = serializers.CharField(source='assigned_worker.hostname', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
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
            'submitted_at',
            'started_at',
            'completed_at',
            'blender_version',
            'render_engine',
            'render_device',
            'render_settings',
            'last_output',
            'error_message',
            'render_time_seconds',
        ]
        read_only_fields = [
            'submitted_at', 'started_at', 'completed_at',
            'last_output', 'error_message',
            'status_display', 'assigned_worker_hostname',
            'asset'
        ]
        extra_kwargs = {
            'status': {'required': False},
            'assigned_worker': {'required': False},
            'animation': {'required': False},
            'render_time_seconds': {'required': False},
            'render_settings': {'required': False},
        }