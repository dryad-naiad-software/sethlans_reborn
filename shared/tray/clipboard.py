# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cross-platform clipboard helper for the setup token.

Hardened per ``setup-token-entry.md`` FR-15 and ``tray-helper-unified.md``
FR-10:

* Token is passed via **stdin**, never argv or environment.
* ``subprocess.run`` is called with the default ``shell=False``; the
  command argument is a fixed literal list — no string concatenation.
* Any exception is caught; the log record contains only the token
  length (``token_len=<N>``), never the token value itself.
* ``result.stdout`` / ``result.stderr`` are NEVER logged — misbehaving
  clipboard implementations could echo the token.
* Platform gate: ``sys.platform == "win32"`` → ``clip``;
  ``"darwin"`` → ``pbcopy``; anything else logs a hint and returns
  ``False`` without spawning a subprocess.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# Subprocess timeout.  Clipboard writes are instantaneous; 5 s is a
# generous upper bound that still prevents hangs.
_CLIPBOARD_TIMEOUT_SECONDS = 5.0


def _run_clipboard(command: list[str], token: str) -> bool:
    """Invoke *command* with *token* on stdin.

    Returns ``True`` iff the command exited with code 0.  All
    exception paths log only the token length and return ``False``.
    """
    token_len = len(token)
    try:
        result = subprocess.run(
            command,
            input=token,
            text=True,
            capture_output=True,
            timeout=_CLIPBOARD_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        logger.warning(
            "clipboard copy failed: command not found; token_len=%d",
            token_len,
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning(
            "clipboard copy failed: timeout; token_len=%d", token_len,
        )
        return False
    except (OSError, subprocess.SubprocessError):
        logger.warning(
            "clipboard copy failed: subprocess error; token_len=%d",
            token_len,
        )
        return False

    if result.returncode != 0:
        logger.warning(
            "clipboard copy failed: returncode=%d; token_len=%d",
            result.returncode, token_len,
        )
        return False
    return True


def copy_token_to_clipboard(token: str) -> bool:
    """Best-effort copy of *token* to the OS clipboard.

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

    if sys.platform == "win32":
        return _run_clipboard(["clip"], token)
    if sys.platform == "darwin":
        return _run_clipboard(["pbcopy"], token)

    logger.info(
        "Clipboard copy skipped on Linux; copy the token from the "
        "console above.",
    )
    return False
