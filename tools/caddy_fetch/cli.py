# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Command-line entry point for ``tools/fetch_caddy.py``.

Exit codes (per spec):
  * 0 — success
  * 1 — network, IO, or argument error
  * 2 — SHA-256 integrity failure
  * 3 — GPG signature verification failure
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
from pathlib import Path

from .download import fetch_and_install
from .exceptions import GpgVerificationError, IntegrityError
from .lockfile import build_url, detect_host_platform, load_lockfile

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_SHA_MISMATCH = 2
EXIT_GPG_FAIL = 3

logger = logging.getLogger("fetch_caddy")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fetch_caddy.py",
        description="Fetch and verify the pinned Caddy binary.",
    )
    parser.add_argument(
        "--target-dir", required=True, type=Path,
        help=(
            "Output directory. In single-platform mode caddy[.exe] is "
            "written directly here. In --multi-arch mode one "
            "subdirectory per platform is created."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--platform",
        help=(
            "Single-platform mode: e.g. linux-amd64, windows-amd64. "
            "When neither --platform nor --multi-arch is given, the "
            "host platform is auto-detected."
        ),
    )
    group.add_argument(
        "--multi-arch",
        help=(
            "Multi-platform mode: comma-separated list "
            "(e.g. linux-amd64,linux-arm64). Each platform goes into "
            "<target-dir>/<platform>/caddy[.exe]."
        ),
    )
    parser.add_argument(
        "--verify-gpg", action="store_true",
        help=(
            "Attempt GPG signature verification (release builds). "
            "Silently skipped if gpg is not on PATH or upstream has no "
            "detached signature."
        ),
    )
    parser.add_argument(
        "--lockfile", type=Path, default=None,
        help="Override path to caddy.lock (defaults to tools/caddy.lock).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args(argv)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [fetch_caddy] %(message)s",
    )


def _target_binary(target_dir: Path, platform_key: str) -> Path:
    binary = "caddy.exe" if platform_key.startswith("windows-") else "caddy"
    return target_dir / binary


def _fetch_for_platform(
    lock: dict, platform_key: str, target_dir: Path, verify_gpg_flag: bool,
) -> None:
    url = build_url(lock, platform_key)
    info = lock["platforms"][platform_key]
    target_binary = _target_binary(target_dir, platform_key)

    # When ``--verify-gpg`` is set, pass the lockfile's per-platform
    # ``sig_url`` into ``fetch_and_install`` (empty string if the
    # lockfile does not advertise one — today's Caddy v2.8.4 state).
    # An empty string triggers the "no signature configured" warning
    # inside ``verify_gpg``; a real URL triggers the actual
    # ``gpg --verify`` path, which can raise
    # :class:`GpgVerificationError` and surface exit code 3.
    signature_url: str | None = None
    if verify_gpg_flag:
        signature_url = info.get("sig_url", "")

    fetch_and_install(
        url, info["sha256"], target_binary,
        signature_url=signature_url,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns a POSIX exit code."""
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    _configure_logging(args.verbose)

    try:
        lock = load_lockfile(args.lockfile)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to load caddy.lock: %s", exc)
        return EXIT_GENERIC

    try:
        if args.multi_arch:
            platforms = [
                p.strip() for p in args.multi_arch.split(",") if p.strip()
            ]
            if not platforms:
                logger.error("--multi-arch requires at least one platform")
                return EXIT_GENERIC
            for platform_key in platforms:
                _fetch_for_platform(
                    lock, platform_key, args.target_dir / platform_key,
                    args.verify_gpg,
                )
        else:
            platform_key = args.platform or detect_host_platform()
            _fetch_for_platform(
                lock, platform_key, args.target_dir, args.verify_gpg,
            )
    except IntegrityError as exc:
        logger.error("SHA-256 verification failed: %s", exc)
        return EXIT_SHA_MISMATCH
    except GpgVerificationError as exc:
        logger.error("GPG verification failed: %s", exc)
        return EXIT_GPG_FAIL
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.error("Fetch failed: %s", exc)
        return EXIT_GENERIC

    return EXIT_OK
