# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the worker agent (``worker/run_worker.py``) in dev mode.

Provisions ``temp/dev-data/worker/`` and points the worker's per-user
data directory at it via ``SETHLANS_WORKER_DATA_DIR``. Optionally
forwards a manager URL + enrollment key as
``SETHLANS_MANAGER_HOST`` / ``SETHLANS_MANAGER_PORT`` /
``SETHLANS_WORKER_ENROLLMENT_KEY`` so the worker enrolls against a
``dev_manager.py`` instance on first start.

Without ``--manager-url`` + ``--enrollment-key``, the worker boots in
unenrolled mode: it logs "not enrolled; run setup" and checks again
every 60 s (enroll it with ``run_worker.py --setup-apply``). The
embedded web UI is forced on so ``--ui-password`` is testable.

CLI:
    python tools/dev_worker.py [--ui-password PW] [--ui-port N]
                               [--manager-url URL] [--enrollment-key KEY]

``.env`` overrides:
    SETHLANS_DEV_DATA_ROOT     — relocate dev data root.
    SETHLANS_DEV_WORKER_UI_PORT — pin the worker UI port.
    SETHLANS_DEV_LOG_LEVEL     — pass-through log level.
    SETHLANS_DEV_TLS_REUSE     — reuse existing dev cert.

Existing ``SETHLANS_WORKER_*`` env vars (``SETHLANS_WORKER_UI_ENABLED``,
``SETHLANS_WORKER_HEARTBEAT_INTERVAL`` etc.) pass through.

