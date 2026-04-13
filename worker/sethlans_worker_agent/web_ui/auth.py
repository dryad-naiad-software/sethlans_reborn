# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Password-based authentication for the worker web UI control endpoints.

Passwords are hashed with PBKDF2-SHA256 and stored as hex strings
(ui_password_hash + ui_password_salt) in config.ini.  Plaintext
passwords are never persisted.

When no password is configured, a default password ("sethlans") is
accepted. The dashboard prompts the user to change it on first load.
"""

import configparser
import hashlib
import logging
import os
import secrets
import tempfile
import threading

from sethlans_worker_agent import config

logger = logging.getLogger(__name__)

# PBKDF2 parameters
_ITERATIONS = 100_000
_HASH_ALGO = 'sha256'
_SALT_LENGTH = 16  # bytes
_DEFAULT_PASSWORD = 'sethlans'

# Module-level cache (loaded once from config.ini)
# Defense-in-depth lock (NF-9): protects _cached_hash, _cached_salt,
# _cache_loaded. Under normal operation NF-8 (async-only handlers)
# prevents concurrent access, but this lock guards against future
# refactors that might introduce run_in_executor or additional threads.
_cache_lock = threading.Lock()
_cached_hash = None
_cached_salt = None
_cache_loaded = False


def _load_cache():
    """Load hash and salt from config into module-level cache."""
    global _cached_hash, _cached_salt, _cache_loaded
    with _cache_lock:
        if _cache_loaded:
            return
        h = config.UI_PASSWORD_HASH
        s = config.UI_PASSWORD_SALT
        if h and s:
            try:
                _cached_hash = bytes.fromhex(h)
                _cached_salt = bytes.fromhex(s)
            except ValueError:
                logger.warning(
                    "Invalid hex in ui_password_hash/salt. "
                    "Treating as unconfigured."
                )
                _cached_hash = None
                _cached_salt = None
        _cache_loaded = True


def _hash_password(password, salt):
    """Derive a PBKDF2-SHA256 key from a password and salt."""
    return hashlib.pbkdf2_hmac(
        _HASH_ALGO,
        password.encode('utf-8'),
        salt,
        iterations=_ITERATIONS,
    )


def _atomic_write_config(parser, ini_path):
    """Write a ConfigParser to ini_path atomically via temp file."""
    ini_dir = str(ini_path.parent)
    fd, tmp_path = tempfile.mkstemp(
        dir=ini_dir, suffix='.tmp', prefix='config_'
    )
    try:
        with os.fdopen(fd, 'w') as f:
            parser.write(f)
        os.replace(tmp_path, str(ini_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_hash_to_config(pw_hash_hex, salt_hex):
    """
    Persist password hash and salt to config.ini via atomic write.

    Reads existing config, updates the two fields, removes legacy
    plaintext fields, then atomically replaces config.ini.
    """
    ini_path = config.config_file_path
    parser = configparser.ConfigParser()
    if ini_path.exists():
        parser.read(ini_path)

    if not parser.has_section('worker'):
        parser.add_section('worker')
    parser.set('worker', 'ui_password_hash', pw_hash_hex)
    parser.set('worker', 'ui_password_salt', salt_hex)

    # Remove legacy plaintext fields
    for legacy in ('ui_token', 'ui_password'):
        if parser.has_option('worker', legacy):
            parser.remove_option('worker', legacy)

    try:
        _atomic_write_config(parser, ini_path)
    except Exception as e:
        logger.error("Failed to persist UI password hash: %s", e)


def set_password(password):
    """
    Hash a plaintext password and persist the hash + salt.

    Updates the module-level cache so subsequent validate calls work
    immediately without re-reading config.ini.
    """
    global _cached_hash, _cached_salt, _cache_loaded
    salt = os.urandom(_SALT_LENGTH)
    pw_hash = _hash_password(password, salt)

    _write_hash_to_config(pw_hash.hex(), salt.hex())

    with _cache_lock:
        _cached_hash = pw_hash
        _cached_salt = salt
        _cache_loaded = True
    logger.info("Worker Web UI password has been set.")


def is_password_configured():
    """Return True if a password hash is stored in config."""
    _load_cache()
    with _cache_lock:
        return _cached_hash is not None and _cached_salt is not None


def validate_password(request_password):
    """
    Validate a plaintext password against the stored hash.

    When no password hash is configured, accepts the default password.
    Uses constant-time comparison to prevent timing attacks.
    """
    _load_cache()
    with _cache_lock:
        cached_h = _cached_hash
        cached_s = _cached_salt
    if cached_h is None or cached_s is None:
        return secrets.compare_digest(request_password, _DEFAULT_PASSWORD)
    candidate = _hash_password(request_password, cached_s)
    return secrets.compare_digest(candidate, cached_h)


def reset_cache():
    """Reset module-level cache. Used by tests."""
    global _cached_hash, _cached_salt, _cache_loaded
    with _cache_lock:
        _cached_hash = None
        _cached_salt = None
        _cache_loaded = False
