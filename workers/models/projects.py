# workers/models/projects.py
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
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn

import uuid
from django.db import models
from django.core.files.base import ContentFile
from django.utils.text import slugify

from .upload_paths import asset_upload_path, thumbnail_upload_path  # thumbnail path reserved for future use


class Project(models.Model):
    """
    Represents a top-level creative project for organizing assets and jobs.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_paused = models.BooleanField(
        default=False,
        help_text="If true, workers will not pick up jobs from this project."
    )

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


class Asset(models.Model):
    """
    Represents a `.blend` file asset uploaded to the manager.

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
    blend_file = models.FileField(
        upload_to=asset_upload_path,
        help_text="The uploaded .blend file.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']

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
