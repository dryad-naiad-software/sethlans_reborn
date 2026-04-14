# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Internal ORM-based enrollment of the local worker.

Used by the setup wizard when the chosen topology is ``manager_worker``.
Bypasses the HTTP enrollment endpoint entirely — all database operations
happen inside a single atomic transaction.
"""

import configparser
import logging
import socket

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.authtoken.models import Token

from sethlans_manager import runtime_state
from workers.models import Worker
from workers.views._helpers import get_or_create_worker_user

logger = logging.getLogger(__name__)

User = get_user_model()


def auto_enroll_local_worker(hostname: str | None = None) -> dict:
    """Enroll the co-located worker without an HTTP round-trip.

    Creates (or reuses) the Django user, ``Worker`` row, and DRF token,
    then returns the envelope the worker agent needs to configure itself.

    Parameters
    ----------
    hostname:
        Defaults to ``socket.gethostname()`` when *None*.

    Returns
    -------
    dict
        Keys: ``api_token``, ``cert_fingerprint``, ``manager_id``,
        ``manager_url``, ``hostname``.

    Raises
    ------
    RuntimeError
        If ``runtime_state.manager_id`` or
        ``runtime_state.cert_fingerprint`` is *None* (server not fully
        initialised).
    """
    if runtime_state.manager_id is None:
        raise RuntimeError(
            "Cannot auto-enroll: runtime_state.manager_id is None "
            "(server not fully initialised)."
        )
    if runtime_state.cert_fingerprint is None:
        raise RuntimeError(
            "Cannot auto-enroll: runtime_state.cert_fingerprint is "
            "None (TLS certificate not loaded)."
        )

    if hostname is None:
        hostname = socket.gethostname()

    username = f"worker_{hostname}"

    with transaction.atomic():
        user = get_or_create_worker_user(User, username, hostname)
        if user is None:
            raise RuntimeError(
                f"Failed to create worker user for '{hostname}'."
            )

        try:
            worker = Worker.objects.select_for_update().get(
                hostname=hostname,
            )
            worker.user = user
            worker.is_active = True
            worker.save(update_fields=["user", "is_active"])
        except Worker.DoesNotExist:
            Worker.objects.create(
                hostname=hostname, user=user, is_active=True,
            )

        token, _ = Token.objects.get_or_create(user=user)

    # Build the manager URL using loopback — this is a local worker.
    ini_path = settings.BASE_DIR / "manager.ini"
    config = configparser.ConfigParser()
    if ini_path.exists():
        config.read(ini_path)
    port = config.get("server", "port", fallback="8080")
    manager_url = f"https://127.0.0.1:{port}"

    logger.info(
        "Auto-enrolled local worker '%s' (token=%s...)",
        hostname,
        token.key[:8],
    )

    return {
        "api_token": token.key,
        "cert_fingerprint": runtime_state.cert_fingerprint,
        "manager_id": runtime_state.manager_id,
        "manager_url": manager_url,
        "hostname": hostname,
    }


def check_local_worker_enrolled() -> dict:
    """Verify the local worker has a Worker row and Token.

    Returns a verification check dict for the setup verify endpoint.
    """
    hostname = socket.gethostname()
    try:
        worker = Worker.objects.select_related('user').filter(
            hostname=hostname,
        ).first()
        if worker is None:
            return {
                "name": "local_worker", "passed": False,
                "error": f"No worker registered for '{hostname}'",
            }
        if worker.user is None:
            return {
                "name": "local_worker", "passed": False,
                "error": "Worker has no associated user",
            }
        token_exists = Token.objects.filter(user=worker.user).exists()
        if not token_exists:
            return {
                "name": "local_worker", "passed": False,
                "error": "Worker user has no API token",
            }
        return {
            "name": "local_worker", "passed": True, "error": None,
        }
    except Exception as exc:
        return {
            "name": "local_worker", "passed": False,
            "error": str(exc),
        }
