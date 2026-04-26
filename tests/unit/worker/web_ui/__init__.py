# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Package marker for worker web_ui unit tests.

Created per spec FR-T-2 / DevOps LOW-1 -- the
``tests/unit/worker/web_ui/`` directory did not exist before the worker
health endpoint feature; this file is required so pytest discovers the
sibling ``test_handlers_health.py`` module.
"""
