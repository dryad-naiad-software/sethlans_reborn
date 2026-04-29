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

When called with ``--seed-pending <path>``, copies the named JSON file
to ``<dev-data>/pending_setup.json`` so the Phase 3 apply command can
pick it up. (Phase 3 doesn't exist yet at the time of writing — the
flag is here so this script doesn't need rewriting once it lands.)

Note: the manager's source-mode bootstrap reads ``manager/manager.ini``
and writes its database / TLS certs under the manager source tree. The
dev script does NOT redirect those into ``temp/dev-data/`` because
``run_manager.py`` is hard-wired to the source-tree paths in dev mode.
What this script does redirect is the *shared* per-user data dir
(setup sentinel, topology, IPC markers) via
``SETHLANS_MANAGER_DATA_DIR``. If you want a clean DB, delete
``manager/db.sqlite3`` between runs.

CLI:
    python tools/dev_manager.py [--port N] [--seed-pending PATH]

``.env`` overrides:
    SETHLANS_DEV_DATA_ROOT     — relocate dev data root.
    SETHLANS_DEV_MANAGER_PORT  — pin the manager port.
    SETHLANS_DEV_TLS_REUSE     — reuse existing dev cert (default 1).
    SETHLANS_DEV_LOG_LEVEL     — DEBUG/INFO/etc.

Existing ``SETHLANS_MANAGER_*`` env vars (``SETHLANS_MANAGER_HOST``,
``SETHLANS_MANAGER_PORT``, ``SETHLANS_TLS_CERT_FILE`` etc.) pass through.

To get an admin user without the wizard, run
``python manager/manage.py setup_auth`` (existing CLI bootstrap)
or seed a ``pending_setup.json`` via ``--seed-pending`` once Phase 3
ships.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools import _dev_common as common  # noqa: E402

DEFAULT_MANAGER_PORT = 8080
MANAGER_PORT_RANGE_END = 8099
PENDING_SETUP_FILENAME = "pending_setup.json"


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


def _seed_pending_setup(seed_path: Path, data_root: Path) -> Path:
    """Copy *seed_path* to ``<data_root>/pending_setup.json``."""
    src = Path(seed_path).resolve()
    if not src.is_file():
        raise SystemExit(f"--seed-pending: file not found: {src}")
    dst = (data_root / PENDING_SETUP_FILENAME).resolve()
    try:
        dst.relative_to(data_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"refusing to write outside data root: {dst}") from exc
    shutil.copyfile(str(src), str(dst))
    if os.name != "nt":
        try:
            os.chmod(str(dst), 0o600)
        except OSError:
            pass
    return dst


def _build_env(
    manager_data: Path, shared_data_dir: Path,
    cert_path: Path, key_path: Path, port: int,
    log_level: str | None,
) -> dict[str, str]:
    """Compose env for ``run_manager.py``."""
    env = {
        "SETHLANS_MANAGER_DATA_DIR": str(manager_data.resolve()),
        "SETHLANS_DATA_DIR": str(shared_data_dir.resolve()),
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
    parser.add_argument(
        "--seed-pending", type=str, default=None,
        help="Path to a pending_setup.json to seed before manager starts.",
    )
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()
    shared_data_dir = data_root
    manager_data = _provision_data_dir(data_root)
    cert_path, key_path = common.ensure_dev_cert(manager_data / "tls")
    port = _resolve_port(args.port)

    seeded: Path | None = None
    if args.seed_pending:
        seeded = _seed_pending_setup(Path(args.seed_pending), data_root)

    log_level = os.environ.get(common.ENV_LOG_LEVEL)

    fields = [
        ("URL:", f"https://localhost:{port}/"),
        ("Dashboard:", f"https://localhost:{port}/dashboard"),
        ("Data:", str(manager_data.resolve())),
        ("Shared:", str(shared_data_dir.resolve())),
        ("Cert:", str(cert_path.resolve())),
        ("Key:", str(key_path.resolve())),
    ]
    if seeded is not None:
        fields.append(("Pending:", str(seeded)))
    fields.append(
        ("Note:", "self-signed cert; first browser visit will warn"),
    )
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
