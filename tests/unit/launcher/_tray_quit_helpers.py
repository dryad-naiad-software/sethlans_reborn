# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared helpers for the issue #163 tray-quit-during-wizard tests.

This module is intentionally a leading-underscore helper, not a test
file. ``_tray_quit_helpers.py`` is split out from the test files so
each individual test file stays under the project's 300-line ceiling
(see ``CLAUDE.md`` and the issue #163 spec's AC-FileSize).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path

from launcher import orchestration


_TEST_TRAY_SECRET = "x" * 48
_TEST_TRAY_PID = 99887

SECRET = b"a" * 32


class FakeProc:
    """Minimal ``subprocess.Popen`` stand-in shared across the tests."""

    def __init__(self, returncode=None, pid=12345):
        self.returncode = returncode
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_called = True
        if self.returncode is None:
            self.returncode = 0

    def kill(self):
        self.kill_called = True
        if self.returncode is None:
            self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode or 0


class FakeBroadcasterSupervisor:
    """Stub used to keep ``start_ipc_poll_thread`` happy in tests."""

    instances: list = []

    def __init__(self):
        self.start_calls = []
        self.stop_called = False
        FakeBroadcasterSupervisor.instances.append(self)

    def start_from_params(self, params):
        self.start_calls.append(params)

    def stop(self, join_timeout=5.0):
        self.stop_called = True


def write_quit_marker(data_dir: Path, secret: str, pid: int) -> None:
    """Drop a tray ``.quit_requested`` marker into ``data_dir``."""
    payload = {
        "secret": secret,
        "pid": pid,
        "target": "all",
        "requested_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    (data_dir / ".quit_requested").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def stage_port_file(tmp_path, port=8101):
    """Pre-write the wizard port file used by run_wizard_mode tests."""
    (tmp_path / "wizard").mkdir(exist_ok=True)
    (tmp_path / "wizard" / "port").write_text(str(port))


def args_ns():
    return argparse.Namespace(no_browser=True, print_url=True)


def write_topology(data_dir, topo):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topo}), encoding="utf-8",
    )


def common_normal_mode_mocks(mocker):
    mocker.patch.object(orchestration, "remove_setup_section")
    mocker.patch.object(orchestration, "start_caddy_supervisor")
    mocker.patch.object(
        orchestration, "_all_live_exited", return_value=True,
    )
