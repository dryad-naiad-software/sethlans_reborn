# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One Blender build the manager keeps for its workers (BS-MGR-3, BS-MGR-3a).

A row stands for one official archive (``version`` x ``platform``) that the
manager fetches once and serves to enrolled workers. Rows are written only
by ``workers.services.blender_cache``: inserted with
``bulk_create(ignore_conflicts=True)``, deleted with ``filter(...).delete()``
and changed with conditional ``filter(pk=..., status=<expected>).update(...)``
calls whose row count is checked. ``update()`` skips ``auto_now``, so every
such call sets ``updated_at`` itself.
"""

from django.db import models

from ..constants import BlenderBuildStatus

ERROR_MAX_LENGTH = 500
SOURCE_MAX_LENGTH = 500


class BlenderBuild(models.Model):
    """One cached Blender archive and the state of its fetch."""

    version = models.CharField(max_length=20, help_text="Full Blender version, e.g. '4.5.8'.")
    platform = models.CharField(max_length=20, help_text="Platform id, e.g. 'linux-x64'.")
    file_name = models.CharField(
        max_length=100,
        help_text="Archive name the manager built itself: blender-<version>-<platform>.<ext>.",
    )
    sha256 = models.CharField(
        max_length=64, blank=True, default='',
        help_text="Official SHA-256 from the release list; set when READY.",
    )
    size = models.PositiveBigIntegerField(null=True, blank=True, help_text="Archive size in bytes; set when READY.")
    status = models.CharField(
        max_length=10,
        choices=BlenderBuildStatus.choices,
        default=BlenderBuildStatus.QUEUED,
    )
    source = models.CharField(
        max_length=SOURCE_MAX_LENGTH, blank=True, default='',
        help_text="Source the release list and checksum came from (for logs).",
    )
    error = models.CharField(
        max_length=ERROR_MAX_LENGTH, blank=True, default='',
        help_text="Why the last fetch failed; truncated in code.",
    )
    next_try_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When a FAILED build may be fetched again.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['version', 'platform']
        constraints = [
            models.UniqueConstraint(fields=['version', 'platform'], name='unique_blender_build'),
        ]

    def __str__(self):
        return f"{self.file_name} ({self.status})"
