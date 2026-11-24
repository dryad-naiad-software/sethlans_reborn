# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.container_setup_marker` (issue #301).

The container has no setup wizard, so its entrypoint writes the
setup-complete marker the heartbeat reads; without it every enrolled
worker idles. These tests use a real data folder under ``tmp_path``:

* no marker -> a completed one is written (version 1, topology
  ``manager``) and :func:`is_setup_complete` turns true;
* a valid marker is left byte-for-byte as it is;
* an invalid marker (empty, malformed, wrong version, unfinished) is
  rewritten with a warning.

Write failures are in ``test_container_setup_marker_write.py``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from launcher import container_setup_marker as sm
from workers.services.sentinel import is_setup_complete

from ._container_marker_helpers import (
    LOGGER,
    assert_completed as _assert_completed,
    leftover_temp_files as _leftover_temp_files,
    marker_path as _marker,
    ours as _ours,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = REPO_ROOT / "manager"


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


# -- no marker --------------------------------------------------------------

def test_missing_marker_is_written_and_completes_setup(data, caplog):
    assert is_setup_complete(data) is False

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert sm.setup_marker_problem(data) is None

    record = _assert_completed(data)
    assert set(record) == {"version", "completed_at", "topology", "checkpoints"}
    infos = _ours(caplog, logging.INFO)
    assert len(infos) == 1
    assert "Wrote setup-complete marker" in infos[0].getMessage()
    assert str(_marker(data)) in infos[0].getMessage()
    assert _ours(caplog, logging.WARNING) == []
    assert _leftover_temp_files(data) == []


def test_missing_data_folder_is_created(tmp_path):
    folder = tmp_path / "not-yet"
    assert sm.setup_marker_problem(folder) is None
    _assert_completed(folder)


def test_accepts_a_string_path(data):
    assert sm.setup_marker_problem(str(data)) is None
    _assert_completed(data)


# -- valid marker -----------------------------------------------------------

def test_valid_marker_is_left_byte_for_byte(data, caplog, mocker):
    # A wizard-written marker from a native install: other topology,
    # real checkpoints, its own formatting. None of it may change.
    original = (
        b'{"version": 1, "completed_at": "2025-01-15T12:00:00Z",'
        b' "topology": "all", "checkpoints": ["topology_chosen", "network_configured"]}\n'
    )
    _marker(data).write_bytes(original)
    create = mocker.spy(sm, "create_sentinel")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert sm.setup_marker_problem(data) is None

    assert _marker(data).read_bytes() == original
    create.assert_not_called()
    infos = _ours(caplog, logging.INFO)
    assert len(infos) == 1 and "is present" in infos[0].getMessage()
    assert _ours(caplog, logging.WARNING) == []


def test_second_start_keeps_the_first_marker(data):
    assert sm.setup_marker_problem(data) is None
    first = _marker(data).read_bytes()
    assert sm.setup_marker_problem(data) is None
    assert _marker(data).read_bytes() == first


# -- invalid marker ---------------------------------------------------------

_UNFINISHED = {"version": 1, "completed_at": None, "topology": None, "checkpoints": ["topology_chosen"]}

INVALID = {
    "empty touch file": "",
    "malformed json": "{not json!!!",
    "json list": '["not", "an", "object"]',
    "wrong version": json.dumps({"version": 2, "completed_at": "2025-01-15T12:00:00Z"}),
    "no version": json.dumps({"completed_at": "2025-01-15T12:00:00Z"}),
    "completed_at null": json.dumps(_UNFINISHED),
    "completed_at empty": json.dumps({**_UNFINISHED, "completed_at": ""}),
    "completed_at missing": json.dumps({"version": 1, "topology": "manager"}),
}


@pytest.mark.parametrize("content", list(INVALID.values()), ids=list(INVALID))
def test_invalid_marker_is_rewritten_with_a_warning(data, caplog, content):
    _marker(data).write_text(content, encoding="utf-8")
    assert is_setup_complete(data) is False

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert sm.setup_marker_problem(data) is None

    _assert_completed(data)
    warnings = _ours(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0].getMessage().startswith(f"Replaced {_marker(data)}")
    assert "not a valid completed setup marker" in warnings[0].getMessage()
    assert _leftover_temp_files(data) == []


def test_dangling_symlink_marker_is_replaced_with_a_warning(data, caplog):
    try:
        os.symlink(data / "gone", _marker(data))
    except OSError:
        if sys.platform != "win32":
            raise
        pytest.skip("symlinks need extra rights on Windows")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert sm.setup_marker_problem(data) is None

    assert not _marker(data).is_symlink()
    _assert_completed(data)
    assert len(_ours(caplog, logging.WARNING)) == 1


def test_non_utf8_marker_is_rewritten(data, caplog):
    # Issue #303: bytes that are not UTF-8 (a UTF-16 file, a torn write)
    # used to raise UnicodeDecodeError and crash the entrypoint.
    _marker(data).write_bytes(b"\xff\xfe{\x00}\x00")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert sm.setup_marker_problem(data) is None

    _assert_completed(data)
    assert len(_ours(caplog, logging.WARNING)) == 1


# -- import cost ----------------------------------------------------------

_IMPORT_PROBE = """
import json, sys
sys.path[:0] = [{repo!r}, {manager!r}]
import launcher.container_setup_marker
print(json.dumps({{
    "heavy": sorted(m for m in sys.modules if m.split(".")[0] in ("django", "rest_framework")),
    "sentinel": "workers.services.sentinel" in sys.modules,
}}))
"""


def test_import_does_not_load_django():
    code = _IMPORT_PROBE.format(repo=str(REPO_ROOT), manager=str(MANAGER_DIR))
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    loaded = json.loads(out.stdout)
    assert loaded == {"heavy": [], "sentinel": True}
