# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.container_checks` (issue #297).

* A missing or relative ``SETHLANS_MANAGER_DATA_DIR`` stops the container
  with exit 2 and a configuration message.
* A bind-mounted ``/data`` that Docker created owned by root stops it
  with exit 3 and a one-paragraph chown fix, not a traceback. The
  unwritable case is simulated by making the probe fail, so the tests
  behave the same on every OS.
* A writable data folder is then restricted to mode 0700; a filesystem
  that refuses chmod is skipped with an INFO log.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from launcher import container_checks as cc

ENV = "SETHLANS_MANAGER_DATA_DIR"


@pytest.fixture
def fixed_ids(monkeypatch):
    monkeypatch.setattr(cc, "_ids", lambda: (1000, 1000))


@pytest.fixture
def chmods(monkeypatch):
    calls = []
    monkeypatch.setattr(cc.os, "chmod", lambda path, mode: calls.append((Path(path), mode)))
    return calls


def _denied(*_a, **_k):
    raise PermissionError(13, "Permission denied")


# -- configuration ----------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_setting_is_a_configuration_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)
    code, message = cc.data_dir_problem()
    assert code == cc.EXIT_DATA_DIR_INVALID == 2
    assert f"{ENV} is not set" in message
    assert "chown" not in message


def test_relative_setting_is_a_configuration_error(monkeypatch, chmods):
    monkeypatch.setenv(ENV, "relative/data")
    code, message = cc.data_dir_problem()
    assert code == cc.EXIT_DATA_DIR_INVALID
    assert "must be an absolute path" in message
    assert "chown" not in message
    assert chmods == []


# -- writable -------------------------------------------------------------

def test_writable_data_dir_is_fine_created_and_restricted(monkeypatch, tmp_path, chmods):
    data = tmp_path / "data"
    monkeypatch.setenv(ENV, str(data))

    assert cc.data_dir_problem() is None
    assert data.is_dir()
    assert list(data.iterdir()) == []  # the probe file is gone
    assert chmods == [(data, 0o700)]


def test_chmod_refused_is_skipped_with_info(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(ENV, str(tmp_path))
    monkeypatch.setattr(cc.os, "chmod", _denied)

    with caplog.at_level(logging.INFO, logger="sethlans.container"):
        assert cc.data_dir_problem() is None
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1 and "chmod 0700" in infos[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permission bits")
def test_real_chmod_sets_owner_only(monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setenv(ENV, str(data))
    assert cc.data_dir_problem() is None
    assert data.stat().st_mode & 0o777 == 0o700


# -- not writable -----------------------------------------------------------

def test_unwritable_data_dir_gives_one_paragraph_fix(monkeypatch, tmp_path, fixed_ids, chmods):
    monkeypatch.setenv(ENV, str(tmp_path))
    monkeypatch.setattr(cc.tempfile, "TemporaryFile", _denied)

    code, message = cc.data_dir_problem()

    assert code == cc.EXIT_DATA_DIR_NOT_WRITABLE == 3
    assert "\n" not in message
    assert str(tmp_path) in message
    assert "Permission denied" in message
    assert "uid 1000, gid 1000" in message
    assert "sudo chown -R 1000:1000 <host folder>" in message
    assert cc.COMPOSE_DATA_FOLDER in message
    assert chmods == []


def test_uncreatable_data_dir_is_reported(monkeypatch, tmp_path, fixed_ids):
    monkeypatch.setenv(ENV, str(tmp_path / "missing"))
    monkeypatch.setattr(cc.Path, "mkdir", lambda self, *a, **k: _denied())
    code, message = cc.data_dir_problem()
    assert code == cc.EXIT_DATA_DIR_NOT_WRITABLE
    assert "sudo chown -R 1000:1000" in message


def test_compose_folder_matches_the_compose_file():
    compose = Path(cc.__file__).resolve().parents[1] / "deploy" / "docker" / "docker-compose.yml"
    if not compose.is_file():
        pytest.skip("deploy/ is not copied into the Docker test image")
    assert "- ./data/manager:/data" in compose.read_text(encoding="utf-8")
    assert cc.COMPOSE_DATA_FOLDER == "deploy/docker/data/manager"


def test_ids_are_the_process_ids():
    uid, gid = cc._ids()
    if hasattr(os, "getuid"):
        assert (uid, gid) == (os.getuid(), os.getgid())
    else:
        assert (uid, gid) == ("?", "?")
