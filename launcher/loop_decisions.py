# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The launcher loop's decisions, as pure functions (D8, FR-LN-1, -2, -6).

No files, processes or clocks: the loop (``launcher.machine_loop``)
gathers the facts (exit code, ``setup_finished()``, the reason) and acts
on the answer.

* :func:`decide_after_setup`: setup mode ended. The settings file
  decides (D8): finished means ``STARTING`` in that mode, whatever the
  exit code; not finished means ``FAILED`` with the reason (exit 10
  with an unfinished file gets its own reason). The person quitting is
  handled by the loop before it asks.
* :func:`decide_after_manager_exit`: the manager ended during
  ``STARTING``.
  - Right after a setup: back to ``SETUP`` with the **same** token and
    the reason in ``last_failure`` (FR-LN-6), for 11, 12, 13 and any
    other ending: setup was open a moment ago, and the open page shows
    the reason.
  - A normal start: 12 (no admin) re-opens ``SETUP`` with a fresh token
    and the reason. 11, 13 and anything else go to ``FAILED`` with the
    reason: setup stays closed to the LAN, and nothing is retried.
* :func:`decide_after_worker_exit`: in ``worker`` mode the worker is
  what the mode stands on. Right after a setup, back to ``SETUP`` with
  the same token, as for the manager; on a normal start ``FAILED``
  (the worker has no "no admin" ending). In ``manager_worker`` the
  manager runs on and the loop reports the worker's end instead.
* :func:`should_open_browser`: on entering ``SETUP``, and on the first
  ``RUNNING`` after an unattended setup only (the setup page already
  goes to ``/login/`` after a browser setup). Never when headless or
  ``--no-browser``.
* :func:`ipc_precedence`: quit beats reset, which beats restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shared.manager_exit_codes import EXIT_NO_ADMIN, EXIT_SETUP_SAVED

SETUP = "SETUP"
STARTING = "STARTING"
RUNNING = "RUNNING"
STOPPING = "STOPPING"
FAILED = "FAILED"

TOKEN_NONE = "none"
TOKEN_FRESH = "fresh"
TOKEN_SAME = "same"

# How the setup that came just before ended (None: a normal start).
SETUP_BY_BROWSER = "browser"
SETUP_UNATTENDED = "unattended"  # --setup-file, chunk 8

IPC_QUIT = "quit"
IPC_RESET = "reset"
IPC_RESTART = "restart"


@dataclass(frozen=True)
class Decision:
    """Where the loop goes next.

    ``token`` says what to do with the setup token on entering ``SETUP``
    (``TOKEN_FRESH`` / ``TOKEN_SAME``); ``last_failure`` is the reason to
    write to ``setup/last_failure`` (``None``: clear it); ``reason`` is
    what the tray shows.
    """

    state: str
    mode: Optional[str] = None
    token: str = TOKEN_NONE
    reason: Optional[str] = None
    last_failure: Optional[str] = None


def decide_after_setup(exit_code: int, finished_mode: Optional[str], reason: str) -> Decision:
    """Setup mode ended with ``exit_code``; ``finished_mode`` is ``setup_finished()`` (D8)."""
    if finished_mode is not None:
        return Decision(STARTING, mode=finished_mode)
    if exit_code == EXIT_SETUP_SAVED:
        reason = "Setup reported the settings saved, but the settings file is not finished"
    return Decision(FAILED, reason=reason)


def decide_after_manager_exit(exit_code: int, *, right_after_setup: bool, reason: str) -> Decision:
    """The manager ended in ``STARTING`` with ``exit_code`` (FR-LN-6, owner Q2/Q3)."""
    if right_after_setup:  # every ending, not only 11/12/13
        return Decision(SETUP, token=TOKEN_SAME, reason=reason, last_failure=reason)
    if exit_code == EXIT_NO_ADMIN:
        return Decision(SETUP, token=TOKEN_FRESH, reason=reason, last_failure=reason)
    return Decision(FAILED, reason=reason)


def decide_after_worker_exit(*, right_after_setup: bool, reason: str) -> Decision:
    """The worker ended in ``STARTING`` in ``worker`` mode (see the module doc)."""
    if right_after_setup:
        return Decision(SETUP, token=TOKEN_SAME, reason=reason, last_failure=reason)
    return Decision(FAILED, reason=reason)


def should_open_browser(entering: str, *, setup_kind: Optional[str], headless: bool, no_browser: bool) -> bool:
    """FR-LN-2 with the owner's Q5 decision; ``setup_kind`` is how the setup before ended."""
    if headless or no_browser:
        return False
    if entering == SETUP:
        return True
    return entering == RUNNING and setup_kind == SETUP_UNATTENDED


def ipc_precedence(
    quit_target: Optional[str], reset_target: Optional[str], restart_target: Optional[str],
) -> Optional[tuple]:
    """``(action, target)`` of the tray request that wins, or ``None``."""
    for action, target in ((IPC_QUIT, quit_target), (IPC_RESET, reset_target), (IPC_RESTART, restart_target)):
        if target is not None:
            return action, target
    return None
