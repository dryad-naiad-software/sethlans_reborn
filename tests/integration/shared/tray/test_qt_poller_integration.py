# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Integration tests for ``QtStatePoller`` against a real Django manager.

Stands up a real Django HTTP server on a loopback port via
pytest-django's ``live_server`` fixture (configured to serve
``sethlans_manager.urls_loopback``), constructs a real
:class:`QtStatePoller` pointing at the live URL, and asserts that
``snapshot_changed`` fires with a payload that reflects live
database state.  Also wires the signal into a real
:class:`ManagerSection.refresh` slot to verify the cross-thread
queued connection drives the GUI-side refresh end-to-end.

Mocking is forbidden for the HTTP transport here on purpose -- the
test exercises the real ``urllib.request.urlopen`` -> Django
loopback URLconf -> JSON parse path.  ``launcher_watch`` IS mocked
to keep the parent-pid branch out of scope (covered by the
launcher lifecycle test).
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for poller")
pytest.importorskip("pytestqt", reason="pytest-qt required")

from PySide6.QtCore import Qt, QObject  # noqa: E402

from shared.tray import poller as poller_mod  # noqa: E402
from shared.tray.menu_manager import ManagerSection  # noqa: E402
from shared.tray.poller import (  # noqa: E402
    ManagerSnapshot,
    QtStatePoller,
)


@pytest.fixture
def stub_launcher_watch(mocker):
    """Force ``is_launcher_alive`` to True for these tests.

    The launcher-gone branch is covered by
    ``tests/integration/launcher/test_tray_lifecycle.py``.
    """
    return mocker.patch.object(
        poller_mod.launcher_watch,
        "is_launcher_alive",
        return_value=True,
    )


@pytest.fixture
def poller_lifecycle(stub_launcher_watch):
    """Yield a factory; teardown follows the documented sequence.

    Spec ``Shutdown sequence``:
        ``stop_event.set()`` ->
        ``poller.join(timeout=3.0)`` ->
        ``QObject.disconnect(poller)`` (static form -- the zero-arg
        instance form raises ``TypeError`` in PySide6).
    Each step is guarded so a failure does not skip the next.
    """
    created: list[QtStatePoller] = []
    stops: list[threading.Event] = []

    def _make(url: str) -> tuple[
        QtStatePoller, threading.Event, threading.Event,
    ]:
        stop = threading.Event()
        flag = threading.Event()
        poller = QtStatePoller(
            loopback_url=url,
            stop_event=stop,
            quit_requested_flag=flag,
        )
        created.append(poller)
        stops.append(stop)
        return poller, stop, flag

    yield _make

    for stop, poller in zip(stops, created):
        try:
            stop.set()
        except Exception:
            pass
        try:
            poller.join(timeout=5.0)
        except Exception:
            pass
        # N1: escalate to test failure if the thread refuses to die.
        # A silent leak into the next test (session-scoped qapp) is
        # strictly worse than a test failure here: a zombie poller
        # tick can post QMetaCallEvents into the next test's Qt
        # objects and cause confusing, non-local flake patterns.
        thread = getattr(poller, "_thread", None)
        if thread is not None and thread.is_alive():
            raise AssertionError(
                "QtStatePoller background thread refused to join "
                "within 5 s; would leak into the next test.",
            )
        try:
            QObject.disconnect(poller)
        except Exception:
            pass


def _loopback_url(live_server) -> str:
    """Build the status_public URL on the live test server."""
    return f"{live_server.url}/api/status/public/"


