# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-process boot id sent as ``worker_boot_id`` in the registration
heartbeat (Unreal foundation FR-RT-12).

A new value per worker process lets the manager tell a restarted worker
from a live one and requeue the Unreal jobs the old process left
RENDERING (boot-time reconciliation). Bound once at import; 32 lowercase
hex characters (``^[0-9a-f]{32}$``, the manager's accepted form).

Separate from ``runtime_state.worker_boot_id`` (the web UI health
endpoint's restart marker) on purpose: the manager-facing id is not
exposed through any worker HTTP endpoint (NF-8).
"""
import uuid

BOOT_ID: str = uuid.uuid4().hex
