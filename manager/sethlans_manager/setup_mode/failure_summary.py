# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The fixed summary of ``last_failure`` that ``/api/health/`` shows (#348).

Health needs no session, so anyone on the LAN can read it. The reason
the launcher wrote can name the admin account, a database host, address,
port and user, or a full path with the OS account name, so health never
shows it. It shows a fixed phrase chosen by the reason's known prefix
(:data:`PREFIX_PHRASES`); a bare ``<program> stopped with exit code N``
(``launcher.components.failure_reason``) is kept as it is, as it holds
nothing but fixed text and a number. Anything else gets
:data:`OTHER_PHRASE`. Every phrase ends with :data:`SEE_WHY`.

The full reason stays on the session-gated ``/api/setup/form/``.
"""
import re
from typing import Optional

from sethlans_manager.setup_mode import leftovers

SEE_WHY = " Enter the setup token to see why."
OTHER_PHRASE = "The manager did not start."
COULD_NOT_START = "The manager could not start."
PREFIX_PHRASES = (
    ("First start failed", "The manager's first start failed."),
    ("Database migration failed", "The database could not be prepared."),
    ("The data folder cannot be used", "The data folder cannot be used."),
    ("Django setup failed", COULD_NOT_START),
    ("TLS certificate error", COULD_NOT_START),
)
# The programs whose start the launcher records in last_failure (the manager only).
KNOWN_PROGRAMS = ("The manager",)
_EXIT_CODE = re.compile(
    r"(?:%s) stopped with exit code -?[0-9]+" % "|".join(re.escape(name) for name in KNOWN_PROGRAMS),
    re.ASCII,
)


def summarize(reason: str) -> str:
    """The fixed phrase for ``reason``; never any of its free text."""
    if _EXIT_CODE.fullmatch(reason):
        return reason
    for prefix, phrase in PREFIX_PHRASES:
        if reason.startswith(prefix):
            return phrase + SEE_WHY
    return OTHER_PHRASE + SEE_WHY


def last_failure_summary(settings_dir=None) -> Optional[str]:
    """:func:`summarize` of ``leftovers.last_failure()``; ``None`` when there is none."""
    reason = leftovers.last_failure(settings_dir)
    return None if reason is None else summarize(reason)
