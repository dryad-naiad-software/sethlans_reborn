# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The second machine's worker for the two-machine memory test E2E (Part B,
``test_unreal_per_machine_two_machines``). Not a test: an operator runs it
by hand on the second machine, from the repository root, while the test
waits on the first machine::

    python tests/e2e/unreal_pm_remote_worker.py --handoff <copy of the handoff file>

or, without the file, with ``SETHLANS_UNREAL_E2E_PM_MANAGER_HOST``,
``SETHLANS_UNREAL_E2E_PM_PORT`` and ``SETHLANS_UNREAL_E2E_PM_ENROLLMENT_KEY``
set to the values the test logged.

Needs here what every real Unreal E2E needs (``SETHLANS_UNREAL_E2E=1``,
``SETHLANS_UNREAL_E2E_SHARE`` naming the same share as the manager, Windows,
UE 5.8.4, the PoC archive on the share) and the Unreal cards of exactly one
card kind (``SETHLANS_COMPUTE_CARDS`` narrows them). Optional:
``SETHLANS_UNREAL_E2E_PM_CACHE_DIR`` (the worker's Unreal cache; default the
same ``-pm`` dir Part A uses, so a warm Part A cache is reused).

What it does, and nothing more: refuses to start while an Unreal editor
runs; snapshots the user's Unreal and Epic state (``unreal_isolation``);
starts one worker with Unreal enabled, its own temporary data dir (its
config and token never touch the user's real worker config) and the
cache dir; the worker enrolls with the run's key, pins the manager's
certificate, copies the archive into its cache itself and takes only the
jobs the manager offers it. It stops the worker when the manager's port
stops answering (the test ended), on Ctrl+C, or after ``--max-hours``;
then checks that no Unreal process survived and that the user's state is
as found, keeps the worker log in the temp dir and deletes the data dir.
It never writes to the share. Exit code 0 when both checks pass; on 1 it
logs how to put the Epic registry key back (``reg import`` of the
before-export in its temp dir). The worker is stopped on every way out.
Run it on a trusted LAN only: it enrolls with the run's key over the
network (the normal enrollment bootstrap).
"""

import argparse
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
MANAGER_GONE_PROBES = 4
PROBE_SECONDS = 30
logger = logging.getLogger("unreal_pm_remote_worker")


def _paths():
    for path in (_REPO, _REPO / "manager", _REPO / "worker"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _settings(args, pm):
    if args.handoff:
        data = json.loads(Path(args.handoff).read_text(encoding="utf-8"))
        return data["manager_host"], int(data["port"]), data["enrollment_key"], data.get("share")
    host = os.environ.get(pm.MANAGER_HOST_ENV, "").strip()
    port = os.environ.get(pm.PORT_ENV, "").strip()
    key = os.environ.get(pm.ENROLLMENT_KEY_ENV, "").strip()
    if not (host and port.isdigit() and key):
        raise SystemExit(f"Give --handoff, or set {pm.MANAGER_HOST_ENV}, {pm.PORT_ENV} and {pm.ENROLLMENT_KEY_ENV}")
    return host, int(port), key, None


def _same_share(a, b):
    def norm(text):
        return (text or "").replace("/", "\\").rstrip("\\").lower()
    return norm(a) == norm(b)


def _preflight(share, cfg, pm, isolation):
    reason = cfg.unreal_skip_reason()
    if reason:
        raise SystemExit(f"Cannot run here: {reason}")
    if share is not None and not _same_share(share, cfg.SHARE):
        raise SystemExit(f"The manager's share and {cfg.SHARE_ENV} differ; set {cfg.SHARE_ENV} to the same share")
    cards, reason = pm.choose_one_kind(1)
    if reason:
        raise SystemExit(f"Cannot run here: {reason}")
    running = isolation.running_unreal_processes()
    if running:
        raise SystemExit(f"Unreal is running (an artist's editor or a leftover); close it first: {running}")
    return cards


def _manager_answers(host, port):
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def _watch(proc, host, port, max_seconds):
    """Until the worker exits, the manager is gone, Ctrl+C or the time limit; the reason."""
    deadline, misses = time.monotonic() + max_seconds, 0
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return f"the worker exited with code {proc.returncode}"
            misses = 0 if _manager_answers(host, port) else misses + 1
            if misses >= MANAGER_GONE_PROBES:
                return "the manager stopped answering (the test ended)"
            time.sleep(PROBE_SECONDS)
    except KeyboardInterrupt:
        return "Ctrl+C"
    return "the time limit"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--handoff", help="the handoff JSON file the test wrote (copied to this machine)")
    parser.add_argument("--max-hours", type=float, default=6.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    _paths()
    from tests.e2e import unreal_e2e_config as cfg
    from tests.e2e import unreal_isolation as isolation
    from tests.e2e import unreal_pm_config as pm
    from tests.e2e.process_manager import build_worker_env, kill_process_tree, start_worker

    host, port, key, share = _settings(args, pm)
    cards = _preflight(share, cfg, pm, isolation)
    cache = pm.cache_dir()
    if " " in cache:
        raise SystemExit(f"{pm.CACHE_ENV} must not contain spaces: {cache}")
    temp = Path(tempfile.mkdtemp(prefix="e2e_unreal_pm_remote_"))
    before_reg = temp / "hkcu_before.reg"
    before = isolation.take_snapshot(before_reg, cfg.archive_on_share())
    logger.info("The user's Epic registry key as found is exported to %s", before_reg)
    env = build_worker_env(key, host, port, worker_data_dir=temp / "worker_data")
    env.update({"SETHLANS_UNREAL_ENABLED": "true", "SETHLANS_UNREAL_SHARE": cfg.SHARE,
                "SETHLANS_UNREAL_CACHE_DIR": cache, **pm.worker_env(cards)})
    proc = start_worker(env)
    try:
        logger.info("Worker started (PID %s) for %s:%s, cards %s, cache %s", proc.pid, host, port,
                    [card.key for card in cards], cache)
        reason = _watch(proc, host, port, int(args.max_hours * 3600))
        logger.info("Stopping the worker: %s", reason)
    finally:
        _stop(proc, temp, kill_process_tree)
    return _check(cache, temp, before, cfg, isolation)


def _stop(proc, temp, kill_process_tree):
    """Kill the worker's process tree, even through a repeated Ctrl+C, and keep its log."""
    while True:
        try:
            stdout, stderr = kill_process_tree(proc)
            break
        except KeyboardInterrupt:
            logger.warning("Still stopping the worker; Ctrl+C is ignored until it has stopped.")
    log = temp / "worker.log"
    log.write_text(stdout + "\n--- stderr ---\n" + stderr, encoding="utf-8", errors="replace")
    shutil.rmtree(temp / "worker_data", ignore_errors=True)  # its config holds the worker's token
    logger.info("Worker log kept at %s", log)


def _check(cache, temp, before, cfg, isolation):
    """0 when no Unreal process survived and the user's state is as found; else 1, with how to restore."""
    orphans = isolation.orphans_of(cache)
    after = isolation.take_snapshot(temp / "hkcu_after.reg", cfg.archive_on_share())
    problems = isolation.snapshot_differences(before, after)
    if orphans or problems:
        before_reg = temp / "hkcu_before.reg"
        logger.error("Unreal processes left: %s; per-user state changed: %s", orphans, problems)
        logger.error("To put the Epic registry key back as found: reg import \"%s\" (changed files listed above "
                     "must be checked by hand; the snapshot holds only their sizes and times).", before_reg)
        return 1
    logger.info("No Unreal process left; per-user state as found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
