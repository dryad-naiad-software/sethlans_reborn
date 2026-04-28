# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""TLS certificate generation for the wizard's HTTPS server.

Implements FR-W4 / FR-W5 / FR-CFG3 of the Spec 1 wizard scaffold:

* Self-signed RSA-2048 X.509 cert with SAN entries for the wizard host
  (mirrors ``shared/cert_utils.enumerate_sans``).
* 7-day cert lifetime with a 24-hour reuse-buffer (SEC-MED-4).
* Atomic write (write temp file in same dir, fsync, ``os.replace``).
* ``chmod 600`` on POSIX, Windows ACL tightened to the current user via
  the same helper used by ``launcher/tray_ipc.py``.
* Public surface: ``ensure_cert(data_dir)`` and ``delete_cert(data_dir)``.

This module is pure-function with no top-level mutable state. The
output paths live under ``<data_dir>/wizard/`` per FR-CFG3.
"""

from __future__ import annotations

import logging
import os
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from shared.cert_utils import enumerate_sans

# shared/file_acls owns the canonical Windows ACL helper for chmod-600-
# equivalent filesystem objects; reuse it per FR-IPC6 / FR-W4 ("same
# helper used by launcher/tray_ipc.py").
from shared.file_acls import tighten_acls_windows

logger = logging.getLogger(__name__)

CERT_FILENAME = "tls.crt"
KEY_FILENAME = "tls.key"
WIZARD_SUBDIR = "wizard"

# SEC-MED-4: cert lifetime 7 days, reuse buffer 24 hours.
_CERT_LIFETIME = timedelta(days=7)
_REUSE_MIN_REMAINING = timedelta(hours=24)


def _wizard_dir(data_dir: Path) -> Path:
    return Path(data_dir) / WIZARD_SUBDIR


def _cert_paths(data_dir: Path) -> Tuple[Path, Path]:
    wd = _wizard_dir(data_dir)
    return wd / CERT_FILENAME, wd / KEY_FILENAME


def _restrict_perms(path: Path) -> None:
    """Restrict *path* to the owning user (``chmod 600`` semantics).

    POSIX: ``os.chmod(path, 0o600)``. Windows: delegate to the tray
    ACL helper, which falls back to a warning if pywin32 is unavailable.
    """
    if platform.system() == "Windows":
        tighten_acls_windows(path)
    else:
        try:
            os.chmod(str(path), 0o600)
        except OSError as exc:
            logger.warning("Could not chmod %s: %s", path, exc)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically.

    Writes to ``<path>.tmp`` in the SAME directory, fsyncs, then
    ``os.replace`` to the destination so the swap is atomic on every
    supported platform (FR-W4: "Temp files MUST be in the same directory
    as the destination so os.replace is atomic").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    _restrict_perms(path)


def _build_cert(key: rsa.RSAPrivateKey) -> x509.Certificate:
    """Build a self-signed cert valid for ``_CERT_LIFETIME`` from now."""
    import socket as _socket
    hostname = _socket.gethostname()
    cn_value = hostname[:64] if len(hostname) > 64 else hostname
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn_value),
    ])
    now = datetime.now(timezone.utc)
    sans = enumerate_sans()
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + _CERT_LIFETIME)
        .add_extension(
            x509.SubjectAlternativeName(sans),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )


def _generate_cert_pair(cert_path: Path, key_path: Path) -> None:
    """Generate a fresh keypair + cert and write both atomically.

    Cert is written BEFORE key per FR-W4. Both via ``os.replace``. Both
    files restricted to the current user.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = _build_cert(key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # FR-W4: cert written before key.
    _atomic_write_bytes(cert_path, cert_pem)
    _atomic_write_bytes(key_path, key_pem)
    logger.info(
        "Generated wizard TLS certificate (valid until %s): %s",
        cert.not_valid_after_utc.isoformat(),
        cert_path,
    )


def _existing_pair_is_reusable(cert_path: Path, key_path: Path) -> bool:
    """Return True if both files exist, parse, key matches cert, and the
    cert has at least 24h remaining (SEC-MED-4 / FR-W5).
    """
    if not (cert_path.exists() and key_path.exists()):
        return False
    try:
        cert_pem = cert_path.read_bytes()
        key_pem = key_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
        key = serialization.load_pem_private_key(key_pem, password=None)
    except Exception as exc:  # noqa: BLE001 — parse failures must trigger regen
        logger.info(
            "Existing wizard cert/key unreadable (%s); regenerating", exc,
        )
        return False
    cert_pub = cert.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_pub != key_pub:
        logger.info("Wizard key does not match cert; regenerating")
        return False
    now = datetime.now(timezone.utc)
    if cert.not_valid_before_utc > now:
        logger.info("Wizard cert not yet valid; regenerating")
        return False
    if cert.not_valid_after_utc < now + _REUSE_MIN_REMAINING:
        logger.info(
            "Wizard cert expires within 24h (at %s); regenerating",
            cert.not_valid_after_utc.isoformat(),
        )
        return False
    return True


def _delete_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not delete %s: %s", path, exc)


def ensure_cert(data_dir: Path) -> Tuple[Path, Path]:
    """Return ``(cert_path, key_path)`` for the wizard's HTTPS server.

    Reuses the existing pair when valid (FR-W5). Otherwise deletes any
    stale files and writes a fresh pair atomically (FR-W4). Always
    creates the parent ``<data_dir>/wizard/`` directory.

    Per FR-W5: reuse iff both files exist, both parse, key matches cert,
    ``not_valid_before <= now()``, ``not_valid_after >= now() + 24h``.
    """
    data_dir = Path(data_dir)
    wd = _wizard_dir(data_dir)
    wd.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = _cert_paths(data_dir)
    if _existing_pair_is_reusable(cert_path, key_path):
        logger.info("Reusing existing wizard TLS cert: %s", cert_path)
        return cert_path, key_path
    # Drop stale files defensively before regenerating.
    _delete_quiet(cert_path)
    _delete_quiet(key_path)
    _generate_cert_pair(cert_path, key_path)
    return cert_path, key_path


def delete_cert(data_dir: Path) -> None:
    """Delete ``tls.crt`` and ``tls.key`` from ``<data_dir>/wizard/``.

    Called by the wizard during the FR-W10 / FR-W17 clean-exit path
    (skipped on `.wizard_reject` so the next run can reuse the cert).
    Idempotent — missing files are not an error.
    """
    cert_path, key_path = _cert_paths(Path(data_dir))
    _delete_quiet(cert_path)
    _delete_quiet(key_path)
