# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Management command to check for Blender patch updates.

Iterates over all SupportedBlenderVersion rows, queries the Blender
release page of the first source that lists the series
(``shared.blender_sources.latest_patch``) for its latest patch, and
updates resolved_version when that patch is newer.

``resolved_version`` only ever moves forward by ``(X, Y, Z)`` integer
tuple: ``4.5.10`` replaces ``4.5.9``, while a lower answer (a mirror that
lags behind the official site) is logged and ignored. The write is
conditional on the value read, so a concurrent run that already moved the
row further is never undone.

This command is idempotent and tolerant of network failures.
Run manually or via an external scheduler (cron, systemd timer).
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from shared.blender_sources import is_version, latest_patch, version_key
from workers.models import SupportedBlenderVersion

logger = logging.getLogger(__name__)

# Seconds per release-list request; with several sources one series
# check can take that many times longer.
LOOKUP_TIMEOUT = 10


def is_newer_patch(candidate, current) -> bool:
    """True when ``candidate`` is a full ``X.Y.Z`` above ``current`` by integer tuple.

    A ``current`` that is not a full version (an empty or damaged row) is
    replaced by any valid ``candidate``.
    """
    if not is_version(candidate):
        return False
    if not is_version(current):
        return True
    return version_key(candidate) > version_key(current)


class Command(BaseCommand):
    help = "Check for new Blender patch versions and update resolved_version."

    def handle(self, *args, **options):
        versions = SupportedBlenderVersion.objects.all()
        if not versions.exists():
            self.stdout.write("No supported versions configured.")
            return

        now = timezone.now()
        for sv in versions:
            self._check(sv, now)

    def _check(self, sv, now):
        """Check one supported series; every write is a queryset ``update()``."""
        rows = SupportedBlenderVersion.objects.filter(pk=sv.pk)
        try:
            latest = latest_patch(sv.series, timeout=LOOKUP_TIMEOUT)
        except Exception as e:
            logger.warning("Failed to check updates for %s: %s", sv.series, e)
            self.stderr.write(f"  WARNING: Could not check {sv.series}: {e}")
            # Still update last_patch_check timestamp
            rows.update(last_patch_check=now)
            return

        if is_newer_patch(latest, sv.resolved_version):
            moved = rows.filter(resolved_version=sv.resolved_version).update(
                resolved_version=latest, last_patch_check=now,
            )
            if moved:
                self.stdout.write(
                    f"  Updated {sv.series}: {sv.resolved_version} -> {latest}"
                )
                return
            # Changed meanwhile by another writer: leave its value alone and
            # do not report the stale value read before the check.
            logger.info(
                "Series %s changed during the check; not writing %s.",
                sv.series, latest,
            )
            rows.update(last_patch_check=now)
            self.stdout.write(
                f"  {sv.series}: changed meanwhile by another writer; "
                f"{latest} not written"
            )
            return
        elif latest != sv.resolved_version:
            # BS-REL-10: never move back (a source behind the official site).
            logger.warning(
                "Ignoring Blender %s for series %s: not newer than %s.",
                latest, sv.series, sv.resolved_version,
            )
        rows.update(last_patch_check=now)
        self.stdout.write(f"  {sv.series}: up to date ({sv.resolved_version})")
