# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/file_acls.py`` (issue #169).

The helper used to live in ``launcher/tray_ipc.py`` which made its
emitted log records misleadingly tagged with the launcher's logger
name when called from the wizard. After the move, the logger name
must reflect the helper's actual home: ``shared.file_acls``.
"""

from __future__ import annotations

import logging
import sys

import pytest

from shared import file_acls


class TestTightenAclsNoOpOnPosix:

    def test_returns_without_raising(self, tmp_path, mocker):
        mocker.patch.object(file_acls.sys, "platform", "linux")
        # Must not raise; must not emit any warnings (early return).
        file_acls.tighten_acls_windows(tmp_path)


class TestTightenAclsLoggerName:
    """AC-LoggerName — emitted records must carry the new module name."""

    def test_warns_when_pywin32_missing(self, tmp_path, mocker, caplog):
        # Force the Windows code path even on POSIX CI runners.
        mocker.patch.object(file_acls.sys, "platform", "win32")
        # Force ``import win32security`` to fail by inserting a sentinel
        # that raises ImportError on attribute access. Easiest: set the
        # entry in sys.modules to None — ``import`` then raises
        # ImportError per CPython spec.
        mocker.patch.dict(sys.modules, {"win32security": None})

        with caplog.at_level(logging.WARNING, logger="shared.file_acls"):
            file_acls.tighten_acls_windows(tmp_path / "secret.key")

        # Exactly one record, from the new module's logger, mentioning
        # the missing dep.
        relevant = [
            r for r in caplog.records
            if r.name == "shared.file_acls"
        ]
        assert relevant, (
            "expected a record on logger 'shared.file_acls'; got: "
            f"{[(r.name, r.getMessage()) for r in caplog.records]}"
        )
        assert any(
            "pywin32 unavailable" in r.getMessage() for r in relevant
        )
        # Negative assertion: must NOT log under the old launcher
        # logger name (the whole point of the refactor).
        assert not any(
            r.name == "launcher.tray_ipc" for r in caplog.records
        ), "logger name leaked back to launcher.tray_ipc"


class TestReExport:
    """AC-LauncherImport — the launcher path keeps working."""

    def test_launcher_path_returns_same_callable(self):
        from launcher import tray_ipc
        assert tray_ipc.tighten_acls_windows is file_acls.tighten_acls_windows


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
