# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Named exceptions for the Caddy fetcher (drive distinct exit codes)."""


class IntegrityError(Exception):
    """Raised when SHA-256 does not match the value pinned in caddy.lock."""


class GpgVerificationError(Exception):
    """Raised when an explicit ``--verify-gpg`` check fails."""
