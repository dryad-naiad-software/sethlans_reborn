# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Developer setup helper — populate .venv-build/ for local frozen builds.

Currently this script does exactly one thing: fetch the Caddy binary
pinned in ``tools/caddy.lock`` into ``.venv-build/caddy/`` so
``packaging/pyinstaller/launcher.spec`` can find it. As future
migration phases add more build-time assets, they are wired in here.

Idempotent: :mod:`tools.caddy_fetch.download` skips re-downloading if
the target binary already exists.

Usage::

    python tools/dev_setup.py              # auto-detect host platform
    python tools/dev_setup.py --platform linux-amd64
    python tools/dev_setup.py --verify-gpg
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the sibling caddy_fetch package importable regardless of CWD.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from caddy_fetch.cli import main as fetch_caddy_main  # noqa: E402

REPO_ROOT = _HERE.parent
DEFAULT_CADDY_TARGET = REPO_ROOT / ".venv-build" / "caddy"


def _build_fetch_argv(args: argparse.Namespace) -> list[str]:
    argv = ["--target-dir", str(DEFAULT_CADDY_TARGET)]
    if args.platform:
        argv += ["--platform", args.platform]
    if args.verify_gpg:
        argv.append("--verify-gpg")
    if args.verbose:
        argv.append("--verbose")
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_setup.py",
        description=(
            "Populate .venv-build/ with developer build prerequisites "
            "(currently: Caddy binary)."
        ),
    )
    parser.add_argument(
        "--platform",
        help=(
            "Override auto-detected platform "
            "(e.g. linux-amd64, darwin-arm64, windows-amd64)."
        ),
    )
    parser.add_argument(
        "--verify-gpg", action="store_true",
        help="Request GPG verification (best-effort).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [dev_setup] %(message)s",
    )
    logger = logging.getLogger("dev_setup")
    logger.info("Fetching Caddy into %s", DEFAULT_CADDY_TARGET)

    rc = fetch_caddy_main(_build_fetch_argv(args))
    if rc != 0:
        logger.error("Caddy fetch failed with exit code %d", rc)
        return rc

    logger.info("Dev setup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
