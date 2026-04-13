# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
TLS certificate setup and configuration for the Sethlans Worker Agent.

Handles BYO certificate loading, self-signed certificate generation,
fingerprint computation, and fingerprint file persistence. The worker's
TLS setup mirrors the manager's ``tls_setup.py`` pattern but uses the
worker's config system (``config.get_config_value``) and data directory.
"""

import logging
import os
import platform
import tempfile
from pathlib import Path

from sethlans_worker_agent import config, config_store
from shared.cert_utils import CertificateError  # noqa: F401 — re-exported
from shared.cert_utils import (
    check_cert_expiry_warning,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)

logger = logging.getLogger(__name__)

# Module-level frozen fingerprint — set once by setup_certificates(),
# read via get_ui_cert_fingerprint(). Write-once; if certificate
# rotation is added in the future, synchronization will be needed.
_ui_cert_fingerprint = ''


def get_tls_config():
    """Read BYO cert/key paths from env vars or config.

    Returns (cert_file, key_file) — both None if not configured.
    """
    cert_file = config.TLS_CERT_FILE or None
    key_file = config.TLS_KEY_FILE or None

    if bool(cert_file) != bool(key_file):
        configured = 'cert_file' if cert_file else 'key_file'
        missing = 'key_file' if cert_file else 'cert_file'
        logger.warning(
            "TLS %s is set but %s is not. Both must be provided "
            "for a custom certificate. Falling back to self-signed cert.",
            configured, missing,
        )
        return (None, None)

    return (cert_file, key_file)


def get_tls_dir():
    """Return the TLS directory path.

    Default: ``<worker_data_dir>/tls/``
    Override: ``SETHLANS_WORKER_TLS_DATA_DIR`` env var (must be absolute).
    """
    env_override = os.getenv('SETHLANS_WORKER_TLS_DATA_DIR')
    if env_override:
        p = Path(env_override)
        if not p.is_absolute():
            raise ValueError(
                f"SETHLANS_WORKER_TLS_DATA_DIR must be an absolute path, "
                f"got: {env_override}"
            )
        return p
    return config_store.get_data_dir() / 'tls'


def _write_fingerprint_file(fingerprint, tls_dir):
    """Write fingerprint to ui_cert_fingerprint.txt atomically.

    Uses write-to-temp + ``os.replace()`` for atomicity.
    Inherits the TLS directory's restricted permissions.
    """
    final_path = tls_dir / 'ui_cert_fingerprint.txt'
    fd, tmp_path = tempfile.mkstemp(
        dir=str(tls_dir), suffix='.tmp', prefix='fp_',
    )
    try:
        os.write(fd, fingerprint.encode('ascii'))
        os.close(fd)
        # Inherit restrictive permissions from tls_dir on POSIX
        if platform.system() != 'Windows':
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, str(final_path))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def setup_certificates():
    """Generate or load TLS certificates.

    Returns ``(cert_path, key_path, fingerprint)``.
    Raises ``CertificateError`` on validation failure.
    Sets the module-level fingerprint via ``_ui_cert_fingerprint``.
    """
    global _ui_cert_fingerprint

    byo_cert_file, byo_key_file = get_tls_config()

    if byo_cert_file and byo_key_file:
        cert_path = Path(byo_cert_file)
        key_path = Path(byo_key_file)
        cert = load_and_validate_cert(cert_path, key_path)
        check_cert_expiry_warning(cert)
        fingerprint = get_cert_fingerprint(cert)
        _ui_cert_fingerprint = fingerprint
        tls_dir = cert_path.parent
        _write_fingerprint_file(fingerprint, tls_dir)
        logger.info("Using BYO TLS certificate: %s", cert_path)
        return (cert_path, key_path, fingerprint)

    tls_dir = get_tls_dir()
    cert_path = tls_dir / 'cert.pem'
    key_path = tls_dir / 'key.pem'

    first_generation = not cert_path.exists()
    if first_generation:
        generate_self_signed_cert(cert_path, key_path)

    cert = load_and_validate_cert(cert_path, key_path)
    check_cert_expiry_warning(cert)

    fingerprint = get_cert_fingerprint(cert)
    _ui_cert_fingerprint = fingerprint
    _write_fingerprint_file(fingerprint, tls_dir)

    if first_generation:
        logger.info("Worker TLS cert fingerprint: %s", fingerprint)

    return (cert_path, key_path, fingerprint)


def get_ui_cert_fingerprint():
    """Return the frozen UI cert fingerprint.

    The fingerprint is write-once: set during ``setup_certificates()``
    and never mutated afterward. If certificate rotation is added in
    the future, synchronization will be needed.
    """
    return _ui_cert_fingerprint
