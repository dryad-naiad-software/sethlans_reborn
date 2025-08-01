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
# workers/admin.py

from django.contrib import admin
from .models import Worker, Job, Animation, Asset # Import Asset


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ('hostname', 'ip_address', 'os', 'last_seen', 'is_active')
    list_filter = ('os', 'is_active')
    search_fields = ('hostname', 'ip_address')
    ordering = ('hostname',)

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'status', 'assigned_worker', 'submitted_at',
        'start_frame', 'end_frame', 'blender_version', 'animation' # Added animation
    )
    list_filter = ('status', 'assigned_worker', 'blender_version', 'animation')
    search_fields = ('name', 'blend_file_path')
    date_hierarchy = 'submitted_at'
    ordering = ('-submitted_at',)
    readonly_fields = ('submitted_at', 'started_at', 'completed_at', 'last_output', 'error_message')

# --- NEW: Register Animation model ---
@admin.register(Animation)
class AnimationAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'start_frame', 'end_frame', 'submitted_at', 'completed_at')
    list_filter = ('status',)
    search_fields = ('name',)
    date_hierarchy = 'submitted_at'
    ordering = ('-submitted_at',)
    readonly_fields = ('submitted_at', 'completed_at')

# --- NEW: Register Asset model ---
@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'blend_file', 'created_at')
    search_fields = ('name',)
    date_hierarchy = 'created_at'