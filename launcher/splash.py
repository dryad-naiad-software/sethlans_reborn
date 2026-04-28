# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Startup splash widget for the Sethlans launcher.

Owns :class:`SethlansSplash`. See
``development/specs/launcher_startup_splash.md`` for the contract.
Holds success (wordmark/Starting/version) and error (traceback +
Show log / Close) states, swapped via :meth:`morph_to_error`.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QFont, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from shared.frozen_paths import get_branding_dir

logger = logging.getLogger(__name__)

_SUCCESS_SIZE = (420, 140)
_ERROR_SIZE = (420, 420)
_WORDMARK_ASSET = "logo-text-dark.png"
_PANEL_BG = "#fafafa"
_MUTED = "#888888"
_ERROR_RED = "#c62828"
_TEXT_DARK = "#1f1f1f"
_BORDER = "#cccccc"
_BUTTON_BG = "#ffffff"
_BUTTON_HOVER = "#f0f0f0"
_REASON_MAX_CHARS = 600

# Forces light theme regardless of OS dark mode (issue #161).
_STYLESHEET = (
    f"QWidget {{ background-color: {_PANEL_BG}; color: {_TEXT_DARK}; }}"
    f"QPlainTextEdit {{ background-color: {_PANEL_BG};"
    f" color: {_TEXT_DARK}; border: 1px solid {_BORDER}; }}"
    f"QPushButton {{ background-color: {_BUTTON_BG};"
    f" color: {_TEXT_DARK}; border: 1px solid {_BORDER};"
    f" padding: 4px 12px; }}"
    f"QPushButton:hover {{ background-color: {_BUTTON_HOVER}; }}"
)


