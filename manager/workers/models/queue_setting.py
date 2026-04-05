# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.db import models


class QueueSetting(models.Model):
    """
    Singleton model for global queue configuration.

    Only one row exists (pk=1). Controls whether workers can claim new jobs.
    """
    queue_paused = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # Prevent deletion of the singleton

    class Meta:
        verbose_name = "Queue Setting"
        verbose_name_plural = "Queue Settings"

    @classmethod
    def get_instance(cls):
        """Return the singleton instance, creating it if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
