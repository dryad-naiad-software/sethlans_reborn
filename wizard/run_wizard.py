# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Entry point for the Sethlans Reborn standalone setup wizard.

Spec 1 (subphase A1) scaffold only — this entry point currently parses
its CLI flags, prints a version banner when ``--version`` is requested,
and exits cleanly. Subphases A2-A6 add the cert/IPC/probe modules, the
waitress HTTPS server, request handlers, the PyInstaller spec, and the
launcher integration.

Flags accepted (no behavior yet beyond ``--version``):
  --version       Print the wizard version and exit 0 (FR-W1).
  --no-browser    Reserved for future use; currently a no-op (FR-L11).
  --print-url     Reserved for future use; currently a no-op (FR-L11).
"""

from __future__ import annotations

import argparse
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

from shared.version import get_version  # noqa: E402  (import after sys.path tweak)

import sethlans_wizard  # noqa: F401, E402  (ensures package importable)


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
        help="Reserved (FR-L11): do not open a browser at handoff.",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        default=False,
        help="Reserved (FR-L11): print the wizard URL to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = build_parser()
    parser.parse_args(argv)
    # A1 is scaffold-only: nothing else to do once flags parse cleanly.
    print(f"sethlans-wizard {get_version()} (scaffold)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
