# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Write failures in :mod:`launcher.container_setup_marker` (issue #301).

* The marker cannot be written (a folder at its path, a full disk, no
  permission): exit 4 with a one-paragraph message naming the marker.
* The writer renames the marker into place and only then fsyncs the
  folder and sets 0600. When one of those later steps fails, the marker
  on disk is already valid: the container starts, with one warning.
* An error that leaves no valid marker (nothing written, or an invalid
  one) is still exit 4.

Split from ``test_container_setup_marker.py`` to keep each file under
the 300-line limit.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher import container_setup_marker as sm
from workers.services import sentinel
from workers.services.sentinel import is_setup_complete

from ._container_marker_helpers import (
    LOGGER,
    assert_completed,
    leftover_temp_files,
    marker_path,
    ours,
)


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


def _assert_failure(problem, folder: Path, reason: str):
    code, message = problem
    assert code == sm.EXIT_SETUP_MARKER_FAILED == 4
    assert "\n" not in message
    assert f"marker {marker_path(folder)} ({reason})" in message
    assert f"data folder {folder} " in message
    assert "enrolled workers stay idle" in message


def _write_then_raise(error):
    """A create_sentinel whose write lands, then a later step fails."""
    def _create(*args, **kwargs):
        sentinel.create_sentinel(*args, **kwargs)
        raise error
    return _create


# -- nothing valid on disk: exit 4 -------------------------------------------

def test_folder_at_marker_path_returns_exit_4(data):
    marker_path(data).mkdir()

    problem = sm.setup_marker_problem(data)

    reason = "Access is denied" if sys.platform == "win32" else "Is a directory"
    _assert_failure(problem, data, reason)
    assert marker_path(data).is_dir()
    assert is_setup_complete(data) is False
    assert leftover_temp_files(data) == []


@pytest.mark.parametrize(("error", "reason"), [
    (OSError(28, "No space left on device"), "No space left on device"),
    (PermissionError(13, "Permission denied"), "Permission denied"),
    (OSError("no errno"), "OSError"),
    (PermissionError(), "PermissionError"),
])
def test_error_before_writing_returns_exit_4_with_reason(data, mocker, error, reason):
    create = mocker.patch.object(sm, "create_sentinel", side_effect=error)

    problem = sm.setup_marker_problem(data)

    _assert_failure(problem, data, reason)
    create.assert_called_once_with(data, topology="manager", checkpoints=[])
    assert not marker_path(data).exists()


def test_error_before_writing_leaves_an_invalid_marker_and_exits_4(data, mocker):
    marker_path(data).write_text("{not json", encoding="utf-8")
    mocker.patch.object(sm, "create_sentinel", side_effect=PermissionError(13, "Permission denied"))

    _assert_failure(sm.setup_marker_problem(data), data, "Permission denied")
    assert marker_path(data).read_text(encoding="utf-8") == "{not json"


def test_error_after_an_invalid_write_still_exits_4(data, mocker):
    # The re-check is "is the marker valid", not "does a file exist".
    def _torn_write(folder, **_kwargs):
        marker_path(folder).write_text("", encoding="utf-8")
        raise OSError(5, "Input/output error")

    mocker.patch.object(sm, "create_sentinel", side_effect=_torn_write)

    _assert_failure(sm.setup_marker_problem(data), data, "Input/output error")


def test_exit_4_logs_nothing_itself(data, mocker, caplog):
    # main() logs the returned message; logging it here too would double it.
    mocker.patch.object(sm, "create_sentinel", side_effect=OSError(28, "No space left on device"))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        sm.setup_marker_problem(data)
    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_non_os_error_is_not_swallowed(data, mocker):
    mocker.patch.object(sm, "create_sentinel", side_effect=TypeError("bug"))
    with pytest.raises(TypeError, match="bug"):
        sm.setup_marker_problem(data)


# -- valid marker on disk, later step failed: start with a warning -----------

@pytest.mark.parametrize("before", [None, ""], ids=["no marker", "empty marker"])
def test_error_after_a_valid_write_starts_with_one_warning(data, mocker, caplog, before):
    if before is not None:
        marker_path(data).write_text(before, encoding="utf-8")
    error = PermissionError(1, "Operation not permitted")
    mocker.patch.object(sm, "create_sentinel", side_effect=_write_then_raise(error))

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert sm.setup_marker_problem(data) is None

    assert_completed(data)
    warnings = ours(caplog, logging.WARNING)
    assert len(warnings) == 1  # not also "Replaced ..." for the old file
    text = warnings[0].getMessage()
    assert str(marker_path(data)) in text
    assert "a step after the write failed" in text
    assert "Operation not permitted" in text
    assert ours(caplog, logging.INFO) == []


def test_real_writer_failing_after_the_rename_starts_with_one_warning(
    data, monkeypatch, caplog,
):
    # Drive the real write_sentinel down its POSIX branch and make the
    # steps after os.replace fail: on Windows os.open() of the folder is
    # refused, on POSIX the 0600 chmod is.
    monkeypatch.setattr(sentinel, "platform", SimpleNamespace(system=lambda: "Linux"))

    def _refuse_chmod(self, *_a, **_k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(type(data), "chmod", _refuse_chmod)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert sm.setup_marker_problem(data) is None

    assert_completed(data)
    assert len(ours(caplog, logging.WARNING)) == 1
    assert leftover_temp_files(data) == []
