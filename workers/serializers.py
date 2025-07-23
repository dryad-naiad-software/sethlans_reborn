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

from rest_framework import serializers
from .models import Worker, Job, JobStatus # Import Job and JobStatus

class WorkerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Worker
        fields = ['id', 'hostname', 'ip_address', 'os', 'last_seen', 'is_active', 'available_tools']
        read_only_fields = ['last_seen']

class JobSerializer(serializers.ModelSerializer):
    # Display the assigned worker's hostname directly, rather than its ID
    assigned_worker_hostname = serializers.CharField(source='assigned_worker.hostname', read_only=True)
    # Human-readable status display (e.g., "Queued" instead of "QUEUED")
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Job
        fields = [
            'id', # Include ID for Angular to reference specific jobs
            'name',
            'blend_file_path',
            'output_file_pattern',
            'start_frame',
            'end_frame',
            'status',
            'status_display',
            'assigned_worker', # This will be the worker's ID for POST/PATCH
            'assigned_worker_hostname',
            'submitted_at',
            'started_at',
            'completed_at',
            'blender_version',
            'render_engine',
            'last_output',
            'error_message',
        ]
        read_only_fields = [
            'submitted_at', 'started_at', 'completed_at',
            'last_output', 'error_message',
            'status_display', 'assigned_worker_hostname',
        ]
        # These fields are set by backend logic/defaults, not required from client on creation
        extra_kwargs = {
            'status': {'required': False},
            'assigned_worker': {'required': False},
        }
