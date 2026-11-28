# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The launcher's one loop (FR-LN-1): ``SETUP``, ``STARTING``, ``RUNNING``,
``STOPPING``, and the tray failure state ``FAILED``.

:func:`run_loop` starts in ``SETUP`` (fresh token) when setup is not
finished (``settings_file.setup_finished``), else in ``STARTING`` for the
finished mode. Each state returns the next ``loop_decisions.Decision``:

* ``SETUP``: the setup step (``setup_step.enter_setup``, the browser page;
  chunk 8 passes the unattended one as ``setup_step``);
* ``STARTING``: ``starting.start_mode``;
* ``RUNNING``: ``starting.enter_running``, then ``running.run``;
* ``FAILED`` (owner Q2/Q3): Caddy stops, so nothing is open on the LAN;
  the tray shows the reason with View Logs and Quit; nothing is retried.
  Only Quit, or a Reset from the tray, leaves it.

A quit request (tray or signal) at any point raises ``QuitRequested``;
the loop then stops everything in ``STOPPING``: the worker, the manager
or setup mode, and the tray, in that order (FR-21; a second Quit kills
at once), then Caddy, and deletes the token file. Any other error stops
the children the same way and is raised to the caller.

``setup/launcher_state.json`` is written on each state entry for the
tray (6c).
"""
from __future__ import annotations

import logging
import sys
from typing import Callable

from launcher import caddy_wiring, cascade, reset_setup, running, setup_state, setup_step, starting, supervision
from launcher import loop_decisions as ld
from launcher.health_probe import QuitRequested
from launcher.loop_context import LoopContext, raise_if_quit
from shared import settings_file

logger = logging.getLogger(__name__)

FAILED_POLL_SECONDS = 0.5
SetupStep = Callable[[LoopContext, ld.Decision], ld.Decision]


def initial_decision() -> ld.Decision:
    """``SETUP`` with a fresh token when setup is not finished, else ``STARTING``
    (a token file left by an earlier run is deleted then)."""
    mode = settings_file.setup_finished()
    if mode is None:
        return ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH)
    setup_state.delete_token()
    return ld.Decision(ld.STARTING, mode=mode)


def _close_setup(ctx: LoopContext) -> None:
    setup_state.delete_token()
    ctx.token = None
    ctx.setup_kind = None


def failed(ctx: LoopContext, reason: str) -> ld.Decision:
    """``FAILED``: wait for Quit (raises) or a tray Reset (``SETUP``)."""
    caddy_wiring.stop_caddy()
    supervision.stop_broadcaster(ctx.params_dir)
    ctx.port = None
    _close_setup(ctx)
    ctx.accept_markers_from_now()  # before the tray can see FAILED
    setup_state.write_launcher_state(ld.FAILED, reason=reason)
    logger.error("Sethlans could not start: %s", reason)
    print(f"[ERROR] Sethlans could not start: {reason}", file=sys.stderr)
    ctx.failed(reason)
    while True:
        raise_if_quit()
        action = ld.ipc_precedence(*ctx.consume_markers())
        if action is not None and action[0] == ld.IPC_QUIT:
            raise QuitRequested()
        if action is not None and action[0] == ld.IPC_RESET:
            reset_setup.reset_running(ctx)
            return ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH)
        supervision.wait_or_quit(FAILED_POLL_SECONDS)


def _step(ctx: LoopContext, decision: ld.Decision, setup: SetupStep) -> ld.Decision:
    logger.info("Launcher state: %s", decision.state)
    if decision.state == ld.SETUP:
        return setup(ctx, decision)
    if decision.state == ld.STARTING:
        return starting.start_mode(ctx, decision.mode)
    if decision.state == ld.RUNNING:
        starting.enter_running(ctx, decision.reason)
        return running.run(ctx)
    if decision.state == ld.FAILED:
        return failed(ctx, decision.reason or "Unknown failure")
    raise ValueError(f"Unknown launcher state {decision.state!r}")


def stop_all(ctx: LoopContext) -> None:
    """``STOPPING`` (module doc); every step is attempted."""
    try:
        setup_state.write_launcher_state(ld.STOPPING)
    except OSError as exc:
        logger.warning("Could not write the launcher state: %s", exc)
    cascade.cascade_quit(ctx.worker, ctx.manager or ctx.setup_proc, ctx.tray,
                         second_quit_check=ctx.second_quit_check)
    ctx.worker = ctx.manager = ctx.setup_proc = None
    caddy_wiring.stop_caddy()
    supervision.stop_broadcaster(ctx.params_dir)
    try:
        _close_setup(ctx)
    except OSError as exc:
        logger.warning("Could not delete the setup token: %s", exc)


def run_loop(ctx: LoopContext, setup: SetupStep = setup_step.enter_setup) -> int:
    """Run the loop until a quit; 0 then. Other errors are raised after ``STOPPING``."""
    try:
        decision = initial_decision()
        while True:
            raise_if_quit()
            decision = _step(ctx, decision, setup)
    except QuitRequested:
        if supervision.was_signalled():
            logger.info("Signal received; stopping")
        else:
            logger.info("Quit requested; stopping")
        ctx.ready()
    finally:
        stop_all(ctx)
    return 0
