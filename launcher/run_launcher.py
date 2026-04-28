# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Bootstrap launcher entry point."""

import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Optional

from launcher import (
    cascade, orchestration, supervision, tray_ipc, wizard_orchestration,
)
from launcher.browser_launch import (  # noqa: F401
    compute_cert_fingerprint as _compute_cert_fingerprint,
    is_headless as _is_headless,
    open_browser,
    print_setup_banner,
)
from launcher.component_paths import (
    find_component_exe, popen_kwargs_for_component,
)
from launcher.paths import (
    get_bin_dir, get_data_dir, get_install_dir, set_file_permissions,
)
from launcher.single_instance import acquire_single_instance_lock, release_lock
from shared.version import get_version

__version__ = get_version()

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


def _is_setup_complete(data_dir: Path) -> bool:
    return (data_dir / ".setup_complete").exists()


def _read_topology(data_dir: Path) -> dict:
    topology_file = data_dir / "topology.json"
    if topology_file.exists():
        with open(topology_file, "r") as f:
            return json.load(f)
    return {}


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
            "waitress_loopback_port_public = 8090\n"
            "waitress_loopback_port_internal = 8088\n"
        )
        ini_path.write_text(ini_content, encoding="utf-8")
        _set_file_permissions(ini_path)
        print(f"Generated manager.ini at {ini_path}")
    return manager_data


def _find_component_exe(component: str) -> Path:
    """Re-export for tests / back-compat. See ``component_paths`` (FR-L12)."""
    return find_component_exe(component)


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
    # FR-10 (D4): launcher-spawned workers force the embedded web UI
    # on so the cold-boot splash can dismiss on /api/health/. Headless
    # workers (worker/run_worker.py direct, docker) keep their default.
    if component == "worker":
        proc_env["SETHLANS_WORKER_UI_ENABLED"] = "true"
    stdout = subprocess.PIPE if component != "tray" else None
    stderr = subprocess.PIPE if component != "tray" else None
    # DEVOPS-MED-4 (Phase F3): see popen_kwargs_for_component docstring.
    popen_kwargs = popen_kwargs_for_component()
    return subprocess.Popen(
        cmd, stdout=stdout, stderr=stderr, env=proc_env, **popen_kwargs,
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
    """Spawn the tray subprocess; fail hard if it does not come up."""
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
    try:
        rc = proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        return proc  # still alive after 3s = healthy
    print(
        f"\n[ERROR] Tray helper exited immediately with code {rc}.\n"
        "Likely the tray bundle is missing PySide6 or its backend.\n"
        "Aborting startup.",
        file=sys.stderr,
    )
    sys.exit(1)


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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sethlans — Distributed Blender Rendering",
    )
    parser.add_argument(
        "--version", action="version", version=f"Sethlans {__version__}",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not open a browser window on startup.",
    )
    parser.add_argument(
        "--print-url", action="store_true",
        help="Print the application URL and skip browser auto-open.",
    )
    return parser.parse_args()


def _prepare_data_dir() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    from launcher.logging_setup import configure as _configure_logging
    _configure_logging("launcher", data_dir=data_dir)
    logger.debug(
        "Launcher starting; version=%s pid=%d data_dir=%s",
        __version__, os.getpid(), data_dir,
    )
    return data_dir


def _run_orchestration(data_dir: Path, args, tray, secret,
                       *, on_cold_boot_ready=None,
                       on_startup_failed=None) -> int:
    if not _is_setup_complete(data_dir):
        # FR-L1: first-run spawns the wizard; the launcher hands off to
        # the runtime per topology.json once .wizard_done is written.
        del tray, secret  # tray IPC is owned by the post-setup loop
        return wizard_orchestration.run_wizard_mode(
            data_dir, args, _bootstrap_first_run, _start_component,
            on_cold_boot_ready=on_cold_boot_ready,
            on_startup_failed=on_startup_failed,
        )
    return orchestration.run_normal_mode(
        data_dir, args, tray, secret, _start_component,
        on_cold_boot_ready=on_cold_boot_ready,
        on_startup_failed=on_startup_failed,
    )


def _pre_orchestration_setup(data_dir: Path):
    """Common pre-orchestration wiring (signals, tray, IPC poll)."""
    supervision.install_signal_handlers()
    tray_ipc.sweep_stale_markers(data_dir)
    secret = tray_ipc.generate_secret()
    tray = _spawn_tray(data_dir, secret)
    manager_data = data_dir / "manager"
    manager_data.mkdir(parents=True, exist_ok=True)
    # #163: poll thread consumes .quit_requested during wizard mode.
    supervision.start_ipc_poll_thread(
        manager_data, secret=secret,
        tray_pid_provider=lambda: tray.pid if tray is not None else -1,
    )
    return tray, secret


def _main_headless(args, data_dir: Path) -> int:
    tray, secret = _pre_orchestration_setup(data_dir)
    try:
        rc = _run_orchestration(data_dir, args, tray, secret)
    except KeyboardInterrupt:
        print("\nSethlans shutting down...")
        rc = 0
    supervision.shutdown_supervisors()
    _teardown_tray(tray)
    return rc


def _main_with_splash(args, data_dir: Path) -> int:
    """Splash-enabled path — Qt scoped to splash lifetime (FR-4)."""
    from launcher.splash_runner import run_with_splash
    return run_with_splash(
        args, data_dir, __version__,
        pre_orchestration_setup=_pre_orchestration_setup,
        run_orchestration=_run_orchestration,
        teardown_tray=_teardown_tray,
    )


def main():
    global _INSTANCE_LOCK
    args = _parse_args()
    data_dir = _prepare_data_dir()
    _INSTANCE_LOCK = acquire_single_instance_lock(data_dir)
    if _INSTANCE_LOCK is None:
        _already_running_notice()
        return 0
    use_splash = not (args.no_browser or args.print_url)
    try:
        if use_splash:
            return _main_with_splash(args, data_dir)
        return _main_headless(args, data_dir)
    finally:
        supervision.get_shutdown_event().set()
        supervision.shutdown_supervisors()
        release_lock(_INSTANCE_LOCK)
        _INSTANCE_LOCK = None


if __name__ == "__main__":
    sys.exit(main() or 0)
