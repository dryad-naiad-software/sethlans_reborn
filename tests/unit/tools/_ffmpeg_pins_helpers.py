# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for the ``tools/check_ffmpeg_pins.py`` tests.

The tool is a stand-alone script, so it is loaded by file path, the
same way CI runs it.  Nothing here touches the network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "tools" / "check_ffmpeg_pins.py"


def load_tool():
    """Load a fresh copy of ``tools/check_ffmpeg_pins.py`` as a module."""
    spec = importlib.util.spec_from_file_location("_check_ffmpeg_pins_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    """Minimal stand-in for the ``urlopen`` response the tool reads."""

    def __init__(self, chunks, url="https://example.invalid/f.zip", length=None):
        self._chunks = list(chunks)
        self._url = url
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.closed = False
        self.read_sizes = []

    def read(self, size):
        self.read_sizes.append(size)
        return self._chunks.pop(0) if self._chunks else b""

    def geturl(self):
        return self._url

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeOpener:
    """Stand-in for ``urllib.request.build_opener(...)``."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response
