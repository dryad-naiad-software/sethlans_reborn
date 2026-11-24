# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
TLS certificate setup and configuration for the Sethlans Manager.

Handles BYO certificate loading, self-signed certificate generation,
and SSL context construction.  Reads ``[tls]`` from the settings file
(:func:`shared.settings_file.settings_path`). The self-signed pair lives
in the manager's identity folder when frozen
(:func:`shared.frozen_paths.get_identity_dir`); from source in
``SETHLANS_MANAGER_DATA_DIR`` when set, else ``MANAGER_DIR`` -- see
:mod:`sethlans_manager.state_dir`.
"""

import logging
import os
import platform
import stat
import sys
from pathlib import Path

from sethlans_manager.cert_utils import (
    CertificateError,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)
from sethlans_manager.state_dir import source_data_dir
from shared.frozen_paths import get_identity_dir, is_frozen
from shared.settings_file import read_settings, settings_path
from shared.tls_utils import build_ssl_context  # noqa: F401

logger = logging.getLogger(__name__)


def _enforce_key_permissions(key_path: Path) -> None:
    """Restrict the private key file to owner-only (POSIX) / user SID (Windows).

    Phase 5 audit (spec line 640): the Waitress process and the
    launcher-supervised Caddy process both read the TLS private key.
    This helper is invoked after cert/key setup to guarantee the key
    file is owner-only (POSIX mode 0600) or current-user-SID restricted
    (Windows NTFS ACL via icacls). Failures are logged and re-raised
    only on POSIX — on Windows, icacls failures downgrade to a warning
    because some environments (e.g. Docker-for-Windows bind mounts)
    refuse ACL edits.
    """
    if not Path(key_path).exists():
        return
    if platform.system() == 'Windows':
        try:
            import subprocess

            # /inheritance:r removes inherited ACEs, /grant:r re-grants
            # the current user full control (matching the generator's
            # tls-directory pattern).
            subprocess.run(
                [
                    'icacls', str(key_path),
                    '/inheritance:r',
                    '/grant:r', f'{os.getlogin()}:F',
                ],
                check=True,
                capture_output=True,
            )
        except Exception as exc:  # noqa: BLE001 - best effort on Win32
            logger.warning(
                "Could not restrict key-file ACL on Windows "
                "(non-fatal): %s",
                exc,
            )
        return
    try:
        os.chmod(str(key_path), stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.error(
            "Failed to chmod TLS key %s to 0600: %s", key_path, exc,
        )
        raise


def get_config_file_path() -> Path:
    """Return the settings file path (the file ``config_loader`` reads).

    Delegates to :func:`shared.settings_file.settings_path`, the only
    resolver of ``sethlans.ini``.
    """
    return settings_path()


def get_tls_config(manager_dir: Path):
    """Read BYO cert/key paths from env vars or the settings file [tls].

    Returns (cert_file, key_file) -- both None if not configured.
    ``manager_dir`` is unused; it is kept so every TLS helper takes the
    same arguments.
    """
    cert_file = os.getenv('SETHLANS_TLS_CERT_FILE')
    key_file = os.getenv('SETHLANS_TLS_KEY_FILE')

    if cert_file and key_file:
        return (cert_file, key_file)

    try:
        config = read_settings(get_config_file_path())
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

    ``SETHLANS_TLS_DATA_DIR`` wins in every mode.
    Frozen mode: <identity dir>/tls/ -- the settings folder's
                 ``manager/tls``; it never moves with the data folder
    Dev mode:    <project_root>/temp/dev-tls/
    Prod mode:   <state dir>/tls/ -- ``SETHLANS_MANAGER_DATA_DIR`` when
                 set (the Docker image: /data/tls), else <manager_dir>/tls/
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
        return get_identity_dir('manager') / 'tls'
    if dev_mode:
        return project_root / 'temp' / 'dev-tls'
    return source_data_dir(manager_dir) / 'tls'


def setup_certificates(dev_mode, manager_dir, project_root):
    """Generate or load TLS certificates.

    Returns (cert_path, key_path, cert).
    Raises ``CertificateError`` on validation failure.

    After the key file is on disk (whether freshly generated or BYO),
    :func:`_enforce_key_permissions` is called to guarantee owner-only
    restrictions (POSIX mode 0600 / Windows NTFS ACL). Spec Phase 5
    audit: Caddy and Waitress both read this key; we must never rely
    on umask alone.
    """
    byo_cert_file, byo_key_file = get_tls_config(manager_dir)

    if byo_cert_file and byo_key_file:
        cert_path = Path(byo_cert_file)
        key_path = Path(byo_key_file)
        cert = load_and_validate_cert(cert_path, key_path)
        _enforce_key_permissions(key_path)
        return (cert_path, key_path, cert)

    tls_dir = get_tls_dir(dev_mode, manager_dir, project_root)
    cert_path = tls_dir / 'cert.pem'
    key_path = tls_dir / 'key.pem'

    first_generation = not cert_path.exists()
    if first_generation:
        generate_self_signed_cert(cert_path, key_path)

    cert = load_and_validate_cert(cert_path, key_path)
    _enforce_key_permissions(key_path)

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
    '_enforce_key_permissions',
]