class SethlansSplash(QWidget):
    """Borderless identity banner shown during launcher startup."""

    def __init__(
        self,
        version: str,
        parent: Optional[QWidget] = None,
        log_path: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self._version = version
        self._log_path = log_path
        self._error_mode = False
        self._trace_area: Optional[QPlainTextEdit] = None
        self._reason_label: Optional[QLabel] = None
        self._title_label: Optional[QLabel] = None
        self._starting_label: Optional[QLabel] = None
        self._error_widgets_built = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setWindowTitle("Sethlans")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(_STYLESHEET)

        self._build_success_layout()
        self.resize(*_SUCCESS_SIZE)
        self._centre_on_primary_screen()

    # ---- Construction ----

    def _build_success_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 10)
        root.setSpacing(8)

        self._wordmark_label = QLabel(self)
        self._wordmark_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = self._load_wordmark_pixmap()
        if pixmap is not None and not pixmap.isNull():
            self._wordmark_label.setPixmap(pixmap)
        else:
            self._wordmark_label.setText("Sethlans")
            self._wordmark_label.setFont(_make_font(18, bold=True))
        wordmark_row = QHBoxLayout()
        wordmark_row.setContentsMargins(0, 20, 0, 0)
        wordmark_row.addSpacing(35)
        wordmark_row.addWidget(self._wordmark_label)
        wordmark_row.addStretch(1)
        root.addLayout(wordmark_row)

        self._starting_label = QLabel("Starting...", self)
        self._starting_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._starting_label.setFont(_make_font(10))
        root.addWidget(self._starting_label)

        version_row = QHBoxLayout()
        version_row.addStretch(1)
        self._version_label = QLabel(f"v{self._version}", self)
        self._version_label.setFont(_make_font(8))
        self._version_label.setStyleSheet(f"color: {_MUTED};")
        align = Qt.AlignmentFlag
        self._version_label.setAlignment(align.AlignRight | align.AlignBottom)
        version_row.addWidget(self._version_label)
        root.addLayout(version_row)

    def _load_wordmark_pixmap(self) -> Optional[QPixmap]:
        try:
            path = get_branding_dir() / _WORDMARK_ASSET
        except Exception:  # pragma: no cover — defensive
            logger.exception("Failed to resolve branding dir")
            return None
        if not path.is_file():
            logger.warning("Wordmark asset missing at %s", path)
            return None
        pix = QPixmap(str(path))
        if pix.isNull():
            logger.warning("Wordmark asset failed to load: %s", path)
            return None
        mode = Qt.TransformationMode.SmoothTransformation
        return pix.scaledToHeight(60, mode)

    def _centre_on_primary_screen(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        screen = app.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(geom.center())
        self.move(frame.topLeft())

    # ---- Public API ----

    def close_for_success(self) -> None:
        """Dismiss the splash cleanly on the happy-path cold_boot_ready."""
        self.close()

    def morph_to_error(self, reason: str, traceback_text: str) -> None:
        """Swap to the error card (main-thread only, idempotent)."""
        if self._error_mode:
            # Refresh copy on subsequent calls.
            self._reason_label.setText(self._format_reason_line(reason))
            self._trace_area.setPlainText(traceback_text)
            return

        self._error_mode = True
        self._build_error_widgets()
        self._starting_label.hide()
        self._title_label.show()
        self._reason_label.setText(self._format_reason_line(reason))
        self._reason_label.show()
        self._trace_area.setPlainText(traceback_text)
        self._trace_area.show()
        for btn in self._error_buttons:
            btn.show()
        self.resize(*_ERROR_SIZE)
        self._centre_on_primary_screen()

    # ---- Error layout ----

    def _build_error_widgets(self) -> None:
        if self._error_widgets_built:
            return
        self._error_widgets_built = True

        layout = self.layout()

        self._title_label = QLabel("Failed to start", self)
        self._title_label.setFont(_make_font(12, bold=True))
        self._title_label.setStyleSheet(f"color: {_ERROR_RED};")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.hide()
        layout.insertWidget(1, self._title_label)

        self._reason_label = QLabel("", self)
        self._reason_label.setFont(_make_font(9))
        self._reason_label.setWordWrap(True)
        flag = Qt.TextInteractionFlag.TextSelectableByMouse
        self._reason_label.setTextInteractionFlags(flag)
        self._reason_label.hide()
        layout.insertWidget(2, self._reason_label)

        self._trace_area = QPlainTextEdit(self)
        self._trace_area.setReadOnly(True)
        wrap = QPlainTextEdit.LineWrapMode.WidgetWidth
        self._trace_area.setLineWrapMode(wrap)
        mono = QFont("Courier New")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(8)
        self._trace_area.setFont(mono)
        self._trace_area.hide()
        layout.insertWidget(3, self._trace_area, 1)

        buttons_row = QHBoxLayout()
        show_log_btn = QPushButton("Show log", self)
        show_log_btn.clicked.connect(self._on_show_log)
        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self._dismiss_and_quit)
        buttons_row.addWidget(show_log_btn)
        buttons_row.addWidget(close_btn)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)
        self._error_buttons = (show_log_btn, close_btn)

    @staticmethod
    def _format_reason_line(reason: str) -> str:
        # Join newlines with spaces; bound at _REASON_MAX_CHARS.
        joined = " ".join(reason.splitlines()) if reason else ""
        if len(joined) > _REASON_MAX_CHARS:
            joined = joined[: _REASON_MAX_CHARS - 3] + "..."
        return f"Reason: {joined}"

    def _on_show_log(self) -> None:
        path = self._log_path
        if path is None or not path.is_file():
            logger.warning("No launcher log file available at %s", path)
            return
        try:
            _open_in_default_app(path)
        except Exception:  # pragma: no cover — OS integration
            logger.exception("Failed to open launcher log at %s", path)

    # ---- Dismissal (issue #162: Tool windows skip quitOnLast...) ----

    def _quit_app(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _dismiss_and_quit(self) -> None:
        """Close-button handler: hide widget AND quit Qt."""
        self.close()
        self._quit_app()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Quit Qt on any close path (alt-F4, OS close, .close())."""
        super().closeEvent(event)
        self._quit_app()

    # ---- Input handling ----

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Swallow alt-F4 in success mode; error mode lets it through.
        if not self._error_mode:
            is_f4 = event.key() == Qt.Key.Key_F4
            mods = event.modifiers()
            if is_f4 and bool(mods & Qt.KeyboardModifier.AltModifier):
                event.accept()
                return
        super().keyPressEvent(event)


def _make_font(point_size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(point_size)
    if bold:
        font.setBold(True)
    return font


def _open_in_default_app(path: Path) -> None:
    """Open ``path`` in the platform default handler."""
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if system == "Darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])


__all__ = ["SethlansSplash"]
