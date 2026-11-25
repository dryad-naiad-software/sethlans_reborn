# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The submit state machine (FR-WZ-2): ``IDLE -> APPLYING -> SAVED``.

One lock guards the value. A submit may start only from ``IDLE``; a
failed one goes back to ``IDLE``; ``SAVED`` is final for the process.
"""
import threading
import time
from typing import Optional

IDLE = "IDLE"
APPLYING = "APPLYING"
SAVED = "SAVED"


class SubmitState:
    def __init__(self):
        self._lock = threading.Lock()
        self._value = IDLE
        self._saved_at: Optional[float] = None

    @property
    def value(self) -> str:
        with self._lock:
            return self._value

    def begin(self) -> bool:
        """``IDLE -> APPLYING``; False (the caller answers 409) from any other state."""
        with self._lock:
            if self._value != IDLE:
                return False
            self._value = APPLYING
            return True

    def reset(self) -> None:
        """``APPLYING -> IDLE`` after a failed submit."""
        with self._lock:
            if self._value == APPLYING:
                self._value = IDLE

    def mark_saved(self) -> None:
        """``APPLYING -> SAVED`` once ``sethlans.ini`` is written."""
        with self._lock:
            if self._value != APPLYING:
                raise RuntimeError(f"mark_saved from {self._value}")
            self._value = SAVED
            self._saved_at = time.monotonic()

    def saved_for(self) -> Optional[float]:
        """Seconds since ``SAVED``, or ``None`` before it."""
        with self._lock:
            return None if self._saved_at is None else time.monotonic() - self._saved_at


STATE = SubmitState()
