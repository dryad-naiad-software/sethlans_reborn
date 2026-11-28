# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/clipboard.py``: the copied setup token does not stay on the clipboard
(6c security review L2)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp")

from PySide6.QtGui import QClipboard, QGuiApplication  # noqa: E402

from shared.tray import clipboard  # noqa: E402

_TOKEN = "Bb4BMMMGqXqAPv9M-sY8Y6q2VPl0kjsqQAQYcH30Qoc"


class TestClearCopiedToken:
    """6c security review L2: the copied setup token leaves the clipboard after 60 s
    (or when setup closes), unless the person has copied something else since."""

    @pytest.fixture(autouse=True)
    def _fresh(self):
        yield
        clipboard.clear_copied_token()

    def test_cleared_when_the_timer_fires(self, qapp):
        clipboard.copy_token_to_clipboard(_TOKEN)
        clipboard.clear_token_later(_TOKEN)
        timer = clipboard._clear_timer
        assert (timer.isActive(), timer.isSingleShot(), timer.interval()) == (True, True, clipboard.CLEAR_AFTER_MS)
        timer.timeout.emit()
        assert QGuiApplication.clipboard().text(QClipboard.Mode.Clipboard) == ""

    def test_something_copied_since_is_left_alone(self, qapp):
        clipboard.copy_token_to_clipboard(_TOKEN)
        clipboard.clear_token_later(_TOKEN)
        QGuiApplication.clipboard().setText("the person's own text", QClipboard.Mode.Clipboard)
        assert clipboard.clear_copied_token() is False
        assert QGuiApplication.clipboard().text(QClipboard.Mode.Clipboard) == "the person's own text"
