# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Token-based authentication for the worker web UI control endpoints.

Handles token generation, persistence (atomic file write), and validation.
The token is logged to stdout only (never to file handlers) on first generation.
"""

import configparser
import logging
import os
import secrets
import sys
import tempfile

from sethlans_worker_agent import config

logger = logging.getLogger(__name__)

# Module-level cached token
_token = None


def _write_token_to_config(token):
    """
    Persist token to config.ini using atomic file write.

    Writes to a temporary file in the same directory, then uses
    os.replace() to atomically swap it into place.
    """
    ini_path = config.config_file_path
    parser = configparser.ConfigParser()
    if ini_path.exists():
        parser.read(ini_path)

    if not parser.has_section('worker'):
        parser.add_section('worker')
    parser.set('worker', 'ui_token', token)

    ini_dir = str(ini_path.parent)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=ini_dir, suffix='.tmp', prefix='config_'
        )
        try:
            with os.fdopen(fd, 'w') as f:
                parser.write(f)
            os.replace(tmp_path, str(ini_path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.error("Failed to persist UI token to config.ini: %s", e)


def get_token():
    """
    Get the current UI authentication token.

    If no token is configured, generates a new one, persists it
    atomically to config.ini, and logs it to stdout only.
    """
    global _token
    if _token is not None:
        return _token

    # Try loading from config
    token = config.UI_TOKEN
    if token:
        _token = token
        return _token

    # Generate new token
    token = secrets.token_urlsafe(32)
    _token = token

    # Persist atomically
    _write_token_to_config(token)

    # Log to stdout ONLY (not file handlers) so token doesn't persist in logs
    sys.stdout.write(
        f"\n{'=' * 60}\n"
        f"  Worker Web UI Token (save this):\n"
        f"  {token}\n"
        f"{'=' * 60}\n\n"
    )
    sys.stdout.flush()

    return _token


def validate_token(request_token):
    """Check if the provided token matches the stored token."""
    expected = get_token()
    if not expected:
        return False
    # Constant-time comparison via secrets.compare_digest
    return secrets.compare_digest(request_token, expected)
