# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cold-boot orchestration helpers shared across launcher entry points.

This module hosts the cross-cutting helpers that drive the cold-boot
splash-dismissal contract from the v2 splash phase states spec:

* :func:`fail_cold_boot` — emit ``startup_failed`` FIRST, then terminate
  cold-boot child(ren) using the parallel-terminate / sequential-wait
  pattern (FR-11 a/b/c).
* :func:`safe_invoke` — invoke a splash callback, swallowing exceptions
  so a misbehaving slot cannot crash orchestration.
* :func:`parallel_terminate` — issue ``terminate()`` on both children
  before awaiting either ``wait()``, so total latency on health timeout
  is bounded by ``max(grace)`` rather than the sum.

Split out of ``launcher/orchestration.py`` so neither file crosses the
300-line ceiling after the v2 refactor (NFR-1).
"""

from __future__ import annotations

import logging
import subprocess
from typing import Callable, Optional

from launcher import cascade

logger = logging.getLogger(__name__)


def safe_invoke(cb: Optional[Callable[..., None]], *args) -> None:
    """Invoke a splash callback, swallowing exceptions.

    The callback hops to the Qt main thread via a queued signal — if a
    future change wires it to a slot that raises, we still want
    orchestration to keep running (the splash is best-effort UI).
    """
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:  # noqa: BLE001
        logger.exception("splash callback raised; ignoring")


def _terminate_proc(proc: Optional[subprocess.Popen]) -> None:
    """Issue ``proc.terminate()`` once, swallowing OSError.

    Used by :func:`parallel_terminate` to issue ``terminate()`` back to
    back on multiple children without one failure skipping the other
    (FR-11(b)).
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError as exc:
        logger.warning("terminate() failed: %s", exc)


def _wait_with_grace(
    proc: Optional[subprocess.Popen], grace: float, name: str,
) -> None:
    """Wait up to ``grace`` seconds for ``proc`` to exit; SIGKILL on timeout."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        logger.warning(
            "%s did not exit within %.1fs after terminate(); SIGKILL",
            name, grace,
        )
        try:
            proc.kill()
        except OSError:
            pass


def parallel_terminate(
    manager_proc: Optional[subprocess.Popen],
    worker_proc: Optional[subprocess.Popen],
) -> None:
    """FR-11(b): issue both ``terminate()`` calls before either ``wait()``.

    Wraps each ``terminate()`` in its own try/except so an exception on
    the manager terminate does not skip the worker terminate (or vice
    versa). Sequential ``wait()`` after both terminate signals are out
    bounds total latency at ``max(grace)`` rather than their sum.
    """
    _terminate_proc(manager_proc)
    _terminate_proc(worker_proc)
    _wait_with_grace(
        manager_proc, cascade.MANAGER_GRACE_SECONDS, "manager",
    )
    _wait_with_grace(
        worker_proc, cascade.WORKER_GRACE_SECONDS, "worker",
    )


def fail_cold_boot(
    reason: str,
    manager_proc: Optional[subprocess.Popen],
    worker_proc: Optional[subprocess.Popen],
    on_startup_failed: Optional[Callable[[str, str], None]],
) -> int:
    """Emit startup_failed, then terminate cold-boot child(ren).

    FR-11(c): ``startup_failed`` MUST be emitted BEFORE termination so
    the splash error card appears within ~250 ms of budget expiry,
    regardless of grace-period durations. Returns ``1`` so the caller
    can propagate the exit code without conditional branching.
    """
    logger.warning("cold-boot health timeout: %s", reason)
    safe_invoke(on_startup_failed, reason, "")
    parallel_terminate(manager_proc, worker_proc)
    return 1


__all__ = [
    "fail_cold_boot",
    "parallel_terminate",
    "safe_invoke",
]
