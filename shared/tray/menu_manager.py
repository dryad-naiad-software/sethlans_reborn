# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Manager-section pystray MenuItem list.

Uses ``visible=callable`` / ``enabled=callable`` for dynamic items so
pystray re-evaluates on ``icon.update_menu()``.
"""

from __future__ import annotations

import configparser
import logging
import re
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable

from shared.tray import ipc
from shared.tray.clipboard import copy_token_to_clipboard
from shared.tray.notifications import NotificationEvent, dispatch

logger = logging.getLogger(__name__)

_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{30,60}$")
_MAX_INI_BYTES = 1024

SENTINEL_NAME = "setup_complete.json"


def _sentinel_exists(manager_data_dir: Path) -> bool:
    # The setup-auth-unification spec uses setup_complete.json;
    # the current launcher code uses .setup_complete. Check both so
    # we coexist during the transition.
    return (
        (manager_data_dir / SENTINEL_NAME).exists()
        or (manager_data_dir.parent / ".setup_complete").exists()
    )


def _read_token(manager_data_dir: Path) -> str:
    """Return the setup token from ``manager.ini`` or empty string."""
    ini_path = manager_data_dir / "manager.ini"
    if not ini_path.exists():
        return ""
    try:
        size = ini_path.stat().st_size
    except OSError:
        return ""
    if size > _MAX_INI_BYTES:
        logger.warning(
            "manager.ini exceeds %d bytes; ignoring for token read",
            _MAX_INI_BYTES,
        )
        return ""
    try:
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        logger.warning("Could not parse manager.ini: %s", exc)
        return ""
    return cfg.get("setup", "token", fallback="") or ""


def _validate_token(token: str) -> bool:
    if not token:
        return False
    if not _TOKEN_SHAPE.match(token):
        logger.warning(
            "Setup token has invalid shape; token_len=%d", len(token),
        )
        return False
    return True


def _open_logs(data_dir: Path) -> None:
    log_path = data_dir / "logs" / "manager.log"
    if not log_path.exists():
        logger.warning("Manager log not found at %s", log_path)
        return
    try:
        if sys.platform == "win32":
            import os
            os.startfile(str(log_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(log_path)], check=False)
        else:
            subprocess.run(["xdg-open", str(log_path)], check=False)
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to open log viewer: %s", exc)


class ManagerSection:
    """Holds manager-section state + callbacks.

    The pystray ``Menu`` is rebuilt by ``shared.tray.app`` on every
    update tick.  This class only owns the *callbacks* and the
    *visibility/enabled* predicates used in that rebuild.
    """

    def __init__(
        self,
        data_dir: Path,
        manager_data_dir: Path,
        manager_host: str,
        manager_port: int,
        quit_requested_flag: threading.Event,
        get_snapshot: Callable,
    ) -> None:
        self.data_dir = data_dir
        self.manager_data_dir = manager_data_dir
        self.host = manager_host
        self.port = manager_port
        self.quit_flag = quit_requested_flag
        self.get_snapshot = get_snapshot

    # ---- visibility ----
    def setup_token_available(self) -> bool:
        if _sentinel_exists(self.manager_data_dir):
            return False
        token = _read_token(self.manager_data_dir)
        return _validate_token(token)

    def wizard_visible(self) -> bool:
        return not _sentinel_exists(self.manager_data_dir)

    def counts_visible(self) -> bool:
        return self.get_snapshot().state == "running"

    # ---- enabled (marker-in-flight gating) ----
    def restart_enabled(self) -> bool:
        return not ipc.marker_exists(self.data_dir, ipc.MARKER_RESTART)

    def quit_enabled(self) -> bool:
        return not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT)

    # ---- click callbacks ----
    def on_open_dashboard(self, icon=None, item=None) -> None:
        url = f"https://{self.host}:{self.port}/"
        webbrowser.open(url)

    def on_open_wizard(self, icon=None, item=None) -> None:
        url = f"https://{self.host}:{self.port}/setup/"
        webbrowser.open(url)

    def on_copy_token(self, icon=None, item=None) -> None:
        # Re-check on click per FR-10a.
        if _sentinel_exists(self.manager_data_dir):
            dispatch(NotificationEvent(
                "Sethlans", "Setup already complete.",
            ))
            return
        token = _read_token(self.manager_data_dir)
        if not _validate_token(token):
            dispatch(NotificationEvent(
                "Sethlans", "Setup token not available.",
            ))
            return
        ok = copy_token_to_clipboard(token)
        dispatch(NotificationEvent(
            "Sethlans",
            "Setup token copied to clipboard." if ok
            else "Copy failed; copy the token from the launcher console.",
        ))

    def on_restart_manager(self, icon=None, item=None) -> None:
        try:
            ipc.request_restart(self.data_dir)
        except Exception as exc:  # pragma: no cover
            logger.warning("request_restart failed: %s", exc)

    def on_quit_manager(self, icon=None, item=None) -> None:
        self.quit_flag.set()
        try:
            ipc.request_quit(self.data_dir, target="manager")
        except Exception as exc:  # pragma: no cover
            logger.warning("request_quit(manager) failed: %s", exc)

    def on_view_logs(self, icon=None, item=None) -> None:
        _open_logs(self.data_dir)

    # ---- header text ----
    def header_text(self) -> str:
        snap = self.get_snapshot()
        state = snap.state
        if state == "starting":
            return "[Manager] Starting..."
        if state == "running":
            return "[Manager] Running"
        if state == "stopped":
            return "[Manager] Stopped"
        if state == "error":
            return f"[Manager] Error: {snap.last_error[:40]}"
        return f"[Manager] {state}"

    def setup_line(self) -> str:
        snap = self.get_snapshot()
        if _sentinel_exists(self.manager_data_dir):
            return "Setup: Ready"
        if snap.setup_mode:
            return "Setup: In progress"
        return "Setup: Needed"

    def workers_line(self) -> str:
        return f"Workers online: {self.get_snapshot().workers_online}"

    def jobs_line(self) -> str:
        snap = self.get_snapshot()
        return (
            f"Jobs queued: {snap.jobs_queued}  |  "
            f"Rendering: {snap.jobs_rendering}"
        )

    def footer_text(self) -> str:
        snap = self.get_snapshot()
        version = snap.version or "?"
        return f"v{version} -- :{self.port}"
