# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared.settings_file_io.read_owned_file``: the tray reads its state and the setup
token only from a regular file this user owns (6c security review L1, FR-SEC-1)."""

from __future__ import annotations

import os

import pytest

from shared import settings_file_io


def test_reads_a_regular_owned_file_capped(tmp_path):
    """The happy path: at most ``limit + 1`` bytes, so the caller sees an oversize."""
    path = tmp_path / "state.json"
    path.write_bytes(b"x" * 100)
    assert settings_file_io.read_owned_file(path, 10) == b"x" * 11


def test_symlink_is_refused_not_followed(tmp_path):
    """L1: a link planted in the setup folder is never followed."""
    target = tmp_path / "elsewhere"
    target.write_bytes(b"{}")
    link = tmp_path / "launcher_state.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    with pytest.raises(OSError):
        settings_file_io.read_owned_file(link, 4096)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this OS")
def test_fifo_is_refused_without_blocking(tmp_path):
    """L1: a FIFO in place of the file neither blocks the tray's GUI thread nor is read."""
    fifo = tmp_path / "setup_token"
    os.mkfifo(fifo)
    with pytest.raises(OSError):
        settings_file_io.read_owned_file(fifo, 1024)


def test_file_of_another_user_is_refused(tmp_path, mocker):
    """L1: a file another user owns is not trusted."""
    path = tmp_path / "setup_token"
    path.write_bytes(b"token")
    owned = mocker.patch.object(settings_file_io.owner_only_acl, "is_owned_by_current_user", return_value=False)
    with pytest.raises(OSError, match="not owned"):
        settings_file_io.read_owned_file(path, 1024)
    owned.assert_called_once_with(path)
