# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Worker-section pystray MenuItem helpers (stub).

The full worker control surface (``yield_now`` / ``claim_now`` /
``pause_until``) is owned by ``worker-host-integration.md`` Q9 and is
out of scope for the tray-helper-unified spec.  This module renders a
minimal header + ``Quit Sethlans`` entry today and exposes hooks the
Q9 implementation can fill in later.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Callable

from shared.tray import ipc

logger = logging.getLogger(__name__)


class WorkerSection:
    """Holds worker-section state + callbacks."""

    def __init__(
        self,
        data_dir: Path,
        worker_host: str = "127.0.0.1",
        worker_port: int = 8081,
        quit_requested_flag: threading.Event | None = None,
        get_worker_state: Callable[[], str] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.host = worker_host
        self.port = worker_port
        self.quit_flag = quit_requested_flag or threading.Event()
        self._get_state = get_worker_state or (lambda: "idle")

    # ---- header / visibility ----
    def header_text(self) -> str:
        state = self._get_state()
        mapping = {
            "rendering": "Rendering",
            "yielding": "Yielding (finishing current frame)",
            "yielded": "Yielded (waiting for idle)",
            "idle": "Idle",
        }
        return f"[Worker] {mapping.get(state, state)}"

    # ---- click callbacks ----
    def on_open_worker_status(self, icon=None, item=None) -> None:
        webbrowser.open(f"http://{self.host}:{self.port}/")

    def on_quit_sethlans(self, icon=None, item=None) -> None:
        try:
            ipc.request_quit(self.data_dir, target="all")
        except Exception as exc:  # pragma: no cover
            logger.warning("request_quit(all) failed: %s", exc)

    # ---- enabled gates ----
    def quit_sethlans_enabled(self) -> bool:
        return not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT)
