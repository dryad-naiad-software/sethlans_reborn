# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""QThread wrapper around the launcher's orchestration routines.

After the splash refactor the Qt event loop owns the main thread while
boot orchestration (``run_setup_mode`` / ``run_normal_mode``) runs on
this worker thread.  Three signals bridge the two:

* :attr:`OrchestrationThread.manager_ready` — emitted from inside the
  wrapped orchestration routine as soon as the manager HTTPS endpoint
  answers.  Wired to :meth:`launcher.splash.SethlansSplash.close_for_success`.
* :attr:`OrchestrationThread.startup_failed` — emitted on any uncaught
  exception raised BEFORE ``manager_ready``.  Wired to
  :meth:`launcher.splash.SethlansSplash.morph_to_error`.
* :attr:`OrchestrationThread.finished_with_code` — emitted when the
  orchestration callable returns normally with its exit code.

``manager_ready`` is fired from the orchestration callable itself via
an ``on_manager_ready`` callback that the thread injects.  The callback
runs on the worker thread and MUST NOT touch Qt widgets — it only
emits the signal, letting Qt's queued-connection delivery hop to the
main thread.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class OrchestrationThread(QThread):
    """Run a launcher orchestration callable on a background QThread."""

    manager_ready = Signal()
    startup_failed = Signal(str, str)
    finished_with_code = Signal(int)

    def __init__(
        self,
        target: Callable[..., int],
        args: tuple = (),
        kwargs: Optional[dict] = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._target = target
        self._args = args
        self._kwargs = dict(kwargs or {})
        self._ready_signalled = False

    # ---- QThread API ----

    def run(self) -> None:  # noqa: D401 — Qt override
        """Invoke the wrapped orchestration function."""
        kwargs = dict(self._kwargs)
        # Inject our ready callback.  Overrides any caller-supplied one
        # (which would be unusual — reserved for tests).
        kwargs.setdefault("on_manager_ready", self._on_manager_ready)

        try:
            rc = self._target(*self._args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            if self._ready_signalled:
                # Post-ready failure: the splash is already gone; re-raise
                # so the normal logging + process-exit behaviour applies.
                logger.exception(
                    "Orchestration raised after manager_ready; "
                    "surfacing to process exit",
                )
                self.finished_with_code.emit(1)
                return
            reason = _format_reason(exc)
            tb_text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            logger.exception("Startup failed before manager_ready")
            self.startup_failed.emit(reason, tb_text)
            self.finished_with_code.emit(1)
            return

        if not self._ready_signalled:
            # Orchestration returned without ever signalling ready.  This
            # only happens in degenerate paths (e.g. immediate early exit
            # during setup mode); still emit so the splash dismisses.
            self.manager_ready.emit()
            self._ready_signalled = True
        self.finished_with_code.emit(int(rc or 0))

    # ---- internals ----

    def _on_manager_ready(self) -> None:
        if self._ready_signalled:
            return
        self._ready_signalled = True
        self.manager_ready.emit()


def _format_reason(exc: BaseException) -> str:
    """Build a single-line reason string for the splash error card."""
    msg = str(exc)
    if msg and "\n" not in msg:
        return msg
    cls = type(exc).__name__
    if not msg:
        return cls
    first = msg.splitlines()[0]
    return f"{cls}: {first}"


__all__ = ["OrchestrationThread"]
