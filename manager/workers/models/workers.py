# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.conf import settings
from django.db import models

from shared.unreal.unc import shares_match

from ..constants import WorkerStatus


def reported_unreal_builds(caps) -> frozenset:
    """Return the supported Unreal builds a capabilities dict reports (FR-RT-5).

    Empty unless ``caps['enabled'] is True``; otherwise the ``build`` of
    every ``installs`` entry whose ``supported is True``.  The claim check
    and the routing note both use this, so their semantics cannot drift.
    """
    if not isinstance(caps, dict) or caps.get('enabled') is not True:
        return frozenset()
    installs = caps.get('installs')
    if not isinstance(installs, list):
        return frozenset()
    return frozenset(
        install['build'] for install in installs
        if isinstance(install, dict) and install.get('supported') is True
        and isinstance(install.get('build'), str)
    )


def reports_share_reachable(caps, share) -> bool:
    """Return whether a worker reports the manager's share as reachable (FR-RT-5).

    True only when ``caps`` is enabled, reports ``share_reachable is
    True``, and its ``share`` (the share the **worker** is configured
    with) equals the manager's ``share`` under ``shares_match``.
    """
    return (
        isinstance(caps, dict)
        and caps.get('enabled') is True
        and caps.get('share_reachable') is True
        and shares_match(caps.get('share', ''), share)
    )


class Worker(models.Model):
    """
    Represents a single rendering machine in the distributed system.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='worker_profile',
    )
    hostname = models.CharField(max_length=255, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    os = models.CharField(max_length=100, blank=True, default='')
    last_seen = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    available_tools = models.JSONField(default=dict, blank=True)
    ui_url = models.URLField(max_length=255, null=True, blank=True)
    cpu_name = models.CharField(max_length=255, blank=True, default='')
    gpu_name = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=WorkerStatus.choices,
        default=WorkerStatus.OFFLINE,
    )
    schedule_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Informational: worker's local claim_window schedule, "
            "reported via heartbeat."
        ),
    )
    ui_cert_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text=(
            "Self-reported SHA-256 fingerprint of the worker's UI "
            "TLS certificate. NOT verified by the manager -- do not "
            "use for security-critical decisions."
        ),
    )
    unreal_capabilities = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Self-reported Unreal Engine capabilities (installs, configured "
            "Sethlans share, reachability). NOT verified by the manager -- "
            "used for routing only."
        ),
    )
    gpu_memory = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Self-reported per-GPU memory reading. NOT verified by the "
            "manager -- for visibility and the routing note only."
        ),
    )
    boot_id = models.CharField(
        max_length=32,
        blank=True,
        default='',
        help_text=(
            "Id of the worker process that last registered (boot-time "
            "reconciliation). Not exposed through the API."
        ),
    )

    def has_blender_version(self, version_str):
        """Check if this worker has a specific Blender version installed."""
        blender_list = (self.available_tools or {}).get('blender', [])
        return version_str in blender_list

    def has_unreal_build(self, build):
        """Check if this worker reports a supported Unreal Engine ``build``."""
        return build in reported_unreal_builds(self.unreal_capabilities)

    def __str__(self):
        return self.hostname

    class Meta:
        ordering = ['hostname']
