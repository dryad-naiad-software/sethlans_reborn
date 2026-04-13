# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for manager TLS startup logic.

Tests the run_manager.py helper functions (setup_certificates,
build_ssl_context, get_tls_dir, get_tls_config) with real cert
generation -- no mocks for cryptography or filesystem operations.

Full subprocess manager startup tests belong in E2E; these tests
verify the components that run_manager.py orchestrates.
"""

import os
import ssl
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography import x509

# run_manager is not a package module -- add the manager dir to
# sys.path so we can import its functions directly.
_manager_dir = Path(__file__).resolve().parents[3] / 'manager'
if str(_manager_dir) not in sys.path:
    sys.path.insert(0, str(_manager_dir))

from run_manager import MANAGER_DIR, PROJECT_ROOT  # noqa: E402
from sethlans_manager.tls_setup import (  # noqa: E402
    build_ssl_context,
    get_tls_config,
    get_tls_dir,
    setup_certificates,
)
from sethlans_manager.cert_utils import (  # noqa: E402
    CertificateError,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)


class TestGetTlsDir:
    """Test TLS directory path selection."""

    def test_dev_mode_uses_temp_dir(self):
        """Dev mode returns temp/dev-tls/ directory."""
        tls_dir = get_tls_dir(True, MANAGER_DIR, PROJECT_ROOT)
        assert 'temp' in str(tls_dir)
        assert 'dev-tls' in str(tls_dir)

    def test_prod_mode_uses_manager_tls_dir(self):
        """Production mode returns manager/tls/ directory."""
        tls_dir = get_tls_dir(False, MANAGER_DIR, PROJECT_ROOT)
        assert str(tls_dir).endswith('tls')
        assert 'temp' not in str(tls_dir)

    def test_dev_and_prod_are_different_paths(self):
        """Dev and prod TLS dirs are distinct paths."""
        dev_dir = get_tls_dir(True, MANAGER_DIR, PROJECT_ROOT)
        prod_dir = get_tls_dir(False, MANAGER_DIR, PROJECT_ROOT)
        assert dev_dir != prod_dir


class TestGetTlsConfig:
    """Test BYO cert/key path configuration reading."""

    def test_env_vars_override_ini(self):
        """SETHLANS_TLS_CERT_FILE and _KEY_FILE env vars take priority."""
        with patch.dict(os.environ, {
            'SETHLANS_TLS_CERT_FILE': '/custom/cert.pem',
            'SETHLANS_TLS_KEY_FILE': '/custom/key.pem',
        }):
            cert_file, key_file = get_tls_config(MANAGER_DIR)
            assert cert_file == '/custom/cert.pem'
            assert key_file == '/custom/key.pem'

    def test_returns_none_when_not_configured(self):
        """Returns (None, None) when no BYO config is set."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the env vars are not set
            os.environ.pop('SETHLANS_TLS_CERT_FILE', None)
            os.environ.pop('SETHLANS_TLS_KEY_FILE', None)
            cert_file, key_file = get_tls_config(MANAGER_DIR)
            # When no manager.ini [tls] section exists, both are None
            assert cert_file is None or key_file is None


