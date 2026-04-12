# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Seed the ``ManagerSettings`` singleton row.

This migration is deliberately self-contained: it does NOT import
``workers.enrollment_key`` so that future refactors of that module do
not break old migrations.  The ten-line ``normalize``/``generate_key``
helpers are inlined.

Import behaviour for the legacy ``manager.ini [security] enrollment_key``
value is a migration-only compatibility shim — it exists so that existing
dev deployments that have a pre-spec enrollment key in INI are carried
forward to the DB on the first run.  Normal runtime code does not touch
``manager.ini`` any more.
"""

import configparser
import re
import secrets
import uuid
from pathlib import Path

from django.db import migrations, models

# --- Inlined key helpers (FR-16 requires self-containment) ---------

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 chars
_KEY_LENGTH = 16
_CANONICAL_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{16}$")


def _generate_key() -> str:
    """Return a freshly-generated 16-char Crockford base32 key."""
    return "".join(
        secrets.choice(_CROCKFORD_ALPHABET) for _ in range(_KEY_LENGTH)
    )


def _normalize(key: str) -> str:
    """Canonicalise a Crockford base32 key; raise ValueError on bad input."""
    if not isinstance(key, str):
        raise ValueError("Enrollment key must be a string")
    s = key.strip().upper().replace("-", "")
    for ch in ("I", "L", "O", "U"):
        if ch in s:
            raise ValueError(
                f"Enrollment key contains ambiguous character: {ch}"
            )
    if not _CANONICAL_RE.match(s):
        raise ValueError("Enrollment key format invalid")
    return s


def _read_legacy_ini_key() -> str:
    """Return the legacy ``[security] enrollment_key`` value if valid.

    Returns an empty string on any error (file missing, section missing,
    value missing, value fails normalisation).  The caller generates a
    fresh key in that case.
    """
    # The migration runs from the project BASE_DIR — use the same path
    # discipline as ``run_manager.py``.
    ini_path = Path(__file__).resolve().parents[2] / "manager.ini"
    if not ini_path.exists():
        return ""
    try:
        parser = configparser.ConfigParser()
        parser.read(ini_path)
        raw = parser.get("security", "enrollment_key", fallback="")
        if not raw:
            return ""
        # Legacy INI may hold a secrets.token_urlsafe(32) — that form
        # won't normalise cleanly.  Only accept the value if it already
        # matches our new Crockford format.
        return _normalize(raw)
    except (configparser.Error, ValueError, OSError):
        return ""


def _seed_manager_settings(apps, schema_editor):
    """Create the singleton row with a fresh UUID and enrollment key."""
    ManagerSettings = apps.get_model("workers", "ManagerSettings")
    if ManagerSettings.objects.filter(pk=1).exists():
        return

    enrollment_key = _read_legacy_ini_key() or _generate_key()
    ManagerSettings.objects.create(
        id=1,
        manager_id=str(uuid.uuid4()),
        enrollment_key=enrollment_key,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("workers", "0016_alter_job_render_device"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManagerSettings",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("manager_id", models.CharField(max_length=36)),
                ("enrollment_key", models.CharField(max_length=16)),
                (
                    "enrollment_key_updated_at",
                    models.DateTimeField(auto_now=True),
                ),
            ],
            options={
                "verbose_name": "Manager settings",
                "verbose_name_plural": "Manager settings",
            },
        ),
        migrations.AddConstraint(
            model_name="managersettings",
            constraint=models.CheckConstraint(
                condition=models.Q(id=1),
                name="manager_settings_singleton",
            ),
        ),
        migrations.RunPython(
            _seed_manager_settings,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
