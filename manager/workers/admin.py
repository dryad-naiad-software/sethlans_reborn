# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.contrib import admin
from .models import Worker, Job, Animation, Asset, SupportedBlenderVersion


@admin.register(SupportedBlenderVersion)
class SupportedBlenderVersionAdmin(admin.ModelAdmin):
    list_display = ('series', 'resolved_version', 'is_default', 'added_at')
    list_filter = ('is_default',)
    ordering = ('major', 'minor')


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
        'start_frame', 'end_frame', 'blender_version', 'animation',
    )
    list_filter = ('status', 'assigned_worker', 'blender_version', 'animation')
    search_fields = ('name',)
    date_hierarchy = 'submitted_at'
    ordering = ('-submitted_at',)
    readonly_fields = (
        'submitted_at', 'started_at', 'completed_at',
        'last_output', 'error_message',
    )


@admin.register(Animation)
class AnimationAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'status', 'start_frame', 'end_frame',
        'submitted_at', 'completed_at',
    )
    list_filter = ('status',)
    search_fields = ('name',)
    date_hierarchy = 'submitted_at'
    ordering = ('-submitted_at',)
    readonly_fields = ('submitted_at', 'completed_at')


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'blend_file', 'created_at')
    search_fields = ('name',)
    date_hierarchy = 'created_at'