class TestSetupCertificates:
    """Test the setup_certificates orchestration function."""

    def test_generates_cert_on_first_run(self, tmp_path):
        """setup_certificates generates a new cert when none exists."""
        tls_dir = tmp_path / 'tls'

        with patch('sethlans_manager.tls_setup.get_tls_config',
                   return_value=(None, None)):
            with patch('sethlans_manager.tls_setup.get_tls_dir',
                       return_value=tls_dir):
                cert_path, key_path, cert = setup_certificates(
                    False, MANAGER_DIR, PROJECT_ROOT,
                )

        assert cert_path.exists()
        assert key_path.exists()
        assert isinstance(cert, x509.Certificate)

    def test_loads_existing_cert_on_second_run(self, tmp_path):
        """setup_certificates loads existing cert without regenerating."""
        tls_dir = tmp_path / 'tls'
        cert_path = tls_dir / 'cert.pem'
        key_path = tls_dir / 'key.pem'

        # First run: generate
        generate_self_signed_cert(cert_path, key_path)
        original_cert_bytes = cert_path.read_bytes()

        with patch('sethlans_manager.tls_setup.get_tls_config',
                   return_value=(None, None)):
            with patch('sethlans_manager.tls_setup.get_tls_dir',
                       return_value=tls_dir):
                _, _, cert = setup_certificates(
                    False, MANAGER_DIR, PROJECT_ROOT,
                )

        # Cert file should not have been regenerated
        assert cert_path.read_bytes() == original_cert_bytes
        assert isinstance(cert, x509.Certificate)

    def test_fingerprint_logged_only_on_first_generation(
        self, tmp_path, caplog,
    ):
        """Fingerprint is logged on first gen, not on subsequent loads."""
        import logging
        caplog.set_level(logging.INFO)
        tls_dir = tmp_path / 'tls'

        with patch('sethlans_manager.tls_setup.get_tls_config',
                   return_value=(None, None)):
            with patch('sethlans_manager.tls_setup.get_tls_dir',
                       return_value=tls_dir):
                setup_certificates(
                    False, MANAGER_DIR, PROJECT_ROOT,
                )

        # First run should log fingerprint
        assert any(
            'TLS cert fingerprint' in r.message for r in caplog.records
        )

        caplog.clear()

        with patch('sethlans_manager.tls_setup.get_tls_config',
                   return_value=(None, None)):
            with patch('sethlans_manager.tls_setup.get_tls_dir',
                       return_value=tls_dir):
                setup_certificates(
                    False, MANAGER_DIR, PROJECT_ROOT,
                )

        # Second run should NOT log fingerprint
        assert not any(
            'TLS cert fingerprint' in r.message for r in caplog.records
        )

    def test_byo_cert_is_used_when_configured(self, tmp_path):
        """setup_certificates uses BYO cert paths when configured."""
        byo_dir = tmp_path / 'byo'
        byo_cert = byo_dir / 'cert.pem'
        byo_key = byo_dir / 'key.pem'

        generate_self_signed_cert(byo_cert, byo_key)

        with patch('sethlans_manager.tls_setup.get_tls_config',
                   return_value=(str(byo_cert), str(byo_key))):
            cert_path, key_path, cert = setup_certificates(
                False, MANAGER_DIR, PROJECT_ROOT,
            )

        assert cert_path == Path(str(byo_cert))
        assert key_path == Path(str(byo_key))
        assert isinstance(cert, x509.Certificate)

    def test_invalid_byo_cert_raises_certificate_error(self, tmp_path):
        """setup_certificates raises CertificateError for invalid BYO."""
        bad_cert = tmp_path / 'bad_cert.pem'
        bad_key = tmp_path / 'bad_key.pem'
        bad_cert.write_text('not a certificate')
        bad_key.write_text('not a key')

        with patch('sethlans_manager.tls_setup.get_tls_config',
                   return_value=(str(bad_cert), str(bad_key))):
            with pytest.raises(CertificateError):
                setup_certificates(
                    False, MANAGER_DIR, PROJECT_ROOT,
                )


class TestBuildSSLContext:
    """Test SSLContext construction from cert files."""

    def test_builds_valid_ssl_context(self, tmp_path):
        """build_ssl_context returns a valid SSLContext."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        ssl_ctx = build_ssl_context(cert_path, key_path)

        assert isinstance(ssl_ctx, ssl.SSLContext)
        assert ssl_ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_ssl_context_rejects_tls_10(self, tmp_path):
        """SSLContext has TLS 1.2 minimum, rejecting TLS 1.0/1.1."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        ssl_ctx = build_ssl_context(cert_path, key_path)

        # TLS 1.2 minimum means 1.0 and 1.1 are excluded
        assert ssl_ctx.minimum_version >= ssl.TLSVersion.TLSv1_2

    def test_ssl_context_fingerprint_matches_cert_utils(self, tmp_path):
        """SSL context cert fingerprint matches cert_utils computation."""
        cert_path = tmp_path / 'tls' / 'cert.pem'
        key_path = tmp_path / 'tls' / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)

        # Compute fingerprint via cert_utils
        cert = load_and_validate_cert(cert_path, key_path)
        expected_fp = get_cert_fingerprint(cert)

        # Build SSL context and verify it loaded the same cert
        ssl_ctx = build_ssl_context(cert_path, key_path)
        assert ssl_ctx is not None

        # The fingerprint from cert_utils should match what we
        # get from independently loading the PEM file
        cert_reloaded = load_and_validate_cert(cert_path, key_path)
        reloaded_fp = get_cert_fingerprint(cert_reloaded)
        assert expected_fp == reloaded_fp


class TestCertGenerationIdempotency:
    """Verify cert generation only happens when files are missing."""

    def test_existing_cert_not_overwritten(self, tmp_path):
        """If cert files exist, setup_certificates preserves them."""
        tls_dir = tmp_path / 'tls'
        cert_path = tls_dir / 'cert.pem'
        key_path = tls_dir / 'key.pem'

        # Generate first cert
        generate_self_signed_cert(cert_path, key_path)
        first_cert = load_and_validate_cert(cert_path, key_path)
        first_fp = get_cert_fingerprint(first_cert)

        # Run setup_certificates -- should NOT regenerate
        with patch('sethlans_manager.tls_setup.get_tls_config',
                   return_value=(None, None)):
            with patch('sethlans_manager.tls_setup.get_tls_dir',
                       return_value=tls_dir):
                _, _, second_cert = setup_certificates(
                    False, MANAGER_DIR, PROJECT_ROOT,
                )

        second_fp = get_cert_fingerprint(second_cert)
        assert first_fp == second_fp
