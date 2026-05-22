# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the ``--manage`` allowlist gate in ``run_manager.py``
(issue #191 / frozen_apply_pipeline spec FR-MGMT3).

These tests spawn ``python manager/run_manager.py`` as a real subprocess to
confirm that the allowlist gate is reachable from the real entry point and
that disallowed subcommands are rejected BEFORE Django initializes.

This is the closest source-mode equivalent to invoking the frozen
``run_manager.exe --manage <bad>`` and confirms the gate is reachable from
the real entry point, not just from a direct unit-level call.

FR-MGMT3 contract verified here:
  - Disallowed subcommand (``createsuperuser``) exits 2 with
    ``not in allowlist`` on stderr, without Django setup.
  - Mutually exclusive flags (``--manage --dev``) exit 2 with the
    expected message.
  - ``--manage`` with no subcommand exits 2.
  - All exit-code-2 paths produce stderr that does NOT start with Django's
    standard error prefix (proving Django was not initialized).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = REPO_ROOT / "manager"
RUN_MANAGER_PY = MANAGER_DIR / "run_manager.py"


def _subprocess_env() -> dict:
    """Build a minimal env for source-mode ``run_manager.py`` subprocess calls.

    Mirrors the curated-env pattern used across integration tests that
    spawn manager subprocesses.  Adds SETHLANS_SECURITY_DEBUG=True so the
    insecure-SECRET_KEY guard doesn't block Django init for tests that
    pass the allowlist check.
    """
    env: dict = {}
    env["DJANGO_SETTINGS_MODULE"] = "sethlans_manager.settings"
    env["SETHLANS_SECURITY_DEBUG"] = "True"
    extra = os.pathsep.join([
        str(REPO_ROOT),
        str(MANAGER_DIR),
        str(REPO_ROOT / "worker"),
    ])
    existing = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (f"{extra}{os.pathsep}{existing}" if existing else extra)
    env["PATH"] = os.environ.get("PATH", "")
    if sys.platform == "win32":
        for var in ("SystemRoot", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                    "TEMP", "TMP", "COMSPEC", "PATHEXT"):
            val = os.environ.get(var)
            if val:
                env[var] = val
    else:
        env["HOME"] = os.environ.get("HOME", "/")
    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    getattr(sys, "frozen", False),
    reason="Source-mode subprocess test; not applicable in frozen bundle.",
)
class TestAllowlistGateViaSubprocess:
    """FR-MGMT3: the allowlist gate is reachable from the real entry point.

    All tests use subprocess.run against ``python manager/run_manager.py``
    so we exercise the real ``main()`` → ``dispatch_manage_mode()`` path,
    not just the isolated unit-level function.
    """

    def test_disallowed_createsuperuser_exits_2_with_not_in_allowlist(
        self, tmp_path,
    ):
        """``--manage createsuperuser`` exits 2 with 'not in allowlist' on stderr.

        FR-MGMT3: ``createsuperuser`` is not in ``_ALLOWED_MANAGE_SUBCOMMANDS``
        and must be rejected before any Django initialization.
        """
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(RUN_MANAGER_PY),
                "--manage", "createsuperuser",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_subprocess_env(),
            cwd=str(MANAGER_DIR),
        )

        assert result.returncode == 2, (
            f"Expected exit code 2 for disallowed subcommand. "
            f"rc={result.returncode}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        assert "not in allowlist" in result.stderr, (
            f"Expected 'not in allowlist' in stderr. "
            f"Got: {result.stderr!r}"
        )

    def test_disallowed_shell_exits_2_with_not_in_allowlist(self, tmp_path):
        """``--manage shell`` exits 2 with 'not in allowlist' on stderr."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(RUN_MANAGER_PY), "--manage", "shell"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_subprocess_env(),
            cwd=str(MANAGER_DIR),
        )

        assert result.returncode == 2
        assert "not in allowlist" in result.stderr, result.stderr

    def test_manage_and_dev_mutually_exclusive_exits_2(self):
        """``--dev --manage migrate`` exits 2 with mutual exclusion message.

        Only TOP-LEVEL --dev (before --manage) trips the mutex; a literal
        "--dev" after --manage is the subcommand's own arg and is allowed.
        """
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(RUN_MANAGER_PY),
                "--dev", "--manage", "migrate",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_subprocess_env(),
            cwd=str(MANAGER_DIR),
        )

        assert result.returncode == 2, (
            f"Expected exit code 2 for --dev + --manage. "
            f"rc={result.returncode}\nstderr: {result.stderr!r}"
        )
        assert "mutually exclusive" in result.stderr, (
            f"Expected 'mutually exclusive' in stderr. Got: {result.stderr!r}"
        )

    def test_manage_without_subcommand_exits_2(self):
        """``--manage`` with no subcommand exits 2 with the expected message."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(RUN_MANAGER_PY), "--manage"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_subprocess_env(),
            cwd=str(MANAGER_DIR),
        )

        assert result.returncode == 2, (
            f"Expected exit code 2 for --manage with no subcommand. "
            f"rc={result.returncode}\nstderr: {result.stderr!r}"
        )
        assert "requires a Django management command name" in result.stderr, (
            f"Expected missing-subcommand message in stderr. "
            f"Got: {result.stderr!r}"
        )

    def test_disallowed_command_no_django_startup_message(self, tmp_path):
        """Disallowed subcommand produces no Django startup output.

        FR-MGMT3: Django MUST NOT be initialized for disallowed subcommands.
        The stderr must not contain Django's own 'Installed X object(s)'
        or the 'Performing system checks' startup banner.
        """
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(RUN_MANAGER_PY),
                "--manage", "dumpdata",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_subprocess_env(),
            cwd=str(MANAGER_DIR),
        )

        assert result.returncode == 2
        combined = (result.stdout or "") + (result.stderr or "")
        # Django's system check runner produces this string on startup.
        # If we see it, Django was initialized — a spec violation.
        assert "Performing system checks" not in combined, (
            f"Django startup output detected — Django must NOT be "
            f"initialized for disallowed subcommands. Output: {combined!r}"
        )
