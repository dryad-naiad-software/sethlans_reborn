# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The tray's markers (``shared.tray.ipc``) are what the launcher accepts
(``launcher.tray_ipc``): action, target, secret, PID and ``requested_at``
(FR-RS-1, chunk 6c; the reset envelope 6a/6b validate)."""

from __future__ import annotations

import datetime as _dt
import os

from launcher import tray_ipc
from shared.tray import ipc

SECRET = "tray-contract-secret-xxxxxxxxxxxxxxxxxxxxxxxxx"


def test_launcher_accepts_each_tray_marker(tmp_path, monkeypatch):
    """Quit, Reset and Restart, as the tray writes them, reach the running loop."""
    monkeypatch.setenv(ipc.ENV_SECRET, SECRET)
    not_before = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=1)
    ipc.request_quit(tmp_path, target="manager")
    ipc.request_reset(tmp_path)
    ipc.request_restart(tmp_path)
    assert tray_ipc.consume_all_markers(tmp_path, SECRET, os.getpid(), not_before) == ("manager", "all", "manager")
    assert list(tmp_path.iterdir()) == []
