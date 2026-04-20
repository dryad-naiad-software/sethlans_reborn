# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Integration test for ``QtStatePoller`` notification edges.

Drives a real :class:`QtStatePoller` through a scripted sequence of
state transitions by patching ``_fetch`` (the HTTP transport is out
of scope here -- transport is covered by
``test_qt_poller_integration.py``).  Asserts that the
``notification`` Qt signal fires exactly once per documented
transition edge:

* ``starting -> running``
* ``running -> error``
* ``error -> running``

A combined sequence asserts that exactly three notifications fire
and no duplicates are emitted.

The final test wires ``poller.notification`` to a real
:func:`shared.tray.notifications.dispatch` slot whose
``QSystemTrayIcon.showMessage`` is mocked (the offscreen Qt platform
does not render real notifications), and asserts the call is
forwarded with the exact ``NotificationEvent`` payload.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for poller")
pytest.importorskip("pytestqt", reason="pytest-qt required")

from PySide6.QtCore import Qt, QObject  # noqa: E402
from PySide6.QtTest import QSignalSpy  # noqa: E402
from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402

from shared.tray import notifications as notif_mod  # noqa: E402
from shared.tray import poller as poller_mod  # noqa: E402
from shared.tray.notifications import NotificationEvent  # noqa: E402
from shared.tray.poller import QtStatePoller  # noqa: E402

# N2: point at a guaranteed-refused local port so a teardown-window
# real fetch (see fixture docstring below) cannot accidentally hit
# whatever dev server happens to be bound to 8088 on the workstation.
# Port 1 is privileged and reserved -- always refused on loopback.
LOOPBACK_URL = "http://127.0.0.1:1/api/status/public/"


@pytest.fixture(autouse=True)
def _stub_launcher_watch(mocker):
    """Force ``is_launcher_alive`` True for every test in the module.

    Prevents the launcher-gone branch from short-circuiting ``_tick``
    and skipping the transition we want to observe.
    """
    return mocker.patch.object(
        poller_mod.launcher_watch,
        "is_launcher_alive",
        return_value=True,
    )


@pytest.fixture
def make_poller(qapp, mocker):
    """Factory: build a poller with a scripted ``_fetch`` sequence.

    Yields ``make(script)`` returning ``(poller, stop, quit_flag)``.
    Teardown follows the documented shutdown sequence.

    IMPORTANT: tests in this file MUST drive transitions via direct
    ``poller._tick()`` calls; they MUST NOT call ``poller.start()``.
    The ``mocker.patch.object(poller, "_fetch", ...)`` patch is torn
    down by pytest-mock BEFORE this fixture's teardown runs (mocker
    is a function-scoped dependency resolved first).  If the
    background thread were live during that window, it would see
    the real ``_fetch`` and start hitting ``LOOPBACK_URL`` — a
    guaranteed-refused port (see N2 above), but still real HTTP
    traffic and real log noise.  Keeping ``start()`` out of this
    file avoids the race entirely.
    """
    created: list[QtStatePoller] = []

    def _make(
        script: list,
    ) -> tuple[QtStatePoller, threading.Event, threading.Event]:
        stop = threading.Event()
        flag = threading.Event()
        poller = QtStatePoller(LOOPBACK_URL, stop, flag)
        # Mock _fetch directly per project convention -- DO NOT mock
        # urllib.request.urlopen (the unit-test conftest pins this).
        mocker.patch.object(
            poller, "_fetch", side_effect=list(script),
        )
        created.append(poller)
        return poller, stop, flag

    yield _make

    _teardown_pollers(created)


def _teardown_pollers(created: list[QtStatePoller]) -> None:
    """Cleanup for ``make_poller`` — extracted to keep the fixture
    itself under the complexity limit.  Sanity-guards against the
    forbidden ``poller.start()`` path first, then runs the documented
    ``stop_event -> join -> disconnect`` teardown sequence.
    """
    for poller in created:
        thread = getattr(poller, "_thread", None)
        if thread is not None and thread.is_alive():
            raise AssertionError(
                "A test in this module called poller.start(); the "
                "mocker._fetch patch is torn down before this "
                "fixture and the live thread would race. See the "
                "make_poller docstring.",
            )

    for poller in created:
        try:
            poller._stop.set()
        except Exception:
            pass
        try:
            poller.join(timeout=3.0)
        except Exception:
            pass
        try:
            QObject.disconnect(poller)
        except Exception:
            pass


def _events(spy: QSignalSpy) -> list[NotificationEvent]:
    """Materialize a ``notification`` ``QSignalSpy`` into events.

    PySide6 ``QSignalSpy`` is NOT iterable and has no ``__len__``;
    must use ``count()`` + ``at(i)``.
    """
    return [spy.at(i)[0] for i in range(spy.count())]


