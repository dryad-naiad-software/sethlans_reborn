# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared steps for the first-start integration tests (setup-rebuild FR-FS).

Not a test module. ``finish_setup`` writes what setup mode leaves in the
per-test ``sethlans.ini``; ``first_start`` runs ``require_first_start`` in
process, as ``run_manager`` does, and returns its exit code.
"""
import hashlib

from django.contrib.auth import get_user_model

from sethlans_manager.first_start import require_first_start
from shared import settings_file

USERNAME = "farmadmin"
PASSWORD = "Farm-Admin-Secret-5d1c9e"
OLD_PASSWORD = "Older-Admin-Secret-8a2f4b"


def finish_setup(ini, mode="manager", first_admin=None):
    """What setup mode leaves: ``[setup]`` finished and, unless kept, ``[first_admin]``."""
    settings_file.set_values(settings_file.SECTION_SETUP, {
        "version": settings_file.SETUP_FORMAT_VERSION, "mode": mode,
        "completed_at": "2026-11-27T10:00:00+00:00",
    }, ini)
    if first_admin is not None:
        settings_file.set_values(settings_file.SECTION_FIRST_ADMIN, first_admin, ini)


def first_admin(password_hash, replace="false"):
    """The ``[first_admin]`` values setup mode writes for ``USERNAME``."""
    return {"username": USERNAME, "email": "admin@example.com", "password_hash": password_hash,
            "replace_password": replace}


def first_start(ini):
    """``require_first_start``; the exit code, or ``None`` when the start goes on."""
    try:
        require_first_start(ini)
    except SystemExit as exc:
        return exc.code
    return None


def has_first_admin(ini) -> bool:
    return settings_file.read_settings(ini).has_section(settings_file.SECTION_FIRST_ADMIN)


def superuser(username, password_hash=None, **flags):
    user = get_user_model().objects.create_superuser(username, "", None, **flags)
    if password_hash is not None:
        user.password = password_hash
        user.save(update_fields=["password"])
    return user


def recorded_digest() -> str:
    """``ManagerSettings.first_admin_applied`` as stored now."""
    from workers.models import ManagerSettings

    return ManagerSettings.objects.get(pk=1).first_admin_applied


def digest_of(password_hash) -> str:
    """The SHA-256 hex digest FR-FS-1 records, computed here, not by the code under test."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()
