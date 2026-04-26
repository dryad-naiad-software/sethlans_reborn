# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Always-on runtime handlers for the worker web UI.

Mirrors the ``web_ui/setup/`` split: handlers in this package are
reachable in both setup mode and runtime mode (no setup-gate dependency)
and serve probe/monitoring traffic.  The first occupant is the
``/api/health/`` endpoint (``handlers_health.py``).
"""
