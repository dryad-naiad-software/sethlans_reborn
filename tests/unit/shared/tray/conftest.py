# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared fixtures + helpers for the tray test suite.

Scoped to ``tests/unit/shared/tray/`` so fixtures defined here are
available to every test module in this directory.  Currently provides
``QtStatePoller`` factories consumed by ``test_qt_poller.py`` and
``test_qt_poller_lifecycle.py``.  Helper functions (``snapshots``,
``state_changes``, ``notification_msgs``, ``setup_msgs``) are exposed
here too, imported by the test modules directly (not as fixtures).
"""

from __future__ import annotations

import threading

import pytest

try:  # PySide6 optional — other tray tests don't need it.
    from PySide6.QtCore import QObject  # noqa: F401
    from PySide6.QtTest import QSignalSpy  # noqa: F401
    from shared.tray import qt_poller as qt_poller_mod
    from shared.tray.qt_notifications import NotificationEvent
    from shared.tray.qt_poller import QtStatePoller
    _QT_AVAILABLE = True
except Exception:  # pragma: no cover
    _QT_AVAILABLE = False


LOOPBACK_URL = "http://127.0.0.1:8088/api/status/public/"


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


def state_changes(spy):
    """Return ``[(prev, nxt), ...]`` from a ``state_changed`` spy."""
    return [(rec[0], rec[1]) for rec in _records(spy)]


def notification_msgs(spy):
    """Return notification messages from a ``notification`` spy."""
    out = []
    for rec in _records(spy):
        evt = rec[0]
        assert isinstance(evt, NotificationEvent)
        out.append(evt.message)
    return out


def setup_msgs(spy):
    return [m for m in notification_msgs(spy)
            if "setup complete" in m.lower()]


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
        qt_poller_mod.launcher_watch,
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


@pytest.fixture
def poller_factory_configurable(qapp, mocker):
    """Like ``poller_factory`` but exposes the ``is_launcher_alive`` mock.

    Yields ``make(script=None, launcher_alive=True)`` returning
    ``(poller, stop, flag, alive_mock)`` so tests can flip the launcher
    state mid-run.
    """
    alive_mock = mocker.patch.object(
        qt_poller_mod.launcher_watch,
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
