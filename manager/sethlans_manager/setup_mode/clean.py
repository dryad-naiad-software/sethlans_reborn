# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Text from the worker program made safe to show (FR-SEC-9).

Shared by ``worker_cli`` (the worker's messages) and ``worker_form`` /
``worker_outcome`` (its answers); kept apart so ``worker_cli`` need not
import ``worker_form``, which imports it.
"""
MAX_TEXT = 64


def clean_text(value, max_length: int = MAX_TEXT) -> str:
    """``value`` as printable text (no control, format or separator
    characters other than the space), cut to ``max_length``; ``""`` for
    anything that is not a string."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable()).strip()[:max_length]
