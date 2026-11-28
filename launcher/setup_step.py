# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``SETUP`` state with the browser page (FR-LN-1, -3, -6; FR-SEC-1; D3).

:func:`enter_setup`:

0. writes the neutral ``STARTING`` (no port) for the tray, and stops the
   broadcaster with its params (no manager runs in setup);
1. writes the setup token: fresh on each entry, or the same one again,
   byte for byte, after a failed first start (FR-LN-6), so the open page
   keeps working;
2. writes or clears ``setup/last_failure``;
3. picks the setup port (``setup_port.choose_setup_port``; the port our
   Caddy already serves counts as free) and runs Caddy on it with the
   manager's certificate (made when missing);
4. starts ``run_manager --setup`` (its stderr in ``logs/setup-start.log``)
   and prints the URL, the token and the certificate's SHA-256;
5. once its ``/api/health/`` answers through Caddy, writes ``SETUP``
   with the port (and the reason) for the tray, dismisses the splash and
   opens the setup page (owner Q5: on entering ``SETUP`` only);
6. waits for setup mode to end; the settings file then decides (D8,
   ``loop_decisions.decide_after_setup``). A finished setup deletes the
   token file (FR-SEC-1); the loop keeps the token for FR-LN-6.

It returns the next ``Decision``. A quit request raises
``QuitRequested`` (the loop stops everything).

Unattended setup (``--setup-file``, chunk 8) is another setup step with
this signature, passed to ``machine_loop.run_loop`` in place of this one;
it ends with ``loop_decisions.SETUP_UNATTENDED`` as ``ctx.setup_kind``.
"""
from __future__ import annotations

import logging

from launcher import browser_launch, caddy_wiring, components, health_probe, setup_port, setup_state, supervision
from launcher import loop_decisions as ld
from launcher.loop_context import LoopContext, end_process, local_health_url, wait_for_exit
from shared import settings_file

logger = logging.getLogger(__name__)

SETUP_PAGE_PATH = "/setup/"
SETUP_HEALTH_TIMEOUT = 120.0
SETUP_STOP_GRACE = 5.0


def _token(ctx: LoopContext, policy: str) -> str:
    if policy == ld.TOKEN_SAME and ctx.token:
        setup_state.rewrite_token(ctx.token)
        return ctx.token
    if policy == ld.TOKEN_SAME:
        logger.error("No earlier setup token to keep; writing a fresh one")
    return setup_state.write_fresh_token()


def _last_failure(reason) -> None:
    if reason is None:
        setup_state.clear_last_failure()
    else:
        setup_state.write_last_failure(reason)


def _serve() -> int:
    """Caddy on the setup port; return the port (raises on failure)."""
    port = setup_port.choose_setup_port(held_by_us=caddy_wiring.running_public_port())
    caddy_wiring.start_or_retarget(port)
    return port


def _open_page(ctx: LoopContext, port: int) -> None:
    ctx.ready()
    headless = browser_launch.is_headless()
    if ld.should_open_browser(ld.SETUP, setup_kind=None, headless=headless,
                              no_browser=ctx.no_browser or ctx.print_url):
        browser_launch.open_browser(port, ctx.no_browser, ctx.print_url, SETUP_PAGE_PATH)


def enter_setup(ctx: LoopContext, decision: ld.Decision) -> ld.Decision:
    """Run ``SETUP`` until setup mode ends (module doc); return what comes next."""
    setup_state.write_launcher_state(ld.STARTING)
    supervision.stop_broadcaster(ctx.params_dir)
    token = _token(ctx, decision.token)
    _last_failure(decision.last_failure)
    try:
        port = _serve()
        ctx.setup_proc = components.start_setup_mode(port, ctx.setup_log)
    except Exception as exc:  # noqa: BLE001 - port, certificate, Caddy or start: one clear failure
        logger.exception("Setup could not start")
        return ld.Decision(ld.FAILED, reason=f"Setup could not start: {exc}")
    logger.info("Setup mode started (pid %s) on port %d", ctx.setup_proc.pid, port)
    browser_launch.print_setup_banner(port, SETUP_PAGE_PATH, token)
    if health_probe.wait_for_health(local_health_url(port), ctx.setup_proc, timeout=SETUP_HEALTH_TIMEOUT):
        setup_state.write_launcher_state(ld.SETUP, port=port, reason=decision.reason)
        _open_page(ctx, port)
    elif ctx.setup_proc.poll() is None:
        end_process(ctx.setup_proc, SETUP_STOP_GRACE)
        ctx.setup_proc = None
        return ld.Decision(ld.FAILED, reason=f"Setup did not answer within {SETUP_HEALTH_TIMEOUT:.0f} s")
    code = wait_for_exit(ctx.setup_proc)
    ctx.setup_proc = None
    logger.info("Setup mode ended with exit code %s", code)
    reason = components.failure_reason(ctx.setup_log, code, "Setup")
    nxt = ld.decide_after_setup(code, settings_file.setup_finished(), reason)
    if nxt.state == ld.STARTING:
        ctx.token = token
        ctx.setup_kind = ld.SETUP_BY_BROWSER
        setup_state.delete_token()
    return nxt
