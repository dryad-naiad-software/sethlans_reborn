# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Qt worker-section menu builder for the PySide6 tray.

See ``tray-pyside6-migration.md`` FR-2, NFR-1.  ``WorkerSection``
exposes ``build_qmenu(parent=None)`` / ``rebuild(parent=None)`` to
construct a ``QMenu``, and ``refresh(snapshot=None)`` to re-evaluate
dynamic text / enabled state.

The shared About Sethlans action normally lives on the manager
section (see ``menu_manager``).  When the topology is worker-only
there is no manager section, so the About affordance must live here
instead -- callers pass ``include_about=True`` in that case.  When
both sections are present the manager section owns About and this
section is built with ``include_about=False`` (the default).

Pure-Python helpers reuse the shared About dialog from ``about``.
The module does NOT construct a ``QApplication``.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from shared.tray import ipc
from shared.tray.about import show_about_dialog

logger = logging.getLogger(__name__)


class WorkerSection:
    """Worker-section menu state + callbacks for the Qt tray.

    The ``include_about`` flag lets worker-only topologies expose the
    shared About Sethlans action (NFR-1) that would otherwise live on
    the manager section.
    """

    def __init__(
        self,
        data_dir: Path,
        quit_requested_flag: threading.Event,
        include_about: bool = False,
        worker_host: str = "127.0.0.1",
        worker_port: int = 8081,
        get_worker_state: Optional[Callable[[], str]] = None,
    ) -> None:
        self.data_dir = data_dir
        self.quit_flag = quit_requested_flag
        self.include_about = include_about
        self.host = worker_host
        self.port = worker_port
        self._get_state = get_worker_state or (lambda: "idle")

        # QAction references held so refresh() can mutate them.
        self._menu: Optional[QMenu] = None
        self._act_header: Optional[QAction] = None
        self._act_open_status: Optional[QAction] = None
        self._act_quit: Optional[QAction] = None
        self._act_about: Optional[QAction] = None

    # ---- dynamic text ----
    def header_text(self) -> str:
        state = self._get_state()
        mapping = {
            "rendering": "Rendering",
            "yielding": "Yielding (finishing current frame)",
            "yielded": "Yielded (waiting for idle)",
            "idle": "Idle",
        }
        return f"[Worker] {mapping.get(state, state)}"

    # ---- enabled gates ----
    def quit_sethlans_enabled(self) -> bool:
        return not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT)

    # ---- click callbacks (no-arg; QAction.triggered signal) ----
    def on_open_worker_status(self) -> None:
        webbrowser.open(f"http://{self.host}:{self.port}/")

    def on_quit_sethlans(self) -> None:
        try:
            ipc.request_quit(self.data_dir, target="all")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("request_quit(all) failed: %s", exc)

    def on_about(self) -> None:
        show_about_dialog(self._menu)

    # ---- menu construction ----
    def build_qmenu(self, parent: Optional[QWidget] = None) -> QMenu:
        """Construct the worker-section QMenu.

        Idempotent: each call returns a freshly-built ``QMenu`` and
        updates the stored ``QAction`` references so ``refresh``
        always operates on the most recently built menu.
        """
        menu = QMenu(parent)
        self._act_header = self._add_disabled(menu, "")
        menu.addSeparator()
        self._act_open_status = self._add(
            menu, "Open Sethlans Status", self.on_open_worker_status,
        )
        self._act_quit = self._add(
            menu, "Quit Sethlans", self.on_quit_sethlans,
        )
        if self.include_about:
            # Worker-only topology: manager section is absent, so the
            # About affordance (NFR-1) must live here.
            self._act_about = self._add(
                menu, "About Sethlans", self.on_about,
            )
        else:
            self._act_about = None
        self._menu = menu
        self.refresh()
        return menu

    def rebuild(self, parent: Optional[QWidget] = None) -> QMenu:
        """Alias for ``build_qmenu``."""
        return self.build_qmenu(parent)

    def refresh(self, snapshot=None) -> None:
        """Re-evaluate header text + quit-enabled gate on all QActions.

        ``snapshot`` is accepted for signal-slot convenience
        (``snapshot_changed.connect(section.refresh)``) but ignored --
        the predicates read from internal state on each call.  Safe to
        call before ``build_qmenu``; in that case it is a no-op.
        """
        if self._menu is None:
            return
        self._set_text(self._act_header, self.header_text())
        self._set_enabled(self._act_quit, self.quit_sethlans_enabled())

    # ---- internals ----
    @staticmethod
    def _add(menu: QMenu, label: str, slot: Callable) -> QAction:
        action = menu.addAction(label)
        action.triggered.connect(slot)
        return action

    @staticmethod
    def _add_disabled(menu: QMenu, text: str) -> QAction:
        action = menu.addAction(text)
        action.setEnabled(False)
        return action

    @staticmethod
    def _set_text(action: Optional[QAction], text: str) -> None:
        if action is not None:
            action.setText(text)

    @staticmethod
    def _set_enabled(action: Optional[QAction], enabled: bool) -> None:
        if action is not None:
            action.setEnabled(enabled)
