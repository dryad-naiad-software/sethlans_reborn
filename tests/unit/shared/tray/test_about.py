# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/about.py`` (NFR-1 LGPLv3 attribution).

Covers the shared About Sethlans dialog used by both the manager and
worker menu sections.  Asserts title, attribution text, license
references, and non-blocking ``show()`` semantics.
"""

from __future__ import annotations

import logging
import sys

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for about")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from shared.tray import about  # noqa: E402
from shared.tray.about import (  # noqa: E402
    ABOUT_BODY,
    ABOUT_TITLE,
    show_about_dialog,
)


class TestAboutConstants:

    def test_title_is_about_sethlans(self):
        assert ABOUT_TITLE == "About Sethlans"

    def test_body_mentions_lgpl(self):
        assert "LGPL" in ABOUT_BODY

    def test_body_mentions_qt(self):
        assert "Qt" in ABOUT_BODY

    def test_body_mentions_pyside6(self):
        assert "PySide6" in ABOUT_BODY

    def test_body_mentions_licenses_directory(self):
        assert "licenses/" in ABOUT_BODY

    def test_body_mentions_bundled_license_files(self):
        assert "licenses/LICENSE.LGPLv3" in ABOUT_BODY
        assert "licenses/Qt-NOTICE.txt" in ABOUT_BODY

    def test_body_mentions_project_gpl(self):
        assert "GPL-2.0-or-later" in ABOUT_BODY


class TestShowAboutDialog:

    def test_returns_none(self, qapp, mocker):
        mocker.patch.object(about, "QMessageBox")
        assert show_about_dialog() is None

    def test_constructs_qmessagebox_with_parent_none(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        show_about_dialog()
        mock_box_cls.assert_called_once_with(None)

    def test_constructs_qmessagebox_with_given_parent(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        sentinel_parent = object()
        show_about_dialog(sentinel_parent)
        mock_box_cls.assert_called_once_with(sentinel_parent)

    def test_sets_window_title_to_about_sethlans(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        show_about_dialog()
        instance = mock_box_cls.return_value
        instance.setWindowTitle.assert_called_once_with("About Sethlans")

    def test_sets_text_to_about_body(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        show_about_dialog()
        instance = mock_box_cls.return_value
        instance.setText.assert_called_once_with(ABOUT_BODY)

    def test_calls_show_not_exec(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        show_about_dialog()
        instance = mock_box_cls.return_value
        instance.show.assert_called_once_with()
        assert not instance.exec.called

    def test_sets_modal_false(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        show_about_dialog()
        instance = mock_box_cls.return_value
        instance.setModal.assert_called_once_with(False)

    def test_sets_delete_on_close_attribute(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        show_about_dialog()
        instance = mock_box_cls.return_value
        assert instance.setAttribute.called

    def test_sets_information_icon(self, qapp, mocker):
        mock_box_cls = mocker.patch.object(about, "QMessageBox")
        show_about_dialog()
        instance = mock_box_cls.return_value
        # When QMessageBox is patched, QMessageBox.Icon.Information
        # resolves to the MagicMock attribute — assert against the
        # same path rather than the real enum.
        instance.setIcon.assert_called_once_with(
            mock_box_cls.Icon.Information,
        )

    def test_exception_is_swallowed(self, qapp, mocker, caplog):
        mocker.patch.object(
            about, "QMessageBox", side_effect=RuntimeError("bad"),
        )
        with caplog.at_level(logging.WARNING, logger=about.logger.name):
            # Must not raise.
            show_about_dialog()
        assert any(
            "About dialog" in rec.message for rec in caplog.records
        )


class TestShowAboutDialogRealWidget:
    """Exercise the function against a real QMessageBox under offscreen."""

    def test_real_dialog_does_not_raise(self, qapp):
        # No mocking — build an actual QMessageBox under offscreen.
        show_about_dialog()

    @pytest.mark.skipif(
        sys.platform == "darwin",
        reason=(
            "macOS QMessageBox.windowTitle() returns '' after native "
            "NSAlert integration applies the title - the dialog renders "
            "correctly but the getter does not survive the round-trip. "
            "See GitHub #82."
        ),
    )
    def test_real_dialog_sets_expected_title(self, qapp, mocker):
        """Capture the constructed box to assert title on the instance."""
        created = []
        real_cls = QMessageBox

        def _factory(parent=None):
            box = real_cls(parent)
            created.append(box)
            return box

        mocker.patch.object(about, "QMessageBox", side_effect=_factory)
        # Preserve enum access on the patched symbol so show_about_dialog
        # can still reach QMessageBox.Icon / QMessageBox.StandardButton.
        about.QMessageBox.Icon = real_cls.Icon
        about.QMessageBox.StandardButton = real_cls.StandardButton
        show_about_dialog()
        assert len(created) == 1
        assert created[0].windowTitle() == "About Sethlans"
        assert created[0].text() == ABOUT_BODY
