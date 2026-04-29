# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the full bootstrap launcher (``launcher/run_launcher.py``) in dev.

This is the "full integration without the installer" mode: the
launcher itself owns spawning the wizard / manager / worker / tray
based on first-run state, and this script just wires the data dir at
``temp/dev-data/launcher/`` and exports a fresh
``SETHLANS_TRAY_IPC_SECRET`` so external tooling can poke the tray IPC
during dev runs.

Streams the launcher's stdout (which includes the launcher's own
prefixed wizard / manager / worker / tray output) through this
script's stdout with a ``[launcher]`` prefix. SIGINT is forwarded so
Ctrl+C tears the whole tree down via the launcher's normal cascade.

Use this when testing the wizard → manager handoff (Phase 3+) or any
behaviour that depends on the launcher's process supervision (tray,
splash, restart cascade). For iterating on a single component in
isolation, prefer :mod:`tools.dev_wizard`, :mod:`tools.dev_manager`,
or :mod:`tools.dev_worker`.

CLI:
    python tools/dev_launcher.py [--no-browser] [--print-url]

``.env`` overrides:
    SETHLANS_DEV_DATA_ROOT — relocate dev data root.
    SETHLANS_DEV_LOG_LEVEL — pass-through log level.
    SETHLANS_DEV_TLS_REUSE — reuse cached dev certs.

Existing ``SETHLANS_*`` env vars (``SETHLANS_TRAY_IPC_SECRET`` if you
already exported one, ``SETHLANS_DATA_DIR``) pass through. The IPC
secret is only auto-generated when the env var is not already set.

Tray IPC: the tray helper reads its IPC secret from
``SETHLANS_TRAY_IPC_SECRET``. To poke the tray from another shell
during a dev run, copy the secret printed in the banner. Markers go
to ``<data_root>/manager/.restart_requested`` and ``.quit_requested``
(see :mod:`launcher.tray_ipc`); HMAC framing is documented there.
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


def _provision_data_dir(data_root: Path) -> Path:
    """Create the launcher data dir tree and return the shared root.

    The launcher resolves its data dir via
    :func:`shared.frozen_paths.get_data_dir`, which honours
    ``SETHLANS_MANAGER_DATA_DIR``. We pin the shared root via
    ``SETHLANS_DATA_DIR`` so the per-component dirs hang off the same
    parent.
    """
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "manager").mkdir(parents=True, exist_ok=True)
    (data_root / "worker").mkdir(parents=True, exist_ok=True)
    (data_root / "wizard").mkdir(parents=True, exist_ok=True)
    return data_root


def _resolve_or_generate_ipc_secret() -> tuple[str, bool]:
    """Return ``(secret, generated)`` — generated=True if we created it."""
    existing = os.environ.get("SETHLANS_TRAY_IPC_SECRET")
    if existing:
        return existing, False
    return common.generate_token(), True


def _build_env(
    data_root: Path, ipc_secret: str, log_level: str | None,
) -> dict[str, str]:
    env = {
        "SETHLANS_DATA_DIR": str(data_root.resolve()),
        "SETHLANS_MANAGER_DATA_DIR": str((data_root / "manager").resolve()),
        "SETHLANS_WORKER_DATA_DIR": str((data_root / "worker").resolve()),
        "SETHLANS_TRAY_IPC_SECRET": ipc_secret,
    }
    if log_level:
        env["SETHLANS_LOG_LEVEL"] = log_level
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_launcher",
        description="Run the Sethlans bootstrap launcher in dev mode.",
    )
    parser.add_argument(
        "--no-browser", action="store_true", default=False,
        help="Pass through to launcher: do not auto-open a browser.",
    )
    parser.add_argument(
        "--print-url", action="store_true", default=False,
        help="Pass through to launcher: print URL only.",
    )
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()
    _provision_data_dir(data_root)

    ipc_secret, generated = _resolve_or_generate_ipc_secret()
    log_level = os.environ.get(common.ENV_LOG_LEVEL)

    fields = [
        ("Data:", str(data_root.resolve())),
        ("Manager:", str((data_root / "manager").resolve())),
        ("Worker:", str((data_root / "worker").resolve())),
        ("Wizard:", str((data_root / "wizard").resolve())),
        ("Logs:", str((data_root / LAUNCHER_LOG_FILENAME).resolve())),
        ("Tray IPC:", ipc_secret),
        ("IPC source:", "generated" if generated else "from env"),
        (
            "Tray markers:",
            str((data_root / "manager").resolve()),
        ),
        ("Note:", "Ctrl+C tears down via launcher cascade"),
    ]
    common.print_banner("Sethlans dev launcher (full stack)", fields)

    env = _build_env(data_root, ipc_secret, log_level)
    extra: list[str] = []
    if args.no_browser:
        extra.append("--no-browser")
    if args.print_url:
        extra.append("--print-url")
    cmd = [
        sys.executable,
        str(_PROJECT_ROOT / "launcher" / "run_launcher.py"),
        *extra,
    ]
    return common.stream_subprocess(cmd, prefix="[launcher]", env=env)


if __name__ == "__main__":
    sys.exit(main())
