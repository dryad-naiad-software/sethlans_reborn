# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher/wizard_caddy_wiring.py`` (issue #170).

Covers:

* ``build_wizard_caddy_supervisor`` constructs a CaddySupervisor with
  the wizard-flavoured renderer + env overlay.
* ``start_wizard_caddy_supervisor`` builds + starts the supervisor and
  returns the handle for the launcher's cleanup path.
* The supervisor's ``stop`` is invoked through the lifecycle helper's
  ``stop_wizard_caddy`` (called by the orchestration cleanup path).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from launcher import wizard_caddy_lifecycle, wizard_caddy_wiring


@pytest.fixture
def wizard_tree(tmp_path):
    """Per-test wizard data dir + pre-written cert/key + dummy binary."""
    data_dir = tmp_path
    wizard_subdir = data_dir / "wizard"
    (wizard_subdir / "tls").mkdir(parents=True)
    cert = wizard_subdir / "tls" / "cert.pem"
    key = wizard_subdir / "tls" / "key.pem"
    cert.write_text("CERT")
    key.write_text("KEY")
    binary = tmp_path / "caddy_stub"
    binary.write_text("#!/bin/sh\nexit 0\n")
    try:
        binary.chmod(0o755)
    except OSError:
        pass
    return {
        "data_dir": data_dir,
        "wizard_subdir": wizard_subdir,
        "cert": cert,
        "key": key,
        "binary": binary,
        "caddyfile": wizard_subdir / "Caddyfile",
    }


# ---------------------------------------------------------------------
# build_wizard_caddy_supervisor — renderer + overlay wiring
# ---------------------------------------------------------------------

class TestBuildSupervisor:

    def test_wires_renderer_and_overlay(self, wizard_tree):
        sv = wizard_caddy_wiring.build_wizard_caddy_supervisor(
            caddyfile_path=wizard_tree["caddyfile"],
            public_tls_port=8100,
            loopback_port=8099,
            cert_path=wizard_tree["cert"],
            key_path=wizard_tree["key"],
            wizard_data_dir=wizard_tree["wizard_subdir"],
            binary_path=wizard_tree["binary"],
        )
        assert sv is not None
        # Wizard-specific env-var name (must not collide with manager
        # / worker overlays in the same image).
        assert sv._caddyfile_path_env == "SETHLANS_WIZARD_CADDYFILE_PATH"
        assert sv._env_overlay_mapping[
            "public_tls_port"
        ] == "SETHLANS_WIZARD_CADDY_PUBLIC_TLS_PORT"
        assert sv._env_overlay_mapping[
            "loopback_port"
        ] == "SETHLANS_WIZARD_LOOPBACK_PORT"
        # Renderer is the wizard-flavoured pure function.
        from sethlans_wizard.caddy_template import render_wizard_caddyfile
        assert sv._caddyfile_renderer is render_wizard_caddyfile

    def test_template_kwargs_match_renderer_signature(self, wizard_tree):
        sv = wizard_caddy_wiring.build_wizard_caddy_supervisor(
            caddyfile_path=wizard_tree["caddyfile"],
            public_tls_port=8100,
            loopback_port=8099,
            cert_path=wizard_tree["cert"],
            key_path=wizard_tree["key"],
            wizard_data_dir=wizard_tree["wizard_subdir"],
            binary_path=wizard_tree["binary"],
        )
        # Render once via the supervisor's stashed kwargs; renderer
        # MUST accept them without TypeError.
        rendered = sv._caddyfile_renderer(**sv._template_kwargs)
        assert isinstance(rendered, str)
        assert rendered.strip()


# ---------------------------------------------------------------------
# start_wizard_caddy_supervisor — high-level helper
# ---------------------------------------------------------------------

