# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/cert_utils.py``."""
import hashlib
import logging
import socket
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from shared.cert_utils import (
    CertificateError,
    _collect_nic_addresses,
    check_cert_expiry_warning,
    enumerate_sans,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)


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


class TestCertificateError:

    def test_is_exception_subclass(self):
        assert issubclass(CertificateError, Exception)

    def test_message_preserved(self):
        err = CertificateError("bad cert")
        assert str(err) == "bad cert"


class TestGenerateSelfSignedCert:

    @pytest.fixture(autouse=True)
    def _mock_env(self, mocker):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value={'10.0.0.1'},
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='testhost',
        )
        mocker.patch(
            'shared.cert_utils.platform.system',
            return_value='Linux',
        )

    def test_creates_cert_and_key_files(self, tmp_path):
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        assert c.exists() and k.exists()

    def test_key_is_rsa_4096(self, tmp_path):
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        key = serialization.load_pem_private_key(
            k.read_bytes(), password=None,
        )
        assert key.key_size == 4096

    def test_cert_valid_10_years(self, tmp_path):
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        cert = x509.load_pem_x509_certificate(c.read_bytes())
        now = datetime.now(timezone.utc)
        delta = abs(cert.not_valid_after_utc - (now + timedelta(days=3650)))
        assert delta < timedelta(hours=2)

    def test_san_includes_localhost_and_127(self, tmp_path):
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        cert = x509.load_pem_x509_certificate(c.read_bytes())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        )
        ips = [str(v) for v in san.value.get_values_for_type(x509.IPAddress)]
        dns = san.value.get_values_for_type(x509.DNSName)
        assert '127.0.0.1' in ips
        assert 'localhost' in dns

    def test_key_file_permissions_posix(self, mocker, tmp_path):
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        mock_chmod.assert_called_once_with(str(k), 0o600)


class TestHostnameCNTruncation:
    """Issue #62: hostnames over 64 chars must not crash cert generation."""

    def _patch_env(self, mocker, hostname):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value={'10.0.0.1'},
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value=hostname,
        )
        mocker.patch(
            'shared.cert_utils.platform.system',
            return_value='Linux',
        )

    def test_long_hostname_truncates_cn_but_san_is_full(
        self, mocker, tmp_path,
    ):
        long_hostname = 'a' * 70
        self._patch_env(mocker, long_hostname)

        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)

        cert = x509.load_pem_x509_certificate(c.read_bytes())
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert len(cn_attrs) == 1
        assert cn_attrs[0].value == 'a' * 64
        assert len(cn_attrs[0].value) == 64

        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        )
        dns = san.value.get_values_for_type(x509.DNSName)
        assert long_hostname in dns
        assert f'{long_hostname}.local' in dns

    def test_64_char_hostname_passes_through(self, mocker, tmp_path):
        boundary_hostname = 'b' * 64
        self._patch_env(mocker, boundary_hostname)

        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)

        cert = x509.load_pem_x509_certificate(c.read_bytes())
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert cn_attrs[0].value == boundary_hostname

        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        )
        dns = san.value.get_values_for_type(x509.DNSName)
        assert boundary_hostname in dns


class TestLoadAndValidateCert:

    def test_valid_cert_loads(self, tmp_path):
        key = _quick_key()
        cert = _quick_cert(key)
        _write_pem(cert, key, tmp_path / 'c.pem', tmp_path / 'k.pem')
        loaded = load_and_validate_cert(
            tmp_path / 'c.pem', tmp_path / 'k.pem',
        )
        assert isinstance(loaded, x509.Certificate)

    def test_expired_cert_raises(self, tmp_path):
        key = _quick_key()
        cert = _quick_cert(key, days_valid=-1, backdate_hours=48)
        _write_pem(cert, key, tmp_path / 'c.pem', tmp_path / 'k.pem')
        with pytest.raises(CertificateError, match='expired'):
            load_and_validate_cert(
                tmp_path / 'c.pem', tmp_path / 'k.pem',
            )

    def test_mismatched_key_raises(self, tmp_path):
        key1 = _quick_key()
        key2 = _quick_key()
        cert = _quick_cert(key1)
        cp = tmp_path / 'c.pem'
        kp = tmp_path / 'k.pem'
        cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        kp.write_bytes(key2.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        with pytest.raises(CertificateError, match='(?i)key.*match'):
            load_and_validate_cert(cp, kp)


class TestGetCertFingerprint:

    def test_returns_64_char_lowercase_hex(self):
        key = _quick_key()
        cert = _quick_cert(key)
        fp = get_cert_fingerprint(cert)
        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in '0123456789abcdef' for c in fp)

    def test_matches_manual_sha256(self):
        key = _quick_key()
        cert = _quick_cert(key)
        der = cert.public_bytes(serialization.Encoding.DER)
        assert get_cert_fingerprint(cert) == hashlib.sha256(der).hexdigest()


class TestCheckCertExpiryWarning:

    def test_warns_within_90_days(self, caplog):
        key = _quick_key()
        cert = _quick_cert(key, days_valid=30)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert any('expires in' in r.message for r in caplog.records)

    def test_no_warning_far_from_expiry(self, caplog):
        key = _quick_key()
        cert = _quick_cert(key, days_valid=3650)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert not any('expires in' in r.message for r in caplog.records)


class TestEnumerateSans:

    def test_includes_localhost_and_loopback(self, mocker):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value=set(),
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='myhost',
        )
        sans = enumerate_sans()
        ips = [str(s.value) for s in sans if isinstance(s, x509.IPAddress)]
        dns = [s.value for s in sans if isinstance(s, x509.DNSName)]
        assert '127.0.0.1' in ips
        assert 'localhost' in dns

    def test_includes_hostname_and_hostname_local(self, mocker):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value=set(),
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='box',
        )
        sans = enumerate_sans()
        dns = [s.value for s in sans if isinstance(s, x509.DNSName)]
        assert 'box' in dns
        assert 'box.local' in dns

    def test_warns_no_routable_ips(self, mocker, caplog):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value=set(),
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='h',
        )
        caplog.set_level(logging.WARNING)
        enumerate_sans()
        assert any(
            'No routable' in r.message for r in caplog.records
        )


class TestCollectNicAddresses:

    def test_returns_ipv4(self, mocker):
        addr = MagicMock(family=socket.AF_INET, address='10.0.0.1')
        mocker.patch(
            'shared.cert_utils.psutil.net_if_addrs',
            return_value={'eth0': [addr]},
        )
        assert '10.0.0.1' in _collect_nic_addresses()

    def test_skips_link_local_ipv6(self, mocker):
        addr = MagicMock(
            family=socket.AF_INET6, address='fe80::1%eth0',
        )
        mocker.patch(
            'shared.cert_utils.psutil.net_if_addrs',
            return_value={'eth0': [addr]},
        )
        result = _collect_nic_addresses()
        assert 'fe80::1' not in result

    def test_empty_interfaces(self, mocker):
        mocker.patch(
            'shared.cert_utils.psutil.net_if_addrs',
            return_value={},
        )
        assert _collect_nic_addresses() == set()
