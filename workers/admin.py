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

from django.contrib import admin
from .models import Worker, Job


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ('hostname', 'ip_address', 'os', 'last_seen', 'is_active')
    list_filter = ('os', 'is_active')
    search_fields = ('hostname', 'ip_address')
    ordering = ('hostname',)

@admin.register(Job) #
class JobAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'status', 'assigned_worker', 'submitted_at',
        'start_frame', 'end_frame', 'blender_version', 'render_engine'
    )
    list_filter = ('status', 'assigned_worker', 'blender_version', 'render_engine')
    search_fields = ('name', 'blend_file_path')
    date_hierarchy = 'submitted_at' # Adds date drill-down options
    ordering = ('-submitted_at',)
    # Make certain fields read-only in the admin if they should only be set programmatically
    readonly_fields = ('submitted_at', 'started_at', 'completed_at', 'last_output', 'error_message')