class TestStartHelper:

    def test_builds_and_starts(self, wizard_tree, mocker):
        fake_supervisor = MagicMock()
        builder = mocker.patch(
            "launcher.wizard_caddy_wiring.build_wizard_caddy_supervisor",
            return_value=fake_supervisor,
        )
        result = wizard_caddy_wiring.start_wizard_caddy_supervisor(
            wizard_tree["data_dir"], wizard_loopback_port=8099,
        )
        assert result is fake_supervisor
        builder.assert_called_once()
        # The builder MUST be passed the wizard's loopback port and
        # the WIZARD_PUBLIC_TLS_PORT constant.
        kw = builder.call_args.kwargs
        assert kw["loopback_port"] == 8099
        assert kw["public_tls_port"] == (
            wizard_caddy_wiring.WIZARD_PUBLIC_TLS_PORT
        )
        # And start() was actually called on the supervisor handle.
        fake_supervisor.start.assert_called_once()

    def test_cleanup_stops_supervisor(self, mocker):
        """Issue #170 FR-8: cleanup helper invokes stop on the handle."""
        fake_supervisor = MagicMock()
        wizard_caddy_lifecycle.stop_wizard_caddy(fake_supervisor)
        fake_supervisor.stop.assert_called_once()
        # Subsequent calls with None must be a no-op (idempotent).
        wizard_caddy_lifecycle.stop_wizard_caddy(None)

    def test_cleanup_swallows_supervisor_errors(self, mocker):
        """Teardown failures don't propagate (best-effort cleanup)."""
        fake_supervisor = MagicMock()
        fake_supervisor.stop.side_effect = RuntimeError("nope")
        # Must not raise.
        wizard_caddy_lifecycle.stop_wizard_caddy(fake_supervisor)

    def test_start_failure_is_logged_and_reraised(
        self, wizard_tree, mocker,
    ):
        """If supervisor.start raises, the helper logs and re-raises.

        The launcher's caller reports the splash error card and exits
        first-run mode; we just verify the error propagates up.
        """
        fake_supervisor = MagicMock()
        fake_supervisor.start.side_effect = RuntimeError(
            "caddy binary missing",
        )
        mocker.patch(
            "launcher.wizard_caddy_wiring.build_wizard_caddy_supervisor",
            return_value=fake_supervisor,
        )
        with pytest.raises(RuntimeError, match="caddy binary missing"):
            wizard_caddy_wiring.start_wizard_caddy_supervisor(
                wizard_tree["data_dir"], wizard_loopback_port=8099,
            )


# ---------------------------------------------------------------------
# Lifecycle helpers — cert generation + port file writes
# ---------------------------------------------------------------------

class TestCertGeneration:

    def test_generate_calls_shared_helper(self, tmp_path, mocker):
        """``generate_wizard_cert`` delegates to shared.cert_utils."""
        gen = mocker.patch(
            "launcher.wizard_caddy_lifecycle.generate_self_signed_cert",
        )
        cert_path, key_path = wizard_caddy_lifecycle.generate_wizard_cert(
            tmp_path,
        )
        assert cert_path == tmp_path / "wizard" / "tls" / "cert.pem"
        assert key_path == tmp_path / "wizard" / "tls" / "key.pem"
        gen.assert_called_once_with(cert_path, key_path)


class TestPublicPortFile:

    def test_write_creates_port_file(self, tmp_path):
        (tmp_path / "wizard").mkdir()
        wizard_caddy_lifecycle.write_wizard_public_port_file(
            tmp_path, 8100,
        )
        port_file = tmp_path / "wizard" / "port"
        assert port_file.exists()
        assert port_file.read_text(encoding="utf-8").strip() == "8100"

    def test_write_failure_is_swallowed(self, tmp_path, caplog):
        """Best-effort: a missing parent dir warns but does not raise."""
        # No ``<tmp_path>/wizard/`` dir; helper creates it normally,
        # but if the parent of that is missing too the helper logs
        # and returns. Easiest reproduction: pass a bogus dir that
        # cannot be created (a file masquerading as a directory).
        sentinel = tmp_path / "blocker"
        sentinel.write_text("not-a-dir")
        # The helper accepts the data dir; ``<sentinel>/wizard/`` cannot
        # exist because ``<sentinel>`` is a regular file.
        with caplog.at_level("WARNING"):
            wizard_caddy_lifecycle.write_wizard_public_port_file(
                sentinel, 8100,
            )
