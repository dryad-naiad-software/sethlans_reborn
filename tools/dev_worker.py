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
unenrolled mode — useful for testing the worker's setup-wizard
self-flow (Spec 3 territory). The embedded web UI is forced on so
``--ui-password`` is testable.

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

Limitation: the worker's config file path is hard-wired to the source
tree (``worker/config.ini``), not the dev data dir. The ``--ui-password``
flag therefore writes the password hash to ``worker/config.ini`` and
the worker also writes its enrollment-derived JSON config to
``temp/dev-data/worker/config.json``. Run ``python tools/dev_clean.py
--component=worker --yes`` to wipe the dev data dir; ``worker/config.ini``
is gitignored and can be deleted by hand.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools import _dev_common as common  # noqa: E402

DEFAULT_UI_PORT = 8081
UI_PORT_RANGE_END = 8089
DEFAULT_UI_PASSWORD = "dev"
WORKER_INI_PATH = _PROJECT_ROOT / "worker" / "config.ini"
SALT_LENGTH = 16
HASH_ITER = 600_000


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


def _seed_ui_password(password: str) -> None:
    """Write the password hash to ``worker/config.ini``.

    Mirrors the algorithm the worker's
    :mod:`worker.sethlans_worker_agent.web_ui.auth` module uses (PBKDF2
    HMAC-SHA-256, 600k iterations, 16-byte salt). Writing the hash
    here avoids importing the worker package (which would trigger
    config_store initialisation against the wrong data dir).
    """
    salt = os.urandom(SALT_LENGTH)
    pw_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, HASH_ITER,
    )

    parser = configparser.ConfigParser()
    if WORKER_INI_PATH.exists():
        parser.read(WORKER_INI_PATH)
    if not parser.has_section("worker"):
        parser.add_section("worker")
    parser.set("worker", "ui_password_hash", pw_hash.hex())
    parser.set("worker", "ui_password_salt", salt.hex())
    for legacy in ("ui_token", "ui_password"):
        if parser.has_option("worker", legacy):
            parser.remove_option("worker", legacy)

    WORKER_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WORKER_INI_PATH.with_suffix(".ini.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        parser.write(fh)
    os.replace(str(tmp), str(WORKER_INI_PATH))


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
        "SETHLANS_DATA_DIR": str(worker_data.parent.resolve()),
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
        help="Skip writing the UI password to worker/config.ini.",
    )
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()
    worker_data = _provision_data_dir(data_root)
    ui_port = _resolve_ui_port(args.ui_port)

    if not args.no_password_seed:
        _seed_ui_password(args.ui_password)

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
         else "(seed skipped — set via UI on first visit)"),
        ("Data:", str(worker_data.resolve())),
        ("INI:", str(WORKER_INI_PATH)),
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
