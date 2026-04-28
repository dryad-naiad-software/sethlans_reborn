# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Splash-enabled boot path for the launcher.

Split from ``launcher/run_launcher.py`` to keep that file under the
300-line ceiling.  The headless path never imports this module — its
top-level PySide6 imports are only reachable when the user launched
Sethlans without ``--no-browser`` / ``--print-url`` (FR-4).

Design: Qt is scoped to splash lifetime only. Once ``cold_boot_ready``
or ``startup_failed`` lands, the Qt event loop quits and control
returns to the launcher's normal shutdown path. The orchestration
QThread keeps running afterwards — Qt does not require its event loop
for a QThread to stay alive as long as we never touch widgets from
that thread again. ``thread.wait()`` then joins the orchestration
before supervisors + tray teardown.

NFR-7: ``app.processEvents()`` is called from a SINGLE site (between
``splash.show()`` and ``thread.start()``). Splash dismissal is driven
solely by Qt-signal slots (``cold_boot_ready`` / ``startup_failed``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QApplication

from launcher import supervision
from launcher.orchestration_thread import OrchestrationThread
from launcher.splash import SethlansSplash

logger = logging.getLogger(__name__)


def _brand_app(app: QApplication) -> None:
    """Brand the Qt app so Alt+Tab / taskbar read "Sethlans".

    Guarded against overriding a caller (e.g., a test harness) that
    already set these on the shared QApplication instance.
    """
    if not app.applicationName() or app.applicationName() == "python":
        app.setApplicationName("Sethlans")
    if not app.applicationDisplayName():
        app.setApplicationDisplayName("Sethlans")
    if not app.organizationName():
        app.setOrganizationName("Dryad and Naiad Software LLC")


def run_with_splash(
    args,
    data_dir: Path,
    version: str,
    pre_orchestration_setup: Callable[[Path], tuple],
    run_orchestration: Callable[..., int],
    teardown_tray: Callable[[object], None],
) -> int:
    """Run the launcher boot flow with the startup splash visible.

    Parameters
    ----------
    args:
        Parsed argparse namespace.
    data_dir:
        Per-user data directory (for the launcher log path).
    version:
        Launcher version string for the splash footer.
    pre_orchestration_setup:
        Callable invoked on ``data_dir`` to spawn the tray and start
        the IPC poll thread.  Returns ``(tray, secret)``.
    run_orchestration:
        Callable ``(data_dir, args, tray, secret, *, on_cold_boot_ready,
        on_startup_failed)`` that dispatches to wizard or normal mode.
    teardown_tray:
        Callable invoked with the tray Popen at shutdown.
    """
    app = QApplication.instance() or QApplication([])
    _brand_app(app)
    log_path = data_dir / "logs" / "launcher.log"
    splash = SethlansSplash(version=version, log_path=log_path)
    splash.show()
    app.processEvents()

    tray, secret = pre_orchestration_setup(data_dir)

    exit_code = {"rc": 0}

    def _runner(on_cold_boot_ready, on_startup_failed):
        return run_orchestration(
            data_dir, args, tray, secret,
            on_cold_boot_ready=on_cold_boot_ready,
            on_startup_failed=on_startup_failed,
        )

    thread = OrchestrationThread(_runner)

    def _on_ready():
        splash.close_for_success()
        app.quit()

    def _on_failed(reason, trace):
        # Option A from the v2 spec's exit-code race note: set rc=1 in
        # the failure slot directly. The slot runs on the Qt main loop
        # while events are still pumping, so the assignment is reliable
        # even if ``finished_with_code`` arrives after ``app.quit()``.
        exit_code["rc"] = 1
        splash.morph_to_error(reason, trace)

    def _on_finished(rc):
        # finished_with_code may arrive after app.quit() in the failure
        # path, where the slot can be dropped by the non-pumping main
        # loop. _on_failed has already set rc=1 in that case, so we
        # only overwrite when the existing rc is still the default 0
        # (or when finished_with_code carries a non-zero code we should
        # preserve over a clobbering 0).
        if exit_code["rc"] == 0:
            exit_code["rc"] = int(rc)

    thread.cold_boot_ready.connect(_on_ready)
    thread.startup_failed.connect(_on_failed)
    thread.finished_with_code.connect(_on_finished)
    thread.start()

    try:
        app.exec()
    except KeyboardInterrupt:
        print("\nSethlans shutting down...")

    # Qt loop exited.  If the splash is still visible (error layout),
    # wait for user dismissal via a second exec() pass.
    if splash.isVisible():
        app.exec()

    # Orchestration may still be running its IPC-poll loop.  Wait for
    # it to return before shutting down supervisors and the tray.
    thread.wait()

    supervision.shutdown_supervisors()
    teardown_tray(tray)
    return exit_code["rc"]


__all__ = ["run_with_splash"]
