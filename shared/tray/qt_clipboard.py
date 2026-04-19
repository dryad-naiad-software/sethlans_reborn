# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Qt-based clipboard helper for the setup token.

Replaces the subprocess-based helper in ``shared/tray/clipboard.py`` with
a ``QClipboard`` implementation for the PySide6 tray migration (see
``tray-pyside6-migration.md`` FR-4).

Security invariants (mirrored from the legacy helper's tests):

* Never raises.  Any Qt failure (ImportError in the import block is not
  possible here because PySide6 is a hard dependency of the tray; but
  ``QGuiApplication.instance()`` may be ``None``, ``clipboard()`` may
  return ``None``, and ``setText`` may raise in theory) is swallowed and
  surfaced as a ``False`` return.
* The token value is NEVER logged.  Only ``token_len=<N>`` appears in
  warning records.
* Empty / non-string input returns ``False`` *before* any Qt call so we
  never touch the clipboard for invalid input.
* ``QClipboard.Mode.Clipboard`` is passed explicitly.  The X11 primary
  selection (``Mode.Selection``) is never used — it would leak the token
  to any middle-click paste.
* A ``qInstallMessageHandler`` shim drops Qt log records in the
  ``qt.gui.clipboard`` category so that environments with
  ``QT_LOGGING_RULES='qt.gui.clipboard.debug=true'`` or
  ``QT_DEBUG_PLUGINS=1`` cannot echo the clipboard payload to stderr.
  Non-clipboard Qt warnings are forwarded to stderr unchanged so general
  Qt diagnostics remain visible.
"""

from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QClipboard, QGuiApplication

logger = logging.getLogger(__name__)

# Category prefix emitted by Qt's clipboard subsystem.  Matches both
# ``qt.gui.clipboard`` and any future sub-categories (e.g.
# ``qt.gui.clipboard.debug``).
_CLIPBOARD_CATEGORY_PREFIX = "qt.gui.clipboard"

# Idempotent install guard for the Qt message handler.  The tray helper
# may call ``copy_token_to_clipboard`` many times over the life of the
# process; we install exactly once.
_handler_lock = threading.Lock()
_handler_installed = False
# The previous Qt message handler returned by ``qInstallMessageHandler``.
# Non-clipboard records are forwarded to it so Qt's own diagnostics
# (plugin loads, font/platform problems) remain visible downstream.
_previous_handler = None


def _qt_message_handler(mode: QtMsgType, context, message: str) -> None:
    """Drop clipboard-category Qt log records; forward everything else.

    Scope is intentionally narrow: only records whose category begins
    with ``qt.gui.clipboard`` are silenced.  Other Qt warnings (plugin
    load errors, font issues, platform problems, etc.) are forwarded to
    the previous handler (if any) or written to stderr so developers
    still see them.
    """
    try:
        category = getattr(context, "category", None) or ""
        if isinstance(category, bytes):
            category = category.decode("utf-8", errors="replace")
        if category and category.startswith(_CLIPBOARD_CATEGORY_PREFIX):
            return
    except Exception:
        # If anything about the context is malformed, fall through to
        # the default forwarding path rather than raising from inside a
        # Qt callback.
        pass

    # Prefer chaining to whatever handler was installed before us so
    # other libraries / Qt's own default formatting still apply.
    if _previous_handler is not None:
        try:
            _previous_handler(mode, context, message)
            return
        except Exception:
            # Fall through to stderr if the previous handler blew up —
            # never raise out of a Qt message handler.
            pass

    try:
        prefix = {
            QtMsgType.QtDebugMsg: "QtDebug",
            QtMsgType.QtInfoMsg: "QtInfo",
            QtMsgType.QtWarningMsg: "QtWarning",
            QtMsgType.QtCriticalMsg: "QtCritical",
            QtMsgType.QtFatalMsg: "QtFatal",
        }.get(mode, "Qt")
        sys.stderr.write(f"{prefix}: {message}\n")
    except Exception:
        # Never raise out of a Qt message handler.
        pass


def _ensure_message_handler_installed() -> None:
    """Install the clipboard-redacting Qt message handler once."""
    global _handler_installed, _previous_handler
    if _handler_installed:
        return
    with _handler_lock:
        if _handler_installed:
            return
        try:
            # qInstallMessageHandler returns the previous handler; we
            # chain to it so non-clipboard messages survive.
            _previous_handler = qInstallMessageHandler(_qt_message_handler)
        except Exception:
            # If the handler cannot be installed we still proceed — the
            # copy itself is what matters; redaction is defense in depth.
            logger.debug("qt clipboard message handler install failed")
        _handler_installed = True


def copy_token_to_clipboard(token: str) -> bool:
    """Best-effort copy of *token* to the OS clipboard via QClipboard.

    Parameters
    ----------
    token : str
        The token to copy.  Only its length is ever logged.

    Returns
    -------
    bool
        ``True`` iff the copy succeeded.  Never raises.
    """
    if not isinstance(token, str) or not token:
        logger.warning("clipboard copy skipped: empty or non-string token")
        return False

    token_len = len(token)

    _ensure_message_handler_installed()

    try:
        app = QGuiApplication.instance()
        if app is None:
            logger.warning(
                "clipboard copy failed: no QGuiApplication instance; "
                "token_len=%d",
                token_len,
            )
            return False

        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            logger.warning(
                "clipboard copy failed: QGuiApplication.clipboard() "
                "returned None; token_len=%d",
                token_len,
            )
            return False

        clipboard.setText(token, QClipboard.Mode.Clipboard)
        return True
    except Exception:
        logger.warning(
            "clipboard copy failed: Qt error; token_len=%d", token_len,
        )
        return False
