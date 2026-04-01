# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker enrollment and token persistence.

Handles:
- Validating the enrollment key configuration.
- Performing enrollment with the manager via the api_handler.
- Atomically persisting the received API token to config.ini.
"""
import configparser
import logging
import os
import tempfile

from sethlans_worker_agent import config
from sethlans_worker_agent import api_handler

logger = logging.getLogger(__name__)

# Minimum recommended enrollment key length.
_MIN_ENROLLMENT_KEY_LENGTH = 32


def validate_enrollment_key():
    """
    Validate the enrollment key length and log a warning if short.

    Returns True if the key is non-empty, False otherwise.
    """
    key = config.ENROLLMENT_KEY
    if not key:
        return False

    if len(key) < _MIN_ENROLLMENT_KEY_LENGTH:
        logger.warning(
            "Enrollment key is shorter than %d characters. "
            "This is insecure. Generate a key with: "
            'python -c "import secrets; '
            'print(secrets.token_urlsafe(32))"',
            _MIN_ENROLLMENT_KEY_LENGTH
        )
    return True


def persist_token_to_config(token):
    """
    Atomically persist the API token to config.ini.

    Uses tempfile + os.replace() to prevent corruption if the
    worker crashes mid-write.
    """
    ini_path = config.config_file_path
    parser = configparser.ConfigParser()
    if ini_path.exists():
        parser.read(ini_path)

    if not parser.has_section('manager'):
        parser.add_section('manager')
    parser.set('manager', 'api_token', token)

    ini_dir = str(ini_path.parent)
    fd, tmp_path = tempfile.mkstemp(
        dir=ini_dir, suffix='.tmp', prefix='config_'
    )
    try:
        with os.fdopen(fd, 'w') as f:
            parser.write(f)
        os.replace(tmp_path, str(ini_path))
        logger.info("API token persisted to config.ini.")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def check_auth_config():
    """
    Check the authentication configuration and determine readiness.

    Returns:
        str: 'token' if API_TOKEN is set, 'enroll' if ENROLLMENT_KEY
             is set, or 'none' if neither is configured.
    """
    if config.API_TOKEN:
        return 'token'
    if validate_enrollment_key():
        return 'enroll'
    return 'none'


def enroll_with_manager(payload):
    """
    Perform enrollment with the manager using the enrollment key.

    On success, persists the token and updates in-memory config.

    Args:
        payload: System info dict for registration.

    Returns:
        int or None: The worker ID on success, None on failure.
    """
    result = api_handler.send_enrollment_heartbeat(payload)
    if not result:
        return None

    token = result.get('token')
    worker_id = result.get('id')

    if not token:
        logger.error(
            "Enrollment response did not include a token."
        )
        return None

    # Persist token atomically before entering the polling loop.
    try:
        persist_token_to_config(token)
    except Exception as e:
        logger.critical(
            "Failed to persist API token to config.ini: %s", e
        )
        return None

    # Update in-memory config so all subsequent requests use it.
    config.API_TOKEN = token
    logger.info("Enrollment successful. Token stored.")

    return worker_id
