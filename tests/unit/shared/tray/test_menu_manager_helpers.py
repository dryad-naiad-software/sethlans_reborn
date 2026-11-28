# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/menu_manager_helpers.py``: View Logs opens the OS viewer (FR-LN-4)."""

from __future__ import annotations

import pytest

from shared.tray import menu_manager_helpers as helpers


@pytest.mark.parametrize(("platform", "call"), [
    ("win32", "startfile"), ("darwin", "open"), ("linux", "xdg-open"),
])
def test_open_path_uses_the_platform_viewer(tmp_path, mocker, monkeypatch, platform, call):
    """View Logs works on every OS: os.startfile, ``open`` or ``xdg-open``."""
    monkeypatch.setattr(helpers.sys, "platform", platform)
    startfile = mocker.patch.object(helpers.os, "startfile", create=True)
    run = mocker.patch.object(helpers.subprocess, "run")
    assert helpers.open_path(tmp_path) is True
    if call == "startfile":
        startfile.assert_called_once_with(str(tmp_path))
    else:
        run.assert_called_once_with([call, str(tmp_path)], check=False)


def test_open_path_missing_or_failing_never_raises(tmp_path, mocker, monkeypatch):
    """A click handler must not raise: a missing folder or a viewer that fails gives False."""
    assert helpers.open_path(tmp_path / "missing") is False
    monkeypatch.setattr(helpers.sys, "platform", "linux")
    mocker.patch.object(helpers.subprocess, "run", side_effect=FileNotFoundError("no xdg-open"))
    assert helpers.open_path(tmp_path) is False
