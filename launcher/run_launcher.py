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
import os
import platform
import secrets
import stat
import subprocess
import sys
import webbrowser
from pathlib import Path

__version__ = "0.1.0"

# Default ports
MANAGER_PORT = 8080
WIZARD_PATH = "/"


# ---- Path helpers (inline, no external deps) ----

def _get_data_dir() -> Path:
    """Return the per-OS Sethlans data directory.

    Windows:  %LOCALAPPDATA%\\Sethlans
    macOS:    ~/Library/Application Support/Sethlans
    Linux:    $XDG_DATA_HOME/sethlans (default ~/.local/share/sethlans)
    """
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            userprofile = os.environ.get("USERPROFILE")
            if userprofile:
                base = os.path.join(userprofile, "AppData", "Local")
            else:
                base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "Sethlans"
    if system == "Darwin":
        return (
            Path.home() / "Library" / "Application Support" / "Sethlans"
        )
    # Linux / other POSIX
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "sethlans"
    return Path.home() / ".local" / "share" / "sethlans"


def _get_install_dir() -> Path:
    """Return the installation directory.

    In frozen mode (PyInstaller), this is the directory containing the
    executable's parent bin/ directory. In source mode, it is the
    project root.
    """
    if getattr(sys, 'frozen', False):
        # Frozen: exe is in bin/launcher/, install dir is two up
        return Path(sys.executable).resolve().parent.parent.parent
    # Source mode: launcher/ is one level below project root
    return Path(__file__).resolve().parent.parent


def _get_bin_dir() -> Path:
    """Return the bin/ directory containing component bundles."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent.parent
    return _get_install_dir()


# ---- Sentinel and config helpers ----

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


def _is_headless() -> bool:
    """Detect headless Linux (no DISPLAY/WAYLAND_DISPLAY)."""
    if platform.system() != "Linux":
        return False
    display = os.environ.get("DISPLAY")
    wayland = os.environ.get("WAYLAND_DISPLAY")
    return not display and not wayland


def _set_file_permissions(path: Path):
    """Set restrictive permissions on a config file.

    POSIX: 0600 (owner read/write only).
    Windows: uses icacls to set owner-only access.
    """
    if platform.system() == "Windows":
        username = os.environ.get("USERNAME", "")
        if username:
            subprocess.run(
                [
                    "icacls", str(path),
                    "/inheritance:r",
                    "/grant:r", f"{username}:(R,W)",
                ],
                capture_output=True,
            )
    else:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ---- Bootstrap (first run) ----

def _bootstrap_first_run(data_dir: Path) -> Path:
    """Perform pre-Django bootstrap for first run.

    Generates SECRET_KEY, writes minimal manager.ini with restricted
    permissions, returns the manager data directory.
    """
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
    bin_dir = _get_bin_dir()

    if getattr(sys, 'frozen', False):
        if platform.system() == "Windows":
            return bin_dir / component / f"run_{component}.exe"
        return bin_dir / component / f"run_{component}"
    else:
        # Source mode: use python to run the script
        if component == "manager":
            return Path(__file__).resolve().parent.parent / (
                "manager" / Path("run_manager.py")
            )
        return Path(__file__).resolve().parent.parent / (
            "worker" / Path("run_worker.py")
        )


def _start_component(component: str, extra_args=None):
    """Start a component subprocess.

    Returns the Popen handle.
    """
    exe = _find_component_exe(component)
    cmd = []
    if getattr(sys, 'frozen', False):
        cmd = [str(exe)]
    else:
        cmd = [sys.executable, str(exe)]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _open_browser(port: int, no_browser: bool, print_url: bool):
    """Open browser to the wizard/dashboard URL.

    On headless Linux, always prints the URL to stdout instead.
    """
    url = f"https://localhost:{port}{WIZARD_PATH}"
    headless = _is_headless()

    if print_url or headless:
        print(f"Sethlans is running at: {url}")
    if not no_browser and not headless:
        webbrowser.open(url)


# ---- Main entry point ----

def main():
    """Bootstrap launcher entry point."""
    parser = argparse.ArgumentParser(
        description="Sethlans — Distributed Blender Rendering",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Sethlans {__version__}",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser window on startup.",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print the application URL to stdout.",
    )
    args = parser.parse_args()

    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    processes = []

    try:
        if not _is_setup_complete(data_dir):
            # First run: bootstrap and start wizard
            _bootstrap_first_run(data_dir)
            print("Starting Sethlans setup wizard...")
            proc = _start_component("manager")
            processes.append(proc)
            _open_browser(
                MANAGER_PORT, args.no_browser, args.print_url,
            )
        else:
            # Subsequent runs: start based on topology
            topology = _read_topology(data_dir)
            topo_type = topology.get("topology", "manager_worker")

            if topo_type in ("manager", "manager_worker"):
                print("Starting Sethlans Manager...")
                proc = _start_component("manager")
                processes.append(proc)

            if topo_type in ("worker", "manager_worker"):
                print("Starting Sethlans Worker...")
                proc = _start_component("worker")
                processes.append(proc)

            _open_browser(
                MANAGER_PORT, args.no_browser, args.print_url,
            )

        # Wait for all child processes
        for proc in processes:
            proc.wait()

    except KeyboardInterrupt:
        print("\nSethlans shutting down...")
        for proc in processes:
            proc.terminate()
        for proc in processes:
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
