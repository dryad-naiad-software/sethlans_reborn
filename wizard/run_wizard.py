# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Entry point for the Sethlans Reborn standalone setup wizard.

Wires the A2-A4 modules (cert, ipc, server, handlers) into a runnable
HTTPS waitress server per Spec 1 (FR-W1, FR-W3, FR-W4, FR-W5, FR-W6,
FR-W11, FR-W12, FR-W17). The launcher (A6) writes the chmod-600
``.setup_token`` and ``.ipc_secret`` files under ``<data_dir>/wizard/``
before spawning this process; we read them, immediately ``unlink()``
them (FR-W6 / SEC-MED-11), then bring up the WSGI app behind a
TLS-wrapped socket.

CLI flags:
  --version       Print the wizard version and exit 0 (FR-W1).
  --no-browser    Reserved (FR-L11): the launcher honours this; the
                  wizard process itself never opens a browser.
  --print-url     Print the wizard URL to stdout, then exit before
                  binding the listener (useful for headless / CI).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# In frozen mode PyInstaller handles sys.path; only add the wizard
# directory and project root when running from source. Mirrors the
# pattern used by ``worker/run_worker.py``.
if not getattr(sys, "frozen", False):
    wizard_dir = str(Path(__file__).resolve().parent)
    if wizard_dir not in sys.path:
        sys.path.insert(0, wizard_dir)
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from shared.frozen_paths import get_shared_data_dir  # noqa: E402
from shared.version import get_version  # noqa: E402

import sethlans_wizard  # noqa: F401, E402  (ensures package importable)
from wizard.sethlans_wizard import bootstrap, cert, server  # noqa: E402

logger = logging.getLogger("wizard")


def build_parser() -> argparse.ArgumentParser:
    """Return the wizard's argparse parser.

    Exposed at module scope so tests can introspect the registered
    flags without invoking ``main()``.
    """
    parser = argparse.ArgumentParser(
        prog="run_wizard",
        description="Sethlans Reborn standalone setup wizard.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sethlans-wizard {get_version()}",
        help="Print the wizard version and exit.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=False,
        help="Reserved (FR-L11): launcher controls browser open; wizard never opens one.",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        default=False,
        help="Print the wizard URL to stdout and exit before binding.",
    )
    return parser


def _print_url_banner(port: int, *, no_browser: bool) -> None:
    """Print the wizard URL to stdout.

    The launcher prints the LAN-bound IP + setup token banner; this is
    the wizard's own startup banner so operators tailing the wizard log
    can see the URL it's serving on.
    """
    print(f"Wizard URL: https://localhost:{port}/")
    if no_browser:
        print("(--no-browser set; wizard will not request browser open)")


def _prepare_runtime(data_dir: Path, subdir: Path, requested_port: int):
    """Read secrets, generate cert, build app. Return ``(app, cert, key)``.

    Raises ``RuntimeError`` with a logged context on any failure so
    ``main()`` can keep its branching shallow.
    """
    try:
        setup_token, ipc_secret = bootstrap.read_secrets(subdir)
    except FileNotFoundError as exc:
        logger.error(
            "Required wizard secret file missing: %s. The launcher must "
            "provision .setup_token and .ipc_secret under %s before "
            "spawning the wizard (FR-L3a / FR-L4a).",
            exc, subdir,
        )
        raise RuntimeError("missing-secret") from exc
    except (OSError, ValueError) as exc:
        logger.error("Could not read wizard secret files: %s", exc)
        raise RuntimeError("bad-secret") from exc

    try:
        cert_path, key_path = cert.ensure_cert(data_dir)
    except Exception as exc:
        logger.exception("Failed to generate / load wizard TLS certificate")
        raise RuntimeError("cert-failed") from exc

    try:
        app = server.create_app(
            data_dir, setup_token, ipc_secret, wizard_port=requested_port,
        )
    except Exception as exc:
        logger.exception("Failed to build wizard WSGI app")
        raise RuntimeError("app-build-failed") from exc

    return app, cert_path, key_path


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    data_dir = Path(get_shared_data_dir()).resolve(strict=False)
    subdir = bootstrap.wizard_subdir(data_dir)

    bootstrap.configure_logging(subdir)
    # MED-1 — install signal handlers IMMEDIATELY after configure_logging,
    # before any I/O. The handler resolves the server ref at fire-time
    # so it's a safe no-op when invoked before the listener binds. This
    # closes the window where SIGTERM during cert generation / secret
    # read leaves the wizard unresponsive to graceful shutdown.
    bootstrap.install_signal_handlers()

    logger.info(
        "sethlans-wizard %s starting (data_dir=%s)", get_version(), data_dir,
    )

    # Resolve requested port BEFORE reading secrets so a malformed
    # SETHLANS_WIZARD_PORT fails fast without consuming the secret files.
    try:
        requested_port = server.resolve_port(os.environ)
    except ValueError as exc:
        logger.error("Invalid SETHLANS_WIZARD_PORT: %s", exc)
        return 2

    env_port = (
        requested_port if os.environ.get(server.WIZARD_PORT_ENV) else None
    )

    if args.print_url:
        _print_url_banner(requested_port, no_browser=args.no_browser)
        return 0

    try:
        app, cert_path, key_path = _prepare_runtime(
            data_dir, subdir, requested_port,
        )
    except RuntimeError:
        return 2

    _print_url_banner(requested_port, no_browser=args.no_browser)

    # FR-W17(b)/(c) — spawn the post-done polling thread before the
    # listener starts so the .wizard_reject / failsafe machinery is
    # already wired up by the time the done handler fires.
    # ipc_secret is held inside the app's closure; pull it out via
    # the introspection attribute set by server.create_app.
    ipc_secret = getattr(app, "_ipc_secret", b"")
    if ipc_secret:
        bootstrap.start_post_done_threads(data_dir, ipc_secret)

    try:
        return bootstrap.serve_with_port_scan(
            app, cert_path, key_path, env_port, subdir,
        )
    except Exception:
        logger.exception("Wizard server crashed")
        return 2


if __name__ == "__main__":
    sys.exit(main())
