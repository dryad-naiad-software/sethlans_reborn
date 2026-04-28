# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""WSGI request handlers for the wizard's HTTP surface (Spec 1).

Each handler module owns one route family:

* ``auth.py`` — ``POST /api/wizard/auth/`` (A3, FR-W7)
* (A4) ``topology.py`` — ``POST /api/wizard/topology/`` (FR-W8)
* (A4) ``done.py`` — ``POST /api/wizard/done/`` (FR-W9 / FR-W9a)
* (A4) ``runtime_ready.py`` — ``GET /api/wizard/runtime-ready/`` (FR-W14)
* ``health.py`` — ``GET /api/health/`` (issue #160; FR-W14 cold-boot probe)

The top-level dispatcher in ``wizard/sethlans_wizard/server.py`` routes
``PATH_INFO`` to the correct handler.
"""
