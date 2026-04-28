# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Runtime hand-off helpers for the wizard channel.

Owns the post-``.wizard_done`` half of the launcher's wizard flow:

* ``hand_off_to_runtime`` (FR-L7) — read topology.json, spawn the
  appropriate runtime component, watch its port-bind for 30 s
  (FR-L7b), write ``.runtime_failed`` HMAC marker on failure
  (FR-IPC8), and trigger FR-L13 cleanup on success.
* ``wait_for_runtime_port_bind`` (FR-L7b) — polled connect_ex on
  127.0.0.1:port with a 30 s wall-clock ceiling.
* ``terminate_wizard`` (FR-L10) — SIGTERM with the WIZARD_GRACE_SECONDS
  grace declared in ``launcher/cascade.py``.

Split from ``launcher/wizard_orchestration.py`` so neither file crosses
the 300-line limit.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from launcher import cascade, supervision, wizard_dir, wizard_ipc
from launcher.health_probe import (
    HEALTH_PROBE_TIMEOUT as _HEALTH_PROBE_TIMEOUT,
    probe_health_once,
)

logger = logging.getLogger(__name__)

# FR-L7b runtime port-bind observation window.
RUNTIME_PORT_BIND_TIMEOUT = 30.0
RUNTIME_PORT_BIND_POLL_INTERVAL = 0.25

# FR-W14a deterministic runtime port for manager-bearing topologies.
RUNTIME_MANAGER_PORT = 8080


# ---- Topology read --------------------------------------------------------

def read_topology_file(data_dir: Path) -> str:
    """Read the topology string from ``<data_dir>/topology.json``."""
    path = Path(data_dir) / "topology.json"
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, OSError):
        return ""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    topology = payload.get("topology", "")
    return topology if isinstance(topology, str) else ""


# ---- Port-bind watch (FR-L7b) ---------------------------------------------

def _port_is_bound(port: int, host: str = "127.0.0.1") -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _probe_runtime_health(
    port: int, host: str = "127.0.0.1",
) -> bool:
    """HIGH-1 (Phase F1): HTTPS GET ``/api/health/``; True on 200 + envelope.

    Delegates to :func:`launcher.health_probe.probe_health_once` so the
    cold-boot splash path and the post-handoff port-bind watch share a
    single envelope contract (OQ-2). The loopback gate (NFR-6) lives on
    the cold-boot helper only — it is not applied here because the
    runtime port is also localhost-bound and adding the gate twice
    would not narrow the security surface further.
    """
    url = f"https://{host}:{port}/api/health/"
    return probe_health_once(url, timeout=_HEALTH_PROBE_TIMEOUT)


