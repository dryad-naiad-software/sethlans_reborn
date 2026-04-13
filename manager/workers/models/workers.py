# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.conf import settings
from django.db import models

from ..constants import WorkerStatus


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

    def has_blender_version(self, version_str):
        """Check if this worker has a specific Blender version installed."""
        blender_list = (self.available_tools or {}).get('blender', [])
        return version_str in blender_list

    def __str__(self):
        return self.hostname

    class Meta:
        ordering = ['hostname']
