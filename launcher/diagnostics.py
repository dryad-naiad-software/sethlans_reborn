# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher process-level diagnostics (issue #168).

Installs ``sys.excepthook`` and ``threading.excepthook`` so uncaught
exceptions on any thread land in ``launcher.log`` instead of
disappearing silently. Also exposes :func:`log_exit_summary` — the
single grep-friendly INFO line emitted at the top of ``main()``'s
``finally`` block so every launcher exit path is attributable.

The original silent-exit observed in #168 left ``launcher.log`` ending
at ``wizard spawned (pid=...)`` with no termination, timeout, or
shutdown line. The hooks installed here ensure any future occurrence
produces actionable evidence; the actual root cause (still unknown at
the time of this module's introduction) will be diagnosed from the
logs the hooks now produce.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level flag so :func:`install_diagnostics` is idempotent
# (NFR-4). Re-entering ``main()`` from tests must not double-install
# the hooks; doing so would shadow ``sys.__excepthook__`` with our own
# wrapper on the second call and break KI pass-through.
_HOOKS_INSTALLED = False

# Fallback path for the file-write branch when the logging handler is
# already torn down. Sits next to ``launcher.log`` in the per-user
# data dir when one was supplied; otherwise the cwd.
_FALLBACK_LOG_NAME = "launcher_excepthook.log"


def _fallback_write(
    exc_type: type,
    exc_value: BaseException,
    exc_tb,
    fallback_dir: Optional[Path] = None,
) -> None:
    """Best-effort write to a sibling log file when stdlib logging is
    unavailable (e.g., handler already closed during interpreter
    shutdown). Swallows every error — if even this fails there is
    nothing more we can do.
    """
    target = (
        Path(fallback_dir) / _FALLBACK_LOG_NAME
        if fallback_dir is not None
        else Path(_FALLBACK_LOG_NAME)
    )
    try:
        with open(target, "a", encoding="utf-8") as fp:
            fp.write(
                f"[{time.ctime()}] Uncaught: "
                f"{exc_type.__name__}: {exc_value}\n",
            )
            traceback.print_exception(exc_type, exc_value, exc_tb, file=fp)
    except Exception:  # noqa: BLE001 — last-ditch sink, must not raise
        pass


def install_diagnostics(fallback_dir: Optional[Path] = None) -> None:
    """Install ``sys.excepthook`` + ``threading.excepthook`` (FR-1/FR-2).

    Idempotent (NFR-4): a second call is a no-op. KeyboardInterrupt is
    delegated unchanged to ``sys.__excepthook__`` so Ctrl-C still
    produces a clean exit (NFR-5).
    """
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return

    def _excepthook(exc_type, exc_value, exc_tb):
        # NFR-5: KI must flow through to the default hook unchanged.
        # We MUST NOT log it; a clean Ctrl-C is not a diagnostic event.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        try:
            logger.error(
                "Uncaught main-thread exception: %s",
                exc_type.__name__,
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:  # noqa: BLE001 — handler may be torn down
            _fallback_write(exc_type, exc_value, exc_tb, fallback_dir)
        # Always defer to the default hook so the process still exits
        # with the original traceback on stderr.
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _threading_excepthook(args):
        # SystemExit on a background thread is a benign control-flow
        # signal; the default hook ignores it and so do we.
        if issubclass(args.exc_type, SystemExit):
            return
        try:
            logger.error(
                "Uncaught exception in thread %r: %s",
                args.thread.name if args.thread else "<unknown>",
                args.exc_type.__name__,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:  # noqa: BLE001 — handler may be torn down
            _fallback_write(
                args.exc_type, args.exc_value, args.exc_traceback,
                fallback_dir,
            )

    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook
    _HOOKS_INSTALLED = True


def log_exit_summary(
    rc: int,
    started_at: float,
    *,
    tray_torn_down: bool,
    supervisors_shut_down: bool,
) -> None:
    """Emit the single grep-friendly INFO line for ``main()``'s exit.

    FR-7: every launcher exit path (normal, exception, KeyboardInterrupt,
    lock-acquire-failure) calls this from inside the ``finally`` block,
    BEFORE any logging handler tear-down. Format is fixed so log
    investigators can grep for ``"Launcher exiting"`` and parse the
    fields without scraping multi-line tracebacks.
    """
    elapsed = time.monotonic() - started_at
    logger.info(
        "Launcher exiting rc=%d elapsed=%.2fs "
        "tray_torn_down=%s supervisors_shut_down=%s",
        rc, elapsed, tray_torn_down, supervisors_shut_down,
    )


def finalize_main(
    rc: int,
    started_at: float,
    *,
    shutdown_supervisors,
    release_lock,
    instance_lock,
) -> None:
    """Run the launcher's ``main()`` finally block (FR-6/FR-7).

    Each teardown phase is wrapped in its own try/except so a failure
    in one phase cannot skip the others or the exit-summary log line.
    The exit summary lands LAST so it reflects whether supervisors
    actually shut down cleanly.
    """
    supervisors_shut_down = False
    try:
        shutdown_supervisors()
        supervisors_shut_down = True
    except Exception:  # noqa: BLE001
        logger.exception("Error during supervisor shutdown")
    try:
        release_lock(instance_lock)
    except Exception:  # noqa: BLE001
        logger.exception("Error releasing single-instance lock")
    log_exit_summary(
        rc=rc, started_at=started_at,
        tray_torn_down=True,
        supervisors_shut_down=supervisors_shut_down,
    )


def _reset_for_tests() -> None:
    """Reset the module-level idempotence flag.

    Test-only helper: production code never calls this. Allows a unit
    test to verify install/re-install behaviour without leaking the
    installed hooks across the rest of the suite.
    """
    global _HOOKS_INSTALLED
    _HOOKS_INSTALLED = False


__all__ = [
    "install_diagnostics",
    "log_exit_summary",
]
