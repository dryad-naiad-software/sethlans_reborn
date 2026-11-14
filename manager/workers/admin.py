# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django import forms
from django.contrib import admin

from .constants import PILOT_TARGET_FIELDS, AssetSourceType, UnrealKind
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
    # ``renderer`` is derived from the project and ``engine_build`` is
    # pinned at submission (spec FR-MR-3, FR-MU-3): never edited here.
    # ``unreal_kind`` is set only by the manager (pilot jobs, FR-PM-9).
    readonly_fields = (
        'submitted_at', 'started_at', 'completed_at',
        'last_output', 'error_message', 'renderer', 'engine_build',
        'unreal_kind',
    )

    def get_readonly_fields(self, request, obj=None):
        """A pilot's target and status are manager-owned (FR-PM-9, FR-RT-14)."""
        fields = tuple(super().get_readonly_fields(request, obj))
        if obj is not None and obj.unreal_kind == UnrealKind.PILOT:
            fields += tuple(f for f in PILOT_TARGET_FIELDS + ('status',) if f not in fields)
        return fields


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


class AssetAdminForm(forms.ModelForm):
    """Require a file for ``blend_file`` assets.

    ``Asset.blend_file`` is ``blank=True`` (shared-project assets have no
    file), so the model form alone would accept a ``.blend`` asset with
    none.  The API enforces the same rule in ``AssetSerializer.get_fields``.
    """

    class Meta:
        model = Asset
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        source_type = cleaned.get('source_type', AssetSourceType.BLEND_FILE)
        if source_type == AssetSourceType.BLEND_FILE and not cleaned.get('blend_file'):
            self.add_error('blend_file', "A .blend file asset requires a file.")
        return cleaned


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    form = AssetAdminForm
    list_display = ('name', 'blend_file', 'created_at')
    search_fields = ('name',)
    date_hierarchy = 'created_at'
