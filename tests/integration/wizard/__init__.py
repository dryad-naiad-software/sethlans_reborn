# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Integration tests for the standalone wizard subprocess (Spec 1 / D1).

Each test in this package spawns ``python wizard/run_wizard.py`` as a
real subprocess against a per-test data directory and exercises the
HTTPS endpoints over a self-signed certificate using ``urllib.request``
+ ``ssl.CERT_NONE``. Fixtures live in :mod:`conftest`.
"""
