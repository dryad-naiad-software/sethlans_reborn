# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Stdlib-only clipboard helper for the bootstrap launcher.

The launcher process does not initialize a ``QGuiApplication`` (Qt
lives in the tray helper, a separate subprocess), so the Qt-based
helper in ``shared/tray/clipboard.py`` cannot be used here. This
module shells out to the OS-native clipboard tool instead.

Tools per platform:

* macOS  -> ``pbcopy``                     (always present)
* Windows -> ``clip``                      (always present)
* Linux  -> ``wl-copy`` | ``xclip`` | ``xsel``
            (any one; may be absent on minimal/headless installs)

If no tool is available (typical on a headless Linux install) the
helper returns ``False`` and the caller falls back to printing
"Copy the token above manually." in the banner.

Security invariants (mirrored from ``shared/tray/clipboard.py``):

* Never raises. All subprocess failures are swallowed and surfaced
  as a ``False`` return.
* The token value is NEVER logged. Only ``token_len=<N>`` appears
  in warning records.
* Empty / non-string input returns ``False`` *before* any subprocess
  call so we never invoke a clipboard tool for invalid input.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

logger = logging.getLogger(__name__)

# Linux clipboard tool fallback chain. Wayland first (modern desktops),
# then X11. Each entry is the argv list to invoke the tool.
_LINUX_TOOLS: tuple[tuple[str, ...], ...] = (
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "-b", "-i"),
)


def _resolve_command(system: str) -> tuple[str, ...] | None:
    """Return the argv tuple of an available clipboard tool, or None."""
    if system == "Darwin":
        if shutil.which("pbcopy"):
            return ("pbcopy",)
        return None
    if system == "Windows":
        if shutil.which("clip"):
            return ("clip",)
        return None
    # Treat anything else as Linux/BSD-family.
    for tool in _LINUX_TOOLS:
        if shutil.which(tool[0]):
            return tool
    return None


def copy_to_clipboard_native(text: str) -> bool:
    """Best-effort copy of *text* to the OS clipboard via a native tool.

    Returns ``True`` iff the copy succeeded. Never raises.
    """
    if not isinstance(text, str) or not text:
        logger.warning("clipboard copy skipped: empty or non-string input")
        return False

    text_len = len(text)
    cmd = _resolve_command(platform.system())
    if cmd is None:
        logger.warning(
            "clipboard copy failed: no native clipboard tool available; "
            "text_len=%d",
            text_len,
        )
        return False

    try:
        subprocess.run(
            list(cmd),
            input=text,
            text=True,
            check=True,
            timeout=2,
        )
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ):
        logger.warning(
            "clipboard copy failed: %s exited non-zero or could not run; "
            "text_len=%d",
            cmd[0],
            text_len,
        )
        return False
