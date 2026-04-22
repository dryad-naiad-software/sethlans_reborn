# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for worker TLS certificate setup (spec FR-4+).

Post-Waitress-migration, the worker Waitress upstream serves
plaintext on loopback; TLS is terminated by Caddy out-of-band. These
tests exercise the cert-generation / load / BYO helpers in
``sethlans_worker_agent.tls_setup`` directly, without starting a
live HTTPS server. The shared ``build_ssl_context`` helper is still
live (``shared/tls_utils.py``) and is covered by the TLS version
floor test.
"""

import logging
import ssl

from sethlans_worker_agent import config, tls_setup
from shared.cert_utils import (
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)
from shared.tls_utils import build_ssl_context

logger = logging.getLogger(__name__)


class TestWorkerTlsAutoGeneration:
    """FR-4: Worker auto-generates cert on first run.

    Caddy consumes the generated cert/key out-of-band; these tests
    verify the helper produces valid files + fingerprint without
    driving a live server.
    """

    def test_cert_files_exist_after_generation(self, mocker, tmp_path):
        """setup_certificates() writes cert.pem and key.pem in tls_dir."""
        tls_dir = tmp_path / 'tls'
        mocker.patch.object(config, 'TLS_CERT_FILE', '')
        mocker.patch.object(config, 'TLS_KEY_FILE', '')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tls_dir,
        )

        cert_path, key_path, _fp = tls_setup.setup_certificates()

        assert cert_path.exists()
        assert key_path.exists()
        assert cert_path.name == 'cert.pem'
        assert key_path.name == 'key.pem'

    def test_cert_fingerprint_is_valid_hex(self, mocker, tmp_path):
        """Auto-generated cert yields a 64-char lowercase hex fingerprint."""
        tls_dir = tmp_path / 'tls'
        mocker.patch.object(config, 'TLS_CERT_FILE', '')
        mocker.patch.object(config, 'TLS_KEY_FILE', '')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tls_dir,
        )

        _cert_path, _key_path, fp = tls_setup.setup_certificates()

        assert len(fp) == 64
        assert all(c in '0123456789abcdef' for c in fp)


class TestWorkerTlsExistingCert:
    """FR-5: Worker loads existing cert without regenerating."""

    def test_existing_cert_not_overwritten(self, mocker, tmp_path):
        """setup_certificates loads existing cert, does not regenerate."""
        tls_dir = tmp_path / 'tls'
        cert_path = tls_dir / 'cert.pem'
        key_path = tls_dir / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)
        original_bytes = cert_path.read_bytes()

        mocker.patch.object(config, 'TLS_CERT_FILE', '')
        mocker.patch.object(config, 'TLS_KEY_FILE', '')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tls_dir,
        )

        _, _, fp = tls_setup.setup_certificates()
        assert cert_path.read_bytes() == original_bytes
        assert len(fp) == 64


class TestWorkerTlsByoCert:
    """FR-8, FR-9: BYO cert loaded when configured."""

    def test_byo_cert_used_for_server(self, mocker, tmp_path):
        """BYO cert/key are used when both are configured."""
        byo_dir = tmp_path / 'byo'
        byo_cert = byo_dir / 'cert.pem'
        byo_key = byo_dir / 'key.pem'
        generate_self_signed_cert(byo_cert, byo_key)

        mocker.patch.object(config, 'TLS_CERT_FILE', str(byo_cert))
        mocker.patch.object(config, 'TLS_KEY_FILE', str(byo_key))

        cert_path, key_path, fp = tls_setup.setup_certificates()
        assert cert_path == byo_cert
        assert key_path == byo_key
        assert len(fp) == 64

    def test_partial_byo_falls_back(self, mocker, tmp_path, caplog):
        """Only cert_file set (no key_file) -> fallback to auto-gen."""
        tls_dir = tmp_path / 'tls'
        mocker.patch.object(config, 'TLS_CERT_FILE', '/some/cert.pem')
        mocker.patch.object(config, 'TLS_KEY_FILE', '')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tls_dir,
        )

        with caplog.at_level(logging.WARNING):
            _, _, fp = tls_setup.setup_certificates()
        assert len(fp) == 64
        assert any(
            'Both must be provided' in r.message
            for r in caplog.records
        )


class TestWorkerTlsVersionFloor:
    """NF-1: The shared ``build_ssl_context`` helper enforces TLS 1.2.

    The worker's Waitress upstream no longer consumes an SSLContext
    (it serves plaintext on loopback and Caddy terminates TLS), but
    the shared helper in ``shared/tls_utils.py`` remains live and is
    re-exported from ``manager/sethlans_manager/tls_setup.py``. This
    test pins the TLS 1.2 floor contract for the helper itself.
    """

    def test_ssl_context_has_tls_12_minimum(self, tmp_path):
        """build_ssl_context() yields a context with TLS 1.2 minimum."""
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        generate_self_signed_cert(cert_path, key_path)

        ctx = build_ssl_context(cert_path, key_path)

        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_generated_cert_loads_and_has_fingerprint(self, tmp_path):
        """Sanity: generated cert loads + produces a hex fingerprint."""
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        generate_self_signed_cert(cert_path, key_path)

        cert = load_and_validate_cert(cert_path, key_path)
        fp = get_cert_fingerprint(cert)
        assert len(fp) == 64
        assert all(c in '0123456789abcdef' for c in fp)
