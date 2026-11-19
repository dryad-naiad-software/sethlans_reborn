# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The pending-setup write fsyncs the file and its directory for real.

Issue #273 companion to ``test_phase1_pending_setup.py``. The wizard
subprocess tests cannot see which fds the handler fsyncs, so here the
real ``_atomic_write`` runs in-process against a tmp filesystem with
pass-through spies on ``os.fsync`` and ``os.replace``. The spies call
the real functions, so the file really is flushed; they only record
which file or directory each fsync touched and in what order.

Contract (FR-PEND1a): fsync(temp file) → os.replace → fsync(parent
dir) on POSIX; Windows has no directory fsync.
"""

from __future__ import annotations

import json
import os
import platform
import stat
from pathlib import Path

from wizard.sethlans_wizard.handlers import pending_setup as pending_handler

IS_WINDOWS = platform.system() == "Windows"


def _install_spies(monkeypatch) -> list[tuple[str, os.stat_result | None]]:
    """Wrap ``os.fsync`` / ``os.replace``; return the shared event log."""
    events: list[tuple[str, os.stat_result | None]] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync_spy(fd):
        st = os.fstat(fd)
        kind = "fsync_dir" if stat.S_ISDIR(st.st_mode) else "fsync_file"
        events.append((kind, st))
        return real_fsync(fd)

    def replace_spy(src, dst, *args, **kwargs):
        events.append(("replace", None))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(pending_handler.os, "fsync", fsync_spy)
    monkeypatch.setattr(pending_handler.os, "replace", replace_spy)
    return events


def _body() -> bytes:
    payload = {
        "schema_version": pending_handler.PENDING_SCHEMA_VERSION,
        "topology": "manager",
        "created_at_unix": 1,
        "admin_user": {"username": "alice"},
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def test_atomic_write_fsyncs_file_then_directory(tmp_path: Path, monkeypatch):
    """The real write fsyncs the file before replace and the dir after."""
    target = tmp_path / "data" / pending_handler.PENDING_SETUP_FILENAME
    body = _body()
    events = _install_spies(monkeypatch)

    pending_handler._atomic_write(target, body)

    kinds = [kind for kind, _ in events]
    expected = ["fsync_file", "replace"]
    if not IS_WINDOWS:
        expected.append("fsync_dir")
    assert kinds == expected, kinds

    # os.replace keeps the inode, so the fsynced temp file IS the target.
    file_stat = events[0][1]
    assert os.path.samestat(file_stat, os.stat(target)), (file_stat, os.stat(target))
    if not IS_WINDOWS:
        dir_stat = events[2][1]
        assert os.path.samestat(dir_stat, os.stat(target.parent))


def test_atomic_write_bytes_identical_from_fresh_handle(tmp_path: Path):
    """After the write returns, a fresh handle reads exactly the body."""
    target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
    body = _body()

    pending_handler._atomic_write(target, body)

    with target.open("rb") as fh:
        assert fh.read() == body
    assert not target.with_name(target.name + ".tmp").exists()
    if not IS_WINDOWS:
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
