# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Bootstrap launcher entry point.

This is the binary the user's Start Menu shortcut / .app bundle /
desktop file invokes. It checks for setup completion, performs
pre-Django bootstrap if needed, and starts the appropriate services.

Does NOT depend on Django — uses only stdlib.
"""

import argparse
import json
import platform
import secrets
import subprocess
import sys
from pathlib import Path

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
from launcher.restart_orchestrator import (
    handle_restart_request,
    poll_for_restart_request,
)
from launcher.setup_helpers import (
    find_available_port,
    generate_setup_token,
    remove_setup_section,
)
from launcher.single_instance import (
    acquire_single_instance_lock,
    release_lock,
)

__version__ = "0.1.0"

MANAGER_PORT = 8080
WIZARD_PATH = "/setup/"
DASHBOARD_PATH = "/"

# Restart-watch cadence (seconds).
RESTART_POLL_INTERVAL = 2.0

# Lock held for launcher lifetime (module-level so GC cannot release).
_INSTANCE_LOCK = None  # type: ignore[var-annotated]


# ---- Re-exports for tests / back-compat (do not remove) ----

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
    """Check if the setup-complete sentinel exists."""
    return (data_dir / ".setup_complete").exists()


def _read_topology(data_dir: Path) -> dict:
    """Read the topology.json file from the data directory."""
    topology_file = data_dir / "topology.json"
    if topology_file.exists():
        with open(topology_file, "r") as f:
            return json.load(f)
    return {}


# ---- First-run bootstrap ----

def _bootstrap_first_run(data_dir: Path) -> Path:
    """Bootstrap first run: generate SECRET_KEY, write manager.ini."""
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
        )
        ini_path.write_text(ini_content, encoding="utf-8")
        _set_file_permissions(ini_path)
        print(f"Generated manager.ini at {ini_path}")

    return manager_data


def _find_component_exe(component: str) -> Path:
    """Locate the executable for a component (manager/worker)."""
    bin_dir = get_bin_dir()
    if getattr(sys, 'frozen', False):
        if platform.system() == "Windows":
            return bin_dir / component / f"run_{component}.exe"
        return bin_dir / component / f"run_{component}"
    if component == "manager":
        return Path(__file__).resolve().parent.parent / (
            "manager" / Path("run_manager.py")
        )
    return Path(__file__).resolve().parent.parent / (
        "worker" / Path("run_worker.py")
    )


def _start_component(component: str, extra_args=None):
    """Start a component subprocess and return the Popen handle."""
    exe = _find_component_exe(component)
    if getattr(sys, 'frozen', False):
        cmd = [str(exe)]
    else:
        cmd = [sys.executable, str(exe)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


# Wrapper kept for back-compat in tests / other callers.
def _open_browser(
    port: int, no_browser: bool, print_url: bool,
    path: str = DASHBOARD_PATH, setup_token: str | None = None,
):
    open_browser(port, no_browser, print_url, path, setup_token)


# ---- Orchestration ----

def _already_running_notice() -> None:
    """Print the second-instance message."""
    print(
        "Sethlans is already running. "
        "Check the system tray / running windows.",
        file=sys.stderr,
    )


def _build_respawn(component: str, extra_args=None):
    """Zero-arg callable used by ``handle_restart_request``."""
    def _respawn():
        return _start_component(component, extra_args=extra_args)
    return _respawn


def _run_setup_mode(data_dir: Path, args) -> int:
    """Setup-wizard mode: launch manager + watch restart sentinel."""
    manager_data = _bootstrap_first_run(data_dir)
    port = find_available_port()
    setup_token = generate_setup_token(manager_data)

    print("Starting Sethlans setup wizard...")
    print_setup_banner(port, WIZARD_PATH, setup_token, data_dir)

    proc = _start_component("manager", extra_args=["--workers", "1"])
    open_browser(
        port, args.no_browser, args.print_url,
        WIZARD_PATH, setup_token,
    )

    # Post-setup respawn: NO --workers 1, NO new setup token.
    respawn = _build_respawn("manager")
    current = proc
    while True:
        try:
            current.wait(timeout=RESTART_POLL_INTERVAL)
            return current.returncode or 0
        except subprocess.TimeoutExpired:
            pass
        if poll_for_restart_request(data_dir):
            new_proc = handle_restart_request(
                current, data_dir, respawn,
            )
            if new_proc is None:
                return 1
            current = new_proc


def _run_normal_mode(data_dir: Path, args) -> int:
    """Post-setup: start manager/worker based on topology."""
    topology = _read_topology(data_dir)
    topo_type = topology.get("topology", "manager_worker")
    manager_data = data_dir / "manager"
    remove_setup_section(manager_data)

    processes = []
    if topo_type in ("manager", "manager_worker"):
        print("Starting Sethlans Manager...")
        processes.append(_start_component("manager"))
    if topo_type in ("worker", "manager_worker"):
        print("Starting Sethlans Worker...")
        processes.append(_start_component("worker"))

    open_browser(
        MANAGER_PORT, args.no_browser, args.print_url,
        DASHBOARD_PATH, None,
    )
    for proc in processes:
        proc.wait()
    return 0


def main():
    """Bootstrap launcher entry point."""
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
        help=(
            "Print the application URL and skip browser auto-open."
        ),
    )
    args = parser.parse_args()

    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    _INSTANCE_LOCK = acquire_single_instance_lock(data_dir)
    if _INSTANCE_LOCK is None:
        _already_running_notice()
        return 0

    try:
        if not _is_setup_complete(data_dir):
            return _run_setup_mode(data_dir, args)
        return _run_normal_mode(data_dir, args)
    except KeyboardInterrupt:
        print("\nSethlans shutting down...")
        return 0
    finally:
        release_lock(_INSTANCE_LOCK)
        _INSTANCE_LOCK = None


if __name__ == "__main__":
    sys.exit(main() or 0)
