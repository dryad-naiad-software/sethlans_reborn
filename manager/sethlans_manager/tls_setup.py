# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
TLS certificate setup and configuration for the Sethlans Manager.

Handles BYO certificate loading, self-signed certificate generation,
and SSL context construction.  Reads config from ``manager.ini``
(data dir when frozen, ``MANAGER_DIR`` when running from source).
"""

import logging
import os
import sys
from pathlib import Path

from sethlans_manager.cert_utils import (
    CertificateError,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)
from shared.frozen_paths import get_data_dir, is_frozen
from shared.tls_utils import build_ssl_context  # noqa: F401

logger = logging.getLogger(__name__)


def get_config_file_path(manager_dir: Path) -> Path:
    """Return the manager.ini path (data dir when frozen)."""
    if is_frozen():
        return get_data_dir('manager') / 'manager.ini'
    return manager_dir / 'manager.ini'


def get_tls_config(manager_dir: Path):
    """Read BYO cert/key paths from env vars or manager.ini [tls].

    Returns (cert_file, key_file) -- both None if not configured.
    """
    import configparser

    cert_file = os.getenv('SETHLANS_TLS_CERT_FILE')
    key_file = os.getenv('SETHLANS_TLS_KEY_FILE')

    if cert_file and key_file:
        return (cert_file, key_file)

    try:
        config = configparser.ConfigParser()
        config_file_path = get_config_file_path(manager_dir)
        if config_file_path.exists():
            config.read(config_file_path)
            if not cert_file and config.has_option('tls', 'cert_file'):
                cert_file = config.get('tls', 'cert_file') or None
            if not key_file and config.has_option('tls', 'key_file'):
                key_file = config.get('tls', 'key_file') or None
    except Exception as e:
        print(
            f"[WARNING] Could not read TLS config: {e}", file=sys.stderr,
        )

    if bool(cert_file) != bool(key_file):
        configured = 'cert_file' if cert_file else 'key_file'
        missing = 'key_file' if cert_file else 'cert_file'
        print(
            f"[WARNING] TLS {configured} is set but {missing} is not. "
            f"Both must be provided for a custom certificate. "
            f"Falling back to self-signed cert.",
            file=sys.stderr,
        )
        return (None, None)

    return (cert_file, key_file)


def get_tls_dir(dev_mode: bool, manager_dir: Path, project_root: Path):
    """Return the TLS directory path based on mode.

    Frozen mode: <data_dir>/tls/
    Dev mode:    <project_root>/temp/dev-tls/
    Prod mode:   <manager_dir>/tls/
    """
    env_override = os.getenv('SETHLANS_TLS_DATA_DIR')
    if env_override:
        p = Path(env_override)
        if not p.is_absolute():
            raise ValueError(
                f"SETHLANS_TLS_DATA_DIR must be an absolute path, "
                f"got: {env_override}"
            )
        return p
    if is_frozen():
        return get_data_dir('manager') / 'tls'
    if dev_mode:
        return project_root / 'temp' / 'dev-tls'
    return manager_dir / 'tls'


def setup_certificates(dev_mode, manager_dir, project_root):
    """Generate or load TLS certificates.

    Returns (cert_path, key_path, cert).
    Raises ``CertificateError`` on validation failure.
    """
    byo_cert_file, byo_key_file = get_tls_config(manager_dir)

    if byo_cert_file and byo_key_file:
        cert_path = Path(byo_cert_file)
        key_path = Path(byo_key_file)
        cert = load_and_validate_cert(cert_path, key_path)
        return (cert_path, key_path, cert)

    tls_dir = get_tls_dir(dev_mode, manager_dir, project_root)
    cert_path = tls_dir / 'cert.pem'
    key_path = tls_dir / 'key.pem'

    first_generation = not cert_path.exists()
    if first_generation:
        generate_self_signed_cert(cert_path, key_path)

    cert = load_and_validate_cert(cert_path, key_path)

    if first_generation:
        fingerprint = get_cert_fingerprint(cert)
        logger.info("TLS cert fingerprint: %s", fingerprint)

    return (cert_path, key_path, cert)


# Re-export CertificateError for callers that imported it via tls_setup.
__all__ = [
    'CertificateError',
    'build_ssl_context',
    'get_config_file_path',
    'get_tls_config',
    'get_tls_dir',
    'setup_certificates',
]
