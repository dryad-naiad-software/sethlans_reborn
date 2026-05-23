# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Runtime hand-off helpers for the wizard channel (post-``.wizard_done``).

Post-issue-#203, ``hand_off_to_runtime`` is reduced to:

1. Read topology.json (sanity check + log on mismatch).
2. Run the apply pipeline (writes ``.setup_complete``).
3. Terminate the wizard subprocess and rmtree its work dir.

Runtime spawn, Caddy start, and the port-bind / health watch are now
owned by :func:`launcher.orchestration.run_normal_mode`, which
:func:`launcher.main_dispatch._run_orchestration` falls through to
after the wizard exits cleanly. This way Caddy + the manager live for
the full launcher lifetime instead of being torn down moments after
the wizard hands off.

Split from ``launcher/wizard_orchestration.py`` for the 300-line limit.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Callable, Optional

from launcher import cascade, wizard_dir

logger = logging.getLogger(__name__)


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


# ---- Runtime spawn per topology -------------------------------------------

def spawn_runtime_for_topology(
    topology: str,
    bootstrap_first_run: Callable[[Path], Path],
    start_component: Callable[..., subprocess.Popen],
    data_dir: Path,
) -> tuple[Optional[subprocess.Popen], Optional[int]]:
    """Spawn the runtime per FR-L7 topology selection.

    Retained for :func:`launcher.orchestration.run_normal_mode` and any
    integration tests still driving runtime spawn directly. The second
    return value (port-to-watch) is kept for backwards compatibility
    with callers expecting the previous tuple shape; normal mode reads
    its probe port from ``manager.ini`` instead.
    """
    if topology in ("manager", "manager_worker", "manager+worker"):
        bootstrap_first_run(data_dir)
        proc = start_component("manager")
        return proc, 8080
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
) -> int:
    """Apply the pending setup and tear down the wizard (issue #203).

    Runtime spawn (manager / worker), Caddy start, and the port-bind /
    health watch are no longer the responsibility of this function —
    :func:`launcher.orchestration.run_normal_mode` owns them now and
    runs immediately after the wizard returns rc=0 (see
    :func:`launcher.main_dispatch._run_orchestration`).
    """
    del ipc_secret  # No longer used: marker-write path removed (#203).
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

    from launcher.apply_pending_setup import run_apply_pipeline_if_needed
    apply_failure = run_apply_pipeline_if_needed(
        topology, data_dir, wizard_proc,
        terminate_wizard, wizard_failure_exit,
    )
    if apply_failure is not None:
        return apply_failure

    # CRITICAL-2 (Phase F1): terminate the wizard BEFORE rmtree-ing its
    # working directory; FR-W17 usually beats us to it but this is the
    # safety net.  FR-W10 cleanup is idempotent against ours.
    terminate_wizard(wizard_proc)

    # FR-L13: best-effort delete <data_dir>/wizard/ after handoff.
    wizard_dir.cleanup_wizard_dir(data_dir)
    logger.info("wizard directory cleanup completed (FR-L13)")
    return 0
