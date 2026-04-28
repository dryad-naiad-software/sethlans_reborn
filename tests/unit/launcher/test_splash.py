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

    def test_window_title_is_sethlans(self, qtbot):
        # Issue #106: the splash widget must set windowTitle("Sethlans")
        # so Alt+Tab / taskbar surfaces read "Sethlans" instead of the
        # frozen exe name.
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        assert widget.windowTitle() == "Sethlans"


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

    def test_reason_line_joins_multiline_with_spaces(self, qtbot):
        # Per the splash_error_card_layout spec, multi-line reasons are
        # joined with spaces (no embedded newlines) so the QLabel's
        # word-wrap engine can lay them out within the 420 px card.
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("line1\nline2\nline3", "tb")
        text = widget._reason_label.text()
        assert "\n" not in text
        assert "line1" in text
        assert "line2" in text
        assert "line3" in text


# ---- Error card layout (spec: splash_error_card_layout.md) -----------

class TestErrorCardLayout:

    def test_error_card_is_420x420_after_morph(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("reason", "trace")
        assert abs(widget.width() - 420) <= 1
        assert abs(widget.height() - 420) <= 1
        assert widget.width() == widget.height()

    def test_error_card_width_unchanged_by_long_traceback(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        long_line = "x" * 5000
        traceback_text = "\n".join(
            ["Traceback (most recent call last):", long_line] + [
                f"  File \"foo.py\", line {i}, in bar" for i in range(50)
            ],
        )
        widget.morph_to_error("boom", traceback_text)
        assert abs(widget.width() - 420) <= 1

    def test_reason_label_word_wraps(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("reason", "trace")
        assert widget._reason_label.wordWrap() is True

    def test_trace_area_word_wraps(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("reason", "trace")
        assert (
            widget._trace_area.lineWrapMode()
            == QPlainTextEdit.LineWrapMode.WidgetWidth
        )

    def test_error_widgets_have_dark_foreground(self, qtbot):
        # The splash's centralized stylesheet sets explicit foreground
        # colors on QWidget / QPlainTextEdit / QPushButton so Windows
        # dark mode does not override the text to white on the splash's
        # forced light background (issue #161).
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("reason", "trace")
        sheet = widget.styleSheet()
        # All three selectors that hold error-card content must declare
        # the dark foreground constant.
        assert "#1f1f1f" in sheet
        assert "QWidget" in sheet
        assert "QPlainTextEdit" in sheet
        assert "QPushButton" in sheet
        # And the panel background is the documented light color.
        assert "#fafafa" in sheet

    def test_format_reason_does_not_truncate_at_160(self, qtbot):
        # Old behavior truncated at 160 chars; new spec preserves up to
        # _REASON_MAX_CHARS (600) intact.
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        long_reason = "a" * 400
        widget.morph_to_error(long_reason, "tb")
        text = widget._reason_label.text()
        assert long_reason in text
        assert "..." not in text

    def test_format_reason_bounds_at_600(self, qtbot):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        huge_reason = "b" * 1000
        widget.morph_to_error(huge_reason, "tb")
        text = widget._reason_label.text()
        # The "Reason: " prefix plus up to 600 chars (with "..." in
        # the last 3 once truncation kicks in).
        assert text.endswith("...")
        # Exclude the "Reason: " prefix when measuring the bound.
        body = text[len("Reason: "):]
        assert len(body) == 600


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


# ---- Issue #162: dismissal must quit Qt ------------------------------

class TestDismissalQuitsQt:
    """The splash uses Qt.WindowType.Tool, which is excluded from
    ``quitOnLastWindowClosed``. Hiding the widget therefore does NOT
    return from ``app.exec()``. Both the Close button and the
    ``closeEvent`` override must explicitly call ``app.quit()`` so the
    launcher can complete teardown after a startup failure.
    """

    def test_close_button_calls_app_quit(self, qtbot, mocker):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "Traceback...\nboom")

        # Patch the QApplication instance lookup INSIDE the splash
        # module so we can observe the quit() call without tearing
        # down the real qtbot QApplication.
        from launcher import splash as splash_mod
        fake_app = mocker.MagicMock()
        mocker.patch.object(
            splash_mod.QApplication, "instance", return_value=fake_app,
        )

        # Find the Close button on the error card and click it.
        close_btn = next(
            b for b in widget.findChildren(QPushButton)
            if b.text() == "Close"
        )
        qtbot.mouseClick(close_btn, Qt.MouseButton.LeftButton)

        fake_app.quit.assert_called()
        # And the widget itself was hidden.
        assert not widget.isVisible()

    def test_close_event_calls_app_quit(self, qtbot, mocker):
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)

        from launcher import splash as splash_mod
        fake_app = mocker.MagicMock()
        mocker.patch.object(
            splash_mod.QApplication, "instance", return_value=fake_app,
        )

        # Programmatic close — simulates alt-F4, OS close, or any
        # caller invoking widget.close() directly.
        widget.close()

        fake_app.quit.assert_called()

    def test_dismiss_and_quit_when_app_instance_is_none(
        self, qtbot, mocker,
    ):
        # Defensive: _quit_app must tolerate a missing QApplication
        # instance (e.g., during teardown). It should NOT raise.
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)

        from launcher import splash as splash_mod
        mocker.patch.object(
            splash_mod.QApplication, "instance", return_value=None,
        )

        # Both paths must be safe.
        widget._dismiss_and_quit()
        widget.close()  # routes through closeEvent
