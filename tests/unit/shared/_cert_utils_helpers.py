# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for the ``shared.cert_utils`` test splits.

Extracted so the generation / atomic-writes / sans test files stay
under the 300-line cap without duplicating the ``_quick_*`` cert
builders. Atomic-writes tests do not import these helpers — they
exercise the helper directly with raw bytes.
"""

from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _quick_key(size=2048):
    """Generate a small RSA key for fast tests."""
    return rsa.generate_private_key(public_exponent=65537, key_size=size)


def _quick_cert(key, days_valid=3650, backdate_hours=1):
    """Build a self-signed test cert."""
    now = datetime.now(timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "test"),
        ]))
        .issuer_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "test"),
        ]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=backdate_hours))
        .not_valid_after(now + timedelta(days=days_valid))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(IPv4Address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )


def _write_pem(cert, key, cert_path, key_path):
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
