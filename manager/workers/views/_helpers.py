# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cross-view helpers shared between the heartbeat and enrollment endpoints.

Kept deliberately small — anything specific to one endpoint stays in the
endpoint's own module.
"""

import logging

from django.db import IntegrityError

logger = logging.getLogger(__name__)


def client_ip(request) -> str:
    """Return the client's IP address.

    Preference order: the first entry in ``X-Forwarded-For`` (if a proxy
    is in front of the manager) falling back to ``REMOTE_ADDR``.  An
    empty string is returned if neither is present.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _is_reusable_worker_user(user, hostname) -> bool:
    """Return True if a pre-existing *user* may back worker *hostname*.

    Only an account that looks like one enrollment created is reused:
    not staff, not superuser, no usable password, and not already
    linked to a different worker.  Anything else (an operator account
    that happens to be called ``worker_<hostname>``, say) must never be
    handed a worker token, or the enrolling party would hold a staff
    credential (issue #220).
    """
    if user.is_staff or user.is_superuser or user.has_usable_password():
        return False
    profile = getattr(user, 'worker_profile', None)
    return profile is None or profile.hostname == hostname


def get_or_create_worker_user(User, username, hostname):
    """Create or fetch the ``User`` row that backs a worker agent.

    Tries ``username`` first, then ``username_1`` ... ``username_9``.
    A candidate is skipped when creating it races another enrollment
    (``IntegrityError``) or when it already exists but is not a
    reusable worker account (see ``_is_reusable_worker_user``).  So
    enrolling hostname ``X`` while an operator account ``worker_X``
    exists gives the worker ``worker_X_1`` instead; re-enrolling ``X``
    later reuses ``worker_X_1``, because it is linked to ``X``.

    Returns ``None`` if all ten candidates are unusable; callers treat
    that as a 500.
    """
    for attempt in range(10):
        candidate = (
            username if attempt == 0
            else f"{username}_{attempt}"
        )
        try:
            user, created = User.objects.get_or_create(
                username=candidate,
                defaults={"is_staff": False, "is_active": True},
            )
        except IntegrityError:
            continue
        if created:
            user.set_unusable_password()
            user.save()
            return user
        if _is_reusable_worker_user(user, hostname):
            return user
        logger.warning(
            "Not reusing existing user '%s' for worker '%s': it is "
            "staff/superuser, has a password, or belongs to another "
            "worker.",
            candidate, hostname,
        )
    logger.error(
        "Failed to create user for worker '%s' after 10 attempts.",
        hostname,
    )
    return None
