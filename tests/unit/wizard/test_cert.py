# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/cert.py`` (Spec 1 / A2).

Covers FR-W4 (atomic write, file modes), FR-W5 (reuse rules),
FR-CFG3 (location), and SEC-MED-4 (24h reuse buffer + 7-day lifetime).
"""

from __future__ import annotations

import os
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from wizard.sethlans_wizard import cert as cert_mod
from wizard.sethlans_wizard.cert import (
    CERT_FILENAME,
    KEY_FILENAME,
    delete_cert,
    ensure_cert,
)


def _read_cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


# ---------------------------------------------------------------------
# ensure_cert: fresh generation
# ---------------------------------------------------------------------

class TestEnsureCertFreshGeneration:

    def test_creates_wizard_subdir(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        assert cert_path == tmp_path / "wizard" / CERT_FILENAME
        assert key_path == tmp_path / "wizard" / KEY_FILENAME
        assert cert_path.parent.is_dir()

    def test_writes_cert_and_key(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        assert cert_path.is_file()
        assert key_path.is_file()
        assert cert_path.stat().st_size > 0
        assert key_path.stat().st_size > 0

    def test_cert_lifetime_is_seven_days(self, tmp_path):
        cert_path, _ = ensure_cert(tmp_path)
        cert = _read_cert(cert_path)
        now = datetime.now(timezone.utc)
        delta = cert.not_valid_after_utc - now
        # Allow generous slack for test scheduling jitter.
        assert timedelta(days=6, hours=23) <= delta <= timedelta(days=7, hours=1)

    def test_key_matches_cert(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        cert = _read_cert(cert_path)
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert_pub = cert.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_pub = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        assert cert_pub == key_pub

    def test_no_temp_files_left_behind(self, tmp_path):
        ensure_cert(tmp_path)
        leftover = list(tmp_path.glob("wizard/*.tmp"))
        assert leftover == []

    def test_posix_file_modes_are_600(self, tmp_path):
        if platform.system() == "Windows":
            return  # POSIX-only assertion; Windows path covered by ACL helper.
        cert_path, key_path = ensure_cert(tmp_path)
        assert (cert_path.stat().st_mode & 0o777) == 0o600
        assert (key_path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------
# ensure_cert: reuse logic (FR-W5)
# ---------------------------------------------------------------------

class TestEnsureCertReuse:

    def test_reuses_when_existing_cert_is_valid(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        cert_bytes_first = cert_path.read_bytes()
        key_bytes_first = key_path.read_bytes()
        # Second call should reuse — bytes identical.
        ensure_cert(tmp_path)
        assert cert_path.read_bytes() == cert_bytes_first
        assert key_path.read_bytes() == key_bytes_first

    def test_regenerates_when_cert_missing(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        cert_path.unlink()
        ensure_cert(tmp_path)
        assert cert_path.is_file()
        assert key_path.is_file()

    def test_regenerates_when_key_missing(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        key_path.unlink()
        ensure_cert(tmp_path)
        assert cert_path.is_file()
        assert key_path.is_file()

    def test_regenerates_when_cert_unparseable(self, tmp_path):
        cert_path, _ = ensure_cert(tmp_path)
        cert_path.write_bytes(b"-----BEGIN CERTIFICATE-----\nGARBAGE\n-----END CERTIFICATE-----\n")
        ensure_cert(tmp_path)
        # Should now be a real cert again.
        _ = _read_cert(cert_path)

    def test_regenerates_when_within_24h_of_expiry(self, tmp_path, monkeypatch):
        """Cert with <24h remaining must be replaced (SEC-MED-4)."""
        cert_path, key_path = ensure_cert(tmp_path)
        original_bytes = cert_path.read_bytes()

        # Hand-craft a cert with only 1h remaining, signed by the existing key.
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        now = datetime.now(timezone.utc)
        short = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "x")]))
            .issuer_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "x")]))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(hours=1))
            .sign(key, hashes.SHA256())
        )
        cert_path.write_bytes(short.public_bytes(serialization.Encoding.PEM))

        ensure_cert(tmp_path)
        # Must have been replaced — bytes must differ.
        assert cert_path.read_bytes() != original_bytes
        new_cert = _read_cert(cert_path)
        # And remaining lifetime must clear the 24h buffer.
        assert (new_cert.not_valid_after_utc - now) > timedelta(hours=24)

    def test_regenerates_when_key_does_not_match_cert(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        # Replace key with a fresh, unrelated key.
        new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path.write_bytes(new_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        ensure_cert(tmp_path)
        # After regen, key must once again match cert.
        cert = _read_cert(cert_path)
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert_pub = cert.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_pub = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        assert cert_pub == key_pub


# ---------------------------------------------------------------------
# delete_cert
# ---------------------------------------------------------------------

class TestDeleteCert:

    def test_removes_both_files(self, tmp_path):
        cert_path, key_path = ensure_cert(tmp_path)
        assert cert_path.exists() and key_path.exists()
        delete_cert(tmp_path)
        assert not cert_path.exists()
        assert not key_path.exists()

    def test_idempotent_when_already_missing(self, tmp_path):
        # No prior ensure_cert call. Must not raise.
        delete_cert(tmp_path)
        # And calling again is fine.
        delete_cert(tmp_path)


# ---------------------------------------------------------------------
# Atomicity helper
# ---------------------------------------------------------------------

class TestAtomicWrite:

    def test_temp_file_lives_in_destination_dir(self, tmp_path, monkeypatch):
        """FR-W4: temp file MUST be in the same directory as the destination."""
        seen_temps = []
        real_replace = os.replace

        def spy_replace(src, dst):
            seen_temps.append(Path(src))
            real_replace(src, dst)

        monkeypatch.setattr(cert_mod.os, "replace", spy_replace)
        ensure_cert(tmp_path)
        assert seen_temps, "expected at least one os.replace call"
        for tmp in seen_temps:
            assert tmp.parent == tmp_path / "wizard"
