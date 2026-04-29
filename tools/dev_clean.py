# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Wipe ``temp/dev-data/`` (or a single component subdir).

Companion to the :mod:`tools.dev_wizard` / :mod:`tools.dev_manager` /
:mod:`tools.dev_launcher` / :mod:`tools.dev_worker` harness scripts.
After a dev iteration leaves the data dir in a weird state — half-set
sentinels, stale TLS certs, an enrolled worker pinned to the wrong
manager — run this to start fresh.

CLI:
    python tools/dev_clean.py [--component wizard|manager|launcher|worker|all]
                              [--yes] [--dry-run]

The ``--yes`` flag bypasses the confirmation prompt; useful for CI or
the test harness. ``--dry-run`` lists what would be deleted without
touching the filesystem.

Defense: refuses to delete anything outside the resolved data root —
so a typo like ``--component=../launcher`` aborts cleanly instead of
nuking the project tree.

``.env`` overrides:
    SETHLANS_DEV_DATA_ROOT — relocate dev data root.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools import _dev_common as common  # noqa: E402

VALID_COMPONENTS = ("wizard", "manager", "launcher", "worker", "all")


def _collect_targets(
    component: str, data_root: Path,
) -> list[Path]:
    """Return the absolute paths the script intends to remove.

    Each path is asserted to live within *data_root* before being
    appended; an escape attempt raises ``SystemExit`` so the caller
    cannot accidentally invoke ``shutil.rmtree`` on the project root.
    """
    root = data_root.resolve()
    targets: list[Path] = []

    if component == "all":
        if root.exists():
            targets.append(root)
        return targets

    sub = (root / component).resolve()
    try:
        sub.relative_to(root)
    except ValueError:
        raise SystemExit(
            f"refusing to delete path outside data root: {sub}",
        )
    if sub.exists():
        targets.append(sub)
    return targets


def _confirm(targets: list[Path]) -> bool:
    """Prompt the operator; return True on a 'y' / 'yes' answer."""
    if not targets:
        sys.stdout.write("Nothing to delete.\n")
        return False
    sys.stdout.write("\nWill recursively delete:\n")
    for path in targets:
        sys.stdout.write(f"  - {path}\n")
    sys.stdout.write("\nProceed? [y/N] ")
    sys.stdout.flush()
    try:
        answer = input().strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _delete(targets: list[Path], dry_run: bool) -> int:
    """Recursively delete *targets*; return process rc (0 ok, 1 partial)."""
    rc = 0
    for path in targets:
        if dry_run:
            sys.stdout.write(f"[dry-run] would rmtree {path}\n")
            continue
        try:
            shutil.rmtree(str(path))
            sys.stdout.write(f"deleted {path}\n")
        except FileNotFoundError:
            pass
        except OSError as exc:
            sys.stderr.write(f"error deleting {path}: {exc}\n")
            rc = 1
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_clean",
        description="Wipe Sethlans dev data dir (temp/dev-data/).",
    )
    parser.add_argument(
        "--component",
        choices=VALID_COMPONENTS,
        default="all",
        help="Component subdir to wipe (default: all).",
    )
    parser.add_argument(
        "--yes", action="store_true", default=False,
        help="Skip the confirmation prompt.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="List targets without deleting.",
    )
    args = parser.parse_args(argv)

    common.load_dotenv()
    data_root = common.resolve_data_root()

    targets = _collect_targets(args.component, data_root)
    if not targets:
        sys.stdout.write(
            f"No data to clean under {data_root.resolve()} "
            f"(component={args.component}).\n",
        )
        return 0

    if not args.yes and not args.dry_run:
        if not _confirm(targets):
            sys.stdout.write("Aborted.\n")
            return 1
    elif args.dry_run:
        sys.stdout.write("\n[dry-run] targets:\n")
        for path in targets:
            sys.stdout.write(f"  - {path}\n")

    return _delete(targets, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
