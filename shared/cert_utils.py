# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Certificate generation and validation utilities for Sethlans.

Provides self-signed X.509 certificate generation with SAN enumeration,
BYO certificate validation, fingerprint computation, and expiry checks.

Shared by both the manager and the worker agent.
"""
import hashlib
import logging
import os
import platform
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)


class CertificateError(Exception):
    """Raised when certificate loading or validation fails."""


def _collect_nic_addresses():
    """Collect IP addresses from all active NICs via psutil.

    Returns a set of IP address strings. Skips link-local IPv6.
    """
    ip_addresses = set()
    for _iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ip_addresses.add(addr.address)
            elif addr.family == socket.AF_INET6:
                # Skip link-local IPv6 (fe80::) — they require scope
                # IDs that most TLS clients don't handle well.
                raw = addr.address.split('%')[0]
                if not raw.startswith('fe80:'):
                    ip_addresses.add(raw)
    return ip_addresses


def _build_san_entries(ip_addresses, dns_names):
    """Convert IP address strings and DNS names into x509 SAN entries."""
    from ipaddress import ip_address

    sans = []
    for ip_str in sorted(ip_addresses):
        try:
            sans.append(x509.IPAddress(ip_address(ip_str)))
        except ValueError:
            logger.debug("Skipping invalid IP for SAN: %s", ip_str)
    for name in sorted(dns_names):
        sans.append(x509.DNSName(name))
    return sans


def enumerate_sans():
    """Return a list of x509 SAN entries for all detectable network
    identities on this host.

    Includes all NIC IP addresses, 127.0.0.1, localhost, the short
    hostname, and hostname.local.
    """
    # Always include loopback and localhost
    ip_addresses = {"127.0.0.1"}
    hostname = socket.gethostname()
    dns_names = {"localhost", hostname, f"{hostname}.local"}

    # Add all NIC addresses
    ip_addresses.update(_collect_nic_addresses())

    # Warn if no routable (non-loopback) IPs found
    routable = ip_addresses - {"127.0.0.1", "::1"}
    if not routable:
        logger.warning(
            "No routable network interfaces detected. Remote workers "
            "and browsers will not be able to connect until a network "
            "is available and the cert is regenerated."
        )

    return _build_san_entries(ip_addresses, dns_names)


def generate_self_signed_cert(cert_path, key_path):
    """Generate a self-signed X.509 cert and RSA 4096-bit private key.

    SANs are enumerated from all active NICs via psutil.net_if_addrs(),
    plus localhost, 127.0.0.1, hostname, and hostname.local.
    Validity: not_valid_before = now - 1 hour, not_valid_after = now + 10 years.
    Key file written with mode 0o600 on POSIX; icacls restriction on Windows.
    """
    cert_path = Path(cert_path)
    key_path = Path(key_path)

    # Create tls directory
    tls_dir = cert_path.parent
    tls_dir.mkdir(parents=True, exist_ok=True)

    # Restrict directory permissions on Windows
    if platform.system() == 'Windows':
        try:
            subprocess.run(
                ['icacls', str(tls_dir), '/inheritance:r',
                 '/grant:r', f'{os.getlogin()}:F'],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, OSError) as e:
            logger.warning("Could not set TLS dir ACL: %s", e)

    # Generate RSA 4096-bit key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )

    # Build certificate
    hostname = socket.gethostname()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
    ])

    now = datetime.now(timezone.utc)
    sans = enumerate_sans()

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(sans),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    # Write key file
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(key_pem)

    # Set key file permissions on POSIX
    if platform.system() != 'Windows':
        os.chmod(str(key_path), 0o600)

    # Write cert file
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path.write_bytes(cert_pem)

    logger.info("Generated self-signed TLS certificate: %s", cert_path)


def load_and_validate_cert(cert_path, key_path):
    """Load PEM cert + key, validate key matches cert, check expiry.

    Returns the loaded x509.Certificate on success.
    Raises CertificateError with a specific message on failure.
    """
    cert_path = Path(cert_path)
    key_path = Path(key_path)

    # Parse certificate
    try:
        cert_pem = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
    except Exception as e:
        raise CertificateError(
            f"Failed to parse certificate at {cert_path}: {e}"
        )

    # Parse private key
    try:
        key_pem = key_path.read_bytes()
        key = serialization.load_pem_private_key(key_pem, password=None)
    except Exception as e:
        raise CertificateError(
            f"Failed to parse private key at {key_path}: {e}"
        )

    # Verify key matches cert
    cert_pub = cert.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_pub != key_pub:
        raise CertificateError(
            "Private key does not match certificate public key"
        )

    # Check expiry
    now = datetime.now(timezone.utc)
    if cert.not_valid_after_utc < now:
        raise CertificateError(
            f"Certificate expired on {cert.not_valid_after_utc}"
        )

    return cert


def get_cert_fingerprint(cert):
    """Return lowercase hex SHA-256 fingerprint of the DER-encoded cert."""
    der_bytes = cert.public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der_bytes).hexdigest()


def check_cert_expiry_warning(cert):
    """Log a warning if cert expires within 90 days."""
    now = datetime.now(timezone.utc)
    days_remaining = (cert.not_valid_after_utc - now).days
    if days_remaining <= 90:
        logger.warning(
            "TLS certificate expires in %d days (on %s). "
            "Consider regenerating or replacing it.",
            days_remaining, cert.not_valid_after_utc.date(),
        )
