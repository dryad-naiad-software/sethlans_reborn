# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for cert_utils round-trip workflows.

Exercises real NIC enumeration (no mocks), full cert generation
round-trips, BYO cert simulation, fingerprint stability, and
SSL context construction.
"""

import hashlib
import platform
import socket
import ssl

import psutil
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from sethlans_manager.cert_utils import (
    CertificateError,
    enumerate_sans,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)


class TestCertRoundTrip:
    """Full generate -> load -> validate -> fingerprint round-trip."""

    def test_generate_load_validate_fingerprint(self, tmp_path):
        """Generate a cert, load it, validate it, compute fingerprint."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        # Load and validate
        cert = load_and_validate_cert(cert_path, key_path)
        assert isinstance(cert, x509.Certificate)

        # Compute fingerprint
        fp = get_cert_fingerprint(cert)
        assert len(fp) == 64
        assert fp == fp.lower()

        # Verify fingerprint matches manual DER hash
        der_bytes = cert.public_bytes(serialization.Encoding.DER)
        expected_fp = hashlib.sha256(der_bytes).hexdigest()
        assert fp == expected_fp

    def test_generated_cert_has_valid_san_entries(self, tmp_path):
        """Generated cert includes required SAN entries from this host."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)
        cert = load_and_validate_cert(cert_path, key_path)

        san_ext = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        )
        ip_strs = [
            str(ip) for ip in
            san_ext.value.get_values_for_type(x509.IPAddress)
        ]
        dns_names = san_ext.value.get_values_for_type(x509.DNSName)

        # Must always include loopback and localhost
        assert '127.0.0.1' in ip_strs
        assert 'localhost' in dns_names

        # Must include hostname and hostname.local
        hostname = socket.gethostname()
        assert hostname in dns_names
        assert f'{hostname}.local' in dns_names

    def test_generated_cert_subject_cn_matches_hostname(self, tmp_path):
        """Generated cert CN is the machine hostname."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)
        cert = load_and_validate_cert(cert_path, key_path)

        from cryptography.x509.oid import NameOID
        cn_attrs = cert.subject.get_attributes_for_oid(
            NameOID.COMMON_NAME
        )
        assert len(cn_attrs) == 1
        assert cn_attrs[0].value == socket.gethostname()

    def test_generated_key_is_rsa_4096(self, tmp_path):
        """Generated private key is RSA 4096-bit."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=None,
        )
        assert key.key_size == 4096


class TestRealNicEnumeration:
    """Test SAN enumeration with real system NIC data (no mocks)."""

    def test_enumerate_sans_returns_entries(self):
        """enumerate_sans returns a non-empty list on any system."""
        sans = enumerate_sans()
        assert len(sans) > 0

    def test_enumerate_sans_includes_loopback(self):
        """enumerate_sans always includes 127.0.0.1 and localhost."""
        sans = enumerate_sans()
        ip_strs = [
            str(s.value) for s in sans
            if isinstance(s, x509.IPAddress)
        ]
        dns_names = [
            s.value for s in sans
            if isinstance(s, x509.DNSName)
        ]
        assert '127.0.0.1' in ip_strs
        assert 'localhost' in dns_names

    def test_enumerate_sans_includes_hostname(self):
        """enumerate_sans includes the machine hostname as DNS name."""
        sans = enumerate_sans()
        dns_names = [
            s.value for s in sans
            if isinstance(s, x509.DNSName)
        ]
        hostname = socket.gethostname()
        assert hostname in dns_names
        assert f'{hostname}.local' in dns_names

    def test_enumerate_sans_matches_psutil_nic_ips(self):
        """All IPv4 addresses from psutil appear in enumerate_sans."""
        sans = enumerate_sans()
        san_ip_strs = {
            str(s.value) for s in sans
            if isinstance(s, x509.IPAddress)
        }

        for _iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    assert addr.address in san_ip_strs, (
                        f"IPv4 {addr.address} missing from SANs"
                    )


class TestBYOCertSimulation:
    """Generate a cert, then load it as if it were a BYO cert."""

    def test_generate_then_load_as_byo(self, tmp_path):
        """A generated cert can be loaded as a BYO cert."""
        gen_dir = tmp_path / 'generated'
        gen_dir.mkdir()
        cert_path = gen_dir / 'cert.pem'
        key_path = gen_dir / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        # Simulate BYO: copy cert/key to a different location
        byo_dir = tmp_path / 'byo'
        byo_dir.mkdir()
        byo_cert = byo_dir / 'my_cert.pem'
        byo_key = byo_dir / 'my_key.pem'
        byo_cert.write_bytes(cert_path.read_bytes())
        byo_key.write_bytes(key_path.read_bytes())

        # Load and validate as BYO
        cert = load_and_validate_cert(byo_cert, byo_key)
        assert isinstance(cert, x509.Certificate)

    def test_byo_fingerprint_matches_original(self, tmp_path):
        """BYO cert fingerprint matches the original generated cert."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)
        original_cert = load_and_validate_cert(cert_path, key_path)
        original_fp = get_cert_fingerprint(original_cert)

        # Load from same files (BYO path)
        byo_cert = load_and_validate_cert(cert_path, key_path)
        byo_fp = get_cert_fingerprint(byo_cert)

        assert original_fp == byo_fp

    def test_byo_mismatched_key_rejected(self, tmp_path):
        """BYO cert with wrong key is rejected."""
        # Generate two separate certs
        dir1 = tmp_path / 'cert1'
        dir1.mkdir()
        dir2 = tmp_path / 'cert2'
        dir2.mkdir()

        generate_self_signed_cert(dir1 / 'cert.pem', dir1 / 'key.pem')
        generate_self_signed_cert(dir2 / 'cert.pem', dir2 / 'key.pem')

        # Try to load cert1 with key2 -- should fail
        with pytest.raises(CertificateError, match='(?i)key.*match'):
            load_and_validate_cert(
                dir1 / 'cert.pem', dir2 / 'key.pem',
            )