def wait_for_runtime_port_bind(
    runtime_proc: subprocess.Popen,
    port: int,
    timeout: float = RUNTIME_PORT_BIND_TIMEOUT,
    poll_interval: float = RUNTIME_PORT_BIND_POLL_INTERVAL,
) -> bool:
    """Poll for runtime *port* binding AND a healthy /api/health/ (FR-L7b).

    HIGH-1 (Phase F1): port-bound is necessary but NOT sufficient — a
    process that has bound the socket but is still loading Django apps
    can answer connect() while ``/api/health/`` 503s or hangs. We
    must see a 200 + envelope before treating the runtime as up.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = runtime_proc.poll()
        if rc is not None and rc != 0:
            logger.error(
                "runtime exited non-zero (code %d) before port-bind", rc,
            )
            return False
        if _port_is_bound(port) and _probe_runtime_health(port):
            return True
        # Issue #163: tray quit during the runtime port-bind window
        # collapses to ``False``; the caller checks the quit event to
        # distinguish from a real port-bind timeout.
        if supervision.wait_or_quit(poll_interval):
            logger.info("Tray quit observed during runtime port-bind wait")
            return False
    logger.warning(
        "runtime did not become healthy on port %d within %.0fs",
        port, timeout,
    )
    return False


def write_runtime_failed_marker(
    data_dir: Path, ipc_secret: bytes, reason: str,
) -> None:
    """Write the FR-L7b ``.runtime_failed`` HMAC-signed marker."""
    marker_path = (
        wizard_dir.wizard_dir(data_dir) / wizard_ipc.MARKER_RUNTIME_FAILED
    )
    try:
        wizard_ipc.write_marker(
            marker_path, "runtime_failed", data_dir, ipc_secret,
            payload={"reason": reason},
        )
        logger.info(".runtime_failed written (FR-L7b)")
    except OSError as exc:
        logger.warning("Could not write .runtime_failed marker: %s", exc)


# ---- Runtime spawn per topology -------------------------------------------

def spawn_runtime_for_topology(
    topology: str,
    bootstrap_first_run: Callable[[Path], Path],
    start_component: Callable[..., subprocess.Popen],
    data_dir: Path,
) -> tuple[Optional[subprocess.Popen], Optional[int]]:
    """Spawn the runtime per FR-L7 topology selection.

    Returns ``(process, port_to_watch)``. ``port_to_watch`` is None for
    worker-only topologies (no port to observe at the launcher level).
    """
    if topology in ("manager", "manager_worker", "manager+worker"):
        bootstrap_first_run(data_dir)
        proc = start_component("manager")
        return proc, RUNTIME_MANAGER_PORT
    if topology in ("worker", "worker_only"):
        proc = start_component("worker")
        return proc, None
    logger.error("Unknown topology %r in topology.json", topology)
    return None, None


# ---- Wizard process termination (FR-L10) ----------------------------------

def terminate_wizard(wizard_proc: Optional[subprocess.Popen]) -> None:
    """Politely terminate the wizard with the FR-L10 5 s grace."""
    if wizard_proc is None or wizard_proc.poll() is not None:
        return
    try:
        wizard_proc.terminate()
    except OSError as exc:
        logger.warning("terminate() on wizard failed: %s", exc)
        return
    try:
        wizard_proc.wait(timeout=cascade.WIZARD_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "wizard did not exit within %.1fs; SIGKILL",
            cascade.WIZARD_GRACE_SECONDS,
        )
        try:
            wizard_proc.kill()
        except OSError:
            pass


def _terminate_runtime(runtime_proc: Optional[subprocess.Popen]) -> None:
    """Politely terminate the runtime; SIGKILL after manager grace (#163)."""
    if runtime_proc is None or runtime_proc.poll() is not None:
        return
    try:
        runtime_proc.terminate()
    except OSError as exc:
        logger.warning("terminate() on runtime failed: %s", exc)
        return
    try:
        runtime_proc.wait(timeout=cascade.MANAGER_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "runtime did not exit within %.1fs; SIGKILL",
            cascade.MANAGER_GRACE_SECONDS,
        )
        try:
            runtime_proc.kill()
        except OSError:
            pass


# ---- Failure / hand-off entrypoints --------------------------------------

def wizard_failure_exit(reason: str) -> int:
    """FR-L9: log + leave data dir intact for re-launch."""
    logger.error("wizard hand-off failed: %s", reason)
    # Do NOT cleanup wizard dir — TLS cert + key preserved per FR-W5.
    return 1


def hand_off_to_runtime(
    payload: dict,
    data_dir: Path,
    ipc_secret: bytes,
    wizard_proc: subprocess.Popen,
    bootstrap_first_run: Callable[[Path], Path],
    start_component: Callable[..., subprocess.Popen],
) -> int:
    """FR-L7 / FR-L7b runtime spawn + port-bind watch + cleanup.

    Splash dismissal is NOT driven from this function (FR-8 / D6 — v2
    spec). The cold-boot splash dismisses on the wizard's own
    ``/api/health/`` from ``run_wizard_mode`` BEFORE the wizard hands
    off; by the time we get here the splash is already gone.
    """
    topology = read_topology_file(data_dir)
    if not topology:
        logger.error("topology.json missing or unreadable post-handoff")
        return wizard_failure_exit("topology_missing")

    expected = payload.get("topology") if isinstance(payload, dict) else None
    if expected and expected != topology:
        logger.warning(
            "topology mismatch: marker=%r topology.json=%r; using file",
            expected, topology,
        )

    runtime_proc, watch_port = spawn_runtime_for_topology(
        topology, bootstrap_first_run, start_component, data_dir,
    )
    if runtime_proc is None:
        return wizard_failure_exit("runtime_spawn_failed")
    logger.info(
        "runtime spawned (topology=%s pid=%s)", topology, runtime_proc.pid,
    )

    if watch_port is not None:
        bound = wait_for_runtime_port_bind(runtime_proc, watch_port)
        if not bound:
            # Issue #163: tray-quit returns False too; distinguish via
            # the quit event so we don't write a misleading marker.
            if supervision.get_quit_requested_event().is_set():
                _terminate_runtime(runtime_proc)
                terminate_wizard(wizard_proc)
                return 0
            write_runtime_failed_marker(
                data_dir, ipc_secret, reason="port_bind_timeout",
            )
            terminate_wizard(wizard_proc)
            return 1

    # CRITICAL-2 (Phase F1): terminate the wizard BEFORE rmtree-ing
    # its working directory. With FR-W17 in place the wizard usually
    # self-exits via the close()-hook + grace timer before we get
    # here, but terminate_wizard is the safety net — without it,
    # cleanup_wizard_dir can rmtree the wizard's TLS files and
    # logfile while the wizard is mid-write, racing the FR-W10
    # cleanup the wizard performs at exit.
    terminate_wizard(wizard_proc)

    # FR-L13: best-effort delete <data_dir>/wizard/ after handoff.
    # Wizard's own FR-W10 cleanup may race; both are idempotent.
    wizard_dir.cleanup_wizard_dir(data_dir)
    logger.info("wizard directory cleanup completed (FR-L13)")
    return 0
