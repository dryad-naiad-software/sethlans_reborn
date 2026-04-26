# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Process-local runtime state for the Sethlans worker agent.

Mirrors the pattern used by ``manager/sethlans_manager/runtime_state.py``.
Holds values that are determined at process startup and read by request
handlers.  Using a mutable module as a singleton avoids any per-request
state-init dance.
"""

import uuid
from typing import Optional

# Populated at module-load time -- a fresh UUID4 hex string per worker
# boot.  Used by ``GET /api/health/`` so external probes (the standalone
# wizard's worker-only redirect probe, monitoring) can detect when the
# worker has actually restarted.  We bind it at module-load time so
# tests and short-lived utility processes get a non-empty value without
# any explicit init step; ``agent.py`` performs an eager top-level
# import of this module so the bind happens single-threaded during
# agent-module load, before Waitress's worker threads can race on
# first-import.
worker_boot_id: Optional[str] = uuid.uuid4().hex
