# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Smoke tests for the Spec 1 / A1 wizard package scaffold.

These tests verify only that the new ``wizard/`` package and its
entry-point script load cleanly and expose the CLI flags required by
FR-W1 / FR-L11. Behavior for those flags lands in subphases A2-A6.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WIZARD_DIR = PROJECT_ROOT / "wizard"
RUN_WIZARD = WIZARD_DIR / "run_wizard.py"


@pytest.fixture(autouse=True)
def _ensure_wizard_on_path():
    """Make ``wizard/`` importable for the duration of the test.

    The repo root is already on ``sys.path`` via conftest, but the
    ``wizard/`` directory is not — ``run_wizard`` lives there as a
    top-level script (mirrors ``worker/run_worker.py``).
    """
    wizard_dir = str(WIZARD_DIR)
    added = False
    if wizard_dir not in sys.path:
        sys.path.insert(0, wizard_dir)
        added = True
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(wizard_dir)
            except ValueError:
                pass


def test_sethlans_wizard_package_imports():
    """The wizard Python package imports cleanly."""
    module = importlib.import_module("wizard.sethlans_wizard")
    assert module is not None
    assert hasattr(module, "__name__")
    assert module.__name__ == "wizard.sethlans_wizard"


def test_run_wizard_module_imports():
    """The ``run_wizard`` entry-point module imports cleanly."""
    # Import as a top-level module (matches how PyInstaller will load it).
    module = importlib.import_module("run_wizard")
    assert module is not None
    assert hasattr(module, "main")
    assert hasattr(module, "build_parser")


def test_run_wizard_argparse_flags_defined():
    """``run_wizard`` declares the FR-W1 / FR-L11 CLI flags."""
    module = importlib.import_module("run_wizard")
    parser = module.build_parser()
    flags = {action.option_strings[0] for action in parser._actions if action.option_strings}
    assert "--version" in flags
    assert "--no-browser" in flags
    assert "--print-url" in flags


def test_run_wizard_version_flag_exits_zero():
    """``python wizard/run_wizard.py --version`` prints and exits 0."""
    result = subprocess.run(
        [sys.executable, str(RUN_WIZARD), "--version"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=15,
    )
    assert result.returncode == 0, (
        f"--version exit code was {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "sethlans-wizard" in combined
