# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Management command to check for Blender patch updates.

Iterates over all SupportedBlenderVersion rows, queries the Blender
release page for the latest patch in each series, and updates
resolved_version when a newer patch is found.

This command is idempotent and tolerant of network failures.
Run manually or via an external scheduler (cron, systemd timer).
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from workers.models import SupportedBlenderVersion
from workers.utils.blender_release_parser import resolve_latest_patch

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check for new Blender patch versions and update resolved_version."

    def handle(self, *args, **options):
        versions = SupportedBlenderVersion.objects.all()
        if not versions.exists():
            self.stdout.write("No supported versions configured.")
            return

        now = timezone.now()
        for sv in versions:
            try:
                latest = resolve_latest_patch(sv.series, timeout=10)
            except Exception as e:
                logger.warning(
                    "Failed to check updates for %s: %s",
                    sv.series, e,
                )
                self.stderr.write(
                    f"  WARNING: Could not check {sv.series}: {e}"
                )
                # Still update last_patch_check timestamp
                SupportedBlenderVersion.objects.filter(
                    pk=sv.pk,
                ).update(last_patch_check=now)
                continue

            if latest and latest != sv.resolved_version:
                self.stdout.write(
                    f"  Updated {sv.series}: "
                    f"{sv.resolved_version} -> {latest}"
                )
                SupportedBlenderVersion.objects.filter(
                    pk=sv.pk,
                ).update(
                    resolved_version=latest,
                    last_patch_check=now,
                )
            else:
                SupportedBlenderVersion.objects.filter(
                    pk=sv.pk,
                ).update(last_patch_check=now)
                self.stdout.write(
                    f"  {sv.series}: up to date "
                    f"({sv.resolved_version})"
                )
