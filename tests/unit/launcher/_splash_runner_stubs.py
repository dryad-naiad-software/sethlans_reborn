# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Qt stand-ins for the ``launcher.splash_runner`` tests (no event loop is spawned)."""

from __future__ import annotations

from launcher import splash_runner


class _StubSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _StubSplash:
    """Stub splash that stays "visible" until close() is called.

    Models the Tool-window behaviour: hiding the widget returns control
    to the runner, but only after the user dismisses the error card.
    """

    def __init__(self, *_args, **_kwargs):
        self._visible = True

    def show(self):
        self._visible = True

    def isVisible(self):
        return self._visible

    def close(self):
        self._visible = False

    def close_for_success(self):
        self.close()

    def morph_to_error(self, *_):
        # Failure path keeps the widget visible until the user clicks
        # Close (which would call .close() above).
        pass


class _ScriptedThread:
    """Thread stub the test drives manually via emit() calls."""

    def __init__(self, *_args, **_kwargs):
        self.cold_boot_ready = _StubSignal()
        self.startup_failed = _StubSignal()
        self.finished_with_code = _StubSignal()
        self.started = False

    def start(self):
        self.started = True

    def wait(self, ms=None):
        return True


class _StubTimer:
    """Stand-in for ``QTimer`` so tests can drive ticks manually.

    Records connected timeout slots, interval, and active state.
    Calling :meth:`fire` invokes every connected slot exactly once,
    simulating a single timer tick.
    """

    def __init__(self):
        self._interval = 0
        self._slots = []
        self.active = False

    # Qt API surface used by splash_runner --------------------------------

    def setInterval(self, ms):  # noqa: N802 — Qt API
        self._interval = int(ms)

    def interval(self):
        return self._interval

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):  # noqa: N802 — Qt API
        return self.active

    @property
    def timeout(self):
        timer = self

        class _TimeoutSignal:
            def connect(self_inner, slot):  # noqa: N805
                timer._slots.append(slot)

        return _TimeoutSignal()

    # Test-driver helper --------------------------------------------------

    def fire(self):
        """Simulate a single timer tick by invoking all connected slots."""
        for slot in list(self._slots):
            slot()


def _patch_runner(mocker, scripted_thread, stub_splash, stub_timer=None):
    """Patch splash_runner so we can drive _on_finished directly.

    Returns the QApplication mock so tests can assert on quit() calls.
    The created ``_StubTimer`` is captured on the QApplication mock as
    ``qapp_inst._stub_timer`` so tests that need it can reach it without
    a second return value.
    """
    qapp_cls = mocker.patch.object(splash_runner, "QApplication")
    qapp_inst = qapp_cls.instance.return_value = mocker.MagicMock()
    qapp_inst.applicationName.return_value = ""
    qapp_inst.applicationDisplayName.return_value = ""
    qapp_inst.organizationName.return_value = ""

    # exec() is a no-op — the test drives signals manually before
    # exec() is called, then exec() returns immediately.
    qapp_inst.exec.return_value = 0

    timer = stub_timer if stub_timer is not None else _StubTimer()
    mocker.patch.object(splash_runner, "QTimer", lambda: timer)
    qapp_inst._stub_timer = timer

    mocker.patch.object(
        splash_runner, "SethlansSplash", lambda *a, **kw: stub_splash,
    )
    mocker.patch.object(
        splash_runner, "OrchestrationThread",
        lambda *a, **kw: scripted_thread,
    )
    mocker.patch.object(splash_runner.supervision, "shutdown_supervisors")
    return qapp_inst
