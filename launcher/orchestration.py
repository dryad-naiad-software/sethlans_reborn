# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Main-loop orchestration for the launcher: normal mode + IPC.

Splash-dismissal contract (v2 — splash phase states spec):

* Cold-boot health is observed via :func:`launcher.health_probe.wait_for_health`
  on each call site (wizard, manager, manager+worker, worker).
* Manager+worker mode polls the two URLs **strictly serially** under a
  single shared 30 s wall-clock deadline (FR-6) — no parallel threads,
  no shared concurrent-futures machinery, no manual thread join.
* On health timeout, ``startup_failed`` is emitted **first** (FR-11(c))
  so the splash error card appears within ~250 ms; child termination
  then runs to completion before this function returns.
* Manager+worker termination on timeout issues both ``proc.terminate()``
  calls back-to-back BEFORE awaiting either ``wait()`` (FR-11(b)) so
  total termination latency is bounded by ``max(grace)`` not the sum.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from launcher import cascade, tray_ipc
from launcher.browser_launch import open_browser
from launcher.caddy_wiring import start_caddy_supervisor
from launcher.cold_boot import fail_cold_boot, safe_invoke
from launcher.health_probe import HEALTH_TIMEOUT, wait_for_health
from launcher.restart_orchestrator import handle_restart_request
from launcher.setup_helpers import remove_setup_section

logger = logging.getLogger(__name__)

RESTART_POLL_INTERVAL = 2.0
DASHBOARD_PATH = "/"
MANAGER_PORT = 8080
WORKER_PORT = 8081

_MANAGER_HEALTH_URL = f"https://127.0.0.1:{MANAGER_PORT}/api/health/"
_WORKER_HEALTH_URL = f"https://127.0.0.1:{WORKER_PORT}/api/health/"

# Timeout reason strings — kept module-level so tests can assert on
# stable identifiers (OQ-4 recommendation; not yet i18n-ified).
_REASON_MANAGER_TIMEOUT = (
    f"manager did not start within {HEALTH_TIMEOUT:.0f} s"
)
_REASON_WORKER_TIMEOUT = (
    f"worker did not start within {HEALTH_TIMEOUT:.0f} s"
)


def _consume_ipc(
    data_dir: Path, secret: str, tray: Optional[subprocess.Popen],
) -> tuple[Optional[str], Optional[str]]:
    tray_pid = tray.pid if tray is not None else -1
    return tray_ipc.consume_pending_ipc(data_dir, secret, tray_pid)


def _all_live_exited(state: dict) -> bool:
    live = [state["manager"], state["worker"]]
    live = [p for p in live if p is not None]
    return bool(live) and all(p.poll() is not None for p in live)


def _make_second_quit_check(data_dir, secret, tray):
    """Return a callable that reports True when a 2nd quit marker lands.

    Consulted inside ``cascade._terminate_with_grace`` at ~1s cadence
    so the second Quit Sethlans click triggers an immediate SIGKILL
    of any survivors (FR-21) instead of waiting out the full grace.
    """
    def _check() -> bool:
        q, _r = _consume_ipc(data_dir, secret, tray)
        return q is not None
    return _check


def _handle_quit(quit_target, state, tray, data_dir=None, secret=None):
    if quit_target is None:
        return None
    if state["cascade"]:
        cascade.emergency_kill_all(
            state["worker"], state["manager"], tray,
        )
        return 0
    state["cascade"] = True
    check = None
    if data_dir is not None and secret is not None:
        check = _make_second_quit_check(data_dir, secret, tray)
    if quit_target == "all":
        cascade.cascade_quit(
            state["worker"], state["manager"], tray,
            second_quit_check=check,
        )
        return 0
    if quit_target == "manager":
        cascade.quit_manager_only(state["manager"])
        if state["worker"] is None:
            return 0
        state["manager"] = None
        state["cascade"] = False
    return None


def _handle_restart(restart_target, state, data_dir, start_component):
    if restart_target != "manager" or state["manager"] is None:
        return

    def _respawn():
        return start_component("manager")

    new_proc = handle_restart_request(
        state["manager"], data_dir, _respawn,
    )
    if new_proc is not None:
        state["manager"] = new_proc


