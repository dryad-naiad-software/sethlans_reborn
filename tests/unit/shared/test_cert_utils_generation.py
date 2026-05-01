# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cert-generation tests for ``shared/cert_utils.py``.

Covers ``CertificateError``, ``generate_self_signed_cert``,
hostname/CN truncation (issues #62 and #121),
``load_and_validate_cert``, and ``get_cert_fingerprint``. The atomic
write helper and ``generate_self_signed_cert`` atomicity wiring live
in ``test_cert_utils_atomic_writes.py``; SANs / NIC / expiry tests
in ``test_cert_utils_sans.py``.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

from shared.cert_utils import (
    CertificateError,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)

from ._cert_utils_helpers import _quick_cert, _quick_key, _write_pem


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
        # Atomic write chmods the tempfile BEFORE rename so the final
        # key.pem is born with 0o600 (no world-readable window). The
        # cert path takes no chmod (posix_mode=None).
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        # Exactly one chmod, with mode 0o600, against a tmp file in
        # the key's parent directory (NOT the final key path — the
        # tmp path is renamed into place after chmod).
        mock_chmod.assert_called_once()
        args, _ = mock_chmod.call_args
        chmod_path, chmod_mode = args
        assert chmod_mode == 0o600
        assert chmod_path != str(k)
        assert str(k.parent) in chmod_path


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

    def test_100_char_hostname_generation_does_not_raise(
        self, mocker, tmp_path,
    ):
        """Issue #121: macOS CI hostnames exceed 64 chars.

        A 100-char hostname (e.g. ``sjc22-bt149-...7EB795E643.local``)
        must not crash cert generation, must produce a <=64-char CN, and
        must preserve the full untruncated hostname in the SAN DNSName
        list. This is what modern TLS clients rely on for verification.
        """
        # Shape mirrors macOS CI runner hostnames (length 100 here).
        prefix = 'sjc22-bt149-github-actions-runner-'
        suffix = '.local'
        filler_len = 100 - len(prefix) - len(suffix)
        long_hostname = prefix + ('x' * filler_len) + suffix
        assert len(long_hostname) == 100
        self._patch_env(mocker, long_hostname)

        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'

        # Must not raise
        generate_self_signed_cert(c, k)

        cert = x509.load_pem_x509_certificate(c.read_bytes())

        # CN is truncated to 64 chars
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert len(cn_attrs) == 1
        assert len(cn_attrs[0].value) == 64
        assert cn_attrs[0].value == long_hostname[:64]

        # Full hostname is preserved untruncated in SAN
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        )
        dns = san.value.get_values_for_type(x509.DNSName)
        assert long_hostname in dns
        # Full hostname length preserved in SAN even though > 64
        matched = [d for d in dns if d == long_hostname]
        assert len(matched) == 1
        assert len(matched[0]) == 100


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
