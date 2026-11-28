# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for :mod:`launcher.single_instance` (FR-14b; owner Q4; security review M1).

They use the real OS lock (``msvcrt.locking`` or ``flock``) on a file in a
temporary folder, held for a moment.
"""

from __future__ import annotations

import os
import subprocess
import sys

import psutil
import pytest

from launcher import single_instance

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_a_second_lock_on_a_folder_fails_and_another_folder_is_free(tmp_path):
    """Owner Q4: "running" is per settings folder; a released lock can be taken again."""
    first = single_instance.acquire_single_instance_lock(tmp_path / "a")
    try:
        assert first is not None
        assert single_instance.acquire_single_instance_lock(tmp_path / "a") is None
        other = single_instance.acquire_single_instance_lock(tmp_path / "b")
        assert other is not None
        single_instance.release_lock(other)
    finally:
        single_instance.release_lock(first)
    again = single_instance.acquire_single_instance_lock(tmp_path / "a")
    assert again is not None
    single_instance.release_lock(again)


def _short_name(path) -> str:
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if length else ""


def _link(target, link) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.mark.parametrize("spelling", ["link", "short-name"])
def test_two_spellings_of_one_folder_share_the_lock(tmp_path, spelling):
    """Security review M1: a junction, symlink or 8.3 short name of the settings folder
    reaches the same lock file, so it cannot start a second launcher there."""
    folder = tmp_path / "a long settings folder name"
    folder.mkdir()
    if spelling == "short-name":
        other = _short_name(folder) if os.name == "nt" else ""
        if not other or os.path.normcase(other) == os.path.normcase(str(folder)):
            pytest.skip("no different 8.3 short name on this file system")
    else:
        other = tmp_path / "link"
        try:
            _link(folder, other)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot make a link here: {exc}")
    first = single_instance.acquire_single_instance_lock(folder)
    try:
        assert first is not None
        assert single_instance.acquire_single_instance_lock(other) is None
    finally:
        single_instance.release_lock(first)


@pytest.mark.timeout(60)
def test_the_lock_goes_with_the_process(tmp_path):
    """FR-14b: a launcher that dies (no release) leaves the lock free for the next one."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from launcher import single_instance as s; "
            "lock = s.acquire_single_instance_lock(sys.argv[2]); print('locked' if lock else 'busy', flush=True); "
            "sys.stdin.readline()")
    child = subprocess.Popen([sys.executable, "-c", code, PROJECT_ROOT, str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        assert single_instance.acquire_single_instance_lock(tmp_path) is None
    finally:
        # A venv's python.exe on Windows runs the interpreter as its own child.
        for proc in [*psutil.Process(child.pid).children(recursive=True), child]:
            proc.kill()
            proc.wait(timeout=30)
        child.stdin.close()
        child.stdout.close()
    lock = single_instance.acquire_single_instance_lock(tmp_path)
    assert lock is not None
    single_instance.release_lock(lock)
