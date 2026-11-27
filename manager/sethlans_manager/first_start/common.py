# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the first-start steps share: the failure type and the admin checks.

The username and email rules live in :mod:`sethlans_manager.admin_fields`,
which setup mode uses too.
"""
from django.contrib.auth import get_user_model

from sethlans_manager.admin_fields import MAX_USERNAME_LENGTH, clean_username, email_problems, username_problems

__all__ = [
    "FirstStartError", "MAX_USERNAME_LENGTH", "clean_username", "email_problems",
    "inactive_admin_exists", "username_problems", "usable_admin_exists",
]


class FirstStartError(Exception):
    """A first-start step failed; ``code`` is the exit code (11 or 12).

    ``reason`` is one line for the log, stderr and the launcher. It never
    holds a password or a password hash.
    """

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def usable_admin_exists() -> bool:
    """True when an active superuser exists (the same rule as setup mode's probe)."""
    return get_user_model().objects.filter(is_superuser=True, is_active=True).exists()


def inactive_admin_exists() -> bool:
    """True when a superuser exists that is not active."""
    return get_user_model().objects.filter(is_superuser=True, is_active=False).exists()
