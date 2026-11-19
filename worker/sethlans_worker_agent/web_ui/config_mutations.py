# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Runtime config mutation handlers for the worker web UI.

Extracted from server.py for file size compliance. Every mutator is
serialized on a single module-level lock. The graphics card choice is not
changed here: it is the ``compute`` section, written only by ``cards.store``.
"""
import threading

from sethlans_worker_agent import config

# Lock serializing all config mutations.
_config_lock = threading.Lock()


def apply_config_change(key, value):
    """Apply a validated config change. Raises ValueError on bad input."""
    with _config_lock:
        if key == 'POLLING_INTERVAL':
            val = int(value)
            if not (1 <= val <= 3600):
                raise ValueError('Polling interval must be 1-3600')
            config.JOB_POLLING_INTERVAL_SECONDS = val
        elif key == 'HEARTBEAT_INTERVAL':
            val = int(value)
            if not (1 <= val <= 3600):
                raise ValueError('Heartbeat interval must be 1-3600')
            config.HEARTBEAT_INTERVAL_SECONDS = val
