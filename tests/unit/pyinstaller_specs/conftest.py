# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared paths + fixtures for the PyInstaller-spec regression tests.

The split across ``test_tray.py``, ``test_windows_resources.py``, and
``test_launcher_and_version_file.py`` keeps each module under the
project's 300-line file-size cap (the predecessor monolithic
``test_pyinstaller_specs.py`` was 580 lines). Constants and
fixture definitions live here so the three test modules don't drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# These tests only read the spec files and requirements-build.txt as
# text, but the whole PyInstaller-specs concern is irrelevant on a test
# environment that doesn't install PyInstaller (build deps live in
# requirements-build.txt, not the unit-test install set). Skip the
# collection on CI runners that omit PyInstaller to keep the test env
# lean. Using ``collect_ignore_glob`` so the skip applies to every
# test module under this subpackage at collection time.
try:  # pragma: no cover - the fast happy path on dev / CI build runners
    import PyInstaller  # noqa: F401
    _PYINSTALLER_AVAILABLE = True
except ImportError:  # pragma: no cover - test env without PyInstaller
    _PYINSTALLER_AVAILABLE = False

if not _PYINSTALLER_AVAILABLE:
    collect_ignore_glob = ["test_*.py"]


REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_REQS = REPO_ROOT / "requirements-build.txt"
SPEC_DIR = REPO_ROOT / "packaging" / "pyinstaller"
TRAY_SPEC = SPEC_DIR / "tray_helper.spec"
LAUNCHER_SPEC = SPEC_DIR / "launcher.spec"
ICON_WIN = REPO_ROOT / "packaging" / "windows" / "sethlans.ico"

ALL_SPECS = {
    "launcher": SPEC_DIR / "launcher.spec",
    "manager": SPEC_DIR / "manager.spec",
    "worker": SPEC_DIR / "worker.spec",
    "tray_helper": TRAY_SPEC,
}


@pytest.fixture(scope="module")
def build_reqs_text() -> str:
    return BUILD_REQS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tray_spec_text() -> str:
    return TRAY_SPEC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def launcher_spec_text() -> str:
    return LAUNCHER_SPEC.read_text(encoding="utf-8")
