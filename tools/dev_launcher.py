# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the full bootstrap launcher (``launcher/run_launcher.py``) in dev.

This is the "full integration without the installer" mode: the launcher
runs its loop (setup page, then the manager and/or worker, the tray and
Caddy) from a dev settings folder at ``temp/dev-data/launcher/``, so the
real per-user ``%LOCALAPPDATA%\\Sethlans`` is never touched. The
manager's and the worker's data folders sit beside it.

Streams the launcher's stdout (which includes the setup URL and token
when setup runs) through this script's stdout with a ``[launcher]``
prefix. SIGINT is forwarded so Ctrl+C tears the whole tree down via the
launcher's normal cascade.

The tray's IPC secret is the launcher's own: it makes one per session
and gives it to the tray alone (FR-LN-7), so this script does not set
one. Tray markers (``.quit_requested``, ``.restart_requested``,
``.reset_requested``) live in the settings folder; see
:mod:`launcher.tray_ipc`.

For iterating on a single component in isolation, prefer
:mod:`tools.dev_manager` or :mod:`tools.dev_worker`.

CLI:
    python tools/dev_launcher.py [--no-browser] [--print-url] [--reset-setup]

``.env`` overrides:
    SETHLANS_DEV_DATA_ROOT — relocate dev data root.
    SETHLANS_DEV_LOG_LEVEL — pass-through log level.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools import _dev_common as common  # noqa: E402

LAUNCHER_LOG_FILENAME = "launcher.log"
PASS_THROUGH_FLAGS = ("no_browser", "print_url", "reset_setup")


def _provision_data_dir(data_root: Path) -> Path:
    """Create the dev data root and the per-component folders; return the root."""
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "manager").mkdir(parents=True, exist_ok=True)
    (data_root / "worker").mkdir(parents=True, exist_ok=True)
    return data_root


def _build_env(data_root: Path, log_level: str | None) -> dict[str, str]:
    """The settings folder and the component folders the launcher's children use."""
    env = {
        "SETHLANS_SETTINGS_DIR": str(data_root.resolve()),
        "SETHLANS_MANAGER_DATA_DIR": str((data_root / "manager").resolve()),
        "SETHLANS_WORKER_DATA_DIR": str((data_root / "worker").resolve()),
    }
    if log_level:
        env["SETHLANS_LOG_LEVEL"] = log_level
    return env


def _launcher_args(args: argparse.Namespace) -> list[str]:
    return [f"--{name.replace('_', '-')}" for name in PASS_THROUGH_FLAGS if getattr(args, name)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_launcher",
        description="Run the Sethlans bootstrap launcher in dev mode.",
    )
    parser.add_argument("--no-browser", action="store_true",
                        help="Pass through to launcher: do not auto-open a browser.")
    parser.add_argument("--print-url", action="store_true",
                        help="Pass through to launcher: print URL only.")
    parser.add_argument("--reset-setup", action="store_true",
                        help="Pass through to launcher: reset setup, then start setup.")
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()
    _provision_data_dir(data_root)
    log_level = os.environ.get(common.ENV_LOG_LEVEL)

    common.print_banner("Sethlans dev launcher (full stack)", [
        ("Settings:", str(data_root.resolve())),
        ("Manager:", str((data_root / "manager").resolve())),
        ("Worker:", str((data_root / "worker").resolve())),
        ("Logs:", str((data_root / "logs" / LAUNCHER_LOG_FILENAME).resolve())),
        ("Note:", "Ctrl+C tears down via launcher cascade"),
    ])

    cmd = [sys.executable, str(_PROJECT_ROOT / "launcher" / "run_launcher.py"), *_launcher_args(args)]
    return common.stream_subprocess(cmd, prefix="[launcher]", env=_build_env(data_root, log_level))


if __name__ == "__main__":
    sys.exit(main())
