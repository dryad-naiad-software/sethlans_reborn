# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared test helpers for ``launcher.wizard_runtime`` unit tests.

Phase F4 split ``test_wizard_runtime.py`` into two files for the
300-line limit; the ``_FakeProc`` Popen stand-in and the shared
``SECRET`` constant are imported by both halves.
"""

from __future__ import annotations

import subprocess


SECRET = b"a" * 32


class FakeProc:
    """Minimal Popen stand-in for the runtime/wizard process."""

    def __init__(self, returncode=None, pid=12345):
        self.returncode = returncode
        self.pid = pid
        self._poll_results = [returncode]
        self.terminate_called = False
        self.kill_called = False
        self.wait_raises = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_called = True

    def kill(self):
        self.kill_called = True

    def wait(self, timeout=None):
        del timeout
        if self.wait_raises:
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)
        return self.returncode or 0