The ``--ui-password`` flag writes the worker page password hash
(Django's ``pbkdf2_sha256`` format, setup-rebuild FR-WK-3) to
``worker.ui_password_hash`` in ``temp/dev-data/worker/config.json``,
next to the enrollment the worker writes there. Run ``python
tools/dev_clean.py --component=worker --yes`` to wipe the dev data dir.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools import _dev_common as common  # noqa: E402

DEFAULT_UI_PORT = 8081
UI_PORT_RANGE_END = 8089
# Passes the worker's page password rules (8+ characters, not all digits).
DEFAULT_UI_PASSWORD = "sethlans-dev"
_WEB_UI_DIR = _PROJECT_ROOT / "worker" / "sethlans_worker_agent" / "web_ui"


def _provision_data_dir(data_root: Path) -> Path:
    """Create the worker-component data dir."""
    target = data_root / "worker"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _resolve_ui_port(cli_port: int | None) -> int:
    """Resolve the worker UI port: CLI > env > scan from 8081."""
    if cli_port is not None:
        return int(cli_port)
    env_port = os.environ.get(common.ENV_WORKER_UI_PORT)
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    candidates = list(range(DEFAULT_UI_PORT, UI_PORT_RANGE_END + 1))
    chosen = common.pick_free_port(candidates)
    return chosen if chosen is not None else DEFAULT_UI_PORT


def _web_ui_module(name: str):
    """Load the worker's stdlib-only ``web_ui/<name>.py`` by path.

    Importing it through the worker package would import ``config`` and
    read the real per-user data dir instead of the dev one.
    """
    spec = importlib.util.spec_from_file_location(f"_dev_{name}", _WEB_UI_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _password_hash_module():
    return _web_ui_module("password_hash")


def _ui_password_problems(password: str) -> list[str]:
    """The worker's page password rules, against this computer's name like the worker's own callers."""
    return _web_ui_module("password_rules").problems(password, socket.gethostname())


def _seed_ui_password(password: str, worker_data: Path) -> None:
    """Write the page password hash to ``<worker_data>/config.json``."""
    config_path = worker_data / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    section = data.get("worker")
    if not isinstance(section, dict):
        section = data["worker"] = {}
    section["ui_password_hash"] = _password_hash_module().make(password)
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(config_path))


def _split_manager_url(url: str) -> tuple[str, int]:
    """Parse ``[https://]host[:port][/...]`` into ``(host, port)``.

    Stdlib only; tolerant of missing scheme. Defaults port to 8080.
    """
    raw = url.strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if ":" in raw:
        host, port_s = raw.rsplit(":", 1)
        try:
            return host, int(port_s)
        except ValueError:
            return host, 8080
    return raw, 8080


def _build_env(
    worker_data: Path, ui_port: int,
    manager_url: str | None, enrollment_key: str | None,
    log_level: str | None,
) -> dict[str, str]:
    env = {
        "SETHLANS_WORKER_DATA_DIR": str(worker_data.resolve()),
        "SETHLANS_SETTINGS_DIR": str(worker_data.parent.resolve()),
        "SETHLANS_WORKER_UI_PORT": str(ui_port),
        "SETHLANS_WORKER_UI_ENABLED": "true",
    }
    if manager_url:
        host, port = _split_manager_url(manager_url)
        env["SETHLANS_MANAGER_HOST"] = host
        env["SETHLANS_MANAGER_PORT"] = str(port)
    if enrollment_key:
        env["SETHLANS_WORKER_ENROLLMENT_KEY"] = enrollment_key
    if log_level:
        env["SETHLANS_LOG_LEVEL"] = log_level
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_worker",
        description="Run the Sethlans worker agent in dev mode.",
    )
    parser.add_argument(
        "--ui-password", type=str, default=DEFAULT_UI_PASSWORD,
        help=f"Worker UI password (default: {DEFAULT_UI_PASSWORD!r}).",
    )
    parser.add_argument(
        "--ui-port", type=int, default=None,
        help=f"Worker UI port (default: scan {DEFAULT_UI_PORT}..).",
    )
    parser.add_argument(
        "--manager-url", type=str, default=None,
        help="Manager URL for enrollment, e.g. https://localhost:8080",
    )
    parser.add_argument(
        "--enrollment-key", type=str, default=None,
        help="Enrollment key paired with --manager-url (HMAC bootstrap).",
    )
    parser.add_argument(
        "--no-password-seed", action="store_true", default=False,
        help="Skip writing the UI password hash to the dev config.json.",
    )
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()
    worker_data = _provision_data_dir(data_root)
    ui_port = _resolve_ui_port(args.ui_port)

    if not args.no_password_seed:
        found = _ui_password_problems(args.ui_password)
        if found:
            print("[dev_worker] ERROR: --ui-password breaks the worker page password rules:", file=sys.stderr)
            for problem in found:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        _seed_ui_password(args.ui_password, worker_data)

    log_level = os.environ.get(common.ENV_LOG_LEVEL)

    enroll_paired = bool(args.manager_url) == bool(args.enrollment_key)
    if not enroll_paired:
        print(
            "[dev_worker] WARN: --manager-url and --enrollment-key must "
            "be paired. Worker will boot unenrolled.", file=sys.stderr,
        )
        args.manager_url = None
        args.enrollment_key = None

    fields = [
        ("UI URL:", f"http://localhost:{ui_port}/"),
        ("UI Password:", args.ui_password if not args.no_password_seed
         else "(seed skipped — page controls refused until one is set)"),
        ("Data:", str(worker_data.resolve())),
        ("Config:", str((worker_data / "config.json").resolve())),
    ]
    if args.manager_url and args.enrollment_key:
        fields.append(("Manager:", args.manager_url))
        fields.append(("Enrollment:", "(set, will enroll on first poll)"))
    else:
        fields.append(
            ("Mode:", "unenrolled (no --manager-url/--enrollment-key)"),
        )
    common.print_banner("Sethlans dev worker", fields)

    env = _build_env(
        worker_data, ui_port,
        args.manager_url, args.enrollment_key,
        log_level,
    )
    cmd = [sys.executable, str(_PROJECT_ROOT / "worker" / "run_worker.py")]
    return common.stream_subprocess(cmd, prefix="[worker]", env=env)


if __name__ == "__main__":
    sys.exit(main())
