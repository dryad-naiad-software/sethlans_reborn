# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for certificate validation, fingerprint, and expiry in cert_utils.

Covers load_and_validate_cert, get_cert_fingerprint,
check_cert_expiry_warning, and BYO cert validation scenarios.
"""
import hashlib
import logging

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from sethlans_manager.cert_utils import (
    CertificateError,
    check_cert_expiry_warning,
    get_cert_fingerprint,
    load_and_validate_cert,
)

from tests.unit.manager.conftest import (
    build_test_cert, generate_test_keypair, write_cert_and_key,
)


# --- load_and_validate_cert ---

class TestLoadAndValidateCert:

    def test_valid_cert_and_key_loads_ok(self, tmp_path):
        key = generate_test_keypair()
        cert = build_test_cert(key)
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        write_cert_and_key(cert, key, cert_path, key_path)

        loaded = load_and_validate_cert(cert_path, key_path)
        assert isinstance(loaded, x509.Certificate)

    def test_expired_cert_raises_certificate_error(self, tmp_path):
        key = generate_test_keypair()
        cert = build_test_cert(key, days_valid=-1, backdate_hours=48)
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        write_cert_and_key(cert, key, cert_path, key_path)

        with pytest.raises(CertificateError, match='expired'):
            load_and_validate_cert(cert_path, key_path)

    def test_mismatched_key_raises_certificate_error(self, tmp_path):
        key1 = generate_test_keypair()
        key2 = generate_test_keypair()
        cert = build_test_cert(key1)
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        cert_path.write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        key_path.write_bytes(key2.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

        with pytest.raises(CertificateError, match='(?i)key.*match'):
            load_and_validate_cert(cert_path, key_path)

    def test_invalid_cert_file_raises_certificate_error(self, tmp_path):
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        cert_path.write_text('not a certificate')
        key_path.write_text('not a key')

        with pytest.raises(CertificateError, match='parse certificate'):
            load_and_validate_cert(cert_path, key_path)

    def test_invalid_key_file_raises_certificate_error(self, tmp_path):
        key = generate_test_keypair()
        cert = build_test_cert(key)
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        cert_path.write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        key_path.write_text('not a valid key')

        with pytest.raises(CertificateError, match='parse private key'):
            load_and_validate_cert(cert_path, key_path)

    def test_accepts_path_objects_and_strings(self, tmp_path):
        key = generate_test_keypair()
        cert = build_test_cert(key)
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'key.pem'
        write_cert_and_key(cert, key, cert_path, key_path)

        loaded = load_and_validate_cert(
            str(cert_path), str(key_path)
        )
        assert isinstance(loaded, x509.Certificate)


# --- get_cert_fingerprint ---

class TestGetCertFingerprint:

    def test_returns_lowercase_64_char_hex(self):
        key = generate_test_keypair()
        cert = build_test_cert(key)
        fp = get_cert_fingerprint(cert)
        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in '0123456789abcdef' for c in fp)

    def test_deterministic_for_same_cert(self):
        key = generate_test_keypair()
        cert = build_test_cert(key)
        fp1 = get_cert_fingerprint(cert)
        fp2 = get_cert_fingerprint(cert)
        assert fp1 == fp2

    def test_matches_manual_sha256_computation(self):
        key = generate_test_keypair()
        cert = build_test_cert(key)
        der_bytes = cert.public_bytes(serialization.Encoding.DER)
        expected = hashlib.sha256(der_bytes).hexdigest()
        assert get_cert_fingerprint(cert) == expected

    def test_different_certs_have_different_fingerprints(self):
        key1 = generate_test_keypair()
        key2 = generate_test_keypair()
        cert1 = build_test_cert(key1)
        cert2 = build_test_cert(key2)
        assert get_cert_fingerprint(cert1) != get_cert_fingerprint(cert2)


# --- check_cert_expiry_warning ---

class TestCheckCertExpiryWarning:

    def test_warns_when_cert_expires_within_90_days(self, caplog):
        key = generate_test_keypair()
        cert = build_test_cert(key, days_valid=30)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert any(
            'expires in' in r.message
            for r in caplog.records
        )

    def test_warns_at_exactly_90_days(self, caplog):
        key = generate_test_keypair()
        cert = build_test_cert(key, days_valid=90)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert any(
            'expires in' in r.message
            for r in caplog.records
        )

    def test_no_warning_when_cert_has_years_remaining(self, caplog):
        key = generate_test_keypair()
        cert = build_test_cert(key, days_valid=3650)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert not any(
            'expires in' in r.message
            for r in caplog.records
        )

    def test_warns_for_cert_expiring_in_1_day(self, caplog):
        key = generate_test_keypair()
        cert = build_test_cert(key, days_valid=1)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert any(
            'expires in' in r.message
            for r in caplog.records
        )


# --- BYO cert validation scenarios ---

class TestBYOCertValidation:
    """End-to-end tests for BYO certificate validation using
    load_and_validate_cert with various cert/key combinations."""

    def test_valid_byo_cert_loads_successfully(self, tmp_path):
        key = generate_test_keypair()
        cert = build_test_cert(key, days_valid=365)
        cert_path = tmp_path / 'byo_cert.pem'
        key_path = tmp_path / 'byo_key.pem'
        write_cert_and_key(cert, key, cert_path, key_path)

        loaded = load_and_validate_cert(cert_path, key_path)
        assert isinstance(loaded, x509.Certificate)

    def test_expired_byo_cert_rejected(self, tmp_path):
        key = generate_test_keypair()
        cert = build_test_cert(key, days_valid=-10, backdate_hours=240)
        cert_path = tmp_path / 'expired.pem'
        key_path = tmp_path / 'key.pem'
        write_cert_and_key(cert, key, cert_path, key_path)

        with pytest.raises(CertificateError) as exc_info:
            load_and_validate_cert(cert_path, key_path)
        assert 'expired' in str(exc_info.value).lower()

    def test_byo_key_mismatch_rejected(self, tmp_path):
        key_cert = generate_test_keypair()
        key_wrong = generate_test_keypair()
        cert = build_test_cert(key_cert, days_valid=365)
        cert_path = tmp_path / 'cert.pem'
        key_path = tmp_path / 'wrong_key.pem'
        cert_path.write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        key_path.write_bytes(key_wrong.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

        with pytest.raises(CertificateError) as exc_info:
            load_and_validate_cert(cert_path, key_path)
        msg = str(exc_info.value).lower()
        assert 'key' in msg
        assert 'match' in msg

    def test_nonexistent_cert_file_raises(self, tmp_path):
        with pytest.raises(CertificateError):
            load_and_validate_cert(
                tmp_path / 'missing.pem',
                tmp_path / 'key.pem'
            )

    def test_nonexistent_key_file_raises(self, tmp_path):
        key = generate_test_keypair()
        cert = build_test_cert(key)
        cert_path = tmp_path / 'cert.pem'
        cert_path.write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        with pytest.raises(CertificateError):
            load_and_validate_cert(cert_path, tmp_path / 'missing.pem')
