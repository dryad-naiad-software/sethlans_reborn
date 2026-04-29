# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the standalone wizard (``wizard/run_wizard.py``) in dev mode.

Provisions ``temp/dev-data/wizard/`` with the launcher-equivalent
``.setup_token`` + ``.ipc_secret`` files (consumed and unlinked by the
wizard at startup, FR-W6 / SEC-MED-11), generates a dev TLS cert under
``temp/dev-data/wizard/tls/`` (kept alongside even though Issue #170
moved TLS termination to Caddy — the file just isn't used here), picks
a free port from the wizard's loopback range (8099, 8101..8104), and
spawns the wizard as a subprocess so Ctrl+C cleanly terminates the
child.

Use this when iterating on wizard backend handlers, frontend pages, or
the IPC marker contract without rebuilding the full launcher /
installer. The browser opens directly against the wizard's loopback
port — no Caddy fronting — so cert warnings will appear (the dev cert
is self-signed). Click through; the wizard listens on plain HTTP per
Issue #170, so the cert is only relevant if you reverse-proxy through
your own TLS terminator.

CLI:
    python tools/dev_wizard.py [--port N]

``.env`` overrides:
    SETHLANS_DEV_DATA_ROOT   — relocate dev data root (default temp/dev-data).
    SETHLANS_DEV_WIZARD_PORT — pin the wizard port instead of scanning.
    SETHLANS_DEV_TLS_REUSE   — 1/0; reuse existing dev cert (default 1).
    SETHLANS_DEV_LOG_LEVEL   — DEBUG/INFO/etc.; passed via env to child.

Existing ``SETHLANS_*`` env vars (``SETHLANS_WIZARD_PORT``,
``SETHLANS_DATA_DIR``) the wizard already honours pass through unchanged.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Project root must be importable so _dev_common can pull in cert_utils.
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools import _dev_common as common  # noqa: E402

WIZARD_PORT_CANDIDATES = (8099, 8101, 8102, 8103, 8104)
SETUP_TOKEN_FILENAME = ".setup_token"
IPC_SECRET_FILENAME = ".ipc_secret"
TRAY_SETUP_TOKEN_FILENAME = "setup_token"


def _atomic_write_secret(path: Path, value: bytes) -> None:
    """Write *value* atomically with chmod-600-equivalent perms.

    Mirrors :func:`launcher.wizard_dir.write_secret_file` so the wizard
    sees the same on-disk shape it would in a real launcher run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
    )
    try:
        os.write(fd, value)
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    if os.name != "nt":
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass


def _provision_data_dir(data_root: Path) -> tuple[Path, str, bytes]:
    """Create the wizard data dir and write the secret pair.

    Returns ``(wizard_dir, setup_token, ipc_secret)`` so the caller can
    print the token and pass the dir to the child as ``SETHLANS_DATA_DIR``.
    """
    shared_dir = data_root
    shared_dir.mkdir(parents=True, exist_ok=True)
    wizard_dir = shared_dir / "wizard"
    wizard_dir.mkdir(parents=True, exist_ok=True)

    setup_token = common.generate_token()
    ipc_secret = common.generate_token().encode("ascii")

    _atomic_write_secret(
        wizard_dir / SETUP_TOKEN_FILENAME, setup_token.encode("utf-8"),
    )
    _atomic_write_secret(
        wizard_dir / IPC_SECRET_FILENAME, ipc_secret,
    )
    # Tray-readable copy (FR-L13 cleans this on handoff in real runs).
    _atomic_write_secret(
        wizard_dir / TRAY_SETUP_TOKEN_FILENAME, setup_token.encode("utf-8"),
    )
    return wizard_dir, setup_token, ipc_secret


def _resolve_port(cli_port: int | None) -> int:
    """Resolve the wizard port: CLI > env > free-port scan."""
    if cli_port is not None:
        return int(cli_port)
    env_port = os.environ.get(common.ENV_WIZARD_PORT)
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    chosen = common.pick_free_port(WIZARD_PORT_CANDIDATES)
    if chosen is None:
        raise SystemExit(
            "no free port in 8099/8101..8104; close a previous instance "
            "or set SETHLANS_DEV_WIZARD_PORT to an explicit port",
        )
    return chosen


def _build_env(
    shared_data_dir: Path, port: int, log_level: str | None,
) -> dict[str, str]:
    """Compose the SETHLANS_* env vars handed to ``run_wizard.py``."""
    env = {
        # frozen_paths.get_shared_data_dir() honours SETHLANS_DATA_DIR.
        "SETHLANS_DATA_DIR": str(shared_data_dir.resolve()),
        # The wizard's own server.resolve_port honours this.
        "SETHLANS_WIZARD_PORT": str(port),
    }
    if log_level:
        env["SETHLANS_LOG_LEVEL"] = log_level
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_wizard",
        description="Run the Sethlans wizard in dev mode.",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="Pin the wizard port (default: scan 8099/8101..8104).",
    )
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()
    shared_data_dir = data_root  # wizard reads <data_dir>/wizard/

    wizard_dir, setup_token, ipc_secret = _provision_data_dir(data_root)
    cert_path, key_path = common.ensure_dev_cert(wizard_dir / "tls")
    port = _resolve_port(args.port)

    log_level = os.environ.get(common.ENV_LOG_LEVEL)

    common.print_banner(
        "Sethlans dev wizard",
        [
            ("URL:", f"http://127.0.0.1:{port}/"),
            ("Token:", setup_token),
            ("IPC:", ipc_secret.decode("ascii")),
            ("Data:", str(wizard_dir.resolve())),
            ("Cert:", str(cert_path.resolve())),
            ("Key:", str(key_path.resolve())),
        ],
    )

    env = _build_env(shared_data_dir, port, log_level)
    cmd = [sys.executable, str(_PROJECT_ROOT / "wizard" / "run_wizard.py")]
    return common.stream_subprocess(cmd, prefix="[wizard]", env=env)


if __name__ == "__main__":
    sys.exit(main())
