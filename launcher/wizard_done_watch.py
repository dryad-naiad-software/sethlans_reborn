# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``.wizard_done`` marker watch (FR-L7a).

Extracted from :mod:`launcher.wizard_orchestration` to keep that
module under the 300-line ceiling. The watch loop polls every
:data:`WIZARD_DONE_POLL_INTERVAL` seconds for an HMAC-validated
``.wizard_done`` marker, exits if the wizard subprocess dies or the
idle timeout elapses, and aborts cleanly on a tray-quit event.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from launcher import supervision, wizard_dir, wizard_ipc

logger = logging.getLogger(__name__)

# FR-L7a polling cadence and default idle timeout. Re-exported by
# :mod:`launcher.wizard_orchestration` for backwards compatibility.
WIZARD_DONE_POLL_INTERVAL = 0.5
DEFAULT_WIZARD_IDLE_TIMEOUT = 30 * 60  # 30 minutes


def wait_for_wizard_done(
    wizard_proc: subprocess.Popen,
    data_dir: Path,
    ipc_secret: bytes,
    idle_timeout: float = DEFAULT_WIZARD_IDLE_TIMEOUT,
    poll_interval: float = WIZARD_DONE_POLL_INTERVAL,
) -> tuple[Optional[dict], str]:
    """Poll for ``.wizard_done`` until one of FR-L7a's exits fires.

    Returns ``(payload_or_None, reason)``. ``reason`` is one of
    ``"done"``, ``"wizard_failed"``, ``"wizard_no_handshake"``,
    ``"idle_timeout"``, ``"quit_requested"``.
    """
    marker_path = (
        wizard_dir.wizard_dir(data_dir) / wizard_ipc.MARKER_WIZARD_DONE
    )
    deadline = time.monotonic() + idle_timeout
    while True:
        # Marker observation has highest precedence: a valid marker even
        # after wizard exit means the handshake completed.
        payload = wizard_ipc.read_marker(
            marker_path, ipc_secret, "wizard_done", data_dir,
        )
        if payload is not None:
            wizard_ipc.delete_marker(marker_path)
            logger.info("wizard_done observed (FR-L7)")
            return payload, "done"

        rc = wizard_proc.poll()
        if rc is not None:
            if rc != 0:
                logger.error(
                    "wizard exited non-zero (code %d) before .wizard_done",
                    rc,
                )
                return None, "wizard_failed"
            logger.error(
                "wizard exited 0 without writing .wizard_done; "
                "treating as handshake failure",
            )
            return None, "wizard_no_handshake"

        if time.monotonic() >= deadline:
            logger.warning(
                "Wizard idle-timeout (%.0fs) elapsed without .wizard_done",
                idle_timeout,
            )
            return None, "idle_timeout"

        # Issue #163: a tray quit during the (potentially 30 minute)
        # wizard wait must abort within one poll interval rather than
        # waiting out the idle timeout.
        if supervision.wait_or_quit(poll_interval):
            logger.info("Tray quit observed during wizard wait")
            return None, "quit_requested"