def _msgs(spy: QSignalSpy) -> list[str]:
    return [evt.message for evt in _events(spy)]


class TestNotificationEdgesSingle:
    """One edge per test -- exactly one notification per edge."""

    def test_starting_to_running_fires_one(self, make_poller):
        # setup_mode=True suppresses the setup-complete edge so the
        # 'manager started' notification is the only one we see.
        poller, _stop, _flag = make_poller(
            script=[{"boot_id": "b1", "setup_mode": True}],
        )
        spy = QSignalSpy(poller.notification)
        poller._tick()
        msgs = _msgs(spy)
        assert spy.count() == 1
        assert "running" in msgs[0].lower()

    def test_running_to_error_fires_one(self, make_poller):
        poller, _stop, _flag = make_poller(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                RuntimeError("net"),
                RuntimeError("net"),
                RuntimeError("net"),
            ],
        )
        poller._tick()  # -> running
        spy = QSignalSpy(poller.notification)
        poller._tick()  # fail 1 (under threshold)
        poller._tick()  # fail 2 (under threshold)
        assert spy.count() == 0
        poller._tick()  # fail 3 -> error edge
        assert spy.count() == 1
        assert "error state" in _msgs(spy)[0].lower()

    def test_error_to_running_fires_one(self, make_poller):
        poller, _stop, _flag = make_poller(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                RuntimeError("e"),
                RuntimeError("e"),
                RuntimeError("e"),
                {"boot_id": "b1", "setup_mode": True},
            ],
        )
        poller._tick()  # running
        for _ in range(3):
            poller._tick()  # error
        assert poller.snapshot.state == "error"
        spy = QSignalSpy(poller.notification)
        poller._tick()  # recovery edge
        assert spy.count() == 1
        assert "recovered" in _msgs(spy)[0].lower()


class TestNotificationEdgesCombined:
    """Combined sequence -- exactly three notifications, no dupes."""

    def test_starting_running_error_running_emits_three(
        self, make_poller,
    ):
        poller, _stop, _flag = make_poller(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                RuntimeError("e"),
                RuntimeError("e"),
                RuntimeError("e"),
                {"boot_id": "b1", "setup_mode": True},
            ],
        )
        spy = QSignalSpy(poller.notification)
        poller._tick()  # starting -> running (notif 1)
        for _ in range(3):
            poller._tick()  # 3 fails -> error (notif 2 on edge)
        poller._tick()  # error -> running (notif 3)
        assert spy.count() == 3
        msgs = [m.lower() for m in _msgs(spy)]
        assert any("manager is running" in m for m in msgs)
        assert any("error state" in m for m in msgs)
        assert any("recovered" in m for m in msgs)

    def test_no_duplicate_emit_within_same_state(self, make_poller):
        """Repeated successful ticks while already running must NOT
        re-fire the running notification (edge-triggered semantics).
        """
        poller, _stop, _flag = make_poller(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                {"boot_id": "b1", "setup_mode": True},
                {"boot_id": "b1", "setup_mode": True},
            ],
        )
        spy = QSignalSpy(poller.notification)
        for _ in range(3):
            poller._tick()
        # Only the very first transition (starting -> running) should
        # have produced a notification.
        assert spy.count() == 1


class TestNotificationDispatchToTrayIcon:
    """Verify the wiring from ``poller.notification`` to a real
    :func:`shared.tray.notifications.dispatch` call ends in
    ``QSystemTrayIcon.showMessage`` being invoked with the event's
    title and body.
    """

    def test_emit_routes_to_qsystemtrayicon_showmessage(
        self, qapp, qtbot, make_poller, mocker,
    ):
        # A real QSystemTrayIcon is required so the isinstance check
        # in ``notifications.dispatch`` passes.
        tray_icon = QSystemTrayIcon()
        # ``showMessage`` does nothing under offscreen Qt -- mock it
        # so we can observe arguments.  Note the patch goes on the
        # instance, not the class.
        show_message = mocker.patch.object(tray_icon, "showMessage")

        poller, _stop, _flag = make_poller(
            script=[{"boot_id": "b1", "setup_mode": True}],
        )

        def _on_notify(event: NotificationEvent) -> None:
            notif_mod.dispatch(tray_icon, event)

        poller.notification.connect(
            _on_notify, type=Qt.DirectConnection,
        )
        poller._tick()
        # DirectConnection means the slot ran synchronously -- no
        # event-loop spin required.
        assert show_message.call_count == 1
        args = show_message.call_args.args
        assert args[0] == "Sethlans Manager"
        assert "running" in args[1].lower()
