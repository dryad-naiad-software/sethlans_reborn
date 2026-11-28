# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Bootstrap launcher entry point (FR-LN-1, -5, -7; FR-RS).

:func:`main` first scrubs the setup secrets out of the environment and
exports the settings location (``child_env.prepare_launcher_environment``,
FR-LN-7), before the tray, Caddy, the browser or any thread. It then
takes the single-instance lock of the settings folder, handles
``--reset-setup`` (``launcher.reset_setup``, owner Q4), starts the tray
and the IPC poll thread, and runs ``launcher.machine_loop`` with the
splash, or headless with ``--no-browser`` / ``--print-url``.

``--setup-file`` (unattended setup) is chunk 8: it plugs into
``machine_loop.run_loop`` as its setup step.
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# In frozen mode PyInstaller handles sys.path; only add the launcher
# directory and project root when running from source (issue #177).
if not getattr(sys, "frozen", False):
    launcher_dir = str(Path(__file__).resolve().parent)
    if launcher_dir not in sys.path:
        sys.path.insert(0, launcher_dir)
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from launcher import (  # noqa: E402
    cascade, child_env, components, diagnostics, machine_loop, reset_setup, supervision, tray_ipc,
)
from launcher.loop_context import LoopContext  # noqa: E402
from launcher.paths import get_data_dir  # noqa: E402
from launcher.single_instance import acquire_single_instance_lock, release_lock  # noqa: E402
from shared.settings_file_io import ensure_owner_only_dir  # noqa: E402
from shared.version import get_version  # noqa: E402

__version__ = get_version()

_INSTANCE_LOCK = None  # type: ignore[var-annotated]
TRAY_START_CHECK_SECONDS = 3.0
LAUNCHER_PID_ENV = "SETHLANS_LAUNCHER_PID"
logger = logging.getLogger(__name__)


def _spawn_tray(secret: str) -> subprocess.Popen:
    """Spawn the tray (the only child given the IPC secret); fail hard if it does not come up."""
    try:
        proc = components.start_tray(secret, {LAUNCHER_PID_ENV: str(os.getpid())})
    except Exception as exc:
        print(f"\n[ERROR] Failed to spawn tray helper: {exc}\n"
              "The Sethlans tray is required for the launcher UX.\n"
              "Aborting startup.", file=sys.stderr)
        sys.exit(1)
    try:
        rc = proc.wait(timeout=TRAY_START_CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        return proc  # still alive: healthy
    print(f"\n[ERROR] Tray helper exited immediately with code {rc}.\n"
          "Likely the tray bundle is missing PySide6 or its backend.\n"
          "Aborting startup.", file=sys.stderr)
    sys.exit(1)


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


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sethlans — Distributed Blender Rendering")
    p.add_argument("--version", action="version", version=f"Sethlans {__version__}")
    p.add_argument("--no-browser", action="store_true",
                   help="Do not open a browser window on startup.")
    p.add_argument("--print-url", action="store_true",
                   help="Print the application URL and skip browser auto-open.")
    p.add_argument("--reset-setup", action="store_true",
                   help="Reset setup (keeps all data), then start setup. "
                        "Refused while Sethlans runs: use Reset in the tray.")
    return p.parse_args(argv)


def _prepare_data_dir() -> Path:
    """The settings folder and its ``logs`` folder, owner-only; logging configured."""
    data_dir = get_data_dir()
    ensure_owner_only_dir(data_dir)
    ensure_owner_only_dir(data_dir / "logs")
    from launcher.logging_setup import configure as _configure_logging
    _configure_logging("launcher", data_dir=data_dir)
    logger.debug("Launcher starting; version=%s pid=%d data_dir=%s", __version__, os.getpid(), data_dir)
    return data_dir


def _pre_orchestration_setup(data_dir: Path):
    """Signals, stale markers, the tray and the IPC poll thread; return ``(tray, secret)``."""
    supervision.install_signal_handlers()
    tray_ipc.sweep_stale_markers(data_dir)
    secret = tray_ipc.generate_secret()
    tray = _spawn_tray(secret)
    supervision.start_ipc_poll_thread(
        data_dir, secret=secret,
        tray_pid_provider=lambda: tray.pid if tray is not None else -1,
    )
    return tray, secret


def _run_orchestration(data_dir: Path, args, tray, secret, *,
                       on_cold_boot_ready=None, on_startup_failed=None) -> int:
    """Run the launcher loop (``machine_loop.run_loop``)."""
    ctx = LoopContext(
        settings_dir=data_dir, secret=secret, tray=tray,
        no_browser=args.no_browser, print_url=args.print_url,
        on_ready=on_cold_boot_ready, on_failed=on_startup_failed,
    )
    return machine_loop.run_loop(ctx)


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


def _already_running(args) -> int:
    if args.reset_setup:
        print(reset_setup.REFUSED, file=sys.stderr)
        return 1
    print("Sethlans is already running. Check the system tray / running windows.", file=sys.stderr)
    return 0


def _main(argv) -> int:
    global _INSTANCE_LOCK
    diagnostics.install_diagnostics()
    started_at = time.monotonic()
    rc = 1
    try:
        args = _parse_args(argv)
        data_dir = _prepare_data_dir()
        _INSTANCE_LOCK = acquire_single_instance_lock(data_dir)
        if _INSTANCE_LOCK is None:
            rc = _already_running(args)
            return rc
        if args.reset_setup:
            reset_setup.reset_stopped()
            print(reset_setup.DONE)
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


def main(argv=None) -> int:
    """Launcher entry (module doc)."""
    child_env.prepare_launcher_environment()  # FR-LN-7: before the tray, Caddy, the browser or any thread
    return _main(argv)


if __name__ == "__main__":
    sys.exit(main() or 0)