@pytest.mark.urls("sethlans_manager.urls_loopback")
@pytest.mark.django_db(transaction=True)
class TestPollerAgainstLiveManager:
    """Real Django HTTP server + real Qt signal delivery."""

    def test_first_tick_emits_snapshot_via_real_http(
        self, qtbot, live_server, poller_lifecycle,
    ):
        """A single tick against the live loopback endpoint must
        produce one ``snapshot_changed`` emit carrying a real
        ``ManagerSnapshot`` whose ``state`` is ``'running'``.
        """
        poller, _stop, _flag = poller_lifecycle(
            _loopback_url(live_server),
        )
        # Drive a single tick directly -- avoids the 2 s wait inside
        # the worker thread and keeps the test deterministic.
        with qtbot.waitSignal(
            poller.snapshot_changed, timeout=5000,
        ) as blocker:
            poller._tick()
        assert blocker.args is not None
        snapshot = blocker.args[0]
        assert isinstance(snapshot, ManagerSnapshot)
        assert snapshot.state == "running"
        # boot_id and version come straight from the live manager,
        # so they should be non-empty strings.  We do not pin exact
        # values because they vary per test run.
        assert isinstance(snapshot.boot_id, str)
        assert isinstance(snapshot.version, str)

    def test_thread_run_loop_emits_snapshot_via_real_http(
        self, qtbot, live_server, poller_lifecycle,
    ):
        """Start the polling thread for real and assert that a
        ``snapshot_changed`` signal is delivered through Qt's queued
        connection within 5 s.
        """
        poller, _stop, _flag = poller_lifecycle(
            _loopback_url(live_server),
        )
        with qtbot.waitSignal(
            poller.snapshot_changed,
            timeout=5000,
            raising=True,
        ):
            poller.start()
        # Sanity: the worker thread really did start and is running.
        assert poller._thread is not None
        assert poller._thread.is_alive()

    def test_snapshot_drives_manager_section_refresh(
        self,
        qtbot,
        live_server,
        poller_lifecycle,
        tmp_path,
    ):
        """Connect ``snapshot_changed`` to ``ManagerSection.refresh``
        with ``Qt.QueuedConnection`` and verify the section's
        rendered header text reflects the live snapshot's state.
        """
        poller, _stop, quit_flag = poller_lifecycle(
            _loopback_url(live_server),
        )

        data_dir = tmp_path / "data"
        manager_dir = tmp_path / "manager"
        data_dir.mkdir()
        manager_dir.mkdir()

        section = ManagerSection(
            data_dir=data_dir,
            manager_data_dir=manager_dir,
            manager_host="localhost",
            manager_port=8080,
            quit_requested_flag=quit_flag,
            get_snapshot=lambda: poller.snapshot,
        )
        # Build the menu so the section's QActions exist; refresh()
        # is a no-op until the menu has been built.
        section.build_qmenu()
        # Initial header reflects the default ManagerSnapshot state
        # (``starting``) before any tick has run.
        assert "Starting" in section.header_text()

        poller.snapshot_changed.connect(
            section.refresh, type=Qt.QueuedConnection,
        )

        with qtbot.waitSignal(
            poller.snapshot_changed, timeout=5000,
        ):
            poller._tick()
        # After one successful tick the snapshot must be ``running``
        # and the section's rendered header must reflect that.
        assert poller.snapshot.state == "running"
        # Drain any pending queued events so refresh runs.
        qtbot.wait(50)
        assert "Running" in section.header_text()


@pytest.mark.urls("sethlans_manager.urls_loopback")
@pytest.mark.django_db(transaction=True)
class TestPollerShutdownSequence:
    """The documented shutdown sequence must terminate the worker
    thread cleanly even when it has been talking to a real server.
    """

    def test_stop_event_then_join_terminates_worker(
        self, qtbot, live_server, poller_lifecycle,
    ):
        poller, stop, _flag = poller_lifecycle(
            _loopback_url(live_server),
        )
        with qtbot.waitSignal(
            poller.snapshot_changed, timeout=5000,
        ):
            poller.start()
        assert poller._thread is not None
        assert poller._thread.is_alive()
        # Spec sequence: stop_event.set() -> join(timeout=3.0).
        stop.set()
        poller.join(timeout=3.0)
        assert poller._thread is not None
        assert not poller._thread.is_alive(), (
            "Polling thread did not exit within 3 s of stop_event."
        )
        # Static-form disconnect must not raise -- regression guard
        # against the pre-existing PySide6 instance-form gotcha.
        QObject.disconnect(poller)
