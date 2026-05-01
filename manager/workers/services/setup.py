# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared service helpers consumed by the headless ``setup_auth`` management
command and by the new ``apply_pending_setup`` management command.

Post Spec 2 cluster B1 (FR-DEL5) this module retains only the three
helpers still imported by those two commands.  The wizard now owns
``manager.ini`` writes (FR-M2-INI), DB validation (FR-M2-4), worker UI
password hashing (FR-M2-6), and FFmpeg verification (parts_check).  The
launcher's ``_bootstrap_first_run`` owns SECRET_KEY generation.

Pure Python — no request/response dependencies.
"""

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command

logger = logging.getLogger(__name__)

User = get_user_model()


def generate_enrollment_key() -> str:
    """Generate a Crockford base32 enrollment key and persist it.

    Calls ``enrollment_key.generate_key()`` and writes the result to
    the ``ManagerSettings`` singleton DB row (pk=1).

    Returns the canonical 16-char key string.
    """
    from workers import enrollment_key as ek
    from workers.models import ManagerSettings

    key = ek.generate_key()
    row, _ = ManagerSettings.objects.get_or_create(pk=1)
    row.enrollment_key = key
    row.save(
        update_fields=["enrollment_key", "enrollment_key_updated_at"],
    )
    return key


def create_admin_user(
    username: str, email: str, password: str,
) -> "User":
    """Create a Django superuser after validating the password.

    Raises ``ValidationError`` if the password fails Django validators
    or if a superuser with the given username already exists.

    Returns the created ``User`` instance.
    """
    if User.objects.filter(username=username).exists():
        raise ValidationError("Username already taken.")

    validate_password(password)

    user = User.objects.create_superuser(
        username=username, email=email, password=password,
    )
    logger.info("Superuser '%s' created.", username)
    return user


def apply_migrations() -> None:
    """Run Django migrations programmatically."""
    logger.info("Applying database migrations...")
    call_command("migrate", "--noinput")
    logger.info("Migrations applied successfully.")
