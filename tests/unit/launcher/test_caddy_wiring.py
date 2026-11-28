# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.caddy_wiring` (D3, FR-LN-3, FR-SF-5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from launcher import caddy_wiring, supervision
from shared import frozen_paths

REPO_MANAGER = Path(caddy_wiring.__file__).resolve().parent.parent / "manager"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    for name in ("SETHLANS_TLS_DATA_DIR", "SETHLANS_MANAGER_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    supervision._reset_for_tests()
    caddy_wiring._reset_for_tests()
    yield
    supervision._reset_for_tests()
    caddy_wiring._reset_for_tests()


@pytest.mark.parametrize("env, expected", [
    pytest.param({}, lambda tmp: REPO_MANAGER / "tls", id="source-default-repo-manager-tls"),
    pytest.param({"SETHLANS_MANAGER_DATA_DIR": "m"}, lambda tmp: tmp / "m" / "tls", id="manager-data-dir"),
    pytest.param({"SETHLANS_TLS_DATA_DIR": "t"}, lambda tmp: tmp / "t", id="tls-data-dir-wins"),
])
def test_tls_folder_is_the_one_the_manager_uses(monkeypatch, settings_dir, tmp_path, env, expected):
    """FR-SF-5 (``caddy_wiring`` reader) and issue #346, the launcher/manager
    TLS-folder mismatch: Caddy must serve the certificate the manager reports to workers,
    i.e. ``tls_setup.get_tls_dir``'s folder, not ``<settings>/manager/tls``."""
    for name, value in env.items():
        monkeypatch.setenv(name, str(tmp_path / value))

    cert, key = caddy_wiring.manager_tls_paths()

    assert cert == expected(tmp_path) / "cert.pem"
    assert key == expected(tmp_path) / "key.pem"
    assert cert.parent != settings_dir / "manager" / "tls"


def test_cert_is_made_only_when_missing(monkeypatch, mocker, tmp_path):
    """Spec common start step 3: the launcher makes the manager certificate if
    missing, and never replaces an existing one (workers pin it)."""
    monkeypatch.setenv("SETHLANS_TLS_DATA_DIR", str(tmp_path / "tls"))
    gen = mocker.patch("shared.cert_utils.generate_self_signed_cert")

    cert, key = caddy_wiring.ensure_manager_tls_cert()
    gen.assert_called_once_with(cert, key)

    cert.parent.mkdir()
    cert.write_text("EXISTING", encoding="utf-8")
    caddy_wiring.ensure_manager_tls_cert()
    gen.assert_called_once()


@pytest.fixture
def caddy(monkeypatch, mocker, settings_dir, tmp_path):
    """A fake supervisor behind ``build_manager_caddy_supervisor``; cert made on disk."""
    monkeypatch.setenv("SETHLANS_TLS_DATA_DIR", str(tmp_path / "tls"))
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "cert.pem").write_text("C", encoding="utf-8")
    settings_dir.mkdir()
    (settings_dir / "sethlans.ini").write_text(
        "[server]\nport = 9443\nwaitress_loopback_port_public = 9090\nwaitress_loopback_port_internal = 9091\n",
        encoding="utf-8",
    )
    fake = mocker.MagicMock()
    fake.is_running.return_value = True
    build = mocker.patch("launcher.caddy_launcher.build_manager_caddy_supervisor", return_value=fake)
    return fake, build


def test_start_then_same_port_is_a_no_op(caddy, tmp_path):
    """D3: one Caddy for setup and the manager, on the manager's certificate;
    asking again for the same Caddyfile does not restart it."""
    fake, build = caddy

    caddy_wiring.start_or_retarget(8081)
    caddy_wiring.start_or_retarget(8081)

    kwargs = build.call_args.kwargs
    assert kwargs["public_tls_port"] == 8081
    assert (kwargs["loopback_plaintext_port"], kwargs["waitress_public_port"],
            kwargs["waitress_internal_port"]) == (8089, 9090, 9091)
    assert kwargs["cert_path"] == tmp_path / "tls" / "cert.pem"
    assert kwargs["manager_data_dir"] == tmp_path / "tls"
    assert kwargs["caddyfile_path"] == frozen_paths.get_identity_dir("manager") / "caddy" / "Caddyfile"
    assert supervision.get_caddy_supervisor() is fake
    fake.start.assert_called_once()
    fake.restart.assert_not_called()


@pytest.mark.parametrize("second_port, running", [
    pytest.param(8080, True, id="port-changed"),
    pytest.param(8081, False, id="caddy-not-running"),
])
def test_retarget_restarts_with_the_new_caddyfile(caddy, second_port, running):
    """D3 / FR-LN-3: a changed port (or a Caddy that is not running) restarts
    the same supervisor with the new kwargs."""
    fake, build = caddy
    caddy_wiring.start_or_retarget(8081)
    fake.is_running.return_value = running

    caddy_wiring.start_or_retarget(second_port)

    build.assert_called_once()
    assert fake.update_template_kwargs.call_args.kwargs["public_tls_port"] == second_port
    fake.restart.assert_called_once()


def test_failed_start_raises_and_the_next_call_tries_again(caddy):
    """D3: a manager without its TLS listener is unreachable, so a failed
    start is raised, and is not remembered as running."""
    fake, _build = caddy
    fake.start.side_effect = RuntimeError("bind failed")
    with pytest.raises(RuntimeError, match="bind failed"):
        caddy_wiring.start_or_retarget(8081)

    caddy_wiring.start_or_retarget(8081)
    fake.restart.assert_called_once()


def test_stop_caddy_stops_and_unregisters(caddy):
    """D3: worker-only mode stops the manager's Caddy; stopping twice is harmless."""
    fake, _build = caddy
    caddy_wiring.start_or_retarget(8081)

    caddy_wiring.stop_caddy()
    caddy_wiring.stop_caddy()

    fake.stop.assert_called_once()
    assert supervision.get_caddy_supervisor() is None


def test_current_loop_entry_uses_the_configured_port(caddy):
    """Current normal mode (until 6b): Caddy listens on ``[server] port``."""
    _fake, build = caddy
    caddy_wiring.start_caddy_supervisor(Path("ignored"))
    assert build.call_args.kwargs["public_tls_port"] == 9443
