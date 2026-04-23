# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Branding tests for ``launcher.splash_runner`` (issue #106).

The splash runner must call ``setApplicationName("Sethlans")`` and
``setApplicationDisplayName("Sethlans")`` on the QApplication so the
Alt+Tab / taskbar entry reads "Sethlans" instead of the frozen exe
name ("run_launcher"). These tests mock QApplication and the splash
widget so no real Qt event loop is spawned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for splash_runner")

from launcher import splash_runner  # noqa: E402


class _StubThread:
    """Minimal OrchestrationThread stub — records connect() but never runs."""

    def __init__(self, *_args, **_kwargs):
        self.manager_ready = _StubSignal()
        self.startup_failed = _StubSignal()
        self.finished_with_code = _StubSignal()

    def start(self):
        # Immediately notify manager_ready so app.exec() returns early
        # via the _on_ready -> app.quit() closure captured by connect().
        for slot in self.manager_ready.slots:
            slot()

    def wait(self):
        pass


class _StubSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _StubSplash:
    def __init__(self, *_args, **_kwargs):
        self._visible = False

    def show(self):
        self._visible = True

    def isVisible(self):
        return self._visible

    def close_for_success(self):
        self._visible = False

    def morph_to_error(self, *_):
        pass


def _patch_qt(mocker):
    """Patch QApplication + splash + thread so no real Qt runs."""
    qapp_cls = mocker.patch.object(splash_runner, "QApplication")
    qapp_instance = qapp_cls.instance.return_value = mocker.MagicMock()
    qapp_instance.applicationName.return_value = ""
    qapp_instance.applicationDisplayName.return_value = ""
    qapp_instance.organizationName.return_value = ""
    qapp_instance.exec.return_value = 0
    mocker.patch.object(splash_runner, "SethlansSplash", _StubSplash)
    mocker.patch.object(splash_runner, "OrchestrationThread", _StubThread)
    mocker.patch.object(
        splash_runner.supervision, "shutdown_supervisors",
    )
    return qapp_instance


class TestSplashRunnerBranding:

    def test_splash_runner_sets_application_display_name(
        self, mocker, tmp_path,
    ):
        qapp_inst = _patch_qt(mocker)

        splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 0,
            teardown_tray=lambda _t: None,
        )

        qapp_inst.setApplicationName.assert_any_call("Sethlans")
        qapp_inst.setApplicationDisplayName.assert_any_call("Sethlans")
        qapp_inst.setOrganizationName.assert_any_call(
            "Dryad and Naiad Software LLC",
        )

    def test_does_not_override_already_set_name(self, mocker, tmp_path):
        # If a calling harness has already branded the QApplication, the
        # runner should not clobber its choice.
        qapp_inst = _patch_qt(mocker)
        qapp_inst.applicationName.return_value = "ExternalHarness"
        qapp_inst.applicationDisplayName.return_value = "Harness"
        qapp_inst.organizationName.return_value = "Some Org"

        splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=Path(tmp_path),
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 0,
            teardown_tray=lambda _t: None,
        )

        qapp_inst.setApplicationName.assert_not_called()
        qapp_inst.setApplicationDisplayName.assert_not_called()
        qapp_inst.setOrganizationName.assert_not_called()
