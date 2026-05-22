# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end frozen install smoke (issue #197).

Spawns the real frozen launcher against an isolated temp data dir,
drives the wizard walk-through via HTTP, waits for the apply pipeline
to write ``.setup_complete``, and probes the manager's
``/api/health/``. Catches the five frozen-mode-only bugs (#190, #191,
#192, #195, #196) that integration tests miss because they source-mode
the launcher and / or monkey-patch the apply pipeline.

This tool is a BUILD-TIME smoke. It is intentionally slow
(~60-90 s wall-clock against a real bundle) and is wired into all
three platform build scripts after the wizard smoke and before the
installer packaging step. See ``development/specs/install_smoke_e2e.md``
for the full functional requirements.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

# Expose tools/ (sibling helpers) on sys.path.
_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from _install_smoke_driver import drive_install  # noqa: E402
from _wizard_smoke_helpers import (  # noqa: E402
    dump_logs, err, install_wall_clock_watchdog, terminate,
)

WALL_CLOCK_BUDGET_SECONDS = 300
DEFAULT_MANAGER_PORT = 8181
LOG_TAIL_DIR_PREFIX = "install_smoke_"


def _binary_name(component: str) -> str:
    suffix = ".exe" if sys.platform == "win32" else ""
    return f"run_{component}{suffix}"


def _locate_binaries(
    dist_root: pathlib.Path,
) -> dict[str, pathlib.Path]:
    """Return ``{component: exe_path}`` for launcher/manager/wizard.

    Raises ``FileNotFoundError`` (with ``MISSING BINARY`` prefix) on
    the first missing binary so the smoke can exit 2 before spawning
    anything.
    """
    binaries: dict[str, pathlib.Path] = {}
    for component in ("launcher", "manager", "wizard"):
        exe = dist_root / component / _binary_name(component)
        if not exe.is_file():
            raise FileNotFoundError(f"MISSING BINARY: {exe}")
        binaries[component] = exe
    return binaries


def _spawn_launcher(
    launcher_exe: pathlib.Path,
    data_dir: pathlib.Path,
    log_out: pathlib.Path,
    log_err: pathlib.Path,
) -> subprocess.Popen:
    """Spawn the frozen launcher with the smoke's isolated data dir.

    Per #199 the smoke MUST set all three env vars (shared + per-component)
    so the manager and worker subprocesses also resolve their data dirs
    inside ``data_dir``. Without ``SETHLANS_MANAGER_DATA_DIR`` the manager's
    DB lands in the real ``%LOCALAPPDATA%\\Sethlans\\manager\\``, polluting
    user state and false-flagging smoke re-runs. ``--no-browser``
    suppresses the open-browser side-effect of FR-L3 / FR-L11.
    """
    env = os.environ.copy()
    env["SETHLANS_DATA_DIR"] = str(data_dir)
    env["SETHLANS_MANAGER_DATA_DIR"] = str(data_dir / "manager")
    env["SETHLANS_WORKER_DATA_DIR"] = str(data_dir / "worker")

    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    out_fh = open(log_out, "w", encoding="utf-8")
    err_fh = open(log_err, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(launcher_exe), "--no-browser"],
        stdout=out_fh, stderr=err_fh, env=env, **popen_kwargs,
    )
    # Stash file handles on the Popen so the caller can close them at
    # teardown. ``stdout``/``stderr`` Popen attributes are ``None`` when
    # files (not pipes) are used so we can't rely on those.
    proc._smoke_out_fh = out_fh  # type: ignore[attr-defined]
    proc._smoke_err_fh = err_fh  # type: ignore[attr-defined]
    return proc


def _close_log_handles(proc: subprocess.Popen) -> None:
    """Close the smoke's captured stdout/stderr file handles."""
    for attr in ("_smoke_out_fh", "_smoke_err_fh"):
        fh = getattr(proc, attr, None)
        if fh is None:
            continue
        try:
            fh.close()
        except OSError:
            pass


def _archive_logs(
    log_out: pathlib.Path, log_err: pathlib.Path,
) -> pathlib.Path | None:
    """Copy launcher stdout/stderr into ``.tmp/install_smoke_<ts>/``.

    FR-SMOKE11(2): persist full logs for post-mortem so the operator
    can debug a failure even after the temp data dir is wiped.
    """
    repo_root = _HERE.parent
    tmp_root = repo_root / ".tmp"
    try:
        tmp_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        err(f"could not create {tmp_root}: {exc}")
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    archive_dir = tmp_root / f"{LOG_TAIL_DIR_PREFIX}{ts}"
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for src in (log_out, log_err):
            if src.is_file():
                shutil.copy2(src, archive_dir / src.name)
    except OSError as exc:
        err(f"could not archive logs to {archive_dir}: {exc}")
        return None
    return archive_dir


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dist-root", default="dist",
        help="Path to the PyInstaller dist tree (default: dist)",
    )
    parser.add_argument(
        "--manager-port", type=int, default=DEFAULT_MANAGER_PORT,
        help=(
            f"Port the manager binds and the smoke probes via HTTPS "
            f"(default: {DEFAULT_MANAGER_PORT}). Avoids the 8080 "
            "production default and the 8100 wizard-smoke pin."
        ),
    )
    parser.add_argument(
        "--skip-install-smoke", action="store_true",
        help=(
            "Skip the install smoke entirely (FR-BUILD4). Build "
            "scripts never pass this by default; developers may add "
            "it manually for iteration."
        ),
    )
    return parser


def _run_smoke(
    binaries: dict[str, pathlib.Path], manager_port: int,
) -> int:
    """Provision tmp dir, spawn launcher, drive install, tear down."""
    tmp = pathlib.Path(
        tempfile.mkdtemp(prefix="sethlans-install-smoke-"),
    )
    log_out = tmp / "launcher-stdout.log"
    log_err = tmp / "launcher-stderr.log"

    print(f"Install smoke: tmp data dir {tmp}")
    print(f"Install smoke: manager port {manager_port}")

    launcher_proc = _spawn_launcher(
        binaries["launcher"], tmp, log_out, log_err,
    )
    try:
        ok = drive_install(
            launcher_proc=launcher_proc,
            data_dir=tmp,
            manager_port=manager_port,
            log_out=log_out,
            log_err=log_err,
        )
    finally:
        terminate(launcher_proc)
        _close_log_handles(launcher_proc)

    if not ok:
        err("--- launcher logs (FR-SMOKE11 dump) ---")
        dump_logs(log_out, log_err)
        archive = _archive_logs(log_out, log_err)
        if archive is not None:
            err(f"Full logs preserved at {archive}")
        # Tear down temp dir AFTER the dump so anything read above is
        # still on disk. FR-SMOKE12: tear-down runs unconditionally.
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    shutil.rmtree(tmp, ignore_errors=True)
    print("Install smoke passed")
    return 0


def main() -> int:
    args = _build_argparser().parse_args()

    if args.skip_install_smoke:
        print("install smoke skipped")
        return 0

    install_wall_clock_watchdog(WALL_CLOCK_BUDGET_SECONDS)

    dist_root = pathlib.Path(args.dist_root).resolve()
    if not dist_root.is_dir():
        err(f"ERROR: dist root not found at {dist_root}")
        return 2
    try:
        binaries = _locate_binaries(dist_root)
    except FileNotFoundError as exc:
        err(str(exc))
        return 2

    return _run_smoke(binaries, args.manager_port)


if __name__ == "__main__":
    sys.exit(main())
