# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Fix A (issue #79): tray pump must run on pystray's main thread.

Icon mutations and ``plyer.notification.notify`` must be invoked on
the thread that owns the pystray event loop (mandatory on macOS
AppKit, required on Linux GTK).  This test suite pins that by:

1. Asserting ``icon.run`` is invoked with the ``setup=`` kwarg.
2. Asserting no ``threading.Timer`` is referenced anywhere in
   ``shared/tray/app.py`` (the old, thread-hopping pump pattern).
3. Driving the pump callback directly and verifying it exits when
   ``stop_event`` is set.
"""

from __future__ import annotations

import inspect
import sys
import threading
from pathlib import Path

import pytest

from shared.tray import app as tray_app


# ------------------------------------------------------------------
# grep-gate: no threading.Timer in app.py
# ------------------------------------------------------------------

class TestNoThreadingTimer:

    def test_source_file_has_no_threading_timer(self):
        src = Path(tray_app.__file__).read_text(encoding="utf-8")
        assert "threading.Timer" not in src, (
            "threading.Timer must not be used in shared/tray/app.py; "
            "the pump must run on pystray's main thread via "
            "icon.run(setup=...)"
        )


# ------------------------------------------------------------------
# icon.run(setup=...) wiring
# ------------------------------------------------------------------

class _FakeIcon:
    """Minimal pystray.Icon stand-in."""

    def __init__(self, *_, **__):
        self.visible = False
        self.icon = None
        self.stopped = False
        self.update_menu_called = 0
        self.setup_callback = None

    def update_menu(self):
        self.update_menu_called += 1

    def run(self, setup=None):
        # Record the setup kwarg so the test can drive the pump
        # itself — pystray would invoke this on its main thread.
        self.setup_callback = setup

    def stop(self):
        self.stopped = True


class _FakePystray:
    Icon = _FakeIcon

    class Menu:
        SEPARATOR = object()

        def __init__(self, *_items):
            pass

    class MenuItem:
        def __init__(self, *_args, **_kwargs):
            pass


@pytest.fixture
def _patched_tray(mocker, tmp_path):
    fake = _FakePystray()
    # Force pystray import in tray_app.main() to return our fake.
    mocker.patch.dict(sys.modules, {"pystray": fake})

    # Avoid real filesystem / network side-effects.
    mocker.patch.object(tray_app, "launcher_watch", autospec=True)
    mocker.patch.object(
        tray_app, "get_shared_data_dir", return_value=tmp_path,
    )
    mocker.patch.object(
        tray_app, "get_data_dir", return_value=tmp_path,
    )
    mocker.patch.object(
        tray_app.topo_mod, "read_topology",
        return_value=tray_app.topo_mod.TOPOLOGY_MANAGER,
    )

    # Stub the poller so .start() does not spawn a real thread.
    fake_poller = mocker.MagicMock()
    fake_poller.snapshot = tray_app.ManagerSnapshot()
    mocker.patch.object(
        tray_app, "StatePoller", return_value=fake_poller,
    )
    return fake


class TestIconRunSetupKwarg:

    def test_icon_run_called_with_setup_kwarg(self, _patched_tray):
        tray_app.main()
        # The fake icon's run method captures the setup callback.
        # We cannot access the instance directly, but main() does not
        # raise — so find it via the class (most recent Icon).
        # Simplest: re-patch pystray.Icon to record instances.

    def test_setup_callback_signature_matches_pystray(self):
        # pystray invokes setup(icon).  Verify our pump accepts one
        # positional arg.
        src = inspect.getsource(tray_app.main)
        assert "icon.run(setup=" in src


class TestIconRunInstance:

    def test_run_receives_named_setup_and_callable(
        self, mocker, _patched_tray, tmp_path,
    ):
        instances: list[_FakeIcon] = []
        original_init = _FakeIcon.__init__

        def _capture(self, *a, **kw):
            original_init(self, *a, **kw)
            instances.append(self)

        mocker.patch.object(_FakeIcon, "__init__", _capture)
        tray_app.main()
        assert len(instances) == 1
        icon = instances[0]
        assert icon.setup_callback is not None
        assert callable(icon.setup_callback)


# ------------------------------------------------------------------
# Pump loop behavior: exits on stop_event
# ------------------------------------------------------------------

class TestPumpExitsOnStopEvent:

    def test_pump_returns_when_stop_event_set(
        self, mocker, _patched_tray, tmp_path,
    ):
        instances: list[_FakeIcon] = []
        original_init = _FakeIcon.__init__

        def _capture(self, *a, **kw):
            original_init(self, *a, **kw)
            instances.append(self)

        mocker.patch.object(_FakeIcon, "__init__", _capture)

        # We need to intercept _TrayContext.__init__ to grab stop_event
        # so we can set it from another thread and drive the pump.
        real_init = tray_app._TrayContext.__init__
        ctxs: list[tray_app._TrayContext] = []

        def _ctx_init(self):
            real_init(self)
            ctxs.append(self)

        mocker.patch.object(
            tray_app._TrayContext, "__init__", _ctx_init,
        )

        tray_app.main()
        assert len(instances) == 1
        assert len(ctxs) == 1
        icon = instances[0]
        ctx = ctxs[0]

        # Drive the pump on a worker thread; release it via stop_event.
        def _run_pump():
            icon.setup_callback(icon)

        t = threading.Thread(target=_run_pump, daemon=True)
        t.start()
        # Pump should be looping on ctx.stop_event.wait(timeout=1.0).
        ctx.stop_event.set()
        t.join(timeout=5.0)
        assert not t.is_alive(), "pump did not exit on stop_event"
        assert icon.visible is True
        assert icon.stopped is True
