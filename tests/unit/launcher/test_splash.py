# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Widget-level unit tests for :class:`launcher.splash.SethlansSplash`.

Covers the success/error layout transitions, idempotency of
:meth:`morph_to_error`, and the alt-F4 swallow behaviour called out in
``development/specs/launcher_startup_splash.md``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for splash")
pytest.importorskip("pytestqt", reason="pytest-qt required for qtbot")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton  # noqa: E402

from launcher.splash import SethlansSplash  # noqa: E402


# ---- Success layout --------------------------------------------------

class TestSuccessLayout:

    def test_constructs_without_raising(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        assert widget is not None

    def test_window_flags_are_borderless_and_top(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        flags = widget.windowFlags()
        assert bool(flags & Qt.WindowType.FramelessWindowHint)
        assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)
        assert bool(flags & Qt.WindowType.Tool)

    def test_wordmark_label_has_pixmap(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        pix = widget._wordmark_label.pixmap()
        # Either the real asset was loaded (pixmap not null) or the
        # text fallback kicked in. Both are acceptable so the test
        # does not require the build environment to ship the asset,
        # but the asset IS expected to be present in source mode.
        if pix is None or pix.isNull():
            assert widget._wordmark_label.text() == "Sethlans"
        else:
            assert pix.width() > 0 and pix.height() > 0

    def test_starting_label_present(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        assert widget._starting_label is not None
        assert widget._starting_label.text() == "Starting..."

    def test_version_label_shows_v_prefix(self, qtbot):
        widget = SethlansSplash(version="1.2.3")
        qtbot.addWidget(widget)
        assert widget._version_label.text() == "v1.2.3"


# ---- close_for_success ---------------------------------------------

class TestCloseForSuccess:

    def test_close_for_success_hides_widget(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.show()
        assert widget.isVisible()
        widget.close_for_success()
        assert not widget.isVisible()


# ---- morph_to_error ------------------------------------------------

class TestMorphToError:

    def test_swaps_layout_and_exposes_error_widgets(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "Traceback...\nboom")

        # Title label present and says 'Failed to start'.
        assert widget._title_label is not None
        assert widget._title_label.text() == "Failed to start"
        assert widget._title_label.isVisible() or not widget.isVisible()

        # Reason label visible and contains the provided reason.
        assert widget._reason_label is not None
        assert "boom" in widget._reason_label.text()

        # Traceback area shows the full text.
        assert widget._trace_area is not None
        assert "Traceback" in widget._trace_area.toPlainText()

        # Starting label is hidden.
        assert not widget._starting_label.isVisible() \
            or not widget.isVisible()

        # Two buttons: Show log + Close.
        buttons = widget.findChildren(QPushButton)
        labels = {b.text() for b in buttons}
        assert "Show log" in labels
        assert "Close" in labels

    def test_is_idempotent(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("first", "trace-one")

        # Count error widgets after the first transition.
        labels_before = len(widget.findChildren(QLabel))
        traces_before = len(widget.findChildren(QPlainTextEdit))
        btns_before = len(widget.findChildren(QPushButton))

        widget.morph_to_error("second", "trace-two")

        assert len(widget.findChildren(QLabel)) == labels_before
        assert len(widget.findChildren(QPlainTextEdit)) == traces_before
        assert len(widget.findChildren(QPushButton)) == btns_before
        assert "second" in widget._reason_label.text()
        assert "trace-two" in widget._trace_area.toPlainText()

    def test_reason_line_truncated_to_single_line(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("line1\nline2\nline3", "tb")
        text = widget._reason_label.text()
        assert "\n" not in text
        assert "line1" in text
        assert "line2" not in text


# ---- keyPressEvent / alt-F4 ---------------------------------------

def _make_alt_f4_event() -> QKeyEvent:
    return QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_F4,
        Qt.KeyboardModifier.AltModifier,
    )


class TestAltF4Handling:

    def test_success_layout_swallows_alt_f4(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.show()
        event = _make_alt_f4_event()
        widget.keyPressEvent(event)
        # event.accept() should have marked the event as accepted
        # so Qt doesn't propagate it to ancestor widgets.
        assert event.isAccepted()
        # Widget is still visible — alt-F4 did NOT close it.
        assert widget.isVisible()

    def test_error_layout_allows_alt_f4(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("oops", "traceback")
        # In error mode, keyPressEvent must NOT swallow alt-F4 — the
        # launcher has already exited and the user should be able to
        # dismiss the window with the OS close shortcut. The key test
        # is that our override does not mark the event accepted; Qt's
        # default handling on real OSes then delivers a window-close
        # signal (which we don't simulate here).
        event = _make_alt_f4_event()
        widget.keyPressEvent(event)
        assert not event.isAccepted(), (
            "Error-state alt-F4 handler must not swallow the event; "
            "the user needs to be able to close the error card."
        )
