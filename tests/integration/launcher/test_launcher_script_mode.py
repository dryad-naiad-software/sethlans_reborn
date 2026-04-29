# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""From-source script-mode invocation regression tests for the launcher.

Issue #177: ``python launcher/run_launcher.py`` failed with
``ModuleNotFoundError: No module named 'launcher'`` because the entry
point lacked the sys.path bootstrap that ``wizard/run_wizard.py`` and
``worker/run_worker.py`` already had. PyInstaller-frozen mode was
unaffected (the bootloader fixes sys.path); only from-source
invocations (the ``tools/dev_launcher.py`` harness, ad-hoc
``python launcher/run_launcher.py`` debugging) tripped it.

The tests below spawn the launcher as a real subprocess from project
root and assert the bootstrap is in place.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER_ENTRY = PROJECT_ROOT / "launcher" / "run_launcher.py"


@pytest.mark.skipif(
    getattr(sys, "frozen", False),
    reason="Frozen mode: PyInstaller bootloader handles sys.path; "
           "the from-source bootstrap is not exercised.",
)
def test_launcher_version_runs_without_module_not_found(tmp_path):
    """Issue #177: ``python launcher/run_launcher.py --version`` must boot.

    Asserts the bootstrap inserts the project root into ``sys.path``
    before the absolute ``from launcher.* import ...`` lines run, so
    the entry point exits 0 with the version banner instead of
    ``ModuleNotFoundError: No module named 'launcher'``.
    """
    result = subprocess.run(
        [sys.executable, str(LAUNCHER_ENTRY), "--version"],
        cwd=str(tmp_path),  # cwd != project_root keeps sys.path[0]=launcher/
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ModuleNotFoundError" not in combined, combined
    assert "No module named 'launcher'" not in combined, combined
    assert result.returncode == 0, (
        f"--version exited rc={result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    # ``argparse`` writes the version banner to stdout (Python 3.4+).
    assert "Sethlans " in result.stdout, result.stdout


@pytest.mark.skipif(
    getattr(sys, "frozen", False),
    reason="Frozen mode: PyInstaller bootloader handles sys.path.",
)
def test_launcher_imports_cleanly_from_arbitrary_cwd(tmp_path):
    """Bootstrap must work regardless of the caller's working directory.

    ``sys.path[0]`` is set to the script's parent directory
    (``launcher/``) even when invoked from an unrelated cwd; the
    bootstrap must explicitly add the project root so
    ``from launcher import ...`` resolves.
    """
    # ``-c`` form: import-only smoke. Avoids running ``main()`` (which
    # would block on the splash / orchestration paths) while still
    # exercising the same module-load sequence as
    # ``python launcher/run_launcher.py``.
    bootstrap_probe = (
        "import runpy, sys; "
        f"sys.argv = [{str(LAUNCHER_ENTRY)!r}, '--version']; "
        f"runpy.run_path({str(LAUNCHER_ENTRY)!r}, run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", bootstrap_probe],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ModuleNotFoundError" not in combined, combined
    # ``--version`` triggers SystemExit(0) inside argparse; runpy
    # propagates it as rc=0.
    assert result.returncode == 0, combined
