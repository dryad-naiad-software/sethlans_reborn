# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker setup wizard package.

Provides the setup gate (503 until wizard completes), route
dispatcher, and handler modules for each wizard step.
"""

from .gate import init_gate, is_in_setup_mode, mark_setup_complete
from .routes import handle_setup_request

__all__ = [
    "init_gate",
    "is_in_setup_mode",
    "mark_setup_complete",
    "handle_setup_request",
]
