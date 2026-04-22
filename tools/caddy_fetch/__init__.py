# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Caddy binary fetch/verify helpers for Sethlans Reborn.

The ``tools/fetch_caddy.py`` CLI re-exports :func:`main` from this
package. The implementation is split across modules to stay under the
300-line Python file limit.
"""

from .cli import main
from .exceptions import GpgVerificationError, IntegrityError

__all__ = [
    "main",
    "GpgVerificationError",
    "IntegrityError",
]