def _read_topology(data_dir: Path) -> dict:
    import json
    f = data_dir / "topology.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _await_cold_boot(
    topo: str,
    manager_proc: Optional[subprocess.Popen],
    worker_proc: Optional[subprocess.Popen],
    on_cold_boot_ready: Optional[Callable[[], None]],
    on_startup_failed: Optional[Callable[[str, str], None]],
) -> Optional[int]:
    """Wait for cold-boot health; return non-None exit code on timeout.

    Manager+worker mode polls strictly serially under a single shared
    30 s deadline (FR-6). On any-side timeout, returns 1 after firing
    startup_failed and terminating the cold-boot child(ren); the caller
    should propagate the return code without spinning up the IPC loop.
    """
    is_manager_topo = topo in ("manager", "manager_worker", "manager+worker")
    is_worker_topo = topo in ("worker", "manager_worker", "manager+worker")
    has_manager = is_manager_topo and manager_proc is not None
    has_worker = is_worker_topo and worker_proc is not None
    deadline = time.monotonic() + HEALTH_TIMEOUT

    if has_manager:
        remaining = deadline - time.monotonic()
        # Look up wait_for_health on the module so tests that patch
        # ``orchestration.wait_for_health`` are honored (default-arg
        # binding would freeze the original at import time).
        if not wait_for_health(
            _MANAGER_HEALTH_URL, manager_proc, timeout=remaining,
        ):
            return fail_cold_boot(
                _REASON_MANAGER_TIMEOUT,
                manager_proc, worker_proc, on_startup_failed,
            )
    if has_worker:
        remaining = deadline - time.monotonic()
        if not wait_for_health(
            _WORKER_HEALTH_URL, worker_proc, timeout=remaining,
        ):
            return fail_cold_boot(
                _REASON_WORKER_TIMEOUT,
                manager_proc, worker_proc, on_startup_failed,
            )
    safe_invoke(on_cold_boot_ready)
    return None


def run_normal_mode(
    data_dir: Path,
    args,
    tray: Optional[subprocess.Popen],
    secret: str,
    start_component: Callable[..., subprocess.Popen],
    *,
    on_cold_boot_ready: Optional[Callable[[], None]] = None,
    on_startup_failed: Optional[Callable[[str, str], None]] = None,
) -> int:
    """Post-setup: start services per topology; watch IPC."""
    topology = _read_topology(data_dir)
    topo = topology.get("topology", "manager_worker")
    manager_data = data_dir / "manager"
    remove_setup_section(manager_data)

    # Caddy terminates TLS in front of Waitress for manager-bearing
    # topologies. Worker-only installs skip it (scoped out per spec).
    if topo in ("manager", "manager_worker", "manager+worker"):
        start_caddy_supervisor(manager_data)

    manager_proc = worker_proc = None
    if topo in ("manager", "manager_worker", "manager+worker"):
        print("Starting Sethlans Manager...")
        manager_proc = start_component("manager")
    if topo in ("worker", "manager_worker", "manager+worker"):
        print("Starting Sethlans Worker...")
        worker_proc = start_component("worker")

    rc = _await_cold_boot(
        topo, manager_proc, worker_proc,
        on_cold_boot_ready, on_startup_failed,
    )
    if rc is not None:
        return rc

    # FR-12: open the browser only after all required URLs are healthy.
    # Worker-only topology has no browser to open (existing semantics).
    if topo in ("manager", "manager_worker", "manager+worker"):
        open_browser(
            MANAGER_PORT, args.no_browser, args.print_url,
            DASHBOARD_PATH, None,
        )

    state = {
        "manager": manager_proc,
        "worker": worker_proc,
        "cascade": False,
    }
    while True:
        if _all_live_exited(state):
            return 0
        quit_target, restart_target = _consume_ipc(
            data_dir, secret, tray,
        )
        rc = _handle_quit(quit_target, state, tray, data_dir, secret)
        if rc is not None:
            return rc
        _handle_restart(
            restart_target, state, data_dir, start_component,
        )
        time.sleep(RESTART_POLL_INTERVAL)
