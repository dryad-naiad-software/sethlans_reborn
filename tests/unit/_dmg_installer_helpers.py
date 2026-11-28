# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared paths and patterns for the macOS DMG build-script tests.

Used by ``test_dmg_installer.py`` (bundle paths, executable rename,
quarantine strip) and ``test_dmg_installer_codesign.py`` (inside-out
ad-hoc signing).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DMG_SCRIPT = REPO_ROOT / "packaging" / "macos" / "build_dmg.sh"
PLIST_TEMPLATE = REPO_ROOT / "packaging" / "macos" / "Info.plist.template"
LAUNCHER_SPEC = REPO_ROOT / "packaging" / "pyinstaller" / "launcher.spec"

# The OUTER-bundle re-sign, WITHOUT --deep. Apple deprecated
# `codesign --deep` for signing in macOS 11 and notarytool will reject
# `--deep`-signed artifacts (issue #149). The script uses inside-out
# signing instead: nested Mach-O first, then component executables,
# then the outer bundle last, each without `--deep`.
OUTER_RESIGN_PATTERN = re.compile(
    r'codesign\s+--force\s+--sign\s+-\s+'
    r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
)

# The run_launcher -> sethlans rename in the staged bundle (issue #86).
# Tolerates whitespace and line continuations.
RENAME_PATTERN = re.compile(
    r'mv\s+\\?\s*'
    r'"\$\{MACOS_DIR\}/run_launcher"\s+\\?\s*'
    r'"\$\{MACOS_DIR\}/sethlans"',
)


def read_required(path: Path, label: str) -> str:
    """Return the text of ``path``, failing the test if it is missing."""
    assert path.is_file(), f"Expected {label} at {path}"
    return path.read_text(encoding="utf-8")
