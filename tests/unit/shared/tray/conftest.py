# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared fixtures + helpers for the tray test suite.

Scoped to ``tests/unit/shared/tray/`` so fixtures defined here are
available to every test module in this directory.  Currently provides
``QtStatePoller`` factories consumed by ``test_poller.py`` and
``test_poller_lifecycle.py``.  Helper functions (``snapshots``,
``state_changes``, ``notification_msgs``, ``setup_msgs``) are exposed
here too, imported by the test modules directly (not as fixtures).
"""

from __future__ import annotations

import threading

import pytest

try:  # PySide6 optional — other tray tests don't need it.
    from PySide6.QtCore import QObject  # noqa: F401
    from PySide6.QtTest import QSignalSpy  # noqa: F401
    from shared.tray import menu_worker as menu_worker_mod
    from shared.tray import poller as poller_mod
    from shared.tray.menu_manager import ManagerSection
    from shared.tray.menu_worker import WorkerSection
    from shared.tray.notifications import NotificationEvent
    from shared.tray.poller import ManagerSnapshot, QtStatePoller
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
# ManagerSection fixtures (used by test_menu_manager_*.py splits)
# ------------------------------------------------------------------

def _make_snapshot(**overrides):
    """Build a ``ManagerSnapshot`` with sensible defaults for tests."""
    defaults = dict(
        state="running",
        setup_mode=False,
        workers_online=3,
        jobs_queued=7,
        jobs_rendering=2,
        version="1.2.3",
        boot_id="boot",
        last_error="",
    )
    defaults.update(overrides)
    return ManagerSnapshot(**defaults)


@pytest.fixture
def make_snapshot():
    """Expose ``_make_snapshot`` as a fixture so tests can call it."""
    return _make_snapshot


@pytest.fixture
def snapshot_holder():
    """Mutable holder so tests can swap the snapshot returned by the
    ``get_snapshot`` callable after ``build_qmenu`` is called."""
    holder = {"snap": _make_snapshot()}

    def _get():
        return holder["snap"]

    holder["get"] = _get
    return holder


@pytest.fixture
def section(qapp, tmp_path, snapshot_holder):
    """Build a ``ManagerSection`` wired to ``snapshot_holder``."""
    quit_flag = threading.Event()
    sec = ManagerSection(
        data_dir=tmp_path / "data",
        manager_data_dir=tmp_path / "manager",
        manager_host="localhost",
        manager_port=8443,
        quit_requested_flag=quit_flag,
        get_snapshot=snapshot_holder["get"],
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "manager").mkdir()
    return sec


@pytest.fixture
def section_factory(qapp, tmp_path):
    """Build ``ManagerSection`` instances with a custom ``notify`` cb.

    Each call returns a freshly constructed ``ManagerSection`` rooted
    at ``tmp_path``; directories are created once per test.
    """
    (tmp_path / "data").mkdir()
    (tmp_path / "manager").mkdir()

    def _make(notify=None, token=None):
        if token is not None:
            (tmp_path / "manager" / "manager.ini").write_text(
                f"[setup]\ntoken = {token}\n", encoding="utf-8",
            )
        flag = threading.Event()
        return ManagerSection(
            data_dir=tmp_path / "data",
            manager_data_dir=tmp_path / "manager",
            manager_host="localhost",
            manager_port=8443,
            quit_requested_flag=flag,
            get_snapshot=lambda: _make_snapshot(),
            notify=notify,
        )

    return _make


# ------------------------------------------------------------------
# WorkerSection fixtures (used by test_menu_worker_*.py splits)
# ------------------------------------------------------------------

@pytest.fixture
def worker_state_holder():
    """Mutable holder so worker-menu tests can swap the state string
    returned by the ``get_worker_state`` callable at any time."""
    holder = {"state": "idle"}
    holder["get"] = lambda: holder["state"]
    return holder


@pytest.fixture
def worker_section(qapp, tmp_path, worker_state_holder, mocker):
    """Build a ``WorkerSection`` wired to ``worker_state_holder``.

    ``ipc.marker_exists`` is patched to False so the quit action
    starts enabled; tests that want the quit-disabled path flip the
    mock's return value.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(
        menu_worker_mod.ipc, "marker_exists", return_value=False,
    )
    return WorkerSection(
        data_dir=data_dir,
        quit_requested_flag=threading.Event(),
        get_worker_state=worker_state_holder["get"],
    )


@pytest.fixture
def worker_section_with_about(qapp, tmp_path, worker_state_holder, mocker):
    """Like ``worker_section`` but with ``include_about=True``."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(
        menu_worker_mod.ipc, "marker_exists", return_value=False,
    )
    return WorkerSection(
        data_dir=data_dir,
        quit_requested_flag=threading.Event(),
        include_about=True,
        get_worker_state=worker_state_holder["get"],
    )


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
