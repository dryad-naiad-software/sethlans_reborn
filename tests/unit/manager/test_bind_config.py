# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``sethlans_manager.bind_config.get_manager_bind``: env > settings file > default.

The one resolver behind the start, the local worker's enrollment URL and
the admin summary (issue #313).
"""

import pytest

import run_manager
from sethlans_manager import bind_config
from shared.settings_file import settings_path


@pytest.fixture(autouse=True)
def _no_bind_env(monkeypatch):
    monkeypatch.delenv("SETHLANS_MANAGER_HOST", raising=False)
    monkeypatch.delenv("SETHLANS_MANAGER_PORT", raising=False)


def _write(text):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_without_env_or_file():
    assert bind_config.get_manager_bind() == ("0.0.0.0", "8080")


def test_file_values():
    _write("[server]\nhost = 10.0.0.5\nport = 9443\n")
    assert bind_config.get_manager_bind() == ("10.0.0.5", "9443")


def test_env_beats_file(monkeypatch):
    _write("[server]\nhost = 10.0.0.5\nport = 9443\n")
    monkeypatch.setenv("SETHLANS_MANAGER_HOST", "127.0.0.1")
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8443")
    assert bind_config.get_manager_bind() == ("127.0.0.1", "8443")


def test_each_value_resolves_on_its_own(monkeypatch):
    _write("[server]\nhost = 10.0.0.5\nport = 9443\n")
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8443")
    assert bind_config.get_manager_bind() == ("10.0.0.5", "8443")


def test_empty_env_counts_as_unset(monkeypatch):
    _write("[server]\nport = 9443\n")
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "")
    assert bind_config.get_manager_bind() == ("0.0.0.0", "9443")


def test_explicit_path(tmp_path):
    other = tmp_path / "other.ini"
    other.write_text("[server]\nport = 7000\n", encoding="utf-8")
    assert bind_config.get_manager_bind(other) == ("0.0.0.0", "7000")


def test_unparsable_file_warns_and_uses_defaults(capsys):
    _write("not an ini file\n")
    assert bind_config.get_manager_bind() == ("0.0.0.0", "8080")
    assert "Could not read the settings file" in capsys.readouterr().err


def test_run_manager_uses_the_same_resolver(monkeypatch):
    _write("[server]\nhost = 10.0.0.5\nport = 9443\n")
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8443")
    assert run_manager.get_manager_bind() == bind_config.get_manager_bind() == ("10.0.0.5", "8443")
