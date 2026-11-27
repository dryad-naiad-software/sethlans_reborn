# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Always-on runtime handlers for the worker web UI.

Handlers in this package are always reachable (the worker has no setup
gate, setup-rebuild FR-WK-7) and serve probe/monitoring traffic.  The first occupant is the
``/api/health/`` endpoint (``handlers_health.py``).
"""
