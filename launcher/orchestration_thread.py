# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""QThread wrapper around the launcher's orchestration routines.

After the splash refactor the Qt event loop owns the main thread while
boot orchestration (``run_wizard_mode`` / ``run_normal_mode``) runs on
this worker thread. Three signals bridge the two:

* :attr:`OrchestrationThread.cold_boot_ready` — emitted from inside
  the wrapped orchestration routine as soon as the cold-boot child's
  ``/api/health/`` endpoint answers (FR-2 / FR-4 — v2 spec). Wired to
  :meth:`launcher.splash.SethlansSplash.close_for_success`.
* :attr:`OrchestrationThread.startup_failed` — emitted on any uncaught
  exception raised BEFORE ``cold_boot_ready``, OR by orchestration on
  health timeout (FR-11(c)). Wired to
  :meth:`launcher.splash.SethlansSplash.morph_to_error`.
* :attr:`OrchestrationThread.finished_with_code` — emitted when the
  orchestration callable returns normally with its exit code.

``cold_boot_ready`` and ``startup_failed`` are fired from the
orchestration callable via the ``on_cold_boot_ready`` and
``on_startup_failed`` callbacks the thread injects. Both run on the
worker thread and MUST NOT touch Qt widgets — they only emit signals,
letting Qt's queued-connection delivery hop to the main thread.

FR-11(a): subprocess termination on cold-boot health timeout runs
synchronously inside the orchestration callable on this QThread, NOT
on the Qt main thread.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class OrchestrationThread(QThread):
    """Run a launcher orchestration callable on a background QThread."""

    cold_boot_ready = Signal()
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
        self._failed_signalled = False

    # ---- QThread API ----

    def run(self) -> None:  # noqa: D401 — Qt override
        """Invoke the wrapped orchestration function."""
        kwargs = dict(self._kwargs)
        # Inject our cold-boot callbacks. setdefault preserves any
        # caller-supplied override (reserved for tests).
        kwargs.setdefault(
            "on_cold_boot_ready", self._on_cold_boot_ready,
        )
        kwargs.setdefault(
            "on_startup_failed", self._on_startup_failed,
        )

        try:
            rc = self._target(*self._args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            if self._ready_signalled:
                # Post-ready failure: the splash is already gone; let
                # the normal logging + process-exit behaviour apply.
                logger.exception(
                    "Orchestration raised after cold_boot_ready; "
                    "surfacing to process exit",
                )
                self.finished_with_code.emit(1)
                return
            reason = _format_reason(exc)
            tb_text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            logger.exception("Startup failed before cold_boot_ready")
            if not self._failed_signalled:
                self._failed_signalled = True
                self.startup_failed.emit(reason, tb_text)
            self.finished_with_code.emit(1)
            return

        if not self._ready_signalled and not self._failed_signalled:
            # Orchestration returned without ever signalling ready or
            # failed. Degenerate path (e.g. immediate early exit); emit
            # ready so the splash dismisses.
            self.cold_boot_ready.emit()
            self._ready_signalled = True
        self.finished_with_code.emit(int(rc or 0))

    # ---- internals ----

    def _on_cold_boot_ready(self) -> None:
        if self._ready_signalled or self._failed_signalled:
            return
        self._ready_signalled = True
        self.cold_boot_ready.emit()

    def _on_startup_failed(self, reason: str, trace: str) -> None:
        # Idempotent — orchestration may surface the failed signal once
        # via the explicit callback path (cold-boot timeout) and
        # ``run`` MUST NOT re-emit if the callable subsequently exits.
        if self._failed_signalled or self._ready_signalled:
            return
        self._failed_signalled = True
        self.startup_failed.emit(reason, trace or "")


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
