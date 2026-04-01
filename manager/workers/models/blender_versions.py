# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
SupportedBlenderVersion model for managing Blender versions on the farm.

This model replaces the static SupportedBlenderVersions enum with a
database-driven version registry. Each row represents a major.minor
series (e.g., "4.2") and its latest resolved patch version (e.g., "4.2.19").
"""

from django.db import models, transaction


class SupportedBlenderVersion(models.Model):
    """
    Represents a supported Blender version series on the render farm.

    Exactly one row must have ``is_default=True`` at any time. The
    ``save()`` override enforces this constraint using only queryset
    ``.update()`` calls to avoid recursive signal triggers.
    """
    major = models.IntegerField(
        help_text="Major version number, derived from series.",
    )
    minor = models.IntegerField(
        help_text="Minor version number, derived from series.",
    )
    series = models.CharField(
        max_length=10,
        unique=True,
        help_text="Major.minor series string, e.g. '4.2' or '5.0'.",
    )
    resolved_version = models.CharField(
        max_length=20,
        help_text="Latest known patch for this series, e.g. '4.2.19'.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Exactly one row must be the default version.",
    )
    added_at = models.DateTimeField(auto_now_add=True)
    last_patch_check = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['major', 'minor']
        verbose_name = "Supported Blender Version"
        verbose_name_plural = "Supported Blender Versions"

    def __str__(self):
        return f"{self.series} ({self.resolved_version})"

    @transaction.atomic
    def save(self, *args, **kwargs):
        # Populate major/minor from series
        parts = self.series.split('.')
        self.major, self.minor = int(parts[0]), int(parts[1])

        if self.is_default:
            SupportedBlenderVersion.objects.exclude(
                pk=self.pk,
            ).update(is_default=False)

        super().save(*args, **kwargs)

        # Ensure at least one default exists (queryset .update() only)
        if not SupportedBlenderVersion.objects.filter(
            is_default=True,
        ).exists():
            if SupportedBlenderVersion.objects.exists():
                SupportedBlenderVersion.objects.order_by(
                    '-major', '-minor',
                )[:1].update(is_default=True)
