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


def get_or_create_worker_user(User, username, hostname):
    """Create or fetch the ``User`` row that backs a worker agent.

    Retries with a numeric suffix on ``IntegrityError`` up to ten times
    — this handles the rare race where two concurrent enrollments pick
    the same base ``username``.  Returns ``None`` if all ten attempts
    fail; callers treat that as a 500.
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
            if created:
                user.set_unusable_password()
                user.save()
            return user
        except IntegrityError:
            continue
    logger.error(
        "Failed to create user for worker '%s' after 10 retries.",
        hostname,
    )
    return None
