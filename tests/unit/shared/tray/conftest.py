# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared fixtures + helpers for the tray test suite.

Scoped to ``tests/unit/shared/tray/`` so fixtures defined here are
available to every test module in this directory: ``QtStatePoller``
factories for the poller tests, and a settings folder with the
launcher-state and settings-file writers for the menu tests. Helper
functions (``snapshots``, the writers, ``texts`` / ``action``) are
imported by the test modules directly.
"""

from __future__ import annotations

import json
import threading

import pytest

try:  # PySide6 optional — other tray tests don't need it.
    from PySide6.QtCore import QObject  # noqa: F401
    from PySide6.QtTest import QSignalSpy  # noqa: F401
    from shared.tray import poller as poller_mod
    from shared.tray.poller import QtStatePoller
    _QT_AVAILABLE = True
except Exception:  # pragma: no cover
    _QT_AVAILABLE = False


LOOPBACK_URL = "http://127.0.0.1:8088/api/status/public/"


@pytest.fixture(autouse=True)
def _settings_file_in_tmp(monkeypatch, tmp_path):
    """Point the settings file at ``<tmp_path>/sethlans.ini`` for every tray test."""
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(tmp_path / "sethlans.ini"))


# ------------------------------------------------------------------
# Helper functions (imported directly by test modules)
# ------------------------------------------------------------------

def _records(spy):
    """Materialize a ``QSignalSpy`` into a list of arg-lists.

    ``QSignalSpy`` is not iterable in PySide6 — must use ``at(i)``.
    """
    return [spy.at(i) for i in range(spy.count())]


def snapshots(spy):
    """Extract the ``ManagerSnapshot`` payload from each spy record."""
    return [rec[0] for rec in _records(spy)]


# ------------------------------------------------------------------
# Internal: build + teardown a poller
# ------------------------------------------------------------------

def _make_poller(mocker, script=None):
    stop = threading.Event()
    quit_flag = threading.Event()
    p = QtStatePoller(LOOPBACK_URL, stop, quit_flag)
    if script is not None:
        mocker.patch.object(p, "_fetch", side_effect=list(script))
    return p, stop, quit_flag


def _teardown_poller(p):
    try:
        p._stop.set()
    except Exception:
        pass
    try:
        p.join(timeout=3.0)
    except Exception:
        pass
    try:
        QObject.disconnect(p)
    except Exception:
        pass


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def poller_factory(qapp, mocker):
    """Factory creating pollers with automatic teardown.

    Yields ``make(script=None)`` returning ``(poller, stop, flag)``.
    ``is_launcher_alive`` is patched to True for every poller created
    by this fixture — tests that need the launcher-gone branch should
    use ``poller_factory_configurable`` instead.
    """
    mocker.patch.object(
        poller_mod.launcher_watch,
        "is_launcher_alive",
        return_value=True,
    )
    created = []

    def _make(script=None):
        p, stop, quit_flag = _make_poller(mocker, script=script)
        created.append(p)
        return p, stop, quit_flag

    yield _make

    for p in created:
        _teardown_poller(p)


# ------------------------------------------------------------------
# Settings folder + launcher state (menu, phase and app tests)
# ------------------------------------------------------------------

def write_launcher_state(settings_dir, state, port=None, reason=None):
    """Write ``<settings>/setup/launcher_state.json`` as the launcher does."""
    folder = settings_dir / "setup"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "port": port, "reason": reason}
    (folder / "launcher_state.json").write_text(json.dumps(payload), encoding="utf-8")


def write_finished_setup(settings_dir, mode):
    """A finished ``[setup]`` in ``<settings>/sethlans.ini`` (FR-SF-4)."""
    (settings_dir / "sethlans.ini").write_text(
        f"[setup]\nversion = 1\nmode = {mode}\ncompleted_at = 2026-11-28T10:00:00+00:00\n",
        encoding="utf-8",
    )


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """``tmp_path`` as the settings folder (``SETHLANS_SETTINGS_DIR``)."""
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def poller_factory_configurable(qapp, mocker):
    """Like ``poller_factory`` but exposes the ``is_launcher_alive`` mock.

    Yields ``make(script=None, launcher_alive=True)`` returning
    ``(poller, stop, flag, alive_mock)`` so tests can flip the launcher
    state mid-run.
    """
    alive_mock = mocker.patch.object(
        poller_mod.launcher_watch,
        "is_launcher_alive",
        return_value=True,
    )
    created = []

    def _make(script=None, launcher_alive=True):
        alive_mock.return_value = launcher_alive
        p, stop, quit_flag = _make_poller(mocker, script=script)
        created.append(p)
        return p, stop, quit_flag, alive_mock

    yield _make

    for p in created:
        _teardown_poller(p)


def texts(menu):
    """A QMenu's item texts, ``---`` for a separator."""
    return ["---" if a.isSeparator() else a.text() for a in menu.actions()]


def action(menu, label):
    """The QMenu's item with ``label``."""
    return next(a for a in menu.actions() if a.text() == label)
