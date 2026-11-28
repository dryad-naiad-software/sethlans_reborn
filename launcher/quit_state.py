# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The launcher's process-wide quit request (issue #163, FR-21).

A quit has a target: ``"all"`` (Quit Sethlans, a signal) or
``"manager"`` (Quit Manager). The rules:

* the target and the event change together under one lock, so no reader
  ever sees the event set with no target;
* ``"all"`` is never downgraded to ``"manager"``;
* :func:`clear_manager_quit` clears only a pending ``"manager"`` request
  (compare-and-clear), so a Quit Sethlans or a signal that arrives while
  the manager is being stopped is not lost;
* the signal handler (:func:`signal_quit`) takes no lock and logs
  nothing: it sets a plain flag, which :func:`quit_target` reports as
  ``"all"``. Waits (:func:`wait_or_quit`) notice it within their timeout.

``launcher.supervision`` re-exports these names.
"""
from __future__ import annotations

import threading
from typing import Optional

QUIT_ALL = "all"
QUIT_MANAGER = "manager"

_lock = threading.Lock()
_event = threading.Event()
_target: Optional[str] = None
_signalled = False  # set by the signal handler only; read without a lock


def request_quit(target: str = QUIT_ALL) -> None:
    """Ask the loop to quit ``target``; a pending ``"all"`` stays ``"all"``."""
    global _target
    with _lock:
        if _target != QUIT_ALL:
            _target = target
        _event.set()


def signal_quit() -> None:
    """For the signal handler: no lock, no logging, one assignment."""
    global _signalled
    _signalled = True


def was_signalled() -> bool:
    return _signalled


def quit_target() -> Optional[str]:
    """``"all"`` after a signal, else the pending target, else ``None``."""
    if _signalled:
        return QUIT_ALL
    with _lock:
        return _target


def quit_requested() -> bool:
    return quit_target() is not None


def clear_manager_quit() -> bool:
    """Clear a pending ``"manager"`` quit (it was handled); True when cleared.

    A pending ``"all"`` or a signal is never cleared.
    """
    global _target
    with _lock:
        if _signalled or _target != QUIT_MANAGER:
            return False
        _target = None
        _event.clear()
        return True


def wait_or_quit(timeout: float) -> bool:
    """Wait up to ``timeout`` seconds; True as soon as a quit is requested."""
    if _signalled:
        return True
    return _event.wait(timeout=timeout) or _signalled


def get_event() -> threading.Event:
    """The event a tray quit sets (a signal sets only the flag)."""
    return _event


def reset_for_tests() -> None:
    global _target, _signalled
    with _lock:
        _target = None
        _signalled = False
        _event.clear()