class TestFingerprintStability:
    """Verify fingerprint is deterministic across load cycles."""

    def test_fingerprint_stable_across_loads(self, tmp_path):
        """Same cert loaded twice produces identical fingerprints."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        cert1 = load_and_validate_cert(cert_path, key_path)
        fp1 = get_cert_fingerprint(cert1)

        cert2 = load_and_validate_cert(cert_path, key_path)
        fp2 = get_cert_fingerprint(cert2)

        assert fp1 == fp2

    def test_different_certs_different_fingerprints(self, tmp_path):
        """Two separately generated certs have different fingerprints."""
        dir1 = tmp_path / 'tls1'
        dir2 = tmp_path / 'tls2'

        generate_self_signed_cert(dir1 / 'cert.pem', dir1 / 'key.pem')
        generate_self_signed_cert(dir2 / 'cert.pem', dir2 / 'key.pem')

        cert1 = load_and_validate_cert(
            dir1 / 'cert.pem', dir1 / 'key.pem',
        )
        cert2 = load_and_validate_cert(
            dir2 / 'cert.pem', dir2 / 'key.pem',
        )

        fp1 = get_cert_fingerprint(cert1)
        fp2 = get_cert_fingerprint(cert2)

        assert fp1 != fp2

    def test_fingerprint_format_is_64_char_lowercase_hex(self, tmp_path):
        """Fingerprint is a 64-char lowercase hex string."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)
        cert = load_and_validate_cert(cert_path, key_path)
        fp = get_cert_fingerprint(cert)

        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in '0123456789abcdef' for c in fp)


class TestSSLContextFromGeneratedCert:
    """Test that generated certs can be used to build an SSLContext."""

    def test_ssl_context_loads_cert_with_tls_12_minimum(self, tmp_path):
        """Generated cert loads into SSLContext with TLS 1.2 floor."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.load_cert_chain(str(cert_path), str(key_path))

        assert ssl_ctx.minimum_version >= ssl.TLSVersion.TLSv1_2

    def test_key_file_readable_on_current_platform(self, tmp_path):
        """Generated key file is a valid PEM RSA key."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        key_bytes = key_path.read_bytes()
        assert key_bytes.startswith(b'-----BEGIN RSA PRIVATE KEY-----')

        if platform.system() != 'Windows':
            mode = key_path.stat().st_mode & 0o777
            assert mode == 0o600
