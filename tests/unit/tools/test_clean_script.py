# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the `clean` command in tools/sethlans.ps1 / tools/sethlans.sh.

These tests cover two bug fixes:
- #45 — clean failed to delete manager/db.sqlite3 when a manager process
  outside the script's PID tracking held the file open.
- #46 — clean targeted $PROJECT_ROOT/media but Django stores uploads at
  $MANAGER_DIR/media, so the real media dir was never cleaned.

Test 4 reproduces #45's locked-file scenario faithfully on Windows. On POSIX,
unlink succeeds even when a process holds the file open, so test 4 only
validates the kill-then-delete sequence runs cleanly — but #45 is fundamentally
a Windows-class bug.

The fake_project fixture and run_clean helper live in conftest.py.
"""

import subprocess
import sys
import time

from .conftest import run_clean


# Test 1: happy path — no holder process
def test_clean_removes_db_when_no_process_running(fake_project):
    """With no holding process, the clean script must delete db.sqlite3."""
    db = fake_project / "manager" / "db.sqlite3"
    assert db.exists(), "precondition: db.sqlite3 should exist"

    result = run_clean(fake_project)

    assert result.returncode == 0, (
        f"clean exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert not db.exists(), "db.sqlite3 should be deleted by clean"


# Test 2: bug #46 — manager/media is targeted, not project_root/media
def test_clean_removes_manager_media_directory(fake_project):
    """clean must delete manager/media (where Django stores uploads)."""
    media = fake_project / "manager" / "media"
    assert (media / "assets" / "test" / "foo.blend").exists()

    result = run_clean(fake_project)

    assert result.returncode == 0, (
        f"clean exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert not media.exists(), "manager/media should be deleted by clean"


# Test 3: bug #46 regression lock — project_root/media is NOT touched
def test_clean_does_not_touch_root_media_directory(fake_project):
    """
    The OLD wrong target was $PROJECT_ROOT/media. The fix must NOT delete
    a stray media directory at the project root — only the one under manager/.
    """
    stray = fake_project / "media"
    stray.mkdir()
    sentinel = stray / "should_not_be_deleted.txt"
    sentinel.write_text("survive")

    result = run_clean(fake_project)

    assert result.returncode == 0, (
        f"clean exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert sentinel.exists(), (
        "PROJECT_ROOT/media should NOT be deleted — clean must only touch manager/media."
    )


# Test 4: bug #45 — clean kills a manager process holding db open
# Fake manage.py: opens db.sqlite3 in r+b (acquiring a Windows file lock) and
# sleeps. Its argv is `manage.py runserver <db_path>`, which satisfies the
# clean script's matcher (`*manage.py*runserver*` plus the project root path
# appearing via the db_path argument).
FAKE_MANAGE_PY = """\
import sys
import time

with open(sys.argv[2], 'r+b') as f:
    time.sleep(120)
"""


def _wait_for_exit(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    """Poll until proc exits or timeout. Returns True if it exited."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return False


def test_clean_kills_process_holding_db_open(fake_project):
    """
    Bug #45 load-bearing test.

    Spawn a python subprocess whose command line contains BOTH the fake
    project root AND `manage.py runserver`, and which holds db.sqlite3
    open. The clean script must detect it, kill it, and successfully
    delete the database file.
    """
    db_path = fake_project / "manager" / "db.sqlite3"
    fake_manage = fake_project / "manager" / "manage.py"
    fake_manage.write_text(FAKE_MANAGE_PY)

    proc = subprocess.Popen(
        [sys.executable, str(fake_manage), "runserver", str(db_path)],
        cwd=str(fake_project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        time.sleep(1.0)
        assert proc.poll() is None, "holder process should still be alive"

        result = run_clean(fake_project)

        assert result.returncode == 0, (
            f"clean exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        assert not db_path.exists(), (
            "db.sqlite3 should be deleted by clean even when a manager "
            "process is holding it open"
        )

        exited = _wait_for_exit(proc, timeout=5.0)
        assert exited, "clean should have killed the holder process"
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


# Test 5: bug #45 safety lock — unrelated python procs are NOT killed
def test_clean_does_not_kill_unrelated_python_process(fake_project, tmp_path_factory):
    """
    Critical safety constraint: the matcher MUST require both the project
    root AND a known entrypoint substring. A python process living entirely
    outside the fake project must survive. This locks in protection against
    killing Claude Code MCP servers, other venvs, etc.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        cwd=str(tmp_path_factory.mktemp("unrelated")),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        time.sleep(0.5)
        assert proc.poll() is None, "unrelated process should be alive"

        result = run_clean(fake_project)

        assert result.returncode == 0, (
            f"clean exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

        time.sleep(0.5)
        assert proc.poll() is None, (
            "clean must NOT kill python processes outside the project root"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
