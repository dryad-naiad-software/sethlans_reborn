# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Bootstrap launcher entry point.

This is the binary the user's Start Menu shortcut / .app bundle /
desktop file invokes.  It detects setup completion, spawns the tray
(first, so the user sees "Starting..."), then manager, then worker,
and drives the IPC + cascade main loop.

Stdlib + shared.tray helpers only; no Django dependency.
"""

import argparse
import json
import logging
import os
import platform
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Optional

from launcher import cascade, orchestration, tray_ipc
from launcher.browser_launch import (  # noqa: F401
    compute_cert_fingerprint as _compute_cert_fingerprint,
    is_headless as _is_headless,
    open_browser,
    print_setup_banner,
)
from launcher.paths import (
    get_bin_dir,
    get_data_dir,
    get_install_dir,
    set_file_permissions,
)
from launcher.single_instance import (
    acquire_single_instance_lock,
    release_lock,
)

__version__ = "0.1.0"

MANAGER_PORT = 8080
DASHBOARD_PATH = "/"

_INSTANCE_LOCK = None  # type: ignore[var-annotated]
logger = logging.getLogger(__name__)


# ---- Re-exports for tests / back-compat ----

def _get_data_dir() -> Path:
    return get_data_dir()


def _get_bin_dir() -> Path:
    return get_bin_dir()


def _get_install_dir() -> Path:
    return get_install_dir()


def _set_file_permissions(path: Path) -> None:
    set_file_permissions(path)


# ---- Sentinel / topology ----

def _is_setup_complete(data_dir: Path) -> bool:
    return (data_dir / ".setup_complete").exists()


def _read_topology(data_dir: Path) -> dict:
    topology_file = data_dir / "topology.json"
    if topology_file.exists():
        with open(topology_file, "r") as f:
            return json.load(f)
    return {}


# ---- First-run bootstrap ----

def _bootstrap_first_run(data_dir: Path) -> Path:
    manager_data = data_dir / "manager"
    manager_data.mkdir(parents=True, exist_ok=True)
    ini_path = manager_data / "manager.ini"
    if not ini_path.exists():
        secret_key = secrets.token_urlsafe(50)
        ini_content = (
            "[security]\n"
            f"secret_key = {secret_key}\n"
            "debug = False\n"
            "\n"
            "[server]\n"
            "host = 0.0.0.0\n"
            f"port = {MANAGER_PORT}\n"
            "loopback_port = 8088\n"
        )
        ini_path.write_text(ini_content, encoding="utf-8")
        _set_file_permissions(ini_path)
        print(f"Generated manager.ini at {ini_path}")
    return manager_data


# ---- Component spawn ----

def _find_component_exe(component: str) -> Path:
    bin_dir = get_bin_dir()
    if getattr(sys, 'frozen', False):
        if platform.system() == "Windows":
            if component == "tray":
                return bin_dir / "tray_helper" / "run_tray_helper.exe"
            return bin_dir / component / f"run_{component}.exe"
        if component == "tray":
            return bin_dir / "tray_helper" / "run_tray_helper"
        return bin_dir / component / f"run_{component}"
    root = Path(__file__).resolve().parent.parent
    if component == "manager":
        return root / "manager" / "run_manager.py"
    if component == "worker":
        return root / "worker" / "run_worker.py"
    if component == "tray":
        return root / "shared" / "run_tray.py"
    raise ValueError(f"unknown component {component!r}")


def _start_component(
    component: str, extra_args=None, env: Optional[dict] = None,
) -> subprocess.Popen:
    exe = _find_component_exe(component)
    if getattr(sys, 'frozen', False):
        cmd = [str(exe)]
    else:
        cmd = [sys.executable, str(exe)]
    if extra_args:
        cmd.extend(extra_args)
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    stdout = subprocess.PIPE if component != "tray" else None
    stderr = subprocess.PIPE if component != "tray" else None
    return subprocess.Popen(
        cmd, stdout=stdout, stderr=stderr, env=proc_env,
    )


def _open_browser(
    port: int, no_browser: bool, print_url: bool,
    path: str = DASHBOARD_PATH, setup_token: str | None = None,
):
    del setup_token  # FR-13: URL never carries ?token=.
    open_browser(port, no_browser, print_url, path, None)


def _spawn_tray(
    data_dir: Path, secret: str,
) -> subprocess.Popen:
    """Spawn the tray subprocess; fail hard if it does not come up.

    The tray is the only UX surface that shows the setup token and the
    running/error state to the user.  If it cannot start, the install
    is effectively silent (no tray, no visible console) and we must
    terminate the launcher rather than leave orphan manager/worker
    processes running invisibly.
    """
    del data_dir
    env = {
        "SETHLANS_TRAY_IPC_SECRET": secret,
        "SETHLANS_LAUNCHER_PID": str(os.getpid()),
    }
    try:
        proc = _start_component("tray", env=env)
    except Exception as exc:
        print(
            f"\n[ERROR] Failed to spawn tray helper: {exc}\n"
            "The Sethlans tray is required for the launcher UX.\n"
            "Aborting startup.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Give the tray a short window to self-abort (e.g. missing pystray
    # backend).  If it exits within 3s with a non-zero code, treat that
    # as a hard failure.
    try:
        rc = proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        return proc  # still alive after 3s = healthy
    print(
        f"\n[ERROR] Tray helper exited immediately with code {rc}.\n"
        "Likely the tray bundle is missing pystray or its backend.\n"
        "Aborting startup.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---- Orchestration ----

def _already_running_notice() -> None:
    print(
        "Sethlans is already running. "
        "Check the system tray / running windows.",
        file=sys.stderr,
    )


def _teardown_tray(tray: Optional[subprocess.Popen]) -> None:
    if tray is None or tray.poll() is not None:
        return
    try:
        tray.terminate()
        tray.wait(timeout=cascade.TRAY_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        try:
            tray.kill()
        except OSError:
            pass


def main():
    global _INSTANCE_LOCK

    parser = argparse.ArgumentParser(
        description="Sethlans — Distributed Blender Rendering",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"Sethlans {__version__}",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not open a browser window on startup.",
    )
    parser.add_argument(
        "--print-url", action="store_true",
        help="Print the application URL and skip browser auto-open.",
    )
    args = parser.parse_args()

    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Alpha: always capture DEBUG logs for the launcher.  Goes to
    # ``<data_dir>/logs/launcher.log`` via rotating handler + stderr.
    from launcher.logging_setup import configure as _configure_logging
    _configure_logging("launcher", data_dir=data_dir)
    logger.debug(
        "Launcher starting; version=%s pid=%d data_dir=%s",
        __version__, os.getpid(), data_dir,
    )

    _INSTANCE_LOCK = acquire_single_instance_lock(data_dir)
    if _INSTANCE_LOCK is None:
        _already_running_notice()
        return 0

    # FR-20e: sweep any stale markers BEFORE spawning anything.
    tray_ipc.sweep_stale_markers(data_dir)

    # FR-20b: per-session IPC secret; FR-4 / FR-19: tray spawns FIRST.
    secret = tray_ipc.generate_secret()
    tray = _spawn_tray(data_dir, secret)

    try:
        if not _is_setup_complete(data_dir):
            rc = orchestration.run_setup_mode(
                data_dir, args, tray, secret,
                _start_component, _bootstrap_first_run,
            )
        else:
            rc = orchestration.run_normal_mode(
                data_dir, args, tray, secret, _start_component,
            )
        _teardown_tray(tray)
        return rc
    except KeyboardInterrupt:
        print("\nSethlans shutting down...")
        _teardown_tray(tray)
        return 0
    finally:
        release_lock(_INSTANCE_LOCK)
        _INSTANCE_LOCK = None


if __name__ == "__main__":
    sys.exit(main() or 0)
