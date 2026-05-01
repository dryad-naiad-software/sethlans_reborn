# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/cert_utils.py``."""
import hashlib
import logging
import os
import socket
import stat
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import shared.cert_utils as cert_utils
from shared.cert_utils import (
    CertificateError,
    _atomic_write_bytes,
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


class TestAtomicWriteBytes:
    """Atomic-write helper used by ``generate_self_signed_cert``.

    Spec 2 introduced a short-lived ``apply_pending_setup`` subprocess
    that calls ``setup_certificates`` via
    ``prime_runtime_state_for_auto_enroll``. A SIGKILL between the key
    write and the cert write previously left a partial pair on disk
    that the next apply attempt could not recover from. The atomic
    helper writes via tempfile + fsync + ``os.replace`` so that a crash
    mid-write leaves the target file either fully present (with the
    new bytes) or fully absent (untouched prior contents).
    """

    def test_writes_bytes_to_target(self, tmp_path):
        target = tmp_path / "out.bin"
        _atomic_write_bytes(target, b"hello world")
        assert target.read_bytes() == b"hello world"

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "out.bin"
        target.write_bytes(b"old contents")
        _atomic_write_bytes(target, b"new contents")
        assert target.read_bytes() == b"new contents"

    def test_uses_os_replace_for_atomicity(self, tmp_path, mocker):
        """The rename MUST go through ``os.replace`` (atomic on
        POSIX/NTFS). If a future refactor regresses to direct
        ``write_bytes``, this test catches it."""
        mock_replace = mocker.patch(
            'shared.cert_utils.os.replace',
            wraps=os.replace,
        )
        target = tmp_path / "out.bin"
        _atomic_write_bytes(target, b"data")
        assert mock_replace.call_count == 1
        # Second positional arg is the final destination.
        _src, dst = mock_replace.call_args.args
        assert dst == str(target)

    def test_posix_chmod_on_tempfile_before_rename(self, mocker, tmp_path):
        """When a posix_mode is set, chmod hits the TMP path (not the
        final path) so the final file is born with the restrictive
        mode — no world-readable window between rename and chmod."""
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Linux',
        )
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        target = tmp_path / "key.pem"
        _atomic_write_bytes(target, b"PEM", posix_mode=0o600)
        mock_chmod.assert_called_once()
        args, _ = mock_chmod.call_args
        chmod_path, chmod_mode = args
        assert chmod_mode == 0o600
        assert chmod_path != str(target)

    def test_no_chmod_on_windows(self, mocker, tmp_path):
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Windows',
        )
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        target = tmp_path / "key.pem"
        _atomic_write_bytes(target, b"PEM", posix_mode=0o600)
        mock_chmod.assert_not_called()

    def test_no_chmod_when_mode_is_none(self, mocker, tmp_path):
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Linux',
        )
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        target = tmp_path / "cert.pem"
        _atomic_write_bytes(target, b"PEM")
        mock_chmod.assert_not_called()

    def test_crash_mid_write_leaves_target_untouched(
        self, mocker, tmp_path,
    ):
        """If ``os.replace`` fails (simulating a crash after the temp
        file is written but before the rename completes), the target
        file MUST NOT exist as a partial copy."""
        mocker.patch(
            'shared.cert_utils.os.replace',
            side_effect=OSError("simulated crash"),
        )
        target = tmp_path / "out.bin"
        with pytest.raises(OSError, match="simulated crash"):
            _atomic_write_bytes(target, b"data")
        assert not target.exists()
        # Tempfile cleanup happens in the except branch — no leftover
        # files in the parent directory.
        leftovers = [
            p for p in tmp_path.iterdir() if p.name != target.name
        ]
        assert leftovers == []

    def test_crash_does_not_clobber_existing_target(
        self, mocker, tmp_path,
    ):
        target = tmp_path / "out.bin"
        target.write_bytes(b"original")
        mocker.patch(
            'shared.cert_utils.os.replace',
            side_effect=OSError("simulated crash"),
        )
        with pytest.raises(OSError):
            _atomic_write_bytes(target, b"new bytes")
        # Original contents preserved — the rename never landed.
        assert target.read_bytes() == b"original"


class TestGenerateSelfSignedCertAtomicity:
    """Spec 2 concurrency MED — verify ``generate_self_signed_cert``
    routes both writes through the atomic helper."""

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

    def test_both_files_use_atomic_helper(self, mocker, tmp_path):
        spy = mocker.patch(
            'shared.cert_utils._atomic_write_bytes',
            wraps=cert_utils._atomic_write_bytes,
        )
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        # Two atomic writes: one per PEM file.
        assert spy.call_count == 2
        # Key first, cert second — preserves "if cert write fails,
        # cert.pem.exists() returns False and the next run regenerates"
        # recovery semantics.
        first_path = spy.call_args_list[0].args[0]
        second_path = spy.call_args_list[1].args[0]
        assert first_path == k
        assert second_path == c
        # Key is written with 0o600; cert has no posix mode.
        assert spy.call_args_list[0].kwargs.get('posix_mode') == 0o600
        assert spy.call_args_list[1].kwargs.get('posix_mode') is None

    def test_both_files_present_after_success(self, tmp_path):
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        assert c.exists()
        assert k.exists()
        assert c.stat().st_size > 0
        assert k.stat().st_size > 0

    @pytest.mark.skipif(
        os.name == 'nt',
        reason='POSIX-only stat.st_mode bits',
    )
    def test_key_born_with_0o600_on_posix(self, mocker, tmp_path):
        # Use the real platform.system (skip the autouse Linux mock for
        # this test by re-patching).
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Linux',
        )
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        mode = stat.S_IMODE(k.stat().st_mode)
        assert mode == 0o600

    def test_no_partial_cert_when_cert_write_fails(
        self, mocker, tmp_path,
    ):
        """If the cert write fails after the key landed, ``cert.pem``
        MUST NOT exist as a partial / empty file. The next apply run
        sees the missing cert and regenerates a fresh keypair."""
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'

        original = cert_utils._atomic_write_bytes
        call_count = {'n': 0}

        def fake_atomic(path, data, posix_mode=None):
            call_count['n'] += 1
            if call_count['n'] == 1:
                # First call: write the key normally.
                original(path, data, posix_mode=posix_mode)
            else:
                # Second call: simulate a crash mid-cert-write.
                raise OSError("simulated cert-write crash")

        mocker.patch(
            'shared.cert_utils._atomic_write_bytes',
            side_effect=fake_atomic,
        )
        with pytest.raises(OSError, match="simulated cert-write crash"):
            generate_self_signed_cert(c, k)
        # Key landed but cert did NOT — clean recovery state.
        assert k.exists()
        assert not c.exists()
