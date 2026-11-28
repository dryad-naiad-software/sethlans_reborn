# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``RUNNING`` state (FR-LN-1, FR-RS-1).

:func:`run` watches the tray and the children until something moves the
loop on:

* tray requests, quit beating reset beating restart
  (``loop_decisions.ipc_precedence``):
  - Quit: everything stops (``QuitRequested``), except Quit Manager
    while a worker runs, which stops the manager only;
  - Reset: ``reset_setup.reset_running``, then ``SETUP`` with a fresh
    token;
  - Restart: the manager alone (``restart_orchestrator``, the new one
    only after the old one has ended), or with target ``all`` both,
    through ``STARTING`` again;
* a child that ends: :func:`on_process_ended` logs it and writes the
  reason for the tray; the other child and the launcher keep running (a
  later decision adds restarts here).
"""
from __future__ import annotations

import logging
from typing import Optional

from launcher import cascade, components, reset_setup, restart_orchestrator, setup_state, supervision
from launcher import loop_decisions as ld
from launcher.health_probe import QuitRequested
from launcher.loop_context import LoopContext
from shared.topology import has_manager

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.5
TARGET_MANAGER = "manager"
TARGET_ALL = "all"


def _alive(proc) -> bool:
    return proc is not None and proc.poll() is None


def _note(ctx: LoopContext, reason: str) -> None:
    setup_state.write_launcher_state(ld.RUNNING, port=ctx.port, reason=reason)


def on_process_ended(ctx: LoopContext, name: str, code: int) -> None:
    """A child ended while running: log it, tell the tray, keep the rest (FR-LN-1)."""
    if name == "manager":
        reason = components.failure_reason(ctx.manager_log, code)
    else:
        reason = components.failure_reason(None, code, "The worker")
    logger.warning("The %s ended (exit code %s): %s; the launcher keeps running", name, code, reason)
    _note(ctx, reason)


def _reap(ctx: LoopContext) -> None:
    for name in ("manager", "worker"):
        proc = getattr(ctx, name)
        if proc is not None and proc.poll() is not None:
            setattr(ctx, name, None)
            on_process_ended(ctx, name, proc.returncode)


def _handle_quit(ctx: LoopContext) -> None:
    """Raise for a full quit. Quit Manager while a worker runs stops only the
    manager (if one still runs) and never the worker; it is then cleared,
    unless a full quit came in meanwhile (``clear_manager_quit``)."""
    target = supervision.quit_target()
    if target is None:
        return
    if target == TARGET_MANAGER and _alive(ctx.worker):
        if ctx.manager is not None:
            cascade.quit_manager_only(ctx.manager)
            ctx.manager = None
        supervision.stop_broadcaster(ctx.params_dir)
        if supervision.clear_manager_quit():
            _note(ctx, "The manager was stopped from the tray")
            return
    raise QuitRequested()


def _restart_manager(ctx: LoopContext) -> None:
    def respawn():
        supervision.stop_broadcaster(ctx.params_dir)  # the new manager writes its params again
        return components.start_manager(ctx.manager_log)

    if ctx.manager is None:
        try:
            ctx.manager = respawn()
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not start the manager: %s", exc)
    else:
        ctx.manager = restart_orchestrator.handle_restart_request(ctx.manager, ctx.settings_dir, respawn)
    if ctx.manager is None:
        _note(ctx, "The manager could not be restarted; see launcher.log")
    else:
        _note(ctx, None)


def _restart(ctx: LoopContext, target: str) -> Optional[ld.Decision]:
    if target == TARGET_ALL:
        cascade.cascade_quit(ctx.worker, ctx.manager, None, second_quit_check=ctx.second_quit_check)
        ctx.worker = ctx.manager = None
        return ld.Decision(ld.STARTING, mode=ctx.mode)
    if has_manager(ctx.mode):
        _restart_manager(ctx)
    else:
        logger.info("Restart of the manager asked in %s mode; nothing to restart", ctx.mode)
    return None


def _act(ctx: LoopContext, action) -> Optional[ld.Decision]:
    name, target = action
    if name == ld.IPC_QUIT:
        supervision.request_quit(target)
        _handle_quit(ctx)
        return None
    if name == ld.IPC_RESET:
        reset_setup.reset_running(ctx)
        return ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH)
    return _restart(ctx, target)


def run(ctx: LoopContext) -> ld.Decision:
    """Watch until a reset or a full restart (the next decision) or a quit (raises)."""
    while True:
        _handle_quit(ctx)
        action = ld.ipc_precedence(*ctx.consume_markers())
        if action is not None:
            nxt = _act(ctx, action)
            if nxt is not None:
                return nxt
        _reap(ctx)
        supervision.wait_or_quit(POLL_SECONDS)
