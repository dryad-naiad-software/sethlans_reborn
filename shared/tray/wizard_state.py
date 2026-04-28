# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read the wizard-subprocess state files (spec FR-3).

The launcher writes two files into ``<data_dir>/wizard/`` while the
setup wizard is running:

* ``port`` — the public TLS port Caddy is bound to (per the wizard /
  Caddy consolidation in #170). The launcher writes this AFTER Caddy
  has bound, so the file's presence is a proxy for "wizard URL is
  reachable now."
* ``.setup_token`` — the one-shot bearer token the user pastes into
  the wizard's ``TokenEntryComponent`` to authenticate the session.

Both files are tiny (<<1 KB). This reader is hardened against missing
files, oversize files, decode failures, and unparseable port values.
It NEVER raises and NEVER logs the token value; only ``token_len`` is
permitted in log records (parity with ``shared/tray/clipboard.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Both wizard files are tiny — port is at most 5 ASCII digits, the
# token is bounded by the launcher generator. The 1 KB cap matches
# ``shared/tray/menu_manager_helpers``'s manager.ini cap.
_MAX_BYTES = 1024


@dataclass(frozen=True)
class WizardState:
    """Snapshot of the wizard's port + setup token, both required."""

    port: int
    token: str


def _read_capped(path: Path) -> Optional[str]:
    """Return the file's stripped contents, or ``None`` on any failure.

    Bounded by ``_MAX_BYTES``. Never raises. Token-bearing paths must
    not log the value — callers should not pass the contents to log
    formatting; this helper logs only the path on failure.
    """
    try:
        if not path.exists():
            return None
    except OSError as exc:
        logger.warning("Could not stat %s: %s", path, exc)
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.warning("stat failed on %s: %s", path, exc)
        return None
    if size > _MAX_BYTES:
        logger.warning(
            "%s exceeds %d bytes; ignoring", path, _MAX_BYTES,
        )
        return None
    try:
        return path.read_text(
            encoding="utf-8", errors="replace",
        ).strip()
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def read_wizard_state(data_dir: Path) -> Optional[WizardState]:
    """Return the wizard state from ``<data_dir>/wizard/``.

    Returns ``None`` when:

    * Either the ``port`` or ``.setup_token`` file is missing /
      unreadable / oversize (>1 KB).
    * The token file is empty after stripping whitespace.
    * The port file does not parse as a positive ``int`` in 1..65535.

    Never raises. Never logs the token value; failure paths log
    ``token_len=<N>`` at most.
    """
    port_raw = _read_capped(data_dir / "wizard" / "port")
    token = _read_capped(data_dir / "wizard" / ".setup_token")
    if port_raw is None or not token:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        logger.warning(
            "wizard/port did not parse as int; token_len=%d", len(token),
        )
        return None
    if port <= 0 or port > 65535:
        logger.warning(
            "wizard/port out of range (%d); token_len=%d",
            port, len(token),
        )
        return None
    # NOTE: token value never logged.
    return WizardState(port=port, token=token)
