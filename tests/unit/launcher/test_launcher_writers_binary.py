# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Every launcher wizard-handoff writer puts exactly its bytes on disk.

The writers use ``os.open`` + ``os.write``. On Windows a descriptor
opened without ``O_BINARY`` is in text mode and ``os.write`` turns
each ``\\n`` into ``\\r\\n``. These tests write content that holds
``\\n``, read it back with a fresh binary handle and compare.
"""

from __future__ import annotations

import json
from pathlib import Path

from launcher import wizard_dir, wizard_ipc
from wizard.sethlans_wizard import ipc as wizard_side_ipc

BODY = b"first line\nsecond line\n\nlast\n"


def _read_raw(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def test_wizard_dir_write_secret_file(tmp_path: Path):
    path = tmp_path / "wizard" / ".ipc_secret"
    wizard_dir.write_secret_file(path, BODY)
    assert _read_raw(path) == BODY


def test_wizard_ipc_atomic_write_bytes(tmp_path: Path):
    path = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_REJECT
    wizard_ipc._atomic_write_bytes(path, BODY)
    assert _read_raw(path) == BODY


def test_wizard_ipc_marker_is_writer_json_and_wizard_accepts_it(
    tmp_path: Path,
):
    """The launcher's marker is its JSON on disk and the wizard verifies it."""
    secret = b"k" * 32
    path = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_REJECT
    wizard_ipc.write_marker(
        path, "wizard_reject", tmp_path, secret,
        payload={"reason": "one\ntwo"},
    )
    raw = _read_raw(path)
    assert raw == json.dumps(json.loads(raw)).encode("utf-8")
    validated = wizard_side_ipc.read_marker(
        path, secret, "wizard_reject", tmp_path,
    )
    assert validated is not None
    assert validated["reason"] == "one\ntwo"
