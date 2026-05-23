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
import time
from pathlib import Path
from typing import Optional

# In frozen mode PyInstaller handles sys.path; only add the launcher
# directory and project root when running from source (issue #177).
# Mirrors ``wizard/run_wizard.py`` / ``worker/run_worker.py``.
if not getattr(sys, "frozen", False):
    launcher_dir = str(Path(__file__).resolve().parent)
    if launcher_dir not in sys.path:
        sys.path.insert(0, launcher_dir)
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from launcher import (  # noqa: E402
    cascade, diagnostics, supervision,
)
from launcher.browser_launch import (  # noqa: F401, E402
    compute_cert_fingerprint as _compute_cert_fingerprint,
    is_headless as _is_headless, open_browser, print_setup_banner,
)
from launcher.component_paths import find_component_exe, popen_kwargs_for_component  # noqa: E402
from launcher.main_dispatch import (  # noqa: F401, E402
    _is_setup_complete,
    _pre_orchestration_setup as _pre_orchestration_setup_impl,
    _run_orchestration as _run_orchestration_impl,
)
from launcher.paths import get_bin_dir, get_data_dir, get_install_dir, set_file_permissions  # noqa: E402
from launcher.single_instance import acquire_single_instance_lock, release_lock  # noqa: E402
from shared.version import get_version  # noqa: E402

__version__ = get_version()

MANAGER_PORT = 8080
DASHBOARD_PATH = "/"
_INSTANCE_LOCK = None  # type: ignore[var-annotated]
logger = logging.getLogger(__name__)

# Re-exports for tests/back-compat — bindings ``mocker.patch`` targets.
_get_data_dir = get_data_dir
_get_bin_dir = get_bin_dir
_get_install_dir = get_install_dir
_set_file_permissions = set_file_permissions


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
        ini_path.write_text(
            "[security]\n"
            f"secret_key = {secret_key}\n"
            "debug = False\n\n"
            "[server]\n"
            "host = 0.0.0.0\n"
            f"port = {MANAGER_PORT}\n"
            "loopback_port = 8088\n"
            "waitress_loopback_port_public = 8090\n"
            "waitress_loopback_port_internal = 8088\n",
            encoding="utf-8",
        )
        _set_file_permissions(ini_path)
        print(f"Generated manager.ini at {ini_path}")
    return manager_data


_find_component_exe = find_component_exe  # tests / back-compat (FR-L12)


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
    # FR-10 (D4): launcher-spawned workers force the embedded web UI on.
    if component == "worker":
        proc_env["SETHLANS_WORKER_UI_ENABLED"] = "true"
    stdout = subprocess.PIPE if component != "tray" else None
    stderr = subprocess.PIPE if component != "tray" else None
    # DEVOPS-MED-4 (Phase F3): see popen_kwargs_for_component docstring.
    popen_kwargs = popen_kwargs_for_component()
    return subprocess.Popen(
        cmd, stdout=stdout, stderr=stderr, env=proc_env, **popen_kwargs,
    )


def _open_browser(port: int, no_browser: bool, print_url: bool,
                  path: str = DASHBOARD_PATH,
                  setup_token: str | None = None):
    del setup_token  # FR-13: URL never carries ?token=.
    open_browser(port, no_browser, print_url, path, None)


def _spawn_tray(data_dir: Path, secret: str) -> subprocess.Popen:
    """Spawn the tray subprocess; fail hard if it does not come up."""
    del data_dir
    env = {
        "SETHLANS_TRAY_IPC_SECRET": secret,
        "SETHLANS_LAUNCHER_PID": str(os.getpid()),
    }
    try:
        proc = _start_component("tray", env=env)
    except Exception as exc:
        print(f"\n[ERROR] Failed to spawn tray helper: {exc}\n"
              "The Sethlans tray is required for the launcher UX.\n"
              "Aborting startup.", file=sys.stderr)
        sys.exit(1)
    try:
        rc = proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        return proc  # still alive after 3s = healthy
    print(f"\n[ERROR] Tray helper exited immediately with code {rc}.\n"
          "Likely the tray bundle is missing PySide6 or its backend.\n"
          "Aborting startup.", file=sys.stderr)
    sys.exit(1)


def _already_running_notice() -> None:
    print(
        "Sethlans is already running. "
        "Check the system tray / running windows.", file=sys.stderr,
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
    p = argparse.ArgumentParser(description="Sethlans — Distributed Blender Rendering")
    p.add_argument("--version", action="version", version=f"Sethlans {__version__}")
    p.add_argument("--no-browser", action="store_true",
                   help="Do not open a browser window on startup.")
    p.add_argument("--print-url", action="store_true",
                   help="Print the application URL and skip browser auto-open.")
    return p.parse_args()


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


def _run_orchestration(data_dir: Path, args, tray, secret, **kw) -> int:
    """Thin shim — delegates to :mod:`launcher.main_dispatch` (issue #203)."""
    return _run_orchestration_impl(
        data_dir, args, tray, secret,
        bootstrap_first_run=_bootstrap_first_run,
        start_component=_start_component, **kw,
    )


def _pre_orchestration_setup(data_dir: Path):
    """Thin shim — delegates to :mod:`launcher.main_dispatch`."""
    return _pre_orchestration_setup_impl(data_dir, _spawn_tray)


def _main_headless(args, data_dir: Path) -> int:
    tray, secret = _pre_orchestration_setup(data_dir)
    try:
        try:
            rc = _run_orchestration(data_dir, args, tray, secret)
        except KeyboardInterrupt:
            print("\nSethlans shutting down...")
            rc = 0
    finally:
        # FR-6: tray teardown reaches every exit path (incl. uncaught).
        _teardown_tray(tray)
    supervision.shutdown_supervisors()
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


def _shutdown_supervisors_for_finally() -> None:
    supervision.get_shutdown_event().set()
    supervision.shutdown_supervisors()


def main():
    global _INSTANCE_LOCK
    diagnostics.install_diagnostics()
    started_at = time.monotonic()
    rc = 1
    try:
        args = _parse_args()
        data_dir = _prepare_data_dir()
        _INSTANCE_LOCK = acquire_single_instance_lock(data_dir)
        if _INSTANCE_LOCK is None:
            _already_running_notice()
            return 0
        use_splash = not (args.no_browser or args.print_url)
        boot = _main_with_splash if use_splash else _main_headless
        rc = boot(args, data_dir)
        return rc
    finally:
        diagnostics.finalize_main(
            rc=rc, started_at=started_at,
            shutdown_supervisors=_shutdown_supervisors_for_finally,
            release_lock=release_lock, instance_lock=_INSTANCE_LOCK,
        )
        _INSTANCE_LOCK = None


if __name__ == "__main__":
    sys.exit(main() or 0)
