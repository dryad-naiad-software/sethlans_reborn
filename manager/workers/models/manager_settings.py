# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``ManagerSettings`` singleton model.

Holds runtime-mutable manager identity and credentials that must persist
across restarts but that must not live in the INI file (rotation must
be writable without a server restart).  Only one row may ever exist —
``id`` is constrained to ``1`` at the database level and ``save()``
forces ``pk=1`` to block the "forgot to use ``get_or_create``" foot-gun.

Seeded in migration ``0017_managersettings.py`` with a freshly-generated
Crockford base32 enrollment key and a UUID4 manager_id.  The migration
is self-contained — it does NOT import ``workers.enrollment_key`` so
future refactors of that module do not break old migrations.
"""

from django.db import models


class ManagerSettings(models.Model):
    """Singleton row holding runtime-mutable manager settings.

    Always has ``pk=1``.  Never create additional rows.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    manager_id = models.CharField(max_length=36)
    enrollment_key = models.CharField(max_length=16)
    enrollment_key_updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Manager settings"
        verbose_name_plural = "Manager settings"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1),
                name="manager_settings_singleton",
            ),
        ]

    def save(self, *args, **kwargs):
        # Force ``pk=1`` so an accidental ``ManagerSettings().save()``
        # never clobbers the singleton with a row at a different id.
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"ManagerSettings(manager_id={self.manager_id})"
