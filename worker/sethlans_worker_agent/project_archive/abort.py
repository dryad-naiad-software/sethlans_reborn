# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cancel, shutdown and deadline checks shared by every phase loop
(Unreal foundation FR-UE-2, FR-PA-4/7, Conc M5).

Every loop (copy wait, extract, overlay copy, working copy) calls
``Abort.check()`` on each iteration. The shutdown event is checked
**directly**, not only through the cancel watcher. ``cancel`` may be any
object with ``is_set()`` (a ``threading.Event`` or the executor's
``CancelCause``).
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from sethlans_worker_agent.project_archive.errors import AbortedError, PrepTimeoutError


@dataclass(frozen=True)
class Abort:
    cancel: Optional[object] = None
    shutdown: Optional[object] = None
    deadline: Optional[float] = None  # in ``clock`` units
    clock: Callable[[], float] = field(default=time.monotonic)

    def cause(self) -> Optional[str]:
        """``'shutdown'`` or ``'canceled'`` when set (shutdown wins), else None."""
        if self.shutdown is not None and self.shutdown.is_set():
            return 'shutdown'
        if self.cancel is not None and self.cancel.is_set():
            return 'canceled'
        return None

    def check(self) -> None:
        """Raise ``AbortedError`` or ``PrepTimeoutError`` when the phase must stop."""
        cause = self.cause()
        if cause is not None:
            raise AbortedError(cause)
        if self.deadline is not None and self.clock() > self.deadline:
            raise PrepTimeoutError("Unreal job preparation exceeded PREP_TIMEOUT_SECONDS.")


NEVER = Abort()
