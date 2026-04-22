# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for download.py tests.

Builds tiny in-memory tar.gz and zip archives and provides a minimal
``urlopen`` stand-in.  Extracted into this private helper module so the
main test files stay under the 300-line Python ceiling.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeResponse:
    """Minimal ``urlopen`` stand-in supporting the ``with`` protocol."""

    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def __enter__(self):
        return self._buf

    def __exit__(self, exc_type, exc, tb):
        self._buf.close()
        return False


def build_targz(
    tmp_path: Path,
    members: dict,
    name: str = "caddy.tar.gz",
) -> Path:
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tar:
        for member_name, data in members.items():
            info = tarfile.TarInfo(name=member_name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return archive


def build_zip(
    tmp_path: Path,
    members: dict,
    name: str = "caddy.zip",
) -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        for member_name, data in members.items():
            zf.writestr(member_name, data)
    return archive
