# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fakes for the launcher loop's unit tests: a child process and a context."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from launcher import supervision
from launcher.broadcaster_supervisor import BROADCASTER_PARAMS_FILENAME

from launcher.loop_context import LoopContext
from tests.unit.launcher._marker_helpers import SECRET, TRAY_PID


class FakeProc:
    """A ``Popen`` stand-in: running until :meth:`end` (or terminate/kill when ``obeys``)."""

    def __init__(self, pid=1000, code=None, obeys=True):
        self.pid = pid
        self.returncode = code
        self.obeys = obeys
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def end(self, code):
        self.returncode = code

    def terminate(self):
        self.terminated = True
        if self.obeys:
            self.returncode = 1

    def kill(self):
        if self.obeys:
            self.returncode = -9


def broadcasting(ctx):
    """A running broadcaster (a mock) and its params file in ``ctx.params_dir``; the path."""
    ctx.params_dir.mkdir(parents=True, exist_ok=True)
    params = ctx.params_dir / BROADCASTER_PARAMS_FILENAME
    params.write_text("{}", encoding="utf-8")
    supervision._broadcaster_supervisor = MagicMock()
    return params


def make_ctx(tmp_path, **fields) -> LoopContext:
    """A context on ``tmp_path`` with splash callbacks as mocks."""
    values = dict(settings_dir=tmp_path, secret=SECRET, tray_pid_override=TRAY_PID,
                  params_dir=tmp_path / "params", on_ready=MagicMock(), on_failed=MagicMock())
    values.update(fields)
    return LoopContext(**values)
