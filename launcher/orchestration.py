# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Main-loop orchestration for the launcher.

Split from ``run_launcher.py`` to keep that file under the 300-line
ceiling.  Handles the setup-mode and normal-mode loops and the IPC
marker consumption + cascade integration.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from launcher import cascade, tray_ipc
from launcher.browser_launch import open_browser, print_setup_banner
from launcher.restart_orchestrator import (
    handle_restart_request,
    poll_for_restart_request,
)
from launcher.setup_helpers import (
    find_available_port,
    generate_setup_token,
    remove_setup_section,
)

logger = logging.getLogger(__name__)

RESTART_POLL_INTERVAL = 2.0
WIZARD_PATH = "/setup/"
DASHBOARD_PATH = "/"
MANAGER_PORT = 8080


def _consume_ipc(
    data_dir: Path, secret: str, tray: Optional[subprocess.Popen],
) -> tuple[Optional[str], Optional[str]]:
    tray_pid = tray.pid if tray is not None else -1
    return tray_ipc.consume_pending_ipc(data_dir, secret, tray_pid)


def run_setup_mode(
    data_dir: Path,
    args,
    tray: Optional[subprocess.Popen],
    secret: str,
    start_component: Callable[..., subprocess.Popen],
    bootstrap_first_run: Callable[[Path], Path],
) -> int:
    """Setup-wizard mode: manager + wait for sentinel / IPC."""
    manager_data = bootstrap_first_run(data_dir)
    port = find_available_port()
    setup_token = generate_setup_token(manager_data)

    print("Starting Sethlans setup wizard...")
    print_setup_banner(port, WIZARD_PATH, setup_token, data_dir)

    proc = start_component("manager", extra_args=["--workers", "1"])
    open_browser(
        port, args.no_browser, args.print_url, WIZARD_PATH, None,
    )

    def _respawn():
        return start_component("manager")

    current = proc
    while True:
        try:
            current.wait(timeout=RESTART_POLL_INTERVAL)
            return current.returncode or 0
        except subprocess.TimeoutExpired:
            pass
        quit_target, restart_target = _consume_ipc(
            data_dir, secret, tray,
        )
        if quit_target == "all":
            check = _make_second_quit_check(data_dir, secret, tray)
            cascade.cascade_quit(
                None, current, tray, second_quit_check=check,
            )
            return 0
        if quit_target == "manager":
            cascade.quit_manager_only(current)
            return 0
        if restart_target == "manager" or poll_for_restart_request(data_dir):
            new_proc = handle_restart_request(current, data_dir, _respawn)
            if new_proc is None:
                return 1
            current = new_proc


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


def run_normal_mode(
    data_dir: Path,
    args,
    tray: Optional[subprocess.Popen],
    secret: str,
    start_component: Callable[..., subprocess.Popen],
) -> int:
    """Post-setup: start services per topology; watch IPC."""
    topology = _read_topology(data_dir)
    topo = topology.get("topology", "manager_worker")
    manager_data = data_dir / "manager"
    remove_setup_section(manager_data)

    manager_proc = worker_proc = None
    if topo in ("manager", "manager_worker", "manager+worker"):
        print("Starting Sethlans Manager...")
        manager_proc = start_component("manager")
    if topo in ("worker", "manager_worker", "manager+worker"):
        print("Starting Sethlans Worker...")
        worker_proc = start_component("worker")

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
