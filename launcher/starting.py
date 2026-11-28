# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``STARTING`` state and the entry into ``RUNNING`` (FR-LN-1, -2, -6; D3).

:func:`start_mode` starts what the finished mode runs:

* ``manager`` and ``manager_worker``: Caddy on ``[server] port``
  (restarted only when its Caddyfile changes), then the manager, its
  stderr in ``logs/manager-start.log``. Its ``/api/health/`` through
  Caddy is waited for up to :data:`HEALTH_TIMEOUT` while it lives. In
  ``manager_worker`` the worker starts only once the manager answers (it
  needs the local worker's token the manager writes, FR-FS-3).
* ``worker``: Caddy stops (D3) and only the worker starts.

A manager that ends, or does not answer in time (it is then stopped), is
decided by ``loop_decisions.decide_after_manager_exit`` with the reason
``components.failure_reason`` reads. In ``worker`` mode the worker is
decided the same way (``decide_after_worker_exit``); in ``manager_worker``
its end is reported in ``RUNNING`` and the manager runs on.

The broadcaster stops (its params deleted) before a manager starts and
when one fails, so it never advertises a manager that is not running;
the new manager writes its params again.

A new manager starts only after the earlier one has ended: on Windows
``manager-start.log`` cannot be replaced while a process holds it, and
that ``PermissionError`` is a clear start failure, never a hang.

:func:`enter_running` clears ``last_failure``, writes the state, dismisses
the splash and opens the browser only after an unattended setup (owner
Q5; FR-LN-2); with ``--print-url`` it prints the address instead.
"""
from __future__ import annotations

import logging
from typing import Optional

from launcher import browser_launch, caddy_wiring, cascade, components, health_probe, setup_state, supervision
from launcher import loop_decisions as ld
from launcher.loop_context import LoopContext, end_process, local_health_url, worker_health_port
from shared.topology import has_manager, has_worker

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 180.0
DASHBOARD_PATH = "/"


def _not_answering(program: str) -> str:
    return f"{program} did not answer within {HEALTH_TIMEOUT:.0f} s"


def _launch_manager(ctx: LoopContext) -> Optional[str]:
    """Caddy and the manager; the reason when they could not start, else ``None``."""
    try:
        ctx.port = caddy_wiring.configured_public_port()
        caddy_wiring.start_or_retarget(ctx.port)
        supervision.stop_broadcaster(ctx.params_dir)  # the new manager writes its params again
        ctx.manager = components.start_manager(ctx.manager_log)
    except PermissionError as exc:
        logger.error("Could not replace %s: %s", ctx.manager_log, exc)
        return (f"The manager could not start: {ctx.manager_log.name} could not be replaced "
                "(an earlier manager may still hold it)")
    except Exception as exc:  # noqa: BLE001 - certificate, Caddy or start: one clear failure
        logger.exception("The manager could not start")
        return f"The manager could not start: {exc}"
    logger.info("Manager started (pid %s); waiting for it on port %d", ctx.manager.pid, ctx.port)
    return None


def _start_manager(ctx: LoopContext, right_after: bool) -> Optional[ld.Decision]:
    """``None`` once the manager answers, else the decision for its failure."""
    reason = _launch_manager(ctx)
    code = -1
    if reason is None:
        if health_probe.wait_for_health(local_health_url(ctx.port), ctx.manager, timeout=HEALTH_TIMEOUT):
            return None
        exited = ctx.manager.poll() is not None
        ended = end_process(ctx.manager, cascade.MANAGER_GRACE_SECONDS)
        code = ended if exited else -1
        reason = (components.failure_reason(ctx.manager_log, code) if exited
                  else _not_answering("The manager"))
    ctx.manager = None
    supervision.stop_broadcaster(ctx.params_dir)
    logger.warning("The manager did not start: %s", reason)
    return ld.decide_after_manager_exit(code, right_after_setup=right_after, reason=reason)


def _start_worker(ctx: LoopContext) -> Optional[str]:
    """The worker; ``None`` once its page answers, else why it did not start."""
    try:
        url = local_health_url(worker_health_port())
        ctx.worker = components.start_worker()
    except Exception as exc:  # noqa: BLE001
        logger.exception("The worker could not start")
        return f"The worker could not start: {exc}"
    logger.info("Worker started (pid %s)", ctx.worker.pid)
    if health_probe.wait_for_health(url, ctx.worker, timeout=HEALTH_TIMEOUT):
        return None
    exited = ctx.worker.poll() is not None
    code = end_process(ctx.worker, cascade.WORKER_GRACE_SECONDS)
    ctx.worker = None
    return (components.failure_reason(None, code, "The worker") if exited
            else _not_answering("The worker"))


def start_mode(ctx: LoopContext, mode: str) -> ld.Decision:
    """Run ``STARTING`` for ``mode``; return ``RUNNING`` or the failure decision."""
    ctx.mode = mode
    right_after = ctx.setup_kind is not None
    setup_state.write_launcher_state(ld.STARTING)
    setup_state.delete_token()  # setup is closed now; the loop keeps the token for FR-LN-6
    if has_manager(mode):
        failed = _start_manager(ctx, right_after)
        if failed is not None:
            return failed
    else:
        caddy_wiring.stop_caddy()
        ctx.port = None
    note = None
    if has_worker(mode):
        note = _start_worker(ctx)
        if note is not None:
            logger.warning("The worker did not start: %s", note)
            if not has_manager(mode):
                return ld.decide_after_worker_exit(right_after_setup=right_after, reason=note)
    return ld.Decision(ld.RUNNING, mode=mode, reason=note)


def _show_dashboard(ctx: LoopContext) -> None:
    """``--print-url`` prints the address; else the browser opens per FR-LN-2 / owner Q5."""
    if ctx.print_url:
        print(f"Sethlans is running at: https://localhost:{ctx.port}{DASHBOARD_PATH}")
    elif ld.should_open_browser(ld.RUNNING, setup_kind=ctx.setup_kind,
                                headless=browser_launch.is_headless(), no_browser=ctx.no_browser):
        browser_launch.open_browser(ctx.port, ctx.no_browser, ctx.print_url, DASHBOARD_PATH)


def enter_running(ctx: LoopContext, note: Optional[str] = None) -> None:
    """``RUNNING`` reached (module doc); ``note`` is a worker that did not start."""
    setup_state.clear_last_failure()
    ctx.accept_markers_from_now()  # before the tray can see RUNNING and offer Reset
    setup_state.write_launcher_state(ld.RUNNING, port=ctx.port, reason=note)
    ctx.ready()
    logger.info("Sethlans is running (%s)", ctx.mode)
    if ctx.port is not None:
        _show_dashboard(ctx)
    ctx.setup_kind = None
    ctx.token = None
