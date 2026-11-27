# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Enrollment when the agent starts (setup-rebuild FR-WK-6, FR-WK-7, FR-DK-5).

``take_key()`` runs in ``agent.main`` right after the page password is read
from the environment, before any thread or child process starts: it removes
``SETHLANS_WORKER_ENROLLMENT_KEY`` from ``os.environ`` and keeps it in this
module (empty or blank counts as unset), so Caddy, Blender and Unreal never
inherit it. Nothing reads the variable after that.

``wait_until_enrolled(shutdown_event)`` runs on the main thread after the
worker page is up and before the main loop:

- A saved API token: ``config`` is reloaded so the saved values are live;
  the worker is enrolled once ``config.API_TOKEN`` holds the token. When the
  reload did not pick it up (the file was being replaced), it looks again
  after 60 s (interruptible).
- No token and a key taken by ``take_key`` (Docker worker images):
  enroll as before, with the manager from ``SETHLANS_MANAGER_HOST`` and
  ``SETHLANS_MANAGER_PORT`` or else from the manager broadcasts (one manager,
  or the one named by ``SETHLANS_MANAGER_ID``), five tries (0, 1, 3, 9, 27 s
  apart), through the same ``setup_cli.enroll.enroll_pairs`` as
  ``--setup-apply``. A try fails on an enrollment error and also when the
  answer cannot be saved (``config_store`` lock timeout or ``OSError``,
  logged). All tries failing returns ``ENROLL_FAILED`` (exit 4, as before).
  The key is dropped from memory once used.
- No token and no key: log "not enrolled; run setup" and look again every
  60 s; the worker keeps running (its page stays reachable) instead of exiting.

Enrollment sends its request through its own unpinned session; the first
``tls_adapter.get_session()`` after it pins to the saved fingerprint (#306).
The key and the token are never logged.
"""
import importlib
import logging
import os
import socket
from typing import MutableMapping, Optional, Tuple

from sethlans_worker_agent import config, config_store, enrollment_client
from sethlans_worker_agent.multicast_listener import MulticastListener
from sethlans_worker_agent.setup_cli import enroll

logger = logging.getLogger(__name__)

ENV_KEY = 'SETHLANS_WORKER_ENROLLMENT_KEY'
NOT_ENROLLED_TEXT = "not enrolled; run setup"
RETRY_SECONDS = 60
BACKOFF_SECONDS = (0, 1, 3, 9, 27)

ENROLLED = 0
ENROLL_FAILED = 4  # worker exit codes: 5 is ui_password_env.UI_PASSWORD_FAILED
SHUTDOWN = -1

_key: Optional[str] = None  # taken from the environment by take_key; used on the main thread only


def take_key(environ: MutableMapping[str, str] = os.environ) -> bool:
    """Remove ``SETHLANS_WORKER_ENROLLMENT_KEY`` from ``environ`` and keep it; ``True`` when one was set."""
    global _key
    value = environ.pop(ENV_KEY, None)
    _key = value if value and value.strip() else None
    return _key is not None


def _token_saved() -> bool:
    return bool(config_store.get('manager.api_token') or config.API_TOKEN)


def _reload_config() -> None:
    """Re-read ``config`` so the saved token, fingerprint and manager address are live in this process."""
    importlib.reload(config)


def _saved_token_live() -> bool:
    """Reload ``config``; ``True`` when ``config.API_TOKEN`` now holds the saved token."""
    _reload_config()
    return bool(config.API_TOKEN)


def _port(text: Optional[str]) -> int:
    try:
        port = int(text)
    except (TypeError, ValueError):
        return enroll.DEFAULT_PORT
    return port if 1 <= port <= 65535 else enroll.DEFAULT_PORT


def _manager_from_env_or_broadcast() -> Optional[Tuple[str, int]]:
    """``(host, port)`` of the manager to enroll with, or ``None`` (logged)."""
    env_host = os.environ.get('SETHLANS_MANAGER_HOST')
    env_port = os.environ.get('SETHLANS_MANAGER_PORT')
    if env_host and env_port:
        return env_host, _port(env_port)
    announcements = MulticastListener().discover()
    if not announcements:
        logger.error("Unattended enrollment: no SETHLANS_MANAGER_HOST and SETHLANS_MANAGER_PORT, and no "
                     "manager broadcast was received.")
        return None
    wanted = os.environ.get('SETHLANS_MANAGER_ID')
    if wanted and wanted in announcements:
        chosen = announcements[wanted]
    elif len(announcements) == 1:
        chosen = next(iter(announcements.values()))
    else:
        logger.error("Unattended enrollment: several managers were found and SETHLANS_MANAGER_ID does not "
                     "name one of them.")
        return None
    return str(chosen.get('ip') or chosen.get('host') or '127.0.0.1'), _port(chosen.get('port'))


def _enroll_from_env(key: str, shutdown_event) -> int:
    manager = _manager_from_env_or_broadcast()
    if manager is None:
        return ENROLL_FAILED
    host, port = manager
    hostname = socket.gethostname()
    for attempt, delay in enumerate(BACKOFF_SECONDS, start=1):
        if delay:
            logger.info("Retrying unattended enrollment in %ds (attempt %d/%d).", delay, attempt,
                        len(BACKOFF_SECONDS))
            if shutdown_event.wait(delay):
                return SHUTDOWN
        try:
            result, pairs = enroll.enroll_pairs(host, port, key, hostname)
        except enrollment_client.EnrollmentError as error:
            logger.warning("Unattended enrollment attempt %d failed (%s): %s", attempt,
                           enroll.error_code(error), error)
            continue
        try:
            config_store.set_many(pairs)
        except (config_store.ConfigLockTimeoutError, OSError) as error:
            logger.warning("Unattended enrollment attempt %d: the answer could not be saved: %s", attempt, error)
            continue
        logger.info("Unattended enrollment with manager %s at %s:%d succeeded.", result['manager_id'], host, port)
        return ENROLLED
    logger.error("Unattended enrollment failed after %d attempts.", len(BACKOFF_SECONDS))
    return ENROLL_FAILED


def wait_until_enrolled(shutdown_event) -> int:
    """``ENROLLED``, ``ENROLL_FAILED`` (key given, every try failed) or ``SHUTDOWN``."""
    global _key
    while not shutdown_event.is_set():
        if _token_saved():
            _key = None  # enrolled already: the key is not needed, so it is not kept in memory
            if _saved_token_live():
                return ENROLLED
            logger.warning("Worker: a manager token is saved but could not be loaded. Checking again in %d s.",
                           RETRY_SECONDS)
        elif _key:
            key, _key = _key, None
            code = _enroll_from_env(key, shutdown_event)
            if code != ENROLLED:
                return code
            continue  # the saved token is loaded at the top of the loop
        else:
            logger.warning("Worker %s: no manager token is saved and %s is not set. Checking again in %d s.",
                           NOT_ENROLLED_TEXT, ENV_KEY, RETRY_SECONDS)
        shutdown_event.wait(RETRY_SECONDS)
    return SHUTDOWN
