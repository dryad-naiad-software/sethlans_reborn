# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``shared/tls_utils.py``.

Covers build_ssl_context: TLS version floor, cert chain loading,
and error handling for invalid paths.
"""
import ssl
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from shared.tls_utils import build_ssl_context


# --- Helpers ---------------------------------------------------------------

def _make_cert_and_key(tmp_path):
    """Generate a cert + key pair on disk and return (cert_path, key_path)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "test"),
        ]))
        .issuer_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "test"),
        ]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(IPv4Address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return cert_path, key_path


# --- build_ssl_context -----------------------------------------------------

class TestBuildSslContext:

    def test_returns_ssl_context(self, tmp_path):
        cert_path, key_path = _make_cert_and_key(tmp_path)
        ctx = build_ssl_context(cert_path, key_path)
        assert isinstance(ctx, ssl.SSLContext)

    def test_minimum_version_is_tls_1_2(self, tmp_path):
        cert_path, key_path = _make_cert_and_key(tmp_path)
        ctx = build_ssl_context(cert_path, key_path)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_protocol_is_tls_server(self, tmp_path):
        cert_path, key_path = _make_cert_and_key(tmp_path)
        ctx = build_ssl_context(cert_path, key_path)
        assert ctx.protocol == ssl.PROTOCOL_TLS_SERVER

    def test_accepts_string_paths(self, tmp_path):
        cert_path, key_path = _make_cert_and_key(tmp_path)
        ctx = build_ssl_context(str(cert_path), str(key_path))
        assert isinstance(ctx, ssl.SSLContext)

    def test_nonexistent_cert_path_raises(self, tmp_path):
        key_path = tmp_path / 'key.pem'
        key_path.write_text('dummy')
        with pytest.raises((ssl.SSLError, FileNotFoundError)):
            build_ssl_context(tmp_path / 'no_cert.pem', key_path)

    def test_invalid_cert_content_raises(self, tmp_path):
        cert_path = tmp_path / 'cert.pem'
        cert_path.write_text('not a certificate')
        key_path = tmp_path / 'key.pem'
        key_path.write_text('not a key')
        with pytest.raises(ssl.SSLError):
            build_ssl_context(cert_path, key_path)

    def test_invalid_key_path_raises(self, tmp_path):
        cert_path, _ = _make_cert_and_key(tmp_path)
        bad_key = tmp_path / 'bad_key.pem'
        bad_key.write_text('not a real key')
        with pytest.raises(ssl.SSLError):
            build_ssl_context(cert_path, bad_key)

    def test_mismatched_cert_and_key_raises(self, tmp_path):
        """A valid cert with a different valid key should fail."""
        cert_path, _ = _make_cert_and_key(tmp_path)
        # Generate a second key
        key2 = rsa.generate_private_key(
            public_exponent=65537, key_size=2048,
        )
        other_key = tmp_path / 'other_key.pem'
        other_key.write_bytes(key2.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        with pytest.raises(ssl.SSLError):
            build_ssl_context(cert_path, other_key)

    def test_nonexistent_paths_raise(self, tmp_path):
        with pytest.raises((ssl.SSLError, FileNotFoundError)):
            build_ssl_context(
                tmp_path / 'missing.pem',
                tmp_path / 'missing_key.pem',
            )
