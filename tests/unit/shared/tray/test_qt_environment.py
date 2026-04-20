# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Smoke test for the Qt test environment.

Catches environment-level regressions (missing Qt platform plugin,
PyInstaller hook issues, Qt lib path problems, pytest-qt config drift)
early. Does not test Qt itself.
"""

import os


class TestQtEnvironment:
    """Verify the pytest Qt environment is wired for headless runs."""

    def test_qtwidgets_imports_cleanly(self):
        """PySide6.QtWidgets must import without error."""
        import PySide6.QtWidgets  # noqa: F401

    def test_offscreen_platform_env_var_set(self):
        """pytest.ini must force QT_QPA_PLATFORM=offscreen for headless CI."""
        assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"

    def test_qapplication_uses_offscreen_platform(self, qapp):
        """pytest-qt's qapp fixture must yield an offscreen QApplication."""
        assert qapp is not None
        assert qapp.platformName() == "offscreen"
        from PySide6.QtWidgets import QApplication
        assert QApplication.instance() is not None
