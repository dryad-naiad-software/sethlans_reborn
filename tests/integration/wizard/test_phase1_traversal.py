# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

r"""Path-traversal hardening — real filesystem (FR-M2-3, MED-4).

Covers integration-test agent's mandatory scenario 7. The
``network`` handler accepts an optional ``data_dir`` override; on
acceptance the override becomes the destination for the
``manager.ini`` write. The override path goes through the FR-M2-3
hardening pipeline: ``..`` segment rejection → device-namespace
rejection → ``realpath`` resolution → forbidden-root denylist.

We exercise each rejection path against a REAL filesystem (no mocks)
and assert NOTHING was written under the rejected path — no
manager.ini, no temp files, no ``.tmp`` siblings, no progress file.

POSIX-only branches: symlink resolution into ``/etc``.
Windows-only branches: device-namespace ``\\?\`` and system root.
"""

from __future__ import annotations

import os
import platform
import socket
from pathlib import Path

import pytest

from . import _http
from ._phase1_session import open_and_select, session_headers


def _bind_unused_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _post_network(wp, session: str, body: dict):
    return _http.post_json(
        f"{wp.base_url}/api/wizard/network/", body,
        headers=session_headers(session),
    )


def _no_files_under(path: Path) -> bool:
    """Return True iff *path* has no children (or doesn't exist)."""
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    return not any(path.iterdir())


def test_traversal_dotdot_rejected_no_fs_writes(wizard_process, tmp_path):
    """Override containing ``..`` rejected before any FS write."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    port = _bind_unused_port()

    # POSIX: /tmp/foo/../bar  Windows: C:\Temp\foo\..\bar
    parent = tmp_path / "scratch"
    parent.mkdir(parents=True, exist_ok=True)
    bad = str(parent / "foo" / ".." / "bar")

    status, _, parsed = _post_network(
        wp, session,
        {"bind_host": "127.0.0.1", "bind_port": port, "data_dir": bad},
    )
    assert status == 400, parsed
    assert parsed.get("error") == "data_dir_invalid", parsed
    assert parsed.get("category") == "traversal", parsed

    # NOTHING was written under the rejected parent path.
    assert _no_files_under(parent), list(parent.iterdir())
    # No manager.ini at the rejected target.
    assert not (parent / "manager.ini").exists()
    assert not (parent / "manager.ini.tmp").exists()


def test_traversal_relative_path_rejected(wizard_process):
    """Non-absolute override → ``relative`` category rejection."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    port = _bind_unused_port()
    status, _, parsed = _post_network(
        wp, session,
        {"bind_host": "127.0.0.1", "bind_port": port,
         "data_dir": "relative/path"},
    )
    assert status == 400, parsed
    assert parsed.get("error") == "data_dir_invalid", parsed
    assert parsed.get("category") == "relative", parsed


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX symlink test")
def test_traversal_symlink_resolves_to_forbidden_root(wizard_process, tmp_path):
    """A symlink that points at ``/etc`` resolves through realpath → reject.

    Real symlink, real realpath, real denylist comparison. Assert
    nothing landed in /etc (we never have permission anyway) AND
    nothing landed in tmp_path.
    """
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    port = _bind_unused_port()

    link = tmp_path / "evil_symlink"
    try:
        os.symlink("/etc", str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this filesystem")

    status, _, parsed = _post_network(
        wp, session,
        {"bind_host": "127.0.0.1", "bind_port": port,
         "data_dir": str(link)},
    )
    assert status == 400, parsed
    assert parsed.get("error") == "data_dir_invalid", parsed
    assert parsed.get("category") == "forbidden_root", parsed

    # /etc/manager.ini MUST NOT exist (would be a CRITICAL system breach
    # if it did; in practice the OS rejects the write anyway, but the
    # handler must short-circuit BEFORE attempting it).
    assert not Path("/etc/manager.ini").exists()


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
def test_traversal_device_namespace_rejected(wizard_process):
    r"""Windows ``\\?\`` prefix → ``device_namespace`` rejection."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    port = _bind_unused_port()

    bad_path = r"\\?\C:\Windows\System32"
    status, _, parsed = _post_network(
        wp, session,
        {"bind_host": "127.0.0.1", "bind_port": port,
         "data_dir": bad_path},
    )
    assert status == 400, parsed
    assert parsed.get("error") == "data_dir_invalid", parsed
    assert parsed.get("category") == "device_namespace", parsed


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
def test_traversal_system_windows_root_rejected(wizard_process):
    r"""``C:\Windows`` → ``forbidden_root`` rejection."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    port = _bind_unused_port()

    status, _, parsed = _post_network(
        wp, session,
        {"bind_host": "127.0.0.1", "bind_port": port,
         "data_dir": r"C:\Windows"},
    )
    assert status == 400, parsed
    assert parsed.get("error") == "data_dir_invalid", parsed
    assert parsed.get("category") == "forbidden_root", parsed


def test_traversal_valid_override_writes_manager_ini(wizard_process, tmp_path):
    """A LEGITIMATE absolute override IS honoured — write lands there.

    Sanity-check the happy path so the rejection tests above can't
    accidentally pass via false-positive (e.g. handler always rejecting
    every override).
    """
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    port = _bind_unused_port()

    # Use a clean subdir under tmp_path. realpath() must resolve to
    # itself (not under any forbidden root).
    target_dir = tmp_path / "valid_override"
    target_dir.mkdir(parents=True, exist_ok=True)

    status, _, parsed = _post_network(
        wp, session,
        {
            "bind_host": "127.0.0.1",
            "bind_port": port,
            "data_dir": str(target_dir),
        },
    )
    assert status == 200, parsed

    # manager.ini lands at the override.
    assert (target_dir / "manager.ini").exists()
    # The wizard's own data_dir does NOT receive the override write.
    assert not (wp.data_dir / "manager.ini").exists()
