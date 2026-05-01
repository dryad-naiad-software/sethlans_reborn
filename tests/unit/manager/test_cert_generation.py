# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for generate_self_signed_cert in cert_utils.

Tests cert/key file creation, X.509 validity, RSA key size, SAN contents,
validity period, backdate, file permissions, and directory creation.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

from sethlans_manager.cert_utils import generate_self_signed_cert


@pytest.fixture
def mock_cert_env(mocker):
    """Patch NIC addresses, hostname, and platform for cert generation."""
    mocks = {
        'nics': mocker.patch(
            'sethlans_manager.cert_utils._collect_nic_addresses',
            return_value=set()
        ),
        'hostname': mocker.patch(
            'sethlans_manager.cert_utils.socket.gethostname',
            return_value='testhost'
        ),
        'platform': mocker.patch(
            'sethlans_manager.cert_utils.platform.system',
            return_value='Linux'
        ),
    }
    return mocks


class TestGenerateSelfSignedCert:

    def test_creates_cert_and_key_files(
        self, mock_cert_env, tmp_path
    ):
        mock_cert_env['nics'].return_value = {'10.0.0.1'}
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        assert cert_path.exists()
        assert key_path.exists()

    def test_cert_is_valid_x509(self, mock_cert_env, tmp_path):
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        assert isinstance(cert, x509.Certificate)

    def test_key_is_rsa_4096_bit(self, mock_cert_env, tmp_path):
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=None
        )
        assert key.key_size == 4096

    def test_cert_san_includes_required_entries(
        self, mock_cert_env, tmp_path
    ):
        mock_cert_env['nics'].return_value = {'10.0.0.1'}
        mock_cert_env['hostname'].return_value = 'myhost'
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san_ext = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        )
        ip_values = [
            str(ip) for ip in san_ext.value.get_values_for_type(
                x509.IPAddress
            )
        ]
        dns_values = san_ext.value.get_values_for_type(x509.DNSName)

        assert '127.0.0.1' in ip_values
        assert '10.0.0.1' in ip_values
        assert 'localhost' in dns_values
        assert 'myhost' in dns_values
        assert 'myhost.local' in dns_values

    def test_cert_validity_10_years(self, mock_cert_env, tmp_path):
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        now = datetime.now(timezone.utc)
        expected_expiry = now + timedelta(days=3650)
        delta = abs(cert.not_valid_after_utc - expected_expiry)
        assert delta < timedelta(hours=2)

    def test_cert_backdate_1_hour(self, mock_cert_env, tmp_path):
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        now = datetime.now(timezone.utc)
        expected_start = now - timedelta(hours=1)
        delta = abs(cert.not_valid_before_utc - expected_start)
        assert delta < timedelta(minutes=5)

    def test_key_file_permissions_posix(
        self, mock_cert_env, mocker, tmp_path
    ):
        # Atomic write chmods the tempfile BEFORE rename so the final
        # key.pem is born with 0o600 (no world-readable window).
        mock_chmod = mocker.patch(
            'sethlans_manager.cert_utils.os.chmod'
        )
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        mock_chmod.assert_called_once()
        args, _ = mock_chmod.call_args
        chmod_path, chmod_mode = args
        assert chmod_mode == 0o600
        # chmod targets the tmp file, not the final key path.
        assert chmod_path != str(key_path)
        assert str(key_path.parent) in chmod_path

    def test_windows_icacls_called(
        self, mock_cert_env, mocker, tmp_path
    ):
        mock_cert_env['platform'].return_value = 'Windows'
        mocker.patch(
            'sethlans_manager.cert_utils.os.getlogin',
            return_value='testuser'
        )
        mock_subprocess = mocker.patch(
            'sethlans_manager.cert_utils.subprocess.run'
        )
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        tls_dir = str(tmp_path / 'tls')
        mock_subprocess.assert_called_once_with(
            ['icacls', tls_dir, '/inheritance:r',
             '/grant:r', 'testuser:F'],
            check=True, capture_output=True,
        )

    def test_windows_icacls_failure_logs_warning(
        self, mock_cert_env, mocker, tmp_path, caplog
    ):
        import subprocess as sp
        mock_cert_env['platform'].return_value = 'Windows'
        mocker.patch(
            'sethlans_manager.cert_utils.os.getlogin',
            return_value='testuser'
        )
        mocker.patch(
            'sethlans_manager.cert_utils.subprocess.run',
            side_effect=sp.CalledProcessError(1, 'icacls')
        )
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        caplog.set_level(logging.WARNING)
        generate_self_signed_cert(cert_path, key_path)

        assert any('TLS dir ACL' in r.message for r in caplog.records)
        assert cert_path.exists()

    def test_creates_parent_directories(
        self, mock_cert_env, tmp_path
    ):
        cert_path = tmp_path / 'deep' / 'nested' / 'tls' / 'cert.pem'
        key_path = tmp_path / 'deep' / 'nested' / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        assert cert_path.exists()
        assert key_path.exists()

    def test_subject_cn_is_hostname(
        self, mock_cert_env, tmp_path
    ):
        mock_cert_env['hostname'].return_value = 'render-node-7'
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert cn[0].value == 'render-node-7'
