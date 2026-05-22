# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.caddy_wiring.ensure_manager_tls_cert`` (#202).

Covers FR-CERT1 / FR-CERT2:

* ``generate_self_signed_cert`` is called when the cert is absent.
* ``generate_self_signed_cert`` is NOT called when the cert already exists
  (idempotent — the launcher reuses the wizard's / manager's previous cert).
* The return value is a ``(cert_path, key_path)`` tuple pointing into
  ``<manager_data>/tls/``.
"""

from __future__ import annotations

import pytest

from launcher import caddy_wiring


class TestEnsureManagerTlsCert:
    """``ensure_manager_tls_cert(manager_data)`` pre-generates the cert."""

    def test_generates_when_cert_absent(self, tmp_path, mocker):
        manager_data = tmp_path / "manager"
        manager_data.mkdir()
        gen = mocker.patch("shared.cert_utils.generate_self_signed_cert")

        cert, key = caddy_wiring.ensure_manager_tls_cert(manager_data)

        assert cert == manager_data / "tls" / "cert.pem"
        assert key == manager_data / "tls" / "key.pem"
        gen.assert_called_once_with(cert, key)
        # The TLS dir MUST exist before Caddy is started so the supervisor
        # can resolve the cert file at spawn time.
        assert (manager_data / "tls").is_dir()

    def test_does_not_regenerate_when_cert_exists(self, tmp_path, mocker):
        """FR-CERT2: idempotent. A pre-existing cert.pem is NOT regenerated."""
        manager_data = tmp_path / "manager"
        tls_dir = manager_data / "tls"
        tls_dir.mkdir(parents=True)
        (tls_dir / "cert.pem").write_text("EXISTING-CERT")
        (tls_dir / "key.pem").write_text("EXISTING-KEY")
        gen = mocker.patch("shared.cert_utils.generate_self_signed_cert")

        cert, key = caddy_wiring.ensure_manager_tls_cert(manager_data)

        gen.assert_not_called()
        assert cert.read_text() == "EXISTING-CERT"
        assert key.read_text() == "EXISTING-KEY"

    def test_returns_paths_inside_manager_data_tls(self, tmp_path, mocker):
        """FR-CERT3: returned paths resolve under ``<manager_data>/tls/``."""
        manager_data = tmp_path / "manager"
        manager_data.mkdir()
        mocker.patch("shared.cert_utils.generate_self_signed_cert")

        cert, key = caddy_wiring.ensure_manager_tls_cert(manager_data)

        assert cert.parent == manager_data / "tls"
        assert key.parent == manager_data / "tls"
        assert cert.name == "cert.pem"
        assert key.name == "key.pem"

    def test_creates_tls_dir_if_missing(self, tmp_path, mocker):
        """The helper must create ``<manager_data>/tls/`` if absent."""
        manager_data = tmp_path / "manager"
        manager_data.mkdir()
        assert not (manager_data / "tls").exists()
        mocker.patch("shared.cert_utils.generate_self_signed_cert")

        caddy_wiring.ensure_manager_tls_cert(manager_data)

        assert (manager_data / "tls").is_dir()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
