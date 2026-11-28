# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.child_env` (FR-LN-7, FR-SF-1)."""

from __future__ import annotations

import os
from pathlib import Path

from launcher import child_env

SECRETS = {
    "SETHLANS_SETUP_ADMIN_PASSWORD": "pw",
    "SETHLANS_ADMIN_PASSWORD": "pw2",
    "SETHLANS_ADMIN_PASSWORD_FILE": "/run/secrets/pw",
    "SETHLANS_ADMIN_USERNAME": "admin",
    "SETHLANS_TRAY_IPC_SECRET": "tray-secret",
}


def _set_secrets(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("sethlans_setup_mixed_case", "x")
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8080")


def _secret_names(env) -> set:
    return {name for name in env if name.upper() in SECRETS or name.upper().startswith("SETHLANS_SETUP_")}


def test_prepare_scrubs_secrets_once_and_exports_the_settings_location(monkeypatch, settings_dir):
    """FR-LN-7: the setup secrets leave the launcher's own environment at start
    (so Caddy and every child inherit none) and are handed back for the
    ``--answers`` child: all of ``SETHLANS_SETUP_*`` and ``SETHLANS_ADMIN_*``
    (a stray ``SETHLANS_ADMIN_USERNAME`` would fail every first start with
    exit 11) and the tray's IPC secret; FR-SF-1: both settings variables are
    exported, absolute."""
    _set_secrets(monkeypatch)

    removed = child_env.prepare_launcher_environment()

    assert {name.upper() for name in removed} == set(SECRETS) | {"SETHLANS_SETUP_MIXED_CASE"}
    assert removed["SETHLANS_ADMIN_PASSWORD"] == "pw2"
    assert not _secret_names(os.environ)
    assert os.environ["SETHLANS_MANAGER_PORT"] == "8080"
    assert Path(os.environ["SETHLANS_SETTINGS_FILE"]) == settings_dir / "sethlans.ini"
    assert Path(os.environ["SETHLANS_SETTINGS_DIR"]) == settings_dir
    assert Path(os.environ["SETHLANS_SETTINGS_FILE"]).is_absolute()


def test_child_env_scrubs_again_then_adds_extra(monkeypatch, settings_dir):
    """FR-LN-7: a secret put back after start never reaches a child, while the
    launcher's own ``extra`` (``SETHLANS_SETUP_PORT``) is added after the scrub."""
    _set_secrets(monkeypatch)

    env = child_env.child_env({"SETHLANS_SETUP_PORT": "8081"})

    assert _secret_names(env) == {"SETHLANS_SETUP_PORT"}
    assert env["SETHLANS_SETUP_PORT"] == "8081"
    assert Path(env["SETHLANS_SETTINGS_DIR"]) == settings_dir
    assert "SETHLANS_SETUP_ADMIN_PASSWORD" in os.environ  # the copy is scrubbed, not the parent


def test_caddy_inherits_the_scrubbed_environment(monkeypatch, mocker, settings_dir, tmp_path):
    """FR-LN-7: Caddy is spawned with the parent's environment, so after the
    start-up scrub it carries no setup secret."""
    from shared.caddy_supervisor import process

    _set_secrets(monkeypatch)
    child_env.prepare_launcher_environment()
    popen = mocker.patch.object(process.subprocess, "Popen")
    mocker.patch.object(process, "_attach_to_windows_job")

    process.spawn(tmp_path / "caddy", tmp_path / "Caddyfile", env={"SETHLANS_MANAGER_CADDY_PUBLIC_TLS_PORT": "8080"})

    env = popen.call_args.kwargs["env"]
    assert not _secret_names(env)
    assert env["SETHLANS_MANAGER_CADDY_PUBLIC_TLS_PORT"] == "8080"
