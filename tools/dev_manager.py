# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the Django manager (``manager/run_manager.py``) in dev mode.

Provisions ``temp/dev-data/manager/`` and points the manager's
per-user data directory at it via ``SETHLANS_MANAGER_DATA_DIR`` (which
:func:`shared.frozen_paths.get_data_dir` honours). Generates / reuses a
self-signed dev TLS cert under ``temp/dev-data/manager/tls/`` so Caddy
has something to serve, and forwards a ``--port N`` flag through as
``SETHLANS_MANAGER_PORT``.

Because ``SETHLANS_MANAGER_DATA_DIR`` is set, the source-mode manager
keeps its state in ``temp/dev-data/manager/`` instead of the source
tree (see ``sethlans_manager.state_dir``): the default SQLite database,
logs, broadcaster params, the secret-key file and FFmpeg. The TLS cert
is passed explicitly via ``SETHLANS_TLS_CERT_FILE`` /
``SETHLANS_TLS_KEY_FILE``. ``SETHLANS_SETTINGS_DIR`` points the settings
folder (``sethlans.ini`` and the files beside it) at the dev data root.
For a clean DB,
delete ``temp/dev-data/manager/db.sqlite3`` between runs.

CLI:
    python tools/dev_manager.py [--port N]

``.env`` overrides:
    SETHLANS_DEV_DATA_ROOT     — relocate dev data root.
    SETHLANS_DEV_MANAGER_PORT  — pin the manager port.
    SETHLANS_DEV_TLS_REUSE     — reuse existing dev cert (default 1).
    SETHLANS_DEV_LOG_LEVEL     — DEBUG/INFO/etc.

Existing ``SETHLANS_MANAGER_*`` env vars (``SETHLANS_MANAGER_HOST``,
``SETHLANS_MANAGER_PORT``, ``SETHLANS_TLS_CERT_FILE`` etc.) pass through.

The manager needs an admin account to start (it exits 12 without one).
Without setup, set ``SETHLANS_ADMIN_USERNAME`` with
``SETHLANS_ADMIN_PASSWORD_FILE`` (or ``SETHLANS_ADMIN_PASSWORD``) for the
first start, or run ``python manager/manage.py createsuperuser`` against
the dev database first.
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

DEFAULT_MANAGER_PORT = 8080
MANAGER_PORT_RANGE_END = 8099


def _provision_data_dir(data_root: Path) -> Path:
    """Create the manager-component data dir and return its path."""
    target = data_root / "manager"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _resolve_port(cli_port: int | None) -> int:
    """Resolve the manager port: CLI > env > scan from 8080."""
    if cli_port is not None:
        return int(cli_port)
    env_port = os.environ.get(common.ENV_MANAGER_PORT)
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    candidates = list(range(DEFAULT_MANAGER_PORT, MANAGER_PORT_RANGE_END + 1))
    chosen = common.pick_free_port(candidates, host="0.0.0.0")
    if chosen is None:
        raise SystemExit(
            f"no free port in {DEFAULT_MANAGER_PORT}..{MANAGER_PORT_RANGE_END}",
        )
    return chosen


def _build_env(
    manager_data: Path, shared_data_dir: Path,
    cert_path: Path, key_path: Path, port: int,
    log_level: str | None,
) -> dict[str, str]:
    """Compose env for ``run_manager.py``."""
    env = {
        "SETHLANS_MANAGER_DATA_DIR": str(manager_data.resolve()),
        "SETHLANS_SETTINGS_DIR": str(shared_data_dir.resolve()),
        "SETHLANS_MANAGER_HOST": "0.0.0.0",
        "SETHLANS_MANAGER_PORT": str(port),
        "SETHLANS_TLS_CERT_FILE": str(cert_path.resolve()),
        "SETHLANS_TLS_KEY_FILE": str(key_path.resolve()),
    }
    if log_level:
        env["SETHLANS_LOG_LEVEL"] = log_level
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_manager",
        description="Run the Sethlans Django manager in dev mode.",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"Manager port (default: scan {DEFAULT_MANAGER_PORT}..).",
    )
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()
    shared_data_dir = data_root
    manager_data = _provision_data_dir(data_root)
    cert_path, key_path = common.ensure_dev_cert(manager_data / "tls")
    port = _resolve_port(args.port)

    log_level = os.environ.get(common.ENV_LOG_LEVEL)

    fields = [
        ("URL:", f"https://localhost:{port}/"),
        ("Dashboard:", f"https://localhost:{port}/dashboard"),
        ("Data:", str(manager_data.resolve())),
        ("Shared:", str(shared_data_dir.resolve())),
        ("Cert:", str(cert_path.resolve())),
        ("Key:", str(key_path.resolve())),
        ("Note:", "self-signed cert; first browser visit will warn"),
    ]
    common.print_banner("Sethlans dev manager", fields)

    env = _build_env(
        manager_data, shared_data_dir,
        cert_path, key_path, port, log_level,
    )
    cmd = [
        sys.executable, str(_PROJECT_ROOT / "manager" / "run_manager.py"),
    ]
    return common.stream_subprocess(cmd, prefix="[manager]", env=env)


if __name__ == "__main__":
    sys.exit(main())
