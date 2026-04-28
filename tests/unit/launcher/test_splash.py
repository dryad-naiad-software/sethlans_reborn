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

        # Trace area carries reason + traceback (issue #165: the
        # standalone _reason_label QLabel is gone).
        assert widget._trace_area is not None
        body = widget._trace_area.toPlainText()
        assert body.startswith("Reason: boom")
        assert "Traceback" in body

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
        body = widget._trace_area.toPlainText()
        assert body.startswith("Reason: second")
        assert "trace-two" in body


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


# ---- Reason + log snippet in trace field (spec #165) -----------------

class TestReasonAndLogSnippetInTraceField:
    """Coverage for splash_error_card_log_snippet.md.

    The standalone _reason_label QLabel was removed; the reason now
    lives inside _trace_area as the first line, optionally followed by
    a launcher.log tail and an optional traceback section.
    """

    def test_morph_to_error_includes_reason_in_trace_field(self, qtbot):
        # AC-ReasonInField
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("test reason", "")
        body = widget._trace_area.toPlainText()
        assert body.startswith("Reason: test reason")

    def test_morph_to_error_includes_log_snippet(self, qtbot, tmp_path):
        # AC-LogSnippetIncluded
        log = tmp_path / "launcher.log"
        lines = [f"line {i}" for i in range(10)]
        log.write_text("\n".join(lines), encoding="utf-8")
        widget = SethlansSplash(version="9.9.9", log_path=log)
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "")
        body = widget._trace_area.toPlainText()
        assert "--- Recent launcher log ---" in body
        # All 10 lines are within the 20-line cap, so all appear.
        for ln in lines:
            assert ln in body

    def test_morph_to_error_handles_missing_log_file(
        self, qtbot, tmp_path,
    ):
        # AC-LogSnippetAbsent
        log = tmp_path / "does-not-exist.log"
        widget = SethlansSplash(version="9.9.9", log_path=log)
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "")
        body = widget._trace_area.toPlainText()
        assert body.startswith("Reason: boom")
        assert "--- Recent launcher log ---" not in body

    def test_morph_to_error_handles_corrupt_log(self, qtbot, tmp_path):
        # AC-LogSnippetCorrupt — non-utf8 bytes must not crash.
        log = tmp_path / "launcher.log"
        log.write_bytes(b"prelude\n\xff\xfe\xfd\nepilogue\n")
        widget = SethlansSplash(version="9.9.9", log_path=log)
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "")
        body = widget._trace_area.toPlainText()
        assert body.startswith("Reason: boom")
        assert "prelude" in body
        assert "epilogue" in body

    def test_log_snippet_is_bounded_to_20_lines(
        self, qtbot, tmp_path,
    ):
        # AC-LogSnippetBounded — write 1000 lines, only last 20 appear.
        log = tmp_path / "launcher.log"
        lines = [f"row-{i}" for i in range(1000)]
        log.write_text("\n".join(lines), encoding="utf-8")
        widget = SethlansSplash(version="9.9.9", log_path=log)
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "")
        body = widget._trace_area.toPlainText()
        # First 980 lines must be absent.
        assert "row-0\n" not in body
        assert "row-979" not in body
        # Last 20 lines (980..999) must all be present.
        for i in range(980, 1000):
            assert f"row-{i}" in body

    def test_morph_to_error_includes_traceback_when_provided(
        self, qtbot,
    ):
        # AC-TracebackIncluded
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        tb = "Traceback (most recent call last):\n  File ..."
        widget.morph_to_error("boom", tb)
        body = widget._trace_area.toPlainText()
        assert "--- Traceback ---" in body
        assert tb in body
        # Traceback section appears after the reason.
        assert body.index("Reason: boom") < body.index("--- Traceback ---")

    def test_morph_to_error_omits_traceback_section_when_empty(
        self, qtbot,
    ):
        # Empty traceback => no --- Traceback --- header rendered.
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "")
        body = widget._trace_area.toPlainText()
        assert "--- Traceback ---" not in body

    def test_no_standalone_reason_label_attribute(self, qtbot):
        # AC-NoStandaloneReasonLabel
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "")
        # The widget no longer carries a _reason_label attribute.
        assert not hasattr(widget, "_reason_label") or \
            getattr(widget, "_reason_label", None) is None

    def test_trace_area_is_copyable(self, qtbot):
        # AC-Copyable — the field is read-only and selectable.
        widget = SethlansSplash(version="9.9.9")
        qtbot.addWidget(widget)
        widget.morph_to_error("boom", "")
        assert widget._trace_area.isReadOnly() is True
        flags = widget._trace_area.textInteractionFlags()
        assert bool(flags & Qt.TextInteractionFlag.TextSelectableByMouse)


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
