# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import uuid
from django.db import models
from django.core.files.base import ContentFile
from django.core.validators import MinLengthValidator
from django.utils.text import slugify

from shared.unreal.validation import overrides_sha256

from ..constants import AssetSourceType, Renderer
from .renderer_constraints import ASSET_SOURCE_FIELDS_CONSISTENT, PROJECT_RENDERER_FIELDS_CONSISTENT
from .upload_paths import asset_upload_path


class Project(models.Model):
    """
    Represents a top-level creative project for organizing assets and jobs.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=40,
        unique=True,
        validators=[MinLengthValidator(4)]
    )
    renderer = models.CharField(
        max_length=16, choices=Renderer.choices, default=Renderer.BLENDER,
        help_text="The render application for every job in this project. Cannot change after creation.",
    )
    blender_version = models.ForeignKey(
        'workers.SupportedBlenderVersion',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='projects',
        help_text="The Blender version series used for all jobs in this project (Blender projects only).",
    )
    engine_build = models.CharField(
        max_length=32, blank=True, default='',
        help_text="Exact Unreal Engine build, e.g. 5.8.4-58902052 (Unreal projects only).",
    )
    unreal_cvar_overrides = models.JSONField(
        default=dict, blank=True,
        help_text=(
            "Per-project Unreal console variables (name -> value). Not pinned per job: "
            "workers read them at claim time, so edits also affect jobs that are still queued."
        ),
    )
    unreal_overrides_sha256 = models.CharField(
        max_length=64, blank=True, default='', db_default='', editable=False,
        help_text="Unreal projects: hash of unreal_cvar_overrides (measurement key); '' for Blender.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Keep ``unreal_overrides_sha256`` in step with the overrides (FR-PM-1).

        A save whose ``update_fields`` names ``unreal_cvar_overrides`` also
        writes the hash.
        """
        self.unreal_overrides_sha256 = (
            overrides_sha256(self.unreal_cvar_overrides)
            if self.renderer == Renderer.UNREAL else ''
        )
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'unreal_cvar_overrides' in update_fields:
            kwargs['update_fields'] = sorted(set(update_fields) | {'unreal_overrides_sha256'})
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-created_at']
        constraints = [PROJECT_RENDERER_FIELDS_CONSISTENT]


class Asset(models.Model):
    """
    Represents the scene data of a project: an uploaded `.blend` file, or
    (``source_type='shared_project'``) a project archive on the Sethlans share.

    Accepts:
      - a normal Django File/InMemoryUploadedFile,
      - OR raw bytes (wrapped into a ContentFile automatically).
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="A unique name for the asset file.",
    )
    source_type = models.CharField(
        max_length=32, choices=AssetSourceType.choices, default=AssetSourceType.BLEND_FILE,
        help_text="How the scene data reaches the workers. Cannot change after creation.",
    )
    blend_file = models.FileField(
        upload_to=asset_upload_path,
        blank=True,
        help_text="The uploaded .blend file (required for blend_file assets).",
    )
    archive_path = models.CharField(
        max_length=512, blank=True, default='',
        help_text="Path of the project .zip relative to the Sethlans share root, '/'-separated.",
    )
    uproject_path = models.CharField(
        max_length=260, blank=True, default='',
        help_text="Relative path of the .uproject inside the archive.",
    )
    archive_sha256 = models.CharField(
        max_length=64, blank=True, default='',
        help_text="Lowercase hex SHA-256 of the archive bytes (mandatory for shared_project assets).",
    )
    overlay_path = models.CharField(
        max_length=512, blank=True, default='',
        help_text="Optional overlay directory relative to the Sethlans share root.",
    )
    overlay_sha256 = models.CharField(
        max_length=64, blank=True, default='',
        help_text="Overlay digest (mandatory whenever overlay_path is set).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']
        constraints = [ASSET_SOURCE_FIELDS_CONSISTENT]

    def _ensure_blend_file_is_named_file(self):
        """
        If `blend_file` is raw bytes or a file-like without a name,
        wrap/replace it with a ContentFile carrying a sensible filename.
        """
        value = self.blend_file

        # Case 1: raw bytes / bytearray assigned directly
        if isinstance(value, (bytes, bytearray)):
            filename = f"{slugify(self.name) or 'asset'}-{uuid.uuid4().hex[:8]}.blend"
            self.blend_file = ContentFile(bytes(value), name=filename)
            return

        # No file (shared-project assets): an empty FieldFile raises
        # ValueError, not AttributeError, from ``hasattr(value, 'read')``.
        if not value:
            return

        # Case 2: a file-like object with no `.name`
        # Guard against Django's FieldFile (which already has a name attribute)
        if hasattr(value, 'read') and not hasattr(value, 'name'):
            filename = f"{slugify(self.name) or 'asset'}-{uuid.uuid4().hex[:8]}.blend"
            data = value.read()
            self.blend_file = ContentFile(data, name=filename)
            return

        # Case 3: Already a Django FieldFile/File with a name -> nothing to do.

    def save(self, *args, **kwargs):
        self._ensure_blend_file_is_named_file()
        super().save(*args, **kwargs)
