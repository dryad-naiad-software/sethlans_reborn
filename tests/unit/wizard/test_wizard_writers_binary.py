# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Every wizard file writer puts exactly the bytes it built on disk.

Each writer opens its temp file with ``os.open`` + ``os.write``. On
Windows a descriptor opened without ``O_BINARY`` is in text mode and
``os.write`` turns each ``\\n`` into ``\\r\\n``. These tests write
content that holds ``\\n``, read the file back with a fresh binary
handle, and compare byte for byte. They run on every OS; only Windows
could ever fail them.
"""

from __future__ import annotations

import configparser
import io
import json
import types
from pathlib import Path

from launcher import wizard_ipc as launcher_ipc
from shared import settings_file_io
from wizard.sethlans_wizard import bootstrap, ipc, manager_ini, progress
from wizard.sethlans_wizard.handlers import pending_setup, topology

BODY = b'{\n  "a": 1,\n  "b": "two"\n}\n'


def _read_raw(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def test_pending_setup_atomic_write(tmp_path: Path):
    target = tmp_path / pending_setup.PENDING_SETUP_FILENAME
    pending_setup._atomic_write(target, BODY)
    assert _read_raw(target) == BODY


def test_settings_file_atomic_write(tmp_path: Path):
    target = tmp_path / manager_ini.MANAGER_INI_FILENAME
    settings_file_io.atomic_replace(target, BODY)
    assert _read_raw(target) == BODY


def test_manager_ini_update_writes_configparser_output(tmp_path: Path):
    target = manager_ini.update_manager_ini(
        "server", {"port": 8080, "host": "0.0.0.0"}, tmp_path / "sethlans.ini",
    )
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read_dict({"server": {"port": "8080", "host": "0.0.0.0"}})
    expected = io.StringIO()
    parser.write(expected)
    raw = _read_raw(target)
    assert b"\n" in raw
    assert b"\r" not in raw
    assert raw == expected.getvalue().encode("utf-8")


def test_progress_write_atomic_locked(tmp_path: Path):
    path = tmp_path / "progress.json"
    payload = {"checkpoints": {"topology_chosen": 1}, "schema_version": 1}
    with progress.get_progress_lock():
        progress._write_atomic_locked(path, payload)
    expected = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    raw = _read_raw(path)
    assert b"\n" in raw
    assert raw == expected


def test_topology_write_atomic(tmp_path: Path, monkeypatch):
    # The real body is single-line JSON; an indented dump gives the
    # writer newlines to mangle, so the test can tell text mode apart.
    def indented_dumps(obj):
        return json.dumps(obj, indent=2)

    monkeypatch.setattr(
        topology, "json", types.SimpleNamespace(dumps=indented_dumps),
    )
    target = topology.write_topology_atomic(tmp_path, "manager")
    raw = _read_raw(target)
    assert b"\n" in raw
    assert raw == indented_dumps(json.loads(raw)).encode("utf-8")


def test_bootstrap_write_port_file(tmp_path: Path):
    bootstrap.write_port_file(tmp_path, 8101)
    assert _read_raw(tmp_path / bootstrap.PORT_FILENAME) == b"8101\n"


def test_ipc_atomic_write_bytes(tmp_path: Path):
    path = tmp_path / "wizard" / ipc.MARKER_WIZARD_DONE
    ipc._atomic_write_bytes(path, BODY)
    assert _read_raw(path) == BODY


def test_ipc_marker_is_writer_json_and_launcher_accepts_it(tmp_path: Path):
    """The on-disk marker is the writer's JSON and still verifies."""
    secret = b"s" * 32
    path = tmp_path / "wizard" / ipc.MARKER_WIZARD_DONE
    ipc.write_marker(
        path, "wizard_done", tmp_path, secret,
        payload={"topology": "manager", "note": "line1\nline2"},
    )
    raw = _read_raw(path)
    assert raw == json.dumps(json.loads(raw)).encode("utf-8")
    validated = launcher_ipc.read_marker(
        path, secret, "wizard_done", tmp_path,
    )
    assert validated is not None
    assert validated["note"] == "line1\nline2"
